"""JSON-lines entry point for the P0 Runner.

The supervisor may use a two-message handshake: the first message prepares
the private attempt and receives ``{"type":"ready"}``; the second starts the
bounded segment.  A single-message mode remains available for local tests.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Mapping

from .contracts import ExecutionContext
from .runner import PROTOCOL_VERSION, Runner, RunnerError


def _json_line(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _read_json_line() -> Mapping[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise RunnerError("expected a JSON input line")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RunnerError("input is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise RunnerError("input must be a JSON object")
    return value


def _context(payload: Mapping[str, Any]) -> ExecutionContext:
    try:
        return ExecutionContext.model_validate(payload.get("context"))
    except Exception as exc:
        raise RunnerError("invalid ExecutionContext") from exc


async def _run_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    context = _context(payload)
    root = payload.get("root")
    executor = payload.get("executor")
    if not isinstance(root, str) or not isinstance(executor, str):
        raise RunnerError("root and executor are required")
    runner = Runner(context, root, executor=executor)
    try:
        await runner.initialize()
        restore = payload.get("restore")
        if restore is not None:
            await runner.restore(restore)

        handshake = payload.get("handshake", False)
        if handshake is True:
            print(_json_line({"type": "ready"}), flush=True)
            command = _read_json_line()
            if command.get("command") != "execute":
                raise RunnerError("expected command=execute after ready")
            segment = command.get("segment", payload.get("segment"))
        elif handshake not in (False, None):
            raise RunnerError("handshake must be boolean")
        else:
            segment = payload.get("segment")

        await runner.execute(segment)
        quiesced = await runner.quiesce()
        export = await runner.export_state(quiesced.barrier_id)
        await runner.close()
        return export
    except Exception:
        try:
            await runner.close()
        except Exception:
            pass
        raise


def main() -> int:
    try:
        payload = _read_json_line()
        export = asyncio.run(_run_payload(payload))
        sys.stdout.write(_json_line(export) + "\n")
        sys.stdout.flush()
        return 0
    except Exception as exc:  # diagnostics never share stdout
        sys.stderr.write(
            f"p0-runner-error: {type(exc).__name__}: {exc}\n",
        )
        sys.stderr.flush()
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess
    raise SystemExit(main())


__all__ = ["main", "PROTOCOL_VERSION"]
