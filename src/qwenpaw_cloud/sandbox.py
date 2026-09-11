"""macos-dev task launcher: Seatbelt from exec, private paths, bounded pipes.

No resource-hard-isolation claim. Native execution never falls back to bare
processes. A failed process-group cleanup permanently quarantines this slot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import signal
import sys
import time

from .contracts import ExecutionContext, Limits


@dataclass(frozen=True)
class PathMap:
    root: Path

    @classmethod
    def create(cls, development_root: Path, context: ExecutionContext):
        base = development_root.resolve()
        source = Path(__file__).resolve().parents[2]
        home = Path.home().resolve()
        if (
            not development_root.is_absolute()
            or base == home
            or base == Path("/")
            or base.is_relative_to(source)
        ):
            raise ValueError(
                "private development root must be outside source and real HOME"
            )
        if any(
            part.startswith(".")
            and part in {".ssh", ".aws", ".config", ".codex"}
            for part in base.parts
        ):
            raise ValueError("credential directories are forbidden")
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        if base.stat().st_mode & 0o077:
            raise ValueError("development root must be mode 0700")
        root = base / (context.scope.key + "-" + context.attempt_id)
        root.mkdir(
            mode=0o700
        )  # new Attempt, never reuse an existing directory
        for name in ("home", "tmp", "state", "workspace", "secrets"):
            (root / name).mkdir(mode=0o700)
        return cls(root)

    def environment(self):
        return {
            "HOME": str(self.root / "home"),
            "TMPDIR": str(self.root / "tmp"),
            "QWENPAW_WORKING_DIR": str(self.root / "state"),
            "QWENPAW_SECRET_DIR": str(self.root / "secrets"),
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }


def _quote(path):
    text = str(path)
    if any(ord(c) < 32 for c in text):
        raise ValueError("control characters in sandbox path")
    return json.dumps(text)


def seatbelt_profile(paths: PathMap):
    # Approved public code/dependencies only; never grant the entire repository
    # or real home. Python interpreter's resolved prefix may live outside venv.
    public = [
        Path("/System/Library"),
        Path("/usr/lib"),
        Path("/usr/share"),
        Path("/bin"),
        Path("/usr/bin"),
        Path("/private/var/db/dyld"),
        Path("/private/var/db/timezone"),
        Path(sys.base_prefix).resolve(),
        Path(sys.prefix).resolve(),
        Path(__file__).resolve().parents[1],
    ]
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec*)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        "(allow process-info* (target self))",
        "(allow signal (target self))",
        '(allow mach-lookup (global-name "com.apple.system.logger"))',
        "(allow mach-lookup (global-name "
        '"com.apple.system.notification_center"))',
        '(allow file-read* (literal "/"))',
        "(allow file-read-metadata)",
        '(allow file-read* (literal "/dev/null") '
        '(literal "/dev/zero") '
        '(literal "/dev/random") '
        '(literal "/dev/urandom") '
        '(literal "/dev/dtracehelper"))',
        '(allow file-write* (literal "/dev/null"))',
    ]
    rules.extend(
        "(allow file-read* (subpath " + _quote(p) + "))" for p in public
    )
    rules.append(
        "(allow file-read* file-write* (subpath "
        + _quote(paths.root.resolve())
        + "))"
    )
    # stdin/stdout/stderr pipes are the only control/data channel. No socket or
    # Unrestricted Mach/posix-shm allowance is
    # unnecessary for this offline profile.
    rules.append("(deny network*)")
    return "\n".join(rules)


class LeaseWatchdog:
    """Uses send-time deadlines; delayed replies never revive work."""

    def __init__(self, limits: Limits):
        self.limits = limits
        self.deadline = (
            time.monotonic() + limits.lease_seconds - limits.safety_seconds
        )
        self.stopped = False

    def renew(self, sent_at: float, valid_seconds: float):
        now = time.monotonic()
        if self.stopped or now >= self.deadline:
            self.stopped = True
            return False
        candidate = sent_at + valid_seconds - self.limits.safety_seconds
        if candidate <= now:
            self.stopped = True
            return False
        self.deadline = candidate
        return True

    def expired(self):
        if time.monotonic() >= self.deadline:
            self.stopped = True
        return self.stopped


class TaskLauncher:
    def __init__(self, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.quarantined = False
        self.last_cleanup = False
        self.last_stderr = b""

    async def run(
        self,
        paths: PathMap,
        payload: dict,
        *,
        native=True,
        watchdog: LeaseWatchdog | None = None,
        cancel: asyncio.Event | None = None,
        command: list[str] | None = None,
        on_ready=None,
    ):
        if self.quarantined:
            raise RuntimeError("SLOT_QUARANTINED")
        if native and (
            platform.system() != "Darwin"
            or not Path("/usr/bin/sandbox-exec").exists()
        ):
            raise RuntimeError("NATIVE_SEATBELT_UNSUPPORTED")
        argv = command or [sys.executable, "-m", "qwenpaw_cloud.runner_entry"]
        if native:
            argv = [
                "/usr/bin/sandbox-exec",
                "-p",
                seatbelt_profile(paths),
                *argv,
            ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=paths.root / "workspace",
            env=paths.environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        self.last_cleanup = False
        output = bytearray()
        errors = bytearray()
        overflow = asyncio.Event()

        async def drain(stream, target):
            while chunk := await stream.read(8192):
                room = self.limits.output_bytes - len(target)
                target.extend(chunk[: max(0, room)])
                if len(chunk) > room:
                    overflow.set()

        async def write_input():
            try:
                proc.stdin.write(json.dumps(payload).encode() + b"\n")
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if not payload.get("handshake"):
                    proc.stdin.close()

        async def read_stdout():
            if payload.get("handshake"):
                line = await proc.stdout.readline()
                if json.loads(line) != {"type": "ready"}:
                    raise RuntimeError("INVALID_READY_HANDSHAKE")
                if on_ready is None:
                    raise RuntimeError("READY_CALLBACK_REQUIRED")
                await on_ready()
                proc.stdin.write(
                    json.dumps(
                        {"command": "execute", "segment": payload["segment"]}
                    ).encode()
                    + b"\n"
                )
                await proc.stdin.drain()
                proc.stdin.close()
            await drain(proc.stdout, output)

        readers = [
            asyncio.create_task(read_stdout()),
            asyncio.create_task(drain(proc.stderr, errors)),
            asyncio.create_task(write_input()),
        ]
        reason = None
        end = time.monotonic() + self.limits.segment_seconds
        try:
            while proc.returncode is None:
                for reader in readers:
                    if (
                        reader.done()
                        and not reader.cancelled()
                        and reader.exception()
                    ):
                        raise reader.exception()
                if overflow.is_set():
                    reason = "OUTPUT_LIMIT"
                elif cancel is not None and cancel.is_set():
                    reason = "CANCELLED"
                elif watchdog is not None and watchdog.expired():
                    reason = "LEASE_DEADLINE"
                elif time.monotonic() >= end:
                    reason = "SEGMENT_TIMEOUT"
                if reason:
                    break
                await asyncio.sleep(0.025)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), self.limits.stop_grace)
            except asyncio.TimeoutError:
                pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*readers), self.limits.stop_grace
                )
            except asyncio.TimeoutError:
                self.quarantined = True
            # A descendant that escaped its process group is outside the P0
            # controlled workload; process isolation remains a Linux P1 item.
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                self.last_cleanup = True
            else:
                self.quarantined = True
            self.last_stderr = bytes(errors)
        if reason or overflow.is_set():
            raise RuntimeError(reason or "OUTPUT_LIMIT")
        if proc.returncode != 0:
            raise RuntimeError(
                (
                    f"RUNNER_EXIT_{proc.returncode}: "
                    f'{errors.decode(errors="replace")[-2000:]}'
                )
            )
        if not self.last_cleanup:
            raise RuntimeError("CLEANUP_NOT_CONFIRMED")
        return bytes(output)
