"""P0 Runner lifecycle and deterministic fixture/native executors.

The fixture executor deliberately has no QwenPaw imports.  The native
executor imports QwenPaw only after the private attempt environment has been
established and drives the normal ``Workspace.stream_query`` pipeline.
"""
from __future__ import annotations

import base64
import binascii
import copy
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from .contracts import ExecutionContext, RuntimeIdentity, Scope

PROTOCOL_VERSION = "p0.v1"
MAX_FILE_BYTES = 1_048_576
MAX_MARKER_BYTES = 4_096
MAX_FILE_ENTRIES = 1_024
MAX_TOTAL_FILE_BYTES = 8 * MAX_FILE_BYTES
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_HOST_HOME = Path.home().resolve()


class RunnerError(RuntimeError):
    """A rejected lifecycle request or invalid local export."""


@dataclass(frozen=True)
class Boundary:
    """The completed execution boundary."""

    reason: str
    cursor: int
    next_segment: int


@dataclass(frozen=True)
class Quiesced:
    """The barrier at which all local writers have stopped."""

    barrier_id: str


class NullMemory:
    """Explicitly empty Memory adapter used by the P0 profile.

    The six snapshot methods satisfy the lifecycle adapter shape while
    making accidental persistence visible.  ``save`` and ``load`` are kept
    as strict compatibility guards for callers that still use the old
    session-style vocabulary.
    """

    backend_id = "none"
    backend_version = "p0"
    snapshot_schema = "none"

    def __init__(self) -> None:
        self._closed = False
        self._token: str | None = None

    async def probe(self, scope: Scope | None = None) -> dict[str, Any]:
        if self._closed:
            raise RunnerError("memory is closed")
        return {
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "snapshot_schema": self.snapshot_schema,
            "scope": scope.model_dump(mode="json") if scope else None,
            "background_writes": False,
            "supports_restore": False,
        }

    async def quiesce(
        self,
        barrier_id: str,
        deadline: float | None = None,
    ) -> str:
        del deadline
        if self._closed:
            raise RunnerError("memory is closed")
        if not isinstance(barrier_id, str) or not barrier_id:
            raise RunnerError("memory barrier_id must be non-empty")
        self._token = barrier_id
        return barrier_id

    async def export_snapshot(
        self,
        token: str,
        checkpoint_id: str | None = None,
    ) -> None:
        del checkpoint_id
        if self._closed:
            raise RunnerError("memory is closed")
        if token != self._token:
            raise RunnerError("invalid memory quiesce token")
        return None

    async def validate_snapshot(
        self,
        snapshot: Any,
        scope: Scope | None = None,
    ) -> None:
        del scope
        if self._closed:
            raise RunnerError("memory is closed")
        if snapshot is not None:
            raise RunnerError("NullMemory accepts only memory=null")

    async def restore_snapshot(
        self,
        snapshot: Any,
        root: Path | None = None,
        scope: Scope | None = None,
    ) -> None:
        del root, scope
        if self._closed:
            raise RunnerError("memory is closed")
        if snapshot is not None:
            raise RunnerError("NullMemory accepts only memory=null")

    async def close(self, deadline: float | None = None) -> None:
        del deadline
        self._closed = True
        self._token = None

    def save(self, *_args: Any, **_kwargs: Any) -> None:
        raise RunnerError("NullMemory cannot save persistent memory")

    def load(self, *_args: Any, **_kwargs: Any) -> None:
        raise RunnerError("NullMemory cannot load persistent memory")


