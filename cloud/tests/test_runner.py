"""P0 Runner lifecycle coverage.

Fixture tests run by default; native execution is covered by the
TaskLauncher-backed Worker integration because it must stay inside Seatbelt.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
import sys

import pytest

from qwenpaw_cloud.contracts import (
    ExecutionContext,
    RuntimeIdentity,
    Scope,
)
from qwenpaw_cloud.runner import (
    MAX_FILE_BYTES,
    NullMemory,
    Runner,
    RunnerError,
)


def _context(session_id: str = "s1") -> ExecutionContext:
    scope = Scope(
        tenant_id="t1",
        owner_user_id="u1",
        scope_type="standalone",
        scope_id=session_id,
    )
    return ExecutionContext(
        scope=scope,
        actor_user_id="u1",
        session_id=session_id,
        run_id=f"run-{session_id}",
        attempt_id=f"attempt-{session_id}",
        lease_epoch=1,
        base_revision=0,
        runtime_identity=RuntimeIdentity(
            commit="a" * 40,
            dirty_diff_digest="d" * 64,
            dependency_lock_digest="b" * 64,
            arch=platform.machine() or "test",
        ),
    )


class _FailingMemory(NullMemory):
    def __init__(self, operation: str):
        super().__init__()
        self.operation = operation

    async def quiesce(self, barrier_id, deadline=None):
        if self.operation == "quiesce":
            raise RuntimeError("quiesce failed")
        return await super().quiesce(barrier_id, deadline)

    async def export_snapshot(self, token, checkpoint_id=None):
        if self.operation == "export":
            raise RuntimeError("export failed")
        return await super().export_snapshot(token, checkpoint_id)

    async def close(self, deadline=None):
        if self.operation == "close":
            raise RuntimeError("close failed")
        return await super().close(deadline)


async def _run_fixture(
    root,
    context,
    marker: str,
    index: int,
    restore=None,
    *,
    executor: str = "fixture",
):
    runner = Runner(context, root, executor=executor)
    await runner.initialize()
    if restore is not None:
        await runner.restore(restore)
    await runner.execute({"marker": marker, "index": index})
    barrier = await runner.quiesce()
    export = await runner.export_state(barrier.barrier_id)
    await runner.close()
    return export


@pytest.mark.asyncio
async def test_fixture_round_trip_and_tool_association(tmp_path):
    context = _context()
    first = await _run_fixture(tmp_path / "a", context, "alpha", 1)

    assert first["schema"] == "p0.v1"
    assert first["memory"] is None
    assert first["cursor"] == 1
    assert first["next_segment"] == 2
    assert first["budget"] == {"segments_used": 1}
    assert first["files"] == {
        "workspace/markers/s1/segment-1.txt": base64.b64encode(
            b"alpha",
        ).decode("ascii"),
    }
    blocks = first["conversation"]["state"]["context"][1]["content"]
    assert {block["type"] for block in blocks} == {
        "tool_call",
        "tool_result",
        "text",
    }

    second = await _run_fixture(
        tmp_path / "b",
        context,
        "beta",
        2,
        restore=first,
    )
    assert "alpha" in json.dumps(second["conversation"], ensure_ascii=False)
    assert "beta" in json.dumps(second["conversation"], ensure_ascii=False)
    assert "workspace/markers/s1/segment-2.txt" in second["files"]


@pytest.mark.asyncio
async def test_same_user_sessions_are_independent(tmp_path):
    first = await _run_fixture(tmp_path / "s1", _context("s1"), "one", 1)
    second = await _run_fixture(tmp_path / "s2", _context("s2"), "two", 1)
    assert first["session_id"] == "s1"
    assert second["session_id"] == "s2"
    assert "two" not in json.dumps(first["conversation"], ensure_ascii=False)
    assert "one" not in json.dumps(second["conversation"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_restore_rejects_scope_paths_and_unmatched_tools(tmp_path):
    context = _context()
    export = await _run_fixture(tmp_path / "a", context, "alpha", 1)

    bad_scope = json.loads(json.dumps(export))
    bad_scope["scope"]["scope_id"] = "other"
    with pytest.raises(RunnerError):
        runner = Runner(context, tmp_path / "bad-scope", executor="fixture")
        await runner.initialize()
        await runner.restore(bad_scope)

    bad_path = json.loads(json.dumps(export))
    bad_path["files"] = {"../outside": "YQ=="}
    with pytest.raises(RunnerError):
        runner = Runner(context, tmp_path / "bad-path", executor="fixture")
        await runner.initialize()
        await runner.restore(bad_path)

    bad_assoc = json.loads(json.dumps(export))
    bad_assoc["conversation"]["state"]["context"][1]["content"][1][
        "id"
    ] = "missing"
    with pytest.raises(RunnerError):
        runner = Runner(context, tmp_path / "bad-assoc", executor="fixture")
        await runner.initialize()
        await runner.restore(bad_assoc)

    bad_identity = json.loads(json.dumps(export))
    bad_identity["runtime_identity"]["commit"] = "b" * 40
    with pytest.raises(RunnerError):
        runner = Runner(context, tmp_path / "bad-identity", executor="fixture")
        await runner.initialize()
        await runner.restore(bad_identity)

    bad_schema = json.loads(json.dumps(export))
    bad_schema["conversation"]["state"]["context"][1]["content"][0][
        "input"
    ] = json.dumps({"marker": "alpha"})
    with pytest.raises(RunnerError):
        runner = Runner(context, tmp_path / "bad-schema", executor="fixture")
        await runner.initialize()
        await runner.restore(bad_schema)


def test_null_memory_is_explicitly_non_persistent():
    memory = NullMemory()
    with pytest.raises(RunnerError):
        memory.save({"anything": True})
    with pytest.raises(RunnerError):
        memory.load()


@pytest.mark.asyncio
async def test_memory_lifecycle_failures_propagate(tmp_path):
    context = _context("memory")

    quiesce_runner = Runner(
        context,
        tmp_path / "quiesce",
        memory=_FailingMemory("quiesce"),
    )
    await quiesce_runner.initialize()
    with pytest.raises(RuntimeError, match="quiesce failed"):
        await quiesce_runner.quiesce()
    await quiesce_runner.close()

    export_runner = Runner(
        context,
        tmp_path / "export",
        memory=_FailingMemory("export"),
    )
    await export_runner.initialize()
    await export_runner.execute({"marker": "memory", "index": 1})
    barrier = await export_runner.quiesce()
    with pytest.raises(RuntimeError, match="export failed"):
        await export_runner.export_state(barrier.barrier_id)
    await export_runner.close()

    close_runner = Runner(
        context,
        tmp_path / "close",
        memory=_FailingMemory("close"),
    )
    await close_runner.initialize()
    with pytest.raises(RuntimeError, match="close failed"):
        await close_runner.close()


@pytest.mark.asyncio
async def test_export_rejects_oversized_file(tmp_path):
    runner = Runner(_context("size"), tmp_path / "size")
    await runner.initialize()
    await runner.execute({"marker": "size", "index": 1})
    (runner.workspace_dir / "oversized.bin").write_bytes(
        b"x" * (MAX_FILE_BYTES + 1),
    )
    barrier = await runner.quiesce()
    with pytest.raises(RunnerError, match="too large"):
        await runner.export_state(barrier.barrier_id)
    await runner.close()


def test_entry_fixture_handshake(tmp_path):
    context = _context()
    payload = {
        "context": context.model_dump(mode="json"),
        "executor": "fixture",
        "root": str((tmp_path / "entry").resolve()),
        "restore": None,
        "handshake": True,
    }
    payload["segment"] = {"marker": "entry", "index": 1}
    command = {"command": "execute"}
    proc = subprocess.run(
        [sys.executable, "-m", "qwenpaw_cloud.runner_entry"],
        input=json.dumps(payload) + "\n" + json.dumps(command) + "\n",
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": "src"},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert json.loads(lines[0]) == {"type": "ready"}
    export = json.loads(lines[1])
    assert export["executor"] == "fixture"
    assert export["next_segment"] == 2
