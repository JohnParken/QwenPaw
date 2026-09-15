# -*- coding: utf-8 -*-
"""Request-scoped, reversible previews for the TL JSON response stream.

The parser in this module is deliberately independent from the TL response
codec.  It only decides which already-decoded characters are safe to show in
the UI; the model adapter still keeps the complete response and performs the
authoritative validation before it yields a model block.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterator

logger = logging.getLogger(__name__)

TL_PREVIEW_CAPABILITY = "tl_preview"
PREVIEW_START = "preview_start"
PREVIEW_UPDATE = "preview_update"
PREVIEW_CLEAR = "preview_clear"
_PREVIEW_TYPES = {PREVIEW_START, PREVIEW_UPDATE, PREVIEW_CLEAR}
_CLEAR_REASONS = {"invalid", "correction", "cancel", "error", "commit"}
_DEFAULT_QUEUE_SIZE = 64
_DEFAULT_PREVIEW_LIMIT = 64 * 1024


def _event_type(value: Any) -> str:
    if isinstance(value, PreviewEvent):
        return value.type
    if isinstance(value, dict):
        return str(value.get("type") or "")
    return str(getattr(value, "type", "") or "")


@dataclass(frozen=True, slots=True)
class PreviewEvent:
    """Typed local event carried beside the durable envelope stream."""

    type: str
    run_id: str
    invocation_id: str
    attempt_id: str
    revision: int | None = None
    kind: str | None = None
    item_index: int | None = None
    text: str | None = None
    reason: str | None = None
    object: str = "preview"

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        del mode
        payload: dict[str, Any] = {
            "type": self.type,
            "object": self.object,
            "run_id": self.run_id,
            "invocation_id": self.invocation_id,
            "attempt_id": self.attempt_id,
        }
        for key, value in (
            ("revision", self.revision),
            ("kind", self.kind),
            ("item_index", self.item_index),
            ("text", self.text),
            ("reason", self.reason),
        ):
            if value is not None:
                payload[key] = value
        return payload

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


class PreviewQueue:
    """Bounded snapshots with a reserved clear slot for every admitted start.

    If lifecycle capacity is exhausted, a new attempt is not previewed. Every
    admitted attempt retains its start and eventual clear, even if the client
    stalls for many model calls. Durable model output is unaffected.

    Only public asyncio.Event APIs are used. This is a single-event-loop
    inbox with coalescing admission, not a task queue: it deliberately does
    not expose Queue.put, task_done or join.
    """

    def __init__(
        self,
        maxsize: int = _DEFAULT_QUEUE_SIZE,
        *,
        decode: Callable[[Any], Any] | None = None,
    ) -> None:
        self._maxsize = max(4, int(maxsize))
        self._items: deque[Any] = deque()
        self._ready = asyncio.Event()
        self._decode = decode or (lambda item: item)
        self._active_attempts: set[tuple] = set()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def qsize(self) -> int:
        return len(self._items)

    def empty(self) -> bool:
        return not self._items

    def get_nowait(self) -> Any:
        if not self._items:
            raise asyncio.QueueEmpty
        item = self._items.popleft()
        if not self._items:
            self._ready.clear()
        return item

    async def get(self) -> Any:
        while True:
            try:
                return self.get_nowait()
            except asyncio.QueueEmpty:
                # Cancellation never removes an item. Multiple waiters
                # recheck after wakeup because another may consume first.
                await self._ready.wait()

    @staticmethod
    def _key(payload):
        return tuple(
            _field(payload, key)
            for key in ("run_id", "invocation_id", "attempt_id")
        )

    def put_preview_nowait(self, event: Any) -> bool:
        payload = self._decode(event)
        event_type = _event_type(payload)
        if event_type not in _PREVIEW_TYPES:
            return False
        key = self._key(payload)
        entries = [(item, self._decode(item)) for item in self._items]
        if event_type == PREVIEW_START:
            lifecycle_count = sum(
                _event_type(old) != PREVIEW_UPDATE for _, old in entries
            )
            if (
                key in self._active_attempts
                or lifecycle_count + len(self._active_attempts) + 2
                > self.maxsize
            ):
                return False
        elif key not in self._active_attempts:
            # Reconnects and refused starts must not resurrect stale drafts.
            return False

        entries = [
            (item, old)
            for item, old in entries
            if not (
                _event_type(old) == PREVIEW_UPDATE
                and self._key(old) == key
                and (
                    event_type != PREVIEW_UPDATE
                    or _field(old, "item_index")
                    == _field(payload, "item_index")
                )
            )
        ]
        if len(entries) >= self.maxsize:
            if event_type == PREVIEW_UPDATE:
                return False
            # The admission invariant guarantees a replaceable snapshot if
            # a lifecycle edge reaches a full queue.
            update_index = next(
                index
                for index, (_, old) in enumerate(entries)
                if _event_type(old) == PREVIEW_UPDATE
            )
            entries.pop(update_index)
        if event_type == PREVIEW_START:
            self._active_attempts.add(key)
        elif event_type == PREVIEW_CLEAR:
            self._active_attempts.remove(key)
        self._items = deque(item for item, _ in entries)
        self._items.append(event)
        self._ready.set()
        return True


def _field(item: Any, key: str) -> Any:
    if isinstance(item, PreviewEvent):
        return getattr(item, key, None)
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


async def drain_preview_events(
    queue: asyncio.Queue | PreviewQueue,
    pending_read: asyncio.Future[Any] | None = None,
) -> AsyncIterator[Any]:
    """Stop prefetch before draining previews ahead of durable output.

    A live reader could otherwise take a clear event while the caller yields
    another queued event. Retain a read that already completed, or cancel a
    pending one, so this iterator becomes the queue's sole consumer.
    """
    if pending_read is not None:
        if not pending_read.done():
            pending_read.cancel()
        await asyncio.gather(pending_read, return_exceptions=True)
        if not pending_read.cancelled():
            yield pending_read.result()
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        yield event


@dataclass
class PreviewScope:
    """Context-local bridge between a provider attempt and the executor."""

    run_id: str = ""
    invocation_id: str = ""
    queue: asyncio.Queue | PreviewQueue = field(default_factory=PreviewQueue)
    sink: Any = None
    max_preview_chars: int = _DEFAULT_PREVIEW_LIMIT
    enabled: bool = True
    attempts: dict[str, "PreviewAttempt"] = field(default_factory=dict)
    closed: bool = False

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = "run_" + uuid.uuid4().hex
        if not self.invocation_id:
            self.invocation_id = "invocation_" + uuid.uuid4().hex
        self.max_preview_chars = max(1, int(self.max_preview_chars))

    def publish(self, event: PreviewEvent) -> bool:
        if self.closed or not self.enabled:
            return False
        delivered = False
        queue = self.queue
        if hasattr(queue, "put_preview_nowait"):
            delivered = bool(queue.put_preview_nowait(event))
        else:
            try:
                queue.put_nowait(event)
                delivered = True
            except asyncio.QueueFull:
                delivered = False

        if self.sink is not None and delivered:
            try:
                target = self.sink
                if hasattr(target, "publish"):
                    result = target.publish(event)
                elif hasattr(target, "put_nowait"):
                    target.put_nowait(event)
                    result = None
                elif callable(target):
                    result = target(event)
                else:
                    result = None
                if inspect.isawaitable(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        result.close()  # type: ignore[union-attr]
                delivered = True
            except Exception:  # pylint: disable=broad-except
                logger.debug("preview sink rejected event", exc_info=True)
        return delivered

    def register(self, attempt: "PreviewAttempt") -> None:
        self.attempts[attempt.attempt_id] = attempt

    def unregister(self, attempt: "PreviewAttempt") -> None:
        self.attempts.pop(attempt.attempt_id, None)

    def clear_active(self, reason: str) -> None:
        for attempt in list(self.attempts.values()):
            attempt.clear(reason)

    def drain_nowait(self) -> list[Any]:
        drained: list[Any] = []
        while True:
            try:
                drained.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                return drained


_CURRENT_PREVIEW_SCOPE: ContextVar[PreviewScope | None] = ContextVar(
    "qwenpaw_tl_preview_scope",
    default=None,
)


@contextmanager
def preview_scope(
    *,
    run_id: str = "",
    invocation_id: str = "",
    queue: asyncio.Queue | PreviewQueue | None = None,
    sink: Any = None,
    max_preview_chars: int = _DEFAULT_PREVIEW_LIMIT,
    enabled: bool = True,
) -> Iterator[PreviewScope]:
    """Bind a preview queue to the current request context.

    The token is always reset, including cancellation and provider errors.
    Nested scopes restore their parent independently.
    """
    scope = PreviewScope(
        run_id=run_id,
        invocation_id=invocation_id,
        queue=queue if queue is not None else PreviewQueue(),
        sink=sink,
        max_preview_chars=max_preview_chars,
        enabled=enabled,
    )
    token = _CURRENT_PREVIEW_SCOPE.set(scope)
    try:
        yield scope
    finally:
        scope.closed = True
        _CURRENT_PREVIEW_SCOPE.reset(token)


# Explicit aliases make the integration seam discoverable without exposing
# the ContextVar itself to callers.
bind_preview_scope = preview_scope
preview_context = preview_scope


def current_preview_scope() -> PreviewScope | None:
    return _CURRENT_PREVIEW_SCOPE.get()


def current_preview_queue() -> asyncio.Queue | PreviewQueue | None:
    scope = current_preview_scope()
    return scope.queue if scope is not None and scope.enabled else None


class _StringState:
    """Incrementally decode strings in selected top-level fields."""

    _ESCAPES = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }

    def __init__(self, limit: int) -> None:
        self.value: list[str] = []
        self.limit = max(1, int(limit))
        self.decoded_length = 0
        self.escape = False
        self.unicode_digits = ""
        self.unicode_mode = False
        self.pending_high: int | None = None
        self.awaiting_low = False

    def _append_text(self, text: str) -> bool:
        if self.decoded_length + len(text) > self.limit:
            return False
        self.value.append(text)
        self.decoded_length += len(text)
        return True

    def _flush_high(self) -> bool:
        if self.pending_high is not None:
            if not self._append_text("\ufffd"):
                return False
            self.pending_high = None
            self.awaiting_low = False
        return True

    def _append_codepoint(self, codepoint: int) -> bool:
        if self.pending_high is not None:
            if 0xDC00 <= codepoint <= 0xDFFF:
                text = chr(
                    0x10000
                    + ((self.pending_high - 0xD800) << 10)
                    + codepoint
                    - 0xDC00,
                )
                self.pending_high = None
                self.awaiting_low = False
                return self._append_text(text)
            if not self._flush_high():
                return False
        if 0xD800 <= codepoint <= 0xDBFF:
            self.pending_high = codepoint
        elif 0xDC00 <= codepoint <= 0xDFFF:
            return self._append_text("\ufffd")
        else:
            return self._append_text(chr(codepoint))
        return True

    def consume(self, char: str) -> tuple[str, bool]:
        """Return ``(status, appended)`` where status is done/error/more."""
        if self.unicode_mode:
            if char not in "0123456789abcdefABCDEF":
                return "error", False
            self.unicode_digits += char
            if len(self.unicode_digits) < 4:
                return "more", False
            if not self._append_codepoint(int(self.unicode_digits, 16)):
                return "error", False
            self.unicode_digits = ""
            self.unicode_mode = False
            return "more", True

        if self.awaiting_low:
            if char != "u":
                if not self._flush_high():
                    return "error", False
                self.awaiting_low = False
                # Keep the pending escape so the recursive call decodes the
                # actual escaped character (for example ``\\n``).
                return self.consume(char)
            self.awaiting_low = False
            self.escape = False
            self.unicode_digits = ""
            self.unicode_mode = True
            return "more", False

        if self.escape:
            if char == "u":
                self.escape = False
                self.unicode_digits = ""
                self.unicode_mode = True
                if self.pending_high is not None:
                    self.awaiting_low = False
                return "more", False
            decoded = self._ESCAPES.get(char)
            if decoded is None:
                return "error", False
            if not self._flush_high() or not self._append_text(decoded):
                return "error", False
            self.escape = False
            return "more", True

        if char == '"':
            if not self._flush_high():
                return "error", False
            return "done", False
        if char == "\\":
            self.escape = True
            if self.pending_high is not None:
                self.awaiting_low = True
            return "more", False
        if ord(char) < 0x20:
            return "error", False
        if not self._flush_high() or not self._append_text(char):
            return "error", False
        return "more", True


class _RawValueState:
    """Incrementally consume one unknown or calls JSON value."""

    def __init__(self, first: str, limit: int) -> None:
        self.raw = [first]
        self.limit = limit
        self.stack: list[str] = []
        self.in_string = False
        self.escape = False
        self.scalar = False
        self.complete = False
        self.error = False
        if first == "{":
            self.stack.append("}")
        elif first == "[":
            self.stack.append("]")
        elif first == '"':
            self.in_string = True
        elif first in "-0123456789tfn":
            self.scalar = True
        else:
            self.error = True
        if len(self.raw) > self.limit:
            self.error = True
        if not self.stack and not self.in_string and not self.scalar:
            self.complete = True

    def consume(self, char: str) -> tuple[str, bool]:
        if self.complete or self.error:
            return ("error" if self.error else "done", False)
        if len(self.raw) >= self.limit:
            self.error = True
            return "error", False
        if self.in_string:
            self.raw.append(char)
            if self.escape:
                self.escape = False
            elif char == "\\":
                self.escape = True
            elif char == '"':
                self.in_string = False
                self.complete = not self.stack
            elif ord(char) < 0x20:
                self.error = True
            return (
                "error" if self.error else "done" if self.complete else "more",
                True,
            )

        if self.scalar:
            if char in ",}]" and not self.stack:
                self.complete = True
                return "done", False
            if char.isspace() and not self.stack:
                self.complete = True
                return "done", False
            self.raw.append(char)
            return "more", True

        self.raw.append(char)
        if char == '"':
            self.in_string = True
            return "more", True
        if char in "[{":
            self.stack.append("]" if char == "[" else "}")
            return "more", True
        if char in "]}":
            if not self.stack or self.stack[-1] != char:
                self.error = True
                return "error", True
            self.stack.pop()
            self.complete = not self.stack
        return ("done" if self.complete else "more", True)

    def text(self) -> str:
        return "".join(self.raw)


def _strict_json(raw: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out

    return json.loads(
        raw,
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"invalid JSON constant {value}"),
        ),
    )


class _PreviewParser:
    """One-pass top-level parser for previewable TL response fields."""

    def __init__(self, attempt: "PreviewAttempt", limit: int) -> None:
        self.attempt = attempt
        self.limit = limit
        self.phase = "start"
        self.current_key: str | None = None
        self.string: _StringState | None = None
        self.string_role: str | None = None
        self.raw_value: _RawValueState | None = None
        self.raw_role: str | None = None
        self.root_keys: set[str] = set()
        self.response_type: str | None = None
        self.content = ""
        self.calls_raw = ""
        self._publish_pending = False
        self.invalid = False

    @staticmethod
    def _space(char: str) -> bool:
        return char in " \t\r\n"

    def _fail(self) -> None:
        if self.invalid:
            return
        self.invalid = True
        self.attempt.clear("invalid")

    def _finish_string(self) -> None:
        assert self.string is not None
        value = "".join(self.string.value)
        role = self.string_role
        self.string = None
        self.string_role = None
        if role == "key":
            if value in self.root_keys:
                self._fail()
                return
            self.root_keys.add(value)
            self.current_key = value
            self.phase = "colon"
            return
        if role == "type":
            self.response_type = value
            self.phase = "value_done"
            self._maybe_publish()
        elif role == "content":
            self.content = value
            self.phase = "value_done"
            self._maybe_publish()

    def _start_value(self, char: str) -> None:
        key = self.current_key
        if key in {"type", "content"}:
            if char != '"':
                self._fail()
                return
            self.string = _StringState(self.limit)
            self.string_role = key
            self.phase = "string_value"
            return
        if key == "calls":
            if char != "[":
                self._fail()
                return
            self.raw_value = _RawValueState(char, self.limit)
            self.raw_role = "calls"
            self.phase = "raw_value"
            return
        self.raw_value = _RawValueState(char, self.limit)
        self.raw_role = "unknown"
        self.phase = "raw_value"

    def _finish_raw(self) -> None:
        assert self.raw_value is not None
        raw = self.raw_value.text()
        role = self.raw_role
        self.raw_value = None
        self.raw_role = None
        try:
            value = _strict_json(raw)
        except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
            self._fail()
            return
        if role == "calls":
            if not isinstance(value, list):
                self._fail()
                return
            self.calls_raw = raw
            self.phase = "value_done"
            self._maybe_publish()
        else:
            self.phase = "value_done"

    def _maybe_publish(self) -> None:
        self._publish_pending = True

    def _publish_pending_updates(self) -> None:
        if not self._publish_pending or self.invalid:
            return
        # Build each cumulative snapshot once per input chunk instead of
        # joining the complete value for every decoded character.
        if self.string is not None and self.string_role == "content":
            self.content = "".join(self.string.value)
        if self.raw_value is not None and self.raw_role == "calls":
            self.calls_raw = self.raw_value.text()
        if self.response_type == "final":
            if self.content:
                self.attempt._update(
                    "final_text", 0, self.content
                )  # pylint: disable=protected-access
        elif self.response_type == "tool_calls":
            text = self.calls_raw.strip()
            if text:
                self.attempt._update(
                    "tool_call", 0, text
                )  # pylint: disable=protected-access
        self._publish_pending = False

    def consume(self, char: str) -> None:
        if self.invalid:
            return

        if self.string is not None:
            status, appended = self.string.consume(char)
            if status == "error":
                self._fail()
            elif appended and self.string_role == "content":
                self._maybe_publish()
            elif status == "done":
                self._finish_string()
            return

        if self.raw_value is not None:
            status, consumed = self.raw_value.consume(char)
            if status == "error":
                self._fail()
                return
            if self.raw_role == "calls" and consumed:
                self._maybe_publish()
            if status == "done":
                self._finish_raw()
                if not consumed:
                    self.consume(char)
            return

        if self.phase == "start":
            if self._space(char):
                return
            if char != "{":
                self._fail()
                return
            self.phase = "key_or_end"
        elif self.phase == "key_or_end":
            if self._space(char):
                return
            if char == "}":
                self.phase = "done"
            elif char == '"':
                self.string = _StringState(self.limit)
                self.string_role = "key"
                self.phase = "key_string"
            else:
                self._fail()
        elif self.phase == "colon":
            if self._space(char):
                return
            if char != ":":
                self._fail()
            else:
                self.phase = "value_start"
        elif self.phase == "value_start":
            if self._space(char):
                return
            self._start_value(char)
        elif self.phase == "value_done":
            if self._space(char):
                return
            if char == ",":
                self.current_key = None
                self.phase = "key_or_end"
            elif char == "}":
                self.phase = "done"
            else:
                self._fail()

    def feed(self, chunk: str) -> None:
        for char in chunk:
            if self.invalid:
                break
            # key_string is represented by the same string state as other
            # strings; closing quote is handled in the common branch.
            self.consume(char)
        self._publish_pending_updates()

    def finish(self) -> bool:
        if self.invalid:
            return False
        if (
            self.phase != "done"
            or self.string is not None
            or self.raw_value is not None
        ):
            self._fail()
            return False
        return True


class PreviewAttempt:
    """An invocation-local preview attempt.

    ``feed`` never changes the authoritative response and never blocks the
    provider.  ``clear`` is idempotent, so cancellation/error cleanup can be
    safely repeated by the model, executor, and channel layers.
    """

    def __init__(
        self,
        scope: PreviewScope | None,
        *,
        run_id: str = "",
        invocation_id: str = "",
        attempt_id: str = "",
        max_preview_chars: int | None = None,
    ) -> None:
        self.scope = scope
        self.run_id = run_id or (scope.run_id if scope else "")
        self.invocation_id = invocation_id or (
            scope.invocation_id if scope else ""
        )
        self.attempt_id = attempt_id or "attempt_" + uuid.uuid4().hex
        self.max_preview_chars = max_preview_chars or (
            scope.max_preview_chars if scope else _DEFAULT_PREVIEW_LIMIT
        )
        self.closed = scope is None or not scope.enabled
        self.revision = 0
        self._parser = _PreviewParser(self, self.max_preview_chars)
        if scope is not None and scope.enabled:
            scope.register(self)
            accepted = self._emit(
                PreviewEvent(
                    type=PREVIEW_START,
                    run_id=self.run_id,
                    invocation_id=self.invocation_id,
                    attempt_id=self.attempt_id,
                ),
            )
            if accepted is None:
                self.closed = True
                scope.unregister(self)

    @property
    def active(self) -> bool:
        return not self.closed

    def _emit(self, event: PreviewEvent) -> PreviewEvent | None:
        if self.scope is None or not self.scope.enabled:
            return None
        return event if self.scope.publish(event) else None

    def _update(
        self, kind: str, item_index: int, text: str
    ) -> PreviewEvent | None:
        if self.closed:
            return None
        if len(text) > self.max_preview_chars:
            self.clear("error")
            return None
        self.revision += 1
        return self._emit(
            PreviewEvent(
                type=PREVIEW_UPDATE,
                run_id=self.run_id,
                invocation_id=self.invocation_id,
                attempt_id=self.attempt_id,
                revision=self.revision,
                kind=kind,
                item_index=item_index,
                text=text,
            ),
        )

    def feed(self, chunk: str | bytes) -> list[PreviewEvent]:
        """Consume a TL ``chunk.content`` fragment.

        The return value is useful for deterministic unit tests; normal
        callers use the request-scoped queue and ignore it.
        """
        if self.closed:
            return []
        if isinstance(chunk, bytes):
            try:
                chunk = chunk.decode("utf-8")
            except UnicodeDecodeError:
                self.clear("error")
                return []
        if not isinstance(chunk, str):
            self.clear("invalid")
            return []
        before = self.revision
        self._parser.feed(chunk)
        if self._parser.invalid:
            return []
        # Events are published synchronously. Return the current snapshot for
        # callers that need an observable feed result without queue plumbing.
        if self.revision == before:
            return []
        return [
            PreviewEvent(
                type=PREVIEW_UPDATE,
                run_id=self.run_id,
                invocation_id=self.invocation_id,
                attempt_id=self.attempt_id,
                revision=self.revision,
                kind=(
                    "final_text"
                    if self._parser.response_type == "final"
                    else "tool_call"
                ),
                item_index=0,
                text=(
                    self._parser.content
                    if self._parser.response_type == "final"
                    else self._parser.calls_raw.strip()
                ),
            ),
        ]

    def finish(self) -> bool:
        """Validate only the preview parser's framing, if a caller needs it."""
        if self.closed:
            return False
        return self._parser.finish()

    def clear(self, reason: str) -> PreviewEvent | None:
        if self.closed:
            return None
        normalized = reason if reason in _CLEAR_REASONS else "error"
        self.closed = True
        if self.scope is not None:
            self.scope.unregister(self)
        return self._emit(
            PreviewEvent(
                type=PREVIEW_CLEAR,
                run_id=self.run_id,
                invocation_id=self.invocation_id,
                attempt_id=self.attempt_id,
                reason=normalized,
            ),
        )


