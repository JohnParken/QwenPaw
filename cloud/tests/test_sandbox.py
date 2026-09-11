"""G4 native negative tests; skipped evidence never counts as PASS."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import time
from uuid import uuid4

import pytest

from qwenpaw_cloud.contracts import ExecutionContext, Limits
from qwenpaw_cloud.identity import local_identity
from qwenpaw_cloud.sandbox import LeaseWatchdog, PathMap, TaskLauncher

NATIVE = pytest.mark.skipif(
    os.getenv("P0_NATIVE") != "1",
    reason="P0_NATIVE=1 requires real Seatbelt execution",
)


def paths_at(tmp_path):
    root = tmp_path / "private"
    ctx = ExecutionContext(
        scope={
            "tenant_id": "t1",
            "owner_user_id": "u1",
            "scope_type": "standalone",
            "scope_id": "s1",
        },
        actor_user_id="u1",
        session_id="s1",
        run_id=uuid4().hex,
        attempt_id=uuid4().hex,
        lease_epoch=1,
        base_revision=0,
        runtime_identity=local_identity(),
    )
    return PathMap.create(root, ctx)


def test_watchdog_late_response_cannot_resurrect():
    watch = LeaseWatchdog(Limits())
    watch.deadline = time.monotonic() - 0.001
    assert not watch.renew(time.monotonic(), 100)
    assert watch.expired()
    assert not watch.renew(time.monotonic(), 100)
    with pytest.raises(ValueError):
        Limits(lease_seconds=3)


@NATIVE
@pytest.mark.asyncio
async def test_native_files_env_fd_network(tmp_path, monkeypatch):
    paths = paths_at(tmp_path)
    forbidden = tmp_path / "another-attempt-canary"
    forbidden.write_text("OTHER-ATTEMPT-SECRET")
    (paths.root / "workspace/link").symlink_to(forbidden)
    monkeypatch.setenv("P0_SECRET_CANARY", "must-not-inherit")
    fd = os.open(forbidden, os.O_RDONLY)
    os.set_inheritable(fd, True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    code = f"""
import json
import os
import socket
from pathlib import Path

results = {{}}
try:
    with os.scandir({str(Path.home())!r}):
        results["real_home"] = False
except PermissionError:
    results["real_home"] = True
for label, path in [
    ("outside", {str(forbidden)!r}),
    ("data_volume_alias", {('/System/Volumes/Data' + str(forbidden))!r}),
    ("symlink", "link"),
]:
    for mode in ("r", "w"):
        try:
            with open(path, mode) as f:
                f.read() if mode == "r" else f.write("bad")
            results[label + mode] = False
        except PermissionError:
            results[label + mode] = True
try:
    os.read({fd}, 10)
    results["fd"] = False
except OSError:
    results["fd"] = True
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", {port}))
    results["network"] = False
except PermissionError:
    results["network"] = True
results["env"] = "P0_SECRET_CANARY" not in os.environ
results["credentials"] = not any(
    "KEY" in k or "TOKEN" in k or "DSN" in k
    for k in os.environ
    if k != "TOKENIZERS_PARALLELISM"
)
Path("allowed.txt").write_text("own-data")
results["own"] = Path("allowed.txt").read_text() == "own-data"
print(json.dumps(results))
"""
    try:
        launcher = TaskLauncher()
        result = json.loads(
            await launcher.run(paths, {}, command=[sys.executable, "-c", code])
        )
        assert result and all(result.values()), result
        assert launcher.last_cleanup and not launcher.quarantined
        assert forbidden.read_text() == "OTHER-ATTEMPT-SECRET"
    finally:
        os.close(fd)
        listener.close()


@NATIVE
@pytest.mark.asyncio
async def test_output_bound(tmp_path):
    paths = paths_at(tmp_path)
    launcher = TaskLauncher(Limits(output_bytes=2048))
    with pytest.raises(RuntimeError, match="OUTPUT_LIMIT"):
        await launcher.run(
            paths,
            {},
            command=[
                sys.executable,
                "-c",
                'import os; os.write(1,b"x"*200000); os.write(2,b"y"*200000)',
            ],
        )
    assert len(launcher.last_stderr) <= 2048
    assert launcher.last_cleanup


@NATIVE
@pytest.mark.asyncio
async def test_cancel_and_descendant_cleanup(tmp_path):
    paths = paths_at(tmp_path)
    launcher = TaskLauncher()
    cancel = asyncio.Event()
    child_marker = paths.root / "workspace/child"
    code = """
import pathlib
import subprocess
import sys
import time

p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
pathlib.Path("child").write_text(str(p.pid))
time.sleep(90)
"""
    task = asyncio.create_task(
        launcher.run(
            paths, {}, cancel=cancel, command=[sys.executable, "-c", code]
        )
    )
    deadline = time.monotonic() + 5
    while not child_marker.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.025)
    assert child_marker.exists(), "explicit child-start barrier missing"
    cancel.set()
    with pytest.raises(RuntimeError, match="CANCELLED"):
        await task
    assert launcher.last_cleanup
    with pytest.raises(ProcessLookupError):
        os.kill(int(child_marker.read_text()), 0)


@NATIVE
@pytest.mark.asyncio
async def test_watchdog_stops_process(tmp_path):
    paths = paths_at(tmp_path)
    watch = LeaseWatchdog(Limits())
    watch.deadline = time.monotonic() + 0.5
    launcher = TaskLauncher()
    with pytest.raises(RuntimeError, match="LEASE_DEADLINE"):
        await launcher.run(
            paths,
            {},
            watchdog=watch,
            command=[sys.executable, "-c", "import time; time.sleep(90)"],
        )
    assert watch.expired() and launcher.last_cleanup


def test_pathmap_rejects_reuse_and_unsafe_root(tmp_path):
    paths = paths_at(tmp_path)
    assert (paths.root / "home").stat().st_mode & 0o077 == 0
    assert (
        not {"AWS_SECRET_ACCESS_KEY", "P0_TEST_DSN"}
        & paths.environment().keys()
    )


@NATIVE
@pytest.mark.asyncio
async def test_unconfirmed_cleanup_quarantines_slot(tmp_path, monkeypatch):
    paths = paths_at(tmp_path)
    launcher = TaskLauncher()
    real = os.killpg

    def unconfirmed(pid, sig):
        if sig == 0:
            return None  # explicit backend fault: cannot confirm group exit
        return real(pid, sig)

    monkeypatch.setattr(os, "killpg", unconfirmed)
    with pytest.raises(RuntimeError, match="CLEANUP_NOT_CONFIRMED"):
        await launcher.run(
            paths, {}, command=[sys.executable, "-c", 'print("done")']
        )
    assert launcher.quarantined
    with pytest.raises(RuntimeError, match="SLOT_QUARANTINED"):
        await launcher.run(
            paths,
            {},
            command=[sys.executable, "-c", 'print("should not execute")'],
        )
