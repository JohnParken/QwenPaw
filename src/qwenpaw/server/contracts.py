"""Service contracts independent of local workspace state."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, AsyncContextManager

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


class NotFound(Exception):
    """Missing or not owned by this principal."""


class Conflict(Exception):
    """Conflicting idempotency key or lifecycle transition."""


class LeaseLost(Conflict):
    """The execution epoch is no longer authoritative."""


class ToolOutcomeUnknown(asyncio.CancelledError):
    """Stop reasoning after an uncertain side effect; do not invite model retries."""


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    session_id: str
    channel_id: str
    run_id: str
    definition_version: str
    epoch: int
    worker_id: str


current_execution: ContextVar[ExecutionContext | None] = ContextVar(
    "qwenpaw_server_execution",
    default=None,
)


class Repository(Protocol):
    """All IDs returned as strings; JSON fields as decoded dictionaries.

    Run: id, session_id, user_id, channel_id, request_id, input (dict),
    definition_version, status, epoch, worker_id, cancel_requested.
    Session: id, user_id, channel_id, external_id, state (dict).
    Event: seq (monotonic per run), payload (dict).
    Methods accepting ExecutionContext MUST fence by worker, epoch and lease.
    User-facing methods MUST hide other users' resources as NotFound.
    """

    async def migrate(self) -> None: ...
    async def close(self) -> None: ...
    async def submit(
        self,
        user_id: str,
        channel_id: str,
        session_id: str,
        request_id: str,
        payload: dict,
        version: str,
    ) -> dict: ...
    async def get_run(self, user_id: str, run_id: str) -> dict: ...
    async def sessions(self, user_id: str, limit: int = 100) -> list[dict]: ...
    async def session(self, user_id: str, session_id: str) -> dict: ...
    async def messages(
        self, user_id: str, session_id: str, limit: int = 100, after: int = 0
    ) -> list[dict]: ...
    async def events(
        self, user_id: str, run_id: str, after: int, limit: int = 100
    ) -> list[dict]: ...
    async def cancel(self, user_id: str, run_id: str) -> dict: ...
    async def claim(
        self, worker_id: str, lease_seconds: int, user_limit: int
    ) -> dict | None: ...
    async def heartbeat(self, ctx: ExecutionContext, lease_seconds: int) -> bool: ...
    async def append(self, ctx: ExecutionContext, payloads: list[dict]) -> None: ...
    async def finish(
        self,
        ctx: ExecutionContext,
        status: str,
        state: dict,
        message: dict | None = None,
    ) -> None: ...
    async def expired(self) -> list[dict]: ...
    async def interrupt(self, run_id: str, epoch: int) -> None: ...
    async def begin_tool(
        self, ctx: ExecutionContext, call_id: str, name: str, arguments: dict
    ) -> dict: ...
    async def end_tool(
        self, ctx: ExecutionContext, call_id: str, status: str, result: dict
    ) -> None: ...
    async def request_approval(
        self, ctx: ExecutionContext, call_id: str, timeout: int
    ) -> dict: ...
    async def approval(self, ctx: ExecutionContext, approval_id: str) -> dict: ...
    async def decide(self, user_id: str, approval_id: str, approved: bool) -> dict: ...
    async def add_file(
        self,
        user_id: str,
        file_id: str,
        name: str,
        key: str,
        size: int,
        ctx: ExecutionContext | None = None,
    ) -> dict: ...
    async def file(self, user_id: str, file_id: str) -> dict: ...
    async def delete_file(self, user_id: str, file_id: str) -> dict: ...
    async def memories(self, user_id: str, query: str) -> list[dict]: ...
    async def remember(self, ctx: ExecutionContext, text: str) -> None: ...
    async def delete_memories(self, user_id: str) -> None: ...
    async def prune_events(self, days: int = 7) -> None: ...

    async def open(self) -> None: ...
    async def ready(self) -> None: ...
    async def statistics(self) -> dict: ...
    async def put_definition(self, version: str, payload: dict) -> dict: ...
    async def definition(self, version: str) -> dict: ...
    async def validate_execution(
        self, ctx: ExecutionContext, allow_cancelled: bool = False
    ) -> dict: ...
    async def sandbox_acquire(self, ctx: ExecutionContext) -> None: ...
    async def sandbox_release(self, session_id: str) -> None: ...
    async def sandbox_idle(self, idle_seconds: int) -> list[str]: ...
    def sandbox_stop_guard(
        self, session_id: str, expected: dict | None = None
    ) -> AsyncContextManager[bool]: ...
    def session_import_guard(
        self, user_id: str, session_id: str, ctx: ExecutionContext | None = None
    ) -> AsyncContextManager[None]: ...
    async def ensure_vector_support(self) -> None: ...
    async def pending_memory_vectors(
        self, user_id: str, model: str, limit: int = 100
    ) -> list[dict]: ...
    async def put_memory_vector(
        self, user_id: str, memory_id: str, model: str, vector: list[float]
    ) -> None: ...
    async def search_memory_vectors(
        self, user_id: str, model: str, vector: list[float], limit: int = 10
    ) -> list[dict]: ...
    async def knowledge_indexed(self, version: str, model: str) -> set[int]: ...
    async def put_knowledge_vector(
        self, version: str, document_id: int, text: str, model: str, vector: list[float]
    ) -> None: ...
    async def search_knowledge_vectors(
        self, version: str, model: str, vector: list[float], limit: int = 10
    ) -> list[str]: ...
    async def claim_memory(
        self, owner: str, lease_seconds: int = 300
    ) -> dict | None: ...
    async def merge_memory(self, job: dict, facts: list[str]) -> bool: ...
    async def complete_memory(self, job: dict) -> bool: ...
    async def fail_memory(self, job: dict, error: str) -> None: ...


class ToolExecutor(Protocol):
    async def invoke(
        self,
        ctx: ExecutionContext,
        call_id: str,
        name: str,
        arguments: dict,
        timeout: int,
    ) -> dict: ...
    async def stop_session(
        self, session_id: str, run_id: str | None = None, epoch: int | None = None
    ) -> None: ...


@dataclass(frozen=True)
class RuntimeServices:
    repository: Repository
    tools: ToolExecutor
    model_factory: Any
    memory: Any = None