class _NoopAttempt(PreviewAttempt):
    def __init__(self) -> None:
        super().__init__(None)


def begin_preview(
    *,
    run_id: str = "",
    invocation_id: str = "",
    attempt_id: str = "",
    max_preview_chars: int | None = None,
) -> PreviewAttempt:
    """Start a preview in the current request scope, or return a no-op."""
    scope = current_preview_scope()
    if scope is None or not scope.enabled or scope.closed:
        return _NoopAttempt()
    return PreviewAttempt(
        scope,
        run_id=run_id,
        invocation_id=invocation_id,
        attempt_id=attempt_id,
        max_preview_chars=max_preview_chars,
    )


def is_preview_event(value: Any) -> bool:
    return _event_type(value) in _PREVIEW_TYPES


def preview_event_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, PreviewEvent):
        return value.as_dict()
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        return (
            dict(payload)
            if isinstance(payload, dict)
            else {"type": _event_type(value)}
        )
    return {"type": _event_type(value)}


__all__ = [
    "PREVIEW_CLEAR",
    "PREVIEW_START",
    "PREVIEW_UPDATE",
    "PreviewAttempt",
    "PreviewEvent",
    "PreviewQueue",
    "PreviewScope",
    "TL_PREVIEW_CAPABILITY",
    "begin_preview",
    "bind_preview_scope",
    "current_preview_queue",
    "current_preview_scope",
    "drain_preview_events",
    "is_preview_event",
    "preview_context",
    "preview_event_payload",
    "preview_scope",
]