def _json_copy(value: Any) -> Any:
    """Return a JSON-safe detached copy, rejecting non-JSON values."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerError("value is not valid JSON") from exc


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _safe_relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RunnerError("file path must be a non-empty POSIX relative path")
    path = Path(value)
    if (
        path.is_absolute()
        or path.anchor
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RunnerError("file path escapes the attempt root")
    if path.parts[0] != "workspace":
        raise RunnerError("file path must be under workspace/")
    return path


def _safe_workspace_target(root: Path, relative: Path) -> Path:
    target = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RunnerError(
                "symbolic links are not allowed in exported paths"
            )
    resolved_root = root.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise RunnerError("file path escapes the attempt root") from exc
    return target


def _read_regular_file(path: Path, limit: int) -> bytes:
    """Read one regular file with a bounded second check for growth."""
    if path.is_symlink():
        raise RunnerError("symbolic links are not allowed in exported paths")
    try:
        info = path.stat()
    except OSError as exc:
        raise RunnerError(f"cannot stat file: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise RunnerError(f"only regular files may be exported: {path}")
    if info.st_size > limit:
        raise RunnerError(f"file is too large: {path}")
    try:
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
    except OSError as exc:
        raise RunnerError(f"cannot read file: {path}") from exc
    if len(content) > limit:
        raise RunnerError(f"file grew beyond limit: {path}")
    return content


def _segment_values(segment: Any) -> tuple[str, int]:
    if isinstance(segment, Mapping):
        marker = segment.get("marker")
        index = segment.get("index")
    else:
        marker = getattr(segment, "marker", None)
        index = getattr(segment, "index", None)
    if not isinstance(marker, str) or not marker:
        raise RunnerError("segment.marker must be a non-empty string")
    if len(marker.encode("utf-8")) > MAX_MARKER_BYTES:
        raise RunnerError("segment.marker is too large")
    if _is_bool(index) or not isinstance(index, int) or index < 1:
        raise RunnerError("segment.index must be a positive integer")
    return marker, index


def _validate_context(context: ExecutionContext) -> None:
    identity = context.runtime_identity
    for name in (
        "commit",
        "dirty_diff_digest",
        "dependency_lock_digest",
        "arch",
    ):
        value = getattr(identity, name)
        if not isinstance(value, str) or not value:
            raise RunnerError(f"runtime_identity.{name} must be non-empty")


def _conversation_context(conversation: Any) -> list[dict[str, Any]]:
    if not isinstance(conversation, dict):
        raise RunnerError("conversation must be an object")
    state = conversation.get("state")
    if not isinstance(state, dict):
        raise RunnerError("conversation.state must be an object")
    context = state.get("context")
    if not isinstance(context, list):
        raise RunnerError("conversation.state.context must be a list")
    for message in context:
        if not isinstance(message, dict) or not isinstance(
            message.get("content"), list
        ):
            raise RunnerError("conversation contains an invalid message")
    return context


def _validate_tool_associations(conversation: Any) -> None:
    calls: dict[str, str] = {}
    results: dict[str, str] = {}
    for message in _conversation_context(conversation):
        for block in message["content"]:
            if not isinstance(block, dict):
                raise RunnerError(
                    "conversation contains an invalid content block"
                )
            block_type = block.get("type")
            if block_type == "tool_call":
                call_id = block.get("id")
                name = block.get("name")
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or not isinstance(name, str)
                    or not name
                ):
                    raise RunnerError("tool call is missing id or name")
                if call_id in calls:
                    raise RunnerError(f"duplicate tool call id: {call_id}")
                if name != "write_marker":
                    raise RunnerError(f"unsupported P0 tool: {name}")
                raw_input = block.get("input")
                if isinstance(raw_input, str):
                    try:
                        tool_input = json.loads(raw_input)
                    except json.JSONDecodeError as exc:
                        raise RunnerError(
                            "tool call input is not valid JSON"
                        ) from exc
                else:
                    tool_input = raw_input
                if (
                    not isinstance(tool_input, dict)
                    or set(tool_input) != {"marker", "index"}
                    or not isinstance(tool_input["marker"], str)
                    or not tool_input["marker"]
                    or len(tool_input["marker"].encode("utf-8"))
                    > MAX_MARKER_BYTES
                    or _is_bool(tool_input["index"])
                    or not isinstance(tool_input["index"], int)
                    or tool_input["index"] < 1
                ):
                    raise RunnerError("invalid write_marker tool schema")
                calls[call_id] = name
            elif block_type == "tool_result":
                result_id = block.get("id")
                name = block.get("name")
                if (
                    not isinstance(result_id, str)
                    or not result_id
                    or not isinstance(name, str)
                    or not name
                ):
                    raise RunnerError("tool result is missing id or name")
                if result_id not in calls:
                    raise RunnerError(
                        f"tool result has no preceding call: {result_id}"
                    )
                if calls[result_id] != name:
                    raise RunnerError(
                        f"tool result name mismatch: {result_id}"
                    )
                if result_id in results:
                    raise RunnerError(f"duplicate tool result id: {result_id}")
                results[result_id] = name
    if set(calls) != set(results):
        missing = sorted(set(calls) - set(results))
        raise RunnerError(f"tool calls without results: {missing}")


class _DeterministicChatModel:
    """Late-bound ChatModelBase subclass used only by the native builder."""

    def __new__(cls, *args: Any, **kwargs: Any):  # pragma: no cover - guard
        from agentscope.model import ChatModelBase

        class DeterministicChatModel(ChatModelBase):
            def __init__(self, marker: str, index: int, call_id: str) -> None:
                super().__init__(
                    credential=None,
                    model="p0-deterministic",
                    parameters=ChatModelBase.Parameters(),
                    stream=False,
                    max_retries=0,
                    context_size=32_768,
                )
                self.marker = marker
                self.index = index
                self.call_id = call_id
                self._tool_pending = True
                self.formatter = SimpleNamespace(
                    supported_input_media_types=(),
                )

            async def _call_api(
                self,
                model_name: str,
                messages: list[Any],
                tools: list[dict] | None = None,
                tool_choice: Any = None,
                **kwargs: Any,
            ) -> Any:
                del model_name, messages, tool_choice, kwargs
                from agentscope.message import TextBlock, ToolCallBlock
                from agentscope.model import ChatResponse

                if self._tool_pending:
                    available = {
                        item.get("function", {}).get("name")
                        for item in (tools or [])
                        if isinstance(item, dict)
                    }
                    if "write_marker" not in available:
                        raise RunnerError(
                            "deterministic write_marker tool missing"
                        )
                    self._tool_pending = False
                    return ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.call_id,
                                name="write_marker",
                                input=json.dumps(
                                    {
                                        "marker": self.marker,
                                        "index": self.index,
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            ),
                        ],
                        is_last=True,
                    )
                return ChatResponse(
                    content=[
                        TextBlock(
                            text=(
                                f"segment {self.index} completed: "
                                f"{self.marker}"
                            ),
                        ),
                    ],
                    is_last=True,
                )

        return DeterministicChatModel(*args, **kwargs)


class _P0AgentBuilder:
    """Trusted native builder; never selected from request/config data."""

    def __init__(
        self,
        app_services: Any = None,
        *,
        marker: str,
        index: int,
        workspace_dir: Path,
        restored_state: dict[str, Any] | None,
        on_built: Callable[[Any], None],
    ) -> None:
        del app_services
        self.marker = marker
        self.index = index
        self.workspace_dir = workspace_dir
        self.restored_state = copy.deepcopy(restored_state)
        self.on_built = on_built

    async def build(self, ctx: Any) -> Any:
        from agentscope.agent import ReActConfig
        from agentscope.tool import FunctionTool, Toolkit
        from qwenpaw.agents.react_agent import QwenPawAgent

        call_id = f"p0-{ctx.session_id}-{self.index}"

        def write_marker(marker: str, index: int) -> str:
            if not isinstance(marker, str) or not marker:
                raise RunnerError("write_marker.marker must be non-empty")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 1
            ):
                raise RunnerError("write_marker.index must be positive")
            target = (
                self.workspace_dir
                / "markers"
                / ctx.session_id
                / f"segment-{index}.txt"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            _safe_workspace_target(
                self.workspace_dir.parent,
                target.relative_to(self.workspace_dir.parent),
            )
            if target.exists() or target.is_symlink():
                raise RunnerError("marker path already exists")
            target.write_text(marker, encoding="utf-8", newline="")
            return f"wrote marker {marker} at segment {index}"

        tool = FunctionTool(
            write_marker,
            name="write_marker",
            description="Write the deterministic P0 marker file.",
            input_schema={
                "type": "object",
                "properties": {
                    "marker": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                },
                "required": ["marker", "index"],
                "additionalProperties": False,
            },
            is_concurrency_safe=False,
            is_read_only=False,
        )
        config = SimpleNamespace(
            language="en",
            tools=None,
            running=SimpleNamespace(
                light_context_config=SimpleNamespace(
                    context_compact_config=SimpleNamespace(enabled=False),
                ),
            ),
        )
        agent = QwenPawAgent(
            name="P0Agent",
            model=_DeterministicChatModel(self.marker, self.index, call_id),
            system_prompt=(
                "You are the P0 deterministic agent. "
                "Call write_marker exactly once, then return completion text."
            ),
            toolkit=Toolkit(tools=[tool]),
            react_config=ReActConfig(max_iters=4),
            middlewares=[],
            agent_config=config,
            workspace_dir=self.workspace_dir,
            request_context={
                "session_id": ctx.session_id,
                "agent_id": "p0",
                "channel": "p0",
                "user_id": getattr(ctx.request, "user_id", ""),
            },
            effective_skills=[],
            governor=None,
        )
        agent.state.session_id = ctx.session_id
        if self.restored_state is not None:
            agent.load_state_dict(self.restored_state, strict=True)
        self.on_built(agent)
        return agent


_TRUSTED_BUILDER: type | None = None


def _trusted_builder_class() -> type:
    """Return the native-only builder subclass without importing QwenPaw."""
    global _TRUSTED_BUILDER
    if _TRUSTED_BUILDER is None:
        from qwenpaw.runtime.builder import AgentBuilder

        class P0AgentBuilder(_P0AgentBuilder, AgentBuilder):
            pass

        _TRUSTED_BUILDER = P0AgentBuilder
    return _TRUSTED_BUILDER


class Runner:
    """One isolated P0 attempt with explicit lifecycle boundaries."""

    def __init__(
        self,
        context: ExecutionContext | Mapping[str, Any],
        root: str | Path,
        *,
        executor: str = "fixture",
        memory: NullMemory | None = None,
    ) -> None:
        try:
            self.context = (
                context
                if isinstance(context, ExecutionContext)
                else ExecutionContext.model_validate(context)
            )
        except Exception as exc:
            raise RunnerError("invalid execution context") from exc
        _validate_context(self.context)
        if executor not in {"fixture", "native"}:
            raise RunnerError("executor must be fixture or native")
        raw_root = Path(root).expanduser()
        if not raw_root.is_absolute():
            raise RunnerError("root must be an absolute path")
        self.root = raw_root.resolve(strict=False)
        if self.root == Path(self.root.anchor or "/"):
            raise RunnerError("root is too broad for a private attempt")
        if self.root.is_relative_to(_SOURCE_ROOT) or self.root.is_relative_to(
            _HOST_HOME
        ):
            raise RunnerError("root must be a private attempt directory")
        if self.root.exists() and not self.root.is_dir():
            raise RunnerError("root must be a directory")
        self.executor = executor
        self.memory = memory or NullMemory()
        self.workspace_dir = self.root / "workspace"
        self._config_root = self.root / ".qwenpaw"
        self._workspace: Any | None = None
        self._last_agent: Any | None = None
        self._conversation: dict[str, Any] = {
            "state": {
                "session_id": self.context.session_id,
                "summary": "",
                "context": [],
            },
        }
        self._cursor = 0
        self._next_segment = 1
        self._segments_used = 0
        self._barrier_id: str | None = None
        self._phase = "NEW"
        self._restore_fingerprint: str | None = None

    async def initialize(
        self,
        context: ExecutionContext | Mapping[str, Any] | None = None,
        pinned_profile: Any = None,
        local_inputs: Any = None,
    ) -> dict[str, Any]:
        del pinned_profile, local_inputs
        if self._phase == "CLOSED":
            raise RunnerError("closed Runner cannot initialize")
        if self._phase != "NEW":
            return {"executor": self.executor, "memory": "none"}
        if context is not None:
            try:
                supplied_context = (
                    context
                    if isinstance(context, ExecutionContext)
                    else ExecutionContext.model_validate(context)
                )
            except Exception as exc:
                raise RunnerError("initialize context is invalid") from exc
            if supplied_context != self.context:
                raise RunnerError("initialize context does not match Runner")
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.root / "home",
            self.root / "tmp",
            self.root / "state",
            self.workspace_dir,
            self.root / "secrets",
            self._config_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        await self.memory.probe(self.context.scope)
        if self.executor == "native":
            os.environ["QWENPAW_WORKING_DIR"] = str(self._config_root)
            os.environ["QWENPAW_SECRET_DIR"] = str(self.root / "secrets")
            os.environ["QWENPAW_WORKING_DIR"] = str(self._config_root)
            os.environ["TMPDIR"] = str(self.root / "tmp")
            os.environ["HOME"] = str(self.root / "home")
            os.environ["QWENPAW_DISABLE_CRON"] = "1"
            from qwenpaw.app.workspace.workspace import Workspace

            self._workspace = Workspace(
                agent_id="default",
                workspace_dir=str(self.workspace_dir),
            )
        self._phase = "READY"
        return {
            "executor": self.executor,
            "memory": "none",
            "session_id": self.context.session_id,
            "scope": self.context.scope.model_dump(mode="json"),
        }

    async def restore(self, committed_manifest: Mapping[str, Any]) -> int:
        if self._phase not in {"READY", "RESTORED"}:
            raise RunnerError("restore requires an initialized Runner")
        if not isinstance(committed_manifest, Mapping):
            raise RunnerError("restore export must be an object")
        try:
            fingerprint = json.dumps(
                committed_manifest,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise RunnerError("restore export is not valid JSON") from exc
        if self._phase == "RESTORED":
            if fingerprint != self._restore_fingerprint:
                raise RunnerError(
                    "Runner already restored from a different export"
                )
            return self._cursor
        allowed = {
            "schema",
            "scope",
            "session_id",
            "runtime_identity",
            "conversation",
            "files",
            "cursor",
            "next_segment",
            "budget",
            "memory",
            "executor",
        }
        if set(committed_manifest) != allowed:
            raise RunnerError("restore export has an invalid field set")
        if committed_manifest.get("schema") != PROTOCOL_VERSION:
            raise RunnerError("unsupported export schema")
        try:
            scope = Scope.model_validate(committed_manifest["scope"])
            identity = RuntimeIdentity.model_validate(
                committed_manifest["runtime_identity"]
            )
        except Exception as exc:
            raise RunnerError(
                "invalid export scope or runtime identity"
            ) from exc
        if scope != self.context.scope:
            raise RunnerError("export scope does not match ExecutionContext")
        if committed_manifest.get("session_id") != self.context.session_id:
            raise RunnerError(
                "export session_id does not match ExecutionContext"
            )
        if identity != self.context.runtime_identity:
            raise RunnerError(
                "export runtime_identity does not match ExecutionContext"
            )
        if committed_manifest.get("executor") != self.executor:
            raise RunnerError("export executor does not match Runner")
        if committed_manifest.get("memory") is not None:
            raise RunnerError("P0 export memory must be null")
        conversation = _json_copy(committed_manifest.get("conversation"))
        _validate_tool_associations(conversation)
        files = committed_manifest.get("files")
        if not isinstance(files, dict):
            raise RunnerError("export files must be an object")
        decoded_files: list[tuple[Path, bytes]] = []
        total_file_bytes = 0
        for entry_number, (name, encoded) in enumerate(files.items(), 1):
            if entry_number > MAX_FILE_ENTRIES:
                raise RunnerError("too many files in restore export")
            relative = _safe_relative_path(name)
            if not isinstance(encoded, str):
                raise RunnerError("export file content must be base64 text")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise RunnerError(f"invalid base64 for {name}") from exc
            if len(content) > MAX_FILE_BYTES:
                raise RunnerError(f"export file is too large: {name}")
            total_file_bytes += len(content)
            if total_file_bytes > MAX_TOTAL_FILE_BYTES:
                raise RunnerError("restore files exceed total size limit")
            decoded_files.append((relative, content))
        cursor = committed_manifest.get("cursor")
        next_segment = committed_manifest.get("next_segment")
        budget = committed_manifest.get("budget")
        if any(
            _is_bool(value) or not isinstance(value, int) or value < 0
            for value in (cursor, next_segment)
        ):
            raise RunnerError(
                "export cursor fields must be non-negative integers"
            )
        if not isinstance(budget, dict) or set(budget) != {"segments_used"}:
            raise RunnerError("invalid export budget")
        segments_used = budget.get("segments_used")
        if (
            _is_bool(segments_used)
            or not isinstance(segments_used, int)
            or segments_used < 0
        ):
            raise RunnerError("budget.segments_used must be non-negative")
        if cursor != segments_used or next_segment != cursor + 1:
            raise RunnerError("export cursor and budget are inconsistent")
        await self.memory.validate_snapshot(None, self.context.scope)
        await self.memory.restore_snapshot(
            None, self.workspace_dir, self.context.scope
        )
        targets: list[tuple[Path, bytes]] = []
        for relative, content in decoded_files:
            target = _safe_workspace_target(self.root, relative)
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file():
                    raise RunnerError(
                        f"restore would overwrite a different file: {relative}"
                    )
                if _read_regular_file(target, MAX_FILE_BYTES) != content:
                    raise RunnerError(
                        f"restore would overwrite a different file: {relative}"
                    )
            else:
                targets.append((target, content))
        for target, content in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self._conversation = conversation
        self._cursor = cursor
        self._next_segment = next_segment
        self._segments_used = segments_used
        self._restore_fingerprint = fingerprint
        self._phase = "RESTORED"
        return self._cursor

    async def execute(
        self, segment: Mapping[str, Any], control_channel: Any = None
    ) -> Boundary:
        del control_channel
        if self._phase not in {"READY", "RESTORED"}:
            raise RunnerError(
                "execute requires an initialized, non-quiesced Runner"
            )
        marker, index = _segment_values(segment)
        if index != self._next_segment:
            raise RunnerError(
                (
                    f"segment index {index} is not next_segment "
                    f"{self._next_segment}"
                )
            )
        self._phase = "EXECUTING"
        try:
            if self.executor == "fixture":
                self._execute_fixture(marker, index)
            else:
                await self._execute_native(marker, index)
        except Exception:
            self._phase = "FAILED"
            raise
        self._segments_used += 1
        self._cursor = self._segments_used
        self._next_segment = self._cursor + 1
        self._phase = "READY"
        return Boundary("completed", self._cursor, self._next_segment)

    def _execute_fixture(self, marker: str, index: int) -> None:
        state = self._conversation["state"]
        context = state["context"]
        call_id = f"p0-{self.context.session_id}-{index}"
        relative = (
            Path("workspace")
            / "markers"
            / self.context.session_id
            / f"segment-{index}.txt"
        )
        target = _safe_workspace_target(self.root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise RunnerError("marker path already exists")
        target.write_text(marker, encoding="utf-8", newline="")
        context.append(
            {
                "id": f"user-{self.context.session_id}-{index}",
                "name": "user",
                "role": "user",
                "content": [
                    {"type": "text", "text": f"write marker {marker}"}
                ],
            },
        )
        context.append(
            {
                "id": call_id,
                "name": "P0Agent",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "id": call_id,
                        "name": "write_marker",
                        "input": json.dumps(
                            {"marker": marker, "index": index},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": call_id,
                        "name": "write_marker",
                        "output": f"wrote marker {marker} at segment {index}",
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "text": f"segment {index} completed: {marker}",
                    },
                ],
            },
        )

    async def _execute_native(self, marker: str, index: int) -> None:
        if self._workspace is None:
            raise RunnerError("native Workspace is not initialized")
        from qwenpaw.schemas import AgentRequest, Message, Role, TextContent

        holder: dict[str, Any] = {}

        def capture(agent: Any) -> None:
            holder["agent"] = agent
            self._last_agent = agent

        def factory(*, app_services: Any = None) -> _P0AgentBuilder:
            builder_class = _trusted_builder_class()
            return builder_class(
                app_services=app_services,
                marker=marker,
                index=index,
                workspace_dir=self.workspace_dir,
                restored_state=self._conversation,
                on_built=capture,
            )

        request = AgentRequest(
            input=[
                Message(
                    role=Role.USER,
                    content=[TextContent(text=f"write marker {marker}")],
                ),
            ],
            session_id=self.context.session_id,
            user_id=self.context.actor_user_id,
            stream=True,
            metadata={"p0_segment_index": index},
        )
        request.agent_id = "default"
        request.channel = "p0"
        request.request_context = {"p0_segment_index": index}
        async for _event in self._workspace.stream_query(
            request,
            builder_factory=factory,
            strict_lifecycle=True,
        ):
            pass
        agent = holder.get("agent")
        if agent is None:
            raise RunnerError("native Runtime did not build an agent")
        state = agent.state_dict()
        if not isinstance(state, dict) or not isinstance(
            state.get("state"), dict
        ):
            raise RunnerError("native agent returned no serializable state")
        self._conversation = _json_copy(state)
        _validate_tool_associations(self._conversation)

    async def quiesce(
        self,
        reason: str = "completed",
        deadline: float | None = None,
    ) -> Quiesced:
        del reason
        if self._phase == "CLOSED":
            raise RunnerError("Runner is closed")
        if self._phase == "QUIESCED":
            assert self._barrier_id is not None
            return Quiesced(self._barrier_id)
        if self._phase not in {"READY", "RESTORED"}:
            raise RunnerError("quiesce requires execution to be idle")
        self._barrier_id = (
            f"barrier-{self.context.session_id}-{self._next_segment}"
        )
        await self.memory.quiesce(self._barrier_id, deadline)
        self._phase = "QUIESCED"
        return Quiesced(self._barrier_id)

    async def export_state(
        self,
        barrier_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        if self._phase != "QUIESCED" or self._barrier_id is None:
            raise RunnerError("export_state requires a quiesced Runner")
        if barrier_id is not None and barrier_id != self._barrier_id:
            raise RunnerError("unknown quiesce barrier")
        if checkpoint_id is not None and (
            not isinstance(checkpoint_id, str) or not checkpoint_id
        ):
            raise RunnerError("checkpoint_id must be non-empty")
        await self.memory.export_snapshot(self._barrier_id, checkpoint_id)
        conversation = _json_copy(self._conversation)
        _validate_tool_associations(conversation)
        files: dict[str, str] = {}
        total_bytes = 0
        if self.workspace_dir.exists():
            for entry_number, path in enumerate(
                sorted(self.workspace_dir.rglob("*")), 1
            ):
                if entry_number > MAX_FILE_ENTRIES:
                    raise RunnerError("too many files in export")
                if path.is_symlink():
                    raise RunnerError(
                        "symbolic links are not allowed in export"
                    )
                if path.is_dir():
                    continue
                if not path.exists():
                    raise RunnerError(f"export path disappeared: {path}")
                relative = path.relative_to(self.root)
                content = _read_regular_file(path, MAX_FILE_BYTES)
                total_bytes += len(content)
                if total_bytes > MAX_TOTAL_FILE_BYTES:
                    raise RunnerError("export files exceed total size limit")
                files[relative.as_posix()] = base64.b64encode(content).decode(
                    "ascii"
                )
        export = {
            "schema": PROTOCOL_VERSION,
            "scope": self.context.scope.model_dump(mode="json"),
            "session_id": self.context.session_id,
            "runtime_identity": self.context.runtime_identity.model_dump(
                mode="json"
            ),
            "conversation": conversation,
            "files": files,
            "cursor": self._cursor,
            "next_segment": self._next_segment,
            "budget": {"segments_used": self._segments_used},
            "memory": None,
            "executor": self.executor,
        }
        return _json_copy(export)

    async def close(self, deadline: float | None = None) -> None:
        if self._phase == "CLOSED":
            return
        if self._phase == "EXECUTING":
            raise RunnerError("cannot close while executing")
        await self.memory.close(deadline)
        self._phase = "CLOSED"


__all__ = [
    "Boundary",
    "NullMemory",
    "PROTOCOL_VERSION",
    "Quiesced",
    "Runner",
    "RunnerError",
]
