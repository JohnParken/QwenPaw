# -*- coding: utf-8 -*-
"""Async client for the fixed two-step TL ``chatbbc`` protocol."""

from __future__ import annotations

import asyncio
import codecs
from contextlib import asynccontextmanager
import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from .tl_config import TLConfig
from .tl_errors import TLError

_END_EVENTS = {"done", "end"}
_KNOWN_EVENTS = {"chunk", "done", "end", "error"}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _loads_json(raw: str | bytes) -> Any:
    return json.loads(raw, parse_constant=_reject_json_constant)


class _SSEParser:
    """Small stateful SSE parser with strict TL event validation."""

    def __init__(self, max_event_bytes: int) -> None:
        self._max_event_bytes = max_event_bytes
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._buffer = ""
        self._scan_index = 0
        self._event_bytes = 0
        self._event_name = ""
        self._data_lines: list[str] = []
        self._started = False

    def feed(self, raw: bytes) -> list[tuple[str, str | None]]:
        """Consume one arbitrary byte packet and return completed events."""
        try:
            text = self._decoder.decode(raw, final=False)
        except UnicodeDecodeError as exc:
            raise TLError(
                "sse_decode",
                "SSE response is not valid UTF-8",
                "invalid_utf8",
            ) from exc

        if not self._started and text:
            self._started = True
            if text.startswith("\ufeff"):
                text = text[1:]
        self._buffer += text

        events: list[tuple[str, str | None]] = []
        while True:
            line_info = self._next_line()
            if line_info is None:
                break
            line, delimiter_bytes = line_info
            self._event_bytes += len(line.encode("utf-8")) + delimiter_bytes
            self._check_event_size()
            event = self._consume_line(line)
            if event is not None:
                events.append(event)

        self._check_event_size(len(self._buffer.encode("utf-8")))
        return events

    def finish(self) -> list[tuple[str, str | None]]:
        """Flush UTF-8 state; an unterminated event is not dispatched."""
        try:
            text = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise TLError(
                "sse_decode",
                "SSE response ended in an incomplete UTF-8 sequence",
                "invalid_utf8",
            ) from exc
        if text:
            self._buffer += text
            self._check_event_size(len(self._buffer.encode("utf-8")))
        return []

    def _next_line(self) -> tuple[str, int] | None:
        for index in range(self._scan_index, len(self._buffer)):
            char = self._buffer[index]
            if char not in "\r\n":
                continue
            if char == "\r":
                if index + 1 == len(self._buffer):
                    self._scan_index = index
                    return None
                if self._buffer[index + 1] == "\n":
                    delimiter_bytes = 2
                    end = index + 2
                else:
                    delimiter_bytes = 1
                    end = index + 1
            else:
                delimiter_bytes = 1
                end = index + 1
            line = self._buffer[:index]
            self._buffer = self._buffer[end:]
            self._scan_index = 0
            return line, delimiter_bytes
        self._scan_index = (
            len(self._buffer) - 1
            if self._buffer.endswith("\r")
            else len(self._buffer)
        )
        return None

    def _consume_line(self, line: str) -> tuple[str, str | None] | None:
        if line == "":
            event = self._dispatch_event()
            self._event_bytes = 0
            self._event_name = ""
            self._data_lines = []
            return event
        if line.startswith(":"):
            return None

        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""

        if field == "event":
            self._event_name = value
        elif field == "data":
            self._data_lines.append(value)
        # SSE permits fields such as id/retry.  They have no TL meaning and
        # are intentionally ignored; event names and payloads remain strict.
        return None

    def _dispatch_event(self) -> tuple[str, str | None] | None:
        if not self._event_name and not self._data_lines:
            return None

        event_name = self._event_name
        data = "\n".join(self._data_lines)
        if event_name == "error":
            # Only accept the proxy's diagnostic field, never stringify an
            # arbitrary payload (which may include prompts or response bodies).
            # TLError applies credential redaction and a length bound again.
            message = "TL SSE error event"
            try:
                payload = _loads_json(data)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                detail = payload.get("message")
                if isinstance(detail, str) and detail.strip():
                    message = f"{message}: {detail}"
            raise TLError("sse_decode", message, "remote_error")

        if data == "[DONE]":
            return "terminal", "[DONE]"

        if event_name not in _KNOWN_EVENTS and event_name != "":
            raise TLError(
                "sse_decode",
                f"unknown TL SSE event '{event_name}'",
                "unknown_event",
            )

        if event_name == "chunk":
            try:
                payload = _loads_json(data)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise TLError(
                    "sse_decode",
                    "TL chunk data is not valid JSON",
                    "invalid_json",
                ) from exc
            if not isinstance(payload, dict):
                raise TLError(
                    "sse_decode",
                    "TL chunk data must be an object",
                    "invalid_payload",
                )
            content = payload.get("content")
            if not isinstance(content, str):
                raise TLError(
                    "sse_decode",
                    "TL chunk content must be a string",
                    "invalid_payload",
                )
            return "chunk", content

        if event_name in _END_EVENTS:
            try:
                payload = _loads_json(data)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise TLError(
                    "sse_decode",
                    f"TL {event_name} event data is not valid JSON",
                    "invalid_json",
                ) from exc
            if not isinstance(payload, dict):
                raise TLError(
                    "sse_decode",
                    f"TL {event_name} event data must be an object",
                    "invalid_payload",
                )
            if "finished" in payload and payload["finished"] is not True:
                raise TLError(
                    "sse_decode",
                    f"TL {event_name} event did not finish successfully",
                    "unfinished",
                )
            return "terminal", event_name

        # An eventless frame is only meaningful as [DONE].  A data payload
        # with no event name must not be silently interpreted as text.
        raise TLError(
            "sse_decode",
            "TL SSE event has no recognized event name",
            "unknown_event",
        )

    def _check_event_size(self, additional_bytes: int = 0) -> None:
        if self._event_bytes + additional_bytes > self._max_event_bytes:
            raise TLError(
                "sse_decode",
                "TL SSE event exceeded max_sse_event_bytes",
                "event_budget",
            )


