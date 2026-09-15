"""Persistence and coordination ports for the office-agent control plane.

The domain imports these protocols, never PostgreSQL or a future Redis client.
They deliberately describe behavior instead of storage primitives.
"""
from __future__ import annotations

from typing import Any, Protocol


class SessionStore(Protocol):
    def create_session(self, session: dict[str, Any]) -> dict[str, Any]: ...
    def get_session(self, session_id: str) -> dict[str, Any] | None: ...
    def list_sessions(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]: ...


class RunStore(Protocol):
    def create_run(self, run: dict[str, Any]) -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...


class RunQueuePort(Protocol):
    def claim_fifo(self, worker_id: str, runtime_identity: dict[str, Any]) -> dict[str, Any] | None: ...


class CapacityPort(Protocol):
    def admit(self, tenant_id: str, user_id: str) -> bool: ...


class LiveOutputPort(Protocol):
    def replace_snapshot(self, run_id: str, snapshot: dict[str, Any]) -> None: ...
    def get_snapshot(self, run_id: str) -> dict[str, Any] | None: ...


class EventStore(Protocol):
    def append_event(self, run_id: str, event: dict[str, Any]) -> None: ...