class TLTransport:
    """Transport implementing one fresh TL session per text attempt."""

    def __init__(
        self,
        base_url: str,
        config: TLConfig,
        api_key: str = "",
        custom_headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if isinstance(config, TLConfig):
            self.config = config
        else:
            try:
                self.config = TLConfig.model_validate(config)
            except Exception as exc:
                raise TLError(
                    "configuration",
                    "invalid TL configuration",
                    "validation",
                ) from exc

        if not isinstance(base_url, str) or not base_url.strip():
            raise TLError(
                "configuration",
                "TL base_url must be a non-empty string",
                "validation",
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.custom_headers = dict(custom_headers or {})
        # An injected client is shared and remains caller-owned.  Owned
        # clients are created inside each attempt so concurrent model calls do
        # not close or mutate one another's HTTP resources.
        self._client = client
        self._owns_client = client is None
        self.last_termination: str | None = None
        self.last_attempt_id: str | None = None

    async def __aenter__(self) -> "TLTransport":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Retain the compatibility hook; attempt clients close per call."""
        return

    @asynccontextmanager
    async def _attempt_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield a shared injected or private per-attempt HTTP client."""
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=None) as client:
            yield client

    async def init_session(self, system_prompt: str) -> str:
        """Create one TL session and return its opaque session ID."""
        self._validate_text(system_prompt, "system_prompt")
        attempt_id = self._new_attempt_id()
        self.last_termination = None
        deadline = time.monotonic() + self.config.timeout_seconds
        try:
            async with self._attempt_client() as client:
                return await self._init_session(
                    system_prompt, attempt_id, client, deadline
                )
        except asyncio.CancelledError:
            raise

    async def iter_text(
        self,
        system_prompt: str,
        user_payload: str,
        stream: bool = True,
    ) -> AsyncIterator[str]:
        """Yield model text from a fresh init→chat attempt.

        This is an async generator so callers can consume both SSE chunks and
        a non-streaming response through one interface.  No retry is performed
        here; a second attempt belongs to the higher-level JSON correction
        policy.
        """
        self._validate_text(system_prompt, "system_prompt")
        self._validate_text(user_payload, "user_payload")
        if not isinstance(stream, bool):
            raise TLError(
                "configuration",
                "stream must be a boolean",
                "validation",
            )

        attempt_id = self._new_attempt_id()
        self.last_termination = None
        deadline = time.monotonic() + self.config.timeout_seconds
        async with self._attempt_client() as client:
            session_id = await self._init_session(
                system_prompt,
                attempt_id,
                client,
                deadline,
            )
            inner = self._chat_text(
                session_id,
                user_payload,
                stream,
                attempt_id,
                client,
                deadline,
            )
            try:
                async for text in inner:
                    yield text
            finally:
                await inner.aclose()

    async def _init_session(
        self,
        system_prompt: str,
        attempt_id: str,
        client: httpx.AsyncClient,
        deadline: float,
    ) -> str:
        request_id = self._new_request_id()
        body = {
            "appId": self.config.app_id,
            "trCode": self.config.tr_code,
            "trVersion": self.config.tr_version,
            "timestamp": int(time.time() * 1000),
            "requestId": request_id,
            "data": {
                "prompt_variables": [
                    {
                        "name": self.config.system_prompt_variable_name,
                        "value": system_prompt,
                    },
                ],
            },
        }
        body_bytes = self._serialize_body(
            body,
            "init_session",
            attempt_id=attempt_id,
        )
        response_body = await self._read_response_body(
            "/chatbbc/init_session",
            body_bytes,
            client=client,
            deadline=deadline,
            stream=False,
            phase="init_session",
            request_id=request_id,
            attempt_id=attempt_id,
        )
        try:
            payload = _loads_json(response_body)
        except (
            UnicodeDecodeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise TLError(
                "init_session",
                "TL init_session response is not valid JSON",
                "invalid_json",
                attempt_id=attempt_id,
                request_id=request_id,
            ) from exc
        self._validate_envelope_code(
            payload,
            "init_session",
            request_id,
            attempt_id=attempt_id,
        )
        if not isinstance(payload.get("data"), dict):
            raise TLError(
                "init_session",
                "TL init_session response data must be an object",
                "invalid_payload",
                attempt_id=attempt_id,
                request_id=request_id,
            )
        session_id = payload["data"].get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise TLError(
                "init_session",
                "TL init_session response has no session_id",
                "invalid_payload",
                attempt_id=attempt_id,
                request_id=request_id,
            )
        return session_id

    async def _chat_text(
        self,
        session_id: str,
        user_payload: str,
        stream: bool,
        attempt_id: str,
        client: httpx.AsyncClient,
        deadline: float,
    ) -> AsyncIterator[str]:
        request_id = self._new_request_id()
        body = {
            "appId": self.config.app_id,
            "trCode": self.config.tr_code,
            "trVersion": self.config.tr_version,
            "timestamp": int(time.time() * 1000),
            "requestId": request_id,
            "data": {
                "session_id": session_id,
                "txt": user_payload,
                "files": [],
                "stream": stream,
            },
        }
        body_bytes = self._serialize_body(
            body,
            "chat_http",
            attempt_id=attempt_id,
        )
        if not stream:
            response_body = await self._read_response_body(
                "/chatbbc/chat",
                body_bytes,
                client=client,
                deadline=deadline,
                stream=False,
                phase="chat_http",
                request_id=request_id,
                attempt_id=attempt_id,
                session_id=session_id,
            )
            try:
                payload = _loads_json(response_body)
            except (
                UnicodeDecodeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                raise TLError(
                    "response_parse",
                    "TL chat response is not valid JSON",
                    "invalid_json",
                    attempt_id=attempt_id,
                    request_id=request_id,
                    session_id=session_id,
                ) from exc
            self._validate_envelope_code(
                payload,
                "response_parse",
                request_id,
                session_id,
                attempt_id=attempt_id,
            )
            if not isinstance(payload.get("data"), dict):
                raise TLError(
                    "response_parse",
                    "TL chat response data must be an object",
                    "invalid_payload",
                    attempt_id=attempt_id,
                    request_id=request_id,
                    session_id=session_id,
                )
            text = payload["data"].get("txt")
            if not isinstance(text, str):
                raise TLError(
                    "response_parse",
                    "TL chat response txt must be a string",
                    "invalid_payload",
                    attempt_id=attempt_id,
                    request_id=request_id,
                    session_id=session_id,
                )
            self._check_response_size(
                text,
                "response_parse",
                session_id,
                request_id=request_id,
                attempt_id=attempt_id,
            )
            self.last_termination = "json"
            yield text
            return

        parser = _SSEParser(self.config.max_sse_event_bytes)
        response_bytes = 0
        response_text_bytes = 0
        async with self._open_response(
            "/chatbbc/chat",
            body_bytes,
            client=client,
            deadline=deadline,
            stream=True,
            phase="chat_http",
            request_id=request_id,
            attempt_id=attempt_id,
            session_id=session_id,
        ) as response:
            async for raw in self._iter_response_bytes(
                response,
                "sse_decode",
                deadline=deadline,
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            ):
                response_bytes += len(raw)
                if response_bytes > self.config.max_wire_response_bytes:
                    raise TLError(
                        "sse_decode",
                        "TL response exceeded max_wire_response_bytes",
                        "wire_budget",
                        attempt_id=attempt_id,
                        request_id=request_id,
                        session_id=session_id,
                    )
                try:
                    events = parser.feed(raw)
                except TLError as exc:
                    self._annotate_error(
                        exc,
                        attempt_id=attempt_id,
                        request_id=request_id,
                        session_id=session_id,
                    )
                    raise
                for kind, value in events:
                    if kind == "chunk":
                        assert value is not None
                        response_text_bytes = self._check_response_size(
                            value,
                            "sse_decode",
                            session_id,
                            request_id=request_id,
                            attempt_id=attempt_id,
                            current_bytes=response_text_bytes,
                        )
                        yield value
                    elif kind == "terminal":
                        self.last_termination = value
                        try:
                            parser.finish()
                        except TLError as exc:
                            self._annotate_error(
                                exc,
                                attempt_id=attempt_id,
                                request_id=request_id,
                                session_id=session_id,
                            )
                            raise
                        return

            try:
                parser.finish()
            except TLError as exc:
                self._annotate_error(
                    exc,
                    attempt_id=attempt_id,
                    request_id=request_id,
                    session_id=session_id,
                )
                raise

        raise TLError(
            "sse_decode",
            "TL SSE response ended without a termination event",
            "unexpected_eof",
            attempt_id=attempt_id,
            request_id=request_id,
            session_id=session_id,
        )

    async def _read_response_body(
        self,
        path: str,
        body: bytes,
        *,
        client: httpx.AsyncClient,
        deadline: float,
        stream: bool,
        phase: str,
        request_id: str,
        attempt_id: str | None = None,
        session_id: str | None = None,
    ) -> bytes:
        response_body = bytearray()
        async with self._open_response(
            path,
            body,
            client=client,
            deadline=deadline,
            stream=stream,
            phase=phase,
            request_id=request_id,
            attempt_id=attempt_id,
            session_id=session_id,
        ) as response:
            async for raw in self._iter_response_bytes(
                response,
                phase,
                deadline=deadline,
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            ):
                response_body.extend(raw)
                if len(response_body) > self.config.max_wire_response_bytes:
                    raise TLError(
                        phase,
                        "TL response exceeded max_wire_response_bytes",
                        "wire_budget",
                        attempt_id=attempt_id,
                        request_id=request_id,
                        session_id=session_id,
                    )
        return bytes(response_body)

    @asynccontextmanager
    async def _open_response(
        self,
        path: str,
        body: bytes,
        *,
        client: httpx.AsyncClient,
        deadline: float,
        stream: bool,
        phase: str,
        request_id: str,
        attempt_id: str | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[httpx.Response]:
        headers = self._headers(stream)
        response: httpx.Response | None = None
        try:
            request = client.build_request(
                "POST",
                f"{self.base_url}{path}",
                content=body,
                headers=headers,
            )
            try:
                response = await asyncio.wait_for(
                    client.send(request, stream=True),
                    timeout=self._remaining(
                        deadline,
                        phase,
                        attempt_id=attempt_id,
                        request_id=request_id,
                        session_id=session_id,
                    ),
                )
            except TimeoutError as exc:
                raise TLError(
                    phase,
                    "TL attempt timed out",
                    "timeout",
                    attempt_id=attempt_id,
                    request_id=request_id,
                    session_id=session_id,
                ) from exc
            if not 200 <= response.status_code < 300:
                raise TLError(
                    phase,
                    f"TL request returned HTTP {response.status_code}",
                    "http_status",
                    status_code=response.status_code,
                    attempt_id=attempt_id,
                    request_id=request_id,
                    session_id=session_id,
                )
            yield response
        except asyncio.CancelledError:
            raise
        except TLError:
            raise
        except httpx.TimeoutException as exc:
            raise TLError(
                phase,
                "TL HTTP request timed out",
                "timeout",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise TLError(
                phase,
                "TL HTTP transport failed",
                "http_error",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            ) from exc
        finally:
            if response is not None:
                try:
                    await response.aclose()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise TLError(
                        phase,
                        "TL response cleanup failed",
                        "cleanup",
                        attempt_id=attempt_id,
                        request_id=request_id,
                        session_id=session_id,
                    ) from exc

    async def _iter_response_bytes(
        self,
        response: httpx.Response,
        phase: str,
        *,
        deadline: float,
        attempt_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        iterator = response.aiter_bytes()
        while True:
            remaining = self._remaining(
                deadline,
                phase,
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            )
            idle_timeout = self.config.stream_idle_timeout_seconds
            timeout = (
                min(remaining, idle_timeout) if idle_timeout else remaining
            )
            try:
                raw = await asyncio.wait_for(
                    iterator.__anext__(), timeout=timeout
                )
            except StopAsyncIteration:
                return
            except asyncio.CancelledError:
                raise
            except TimeoutError as exc:
                timed_out = time.monotonic() >= deadline
                kind = (
                    "timeout"
                    if timed_out or not idle_timeout
                    else "idle_timeout"
                )
                message = (
                    "TL attempt timed out"
                    if kind == "timeout"
                    else "TL response exceeded stream idle timeout"
                )
                raise TLError(
                    phase,
                    message,
                    kind,
                    attempt_id=attempt_id,
                    request_id=request_id,
                    session_id=session_id,
                ) from exc
            yield raw

    @staticmethod
    def _remaining(
        deadline: float,
        phase: str,
        *,
        attempt_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TLError(
                phase,
                "TL attempt timed out",
                "timeout",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            )
        return remaining

    def _serialize_body(
        self,
        envelope: dict[str, Any],
        phase: str,
        *,
        attempt_id: str | None = None,
    ) -> bytes:
        try:
            body = json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise TLError(
                phase,
                "TL request body could not be encoded as JSON",
                "invalid_request",
                attempt_id=attempt_id,
            ) from exc
        if len(body) > self.config.max_request_bytes:
            raise TLError(
                phase,
                "TL request exceeded max_request_bytes",
                "request_budget",
                attempt_id=attempt_id,
            )
        return body

    def _headers(self, stream: bool) -> dict[str, str]:
        headers = dict(self.custom_headers)
        self._set_header(headers, "Content-Type", "application/json")
        if self.api_key and not self._has_header(headers, "Authorization"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        if stream:
            self._set_header(headers, "Accept", "text/event-stream")
        return headers

    @staticmethod
    def _has_header(headers: Mapping[str, str], name: str) -> bool:
        name = name.casefold()
        return any(key.casefold() == name for key in headers)

    @staticmethod
    def _set_header(headers: dict[str, str], name: str, value: str) -> None:
        for key in list(headers):
            if key.casefold() == name.casefold():
                del headers[key]
        headers[name] = value

    @staticmethod
    def _validate_text(value: Any, field_name: str) -> None:
        if not isinstance(value, str):
            raise TLError(
                "configuration",
                f"{field_name} must be a string",
                "validation",
            )

    def _check_response_size(
        self,
        text: str,
        stage: str,
        session_id: str,
        *,
        attempt_id: str | None = None,
        request_id: str | None = None,
        current_bytes: int = 0,
    ) -> int:
        try:
            text_bytes = len(text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise TLError(
                stage,
                "TL response content is not valid UTF-8",
                "invalid_utf8",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            ) from exc
        total_bytes = current_bytes + text_bytes
        if total_bytes > self.config.max_response_bytes:
            raise TLError(
                stage,
                "TL response exceeded max_response_bytes",
                "response_budget",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            )
        return total_bytes

    @staticmethod
    def _validate_envelope_code(
        payload: Any,
        stage: str,
        request_id: str,
        session_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            raise TLError(
                stage,
                "TL response envelope must be an object",
                "invalid_payload",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            )
        code = payload.get("code")
        if not isinstance(code, (int, float)) or isinstance(code, bool):
            raise TLError(
                stage,
                "TL response code must be a number",
                "invalid_payload",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            )
        if code != 0:
            raise TLError(
                stage,
                f"TL request returned business code {code}",
                "business_error",
                attempt_id=attempt_id,
                request_id=request_id,
                session_id=session_id,
            )

    @staticmethod
    def _annotate_error(
        error: TLError,
        *,
        attempt_id: str,
        request_id: str,
        session_id: str,
    ) -> None:
        """Attach request correlation fields to parser errors in place."""
        error.attempt_id = attempt_id
        error.request_id = request_id
        error.session_id = session_id

    def _new_attempt_id(self) -> str:
        value = uuid.uuid4().hex
        self.last_attempt_id = value
        return value

    @staticmethod
    def _new_request_id() -> str:
        return str(uuid.uuid4())
