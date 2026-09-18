"""Sustained real BFF/SSE load; persisted-event latency requires synchronized clocks."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import AsyncExitStack
from datetime import datetime
import json
import time
import uuid

import httpx


def stats(values):
    ordered = sorted(values)
    return {
        "count": len(values),
        **{
            f"p{int(p * 100)}": (
                ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
                if ordered
                else None
            )
            for p in (0.5, 0.95, 0.99)
        },
    }


async def subscribe(client, url, headers, started, deadline, metrics):
    last = 0
    first = True
    while time.perf_counter() < deadline:
        try:
            async with client.stream(
                "GET", url, headers={**headers, "Last-Event-ID": str(last)}
            ) as response:
                response.raise_for_status()
                event_type, event_id, data = "message", None, []
                terminal = None
                async for line in response.aiter_lines():
                    if line.startswith("id:"):
                        event_id = int(line[3:].strip())
                    elif line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        payload = json.loads("\n".join(data))
                        if event_type == "end":
                            terminal = payload["status"]
                            data = []
                            continue
                        if event_id is not None:
                            if event_id <= last:
                                metrics["errors"]["duplicate_event"] += 1
                            last = max(last, event_id)
                        if first:
                            metrics["first_event"].append(time.perf_counter() - started)
                            first = False
                        if payload.get("persisted_at"):
                            metrics["event_lag"].append(
                                time.time()
                                - datetime.fromisoformat(
                                    payload["persisted_at"]
                                ).timestamp()
                            )
                        event_type, event_id, data = "message", None, []
                    if time.perf_counter() >= deadline:
                        break
            if terminal is not None:
                return terminal
            metrics["errors"]["stream_ended_before_terminal"] += 1
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            metrics["errors"][type(exc).__name__] += 1
        await asyncio.sleep(0.05)
    metrics["errors"]["terminal_timeout"] += 1
    return None


async def run_slot(client, args, slot, deadline, metrics):
    await asyncio.sleep(args.ramp_seconds * slot / args.runs)
    readers = args.subscribers // args.runs + (slot < args.subscribers % args.runs)
    base = args.base_url.rstrip("/")
    while time.perf_counter() < deadline:
        user = f"load-{metrics['test_id']}-{slot}"
        headers = {"Authorization": f"Bearer {args.token}", "X-QwenPaw-User": user}
        body = {
            "usrid": user,
            "sessionid": f"load-session-{slot}",
            "channelid": "load-test",
            "request_id": uuid.uuid4().hex,
            "message": args.message,
        }
        started = time.perf_counter()
        try:
            response = await client.post(f"{base}/v1/runs", headers=headers, json=body)
            metrics["submit"].append(time.perf_counter() - started)
            response.raise_for_status()
            run_id = response.json()["id"]
            metrics["submitted"] += 1
            statuses = await asyncio.gather(
                *(
                    subscribe(
                        client,
                        f"{base}/v1/runs/{run_id}/events",
                        headers,
                        started,
                        deadline + args.drain_seconds,
                        metrics,
                    )
                    for _ in range(readers)
                )
            )
            if len(set(statuses)) != 1:
                metrics["errors"]["inconsistent_terminal"] += 1
            status = statuses[0]
            metrics["terminal_statuses"][str(status)] += 1
            if status == "completed":
                metrics["completed"] += 1
            elif status is not None:
                metrics["errors"]["run_" + status] += 1
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            metrics["errors"]["submit_" + type(exc).__name__] += 1
            await asyncio.sleep(0.1)


async def run(args):
    if args.runs < 1 or args.subscribers < args.runs:
        raise ValueError("Require subscribers >= runs >= 1")
    started = time.perf_counter()
    metrics = {
        "test_id": uuid.uuid4().hex,
        "submit": [],
        "first_event": [],
        "event_lag": [],
        "errors": Counter(),
        "terminal_statuses": Counter(),
        "submitted": 0,
        "completed": 0,
    }
    # A single pool scans hundreds of idle SSE sockets on every request.
    # Isolate each simulated user's small pool so the load generator itself
    # does not dominate the observed platform latency.
    async with AsyncExitStack() as stack:
        clients = []
        connections = (args.subscribers + args.runs - 1) // args.runs + 1
        for _ in range(args.runs):
            clients.append(
                await stack.enter_async_context(
                    httpx.AsyncClient(
                        trust_env=False,
                        timeout=httpx.Timeout(60),
                        limits=httpx.Limits(
                            max_connections=connections,
                            max_keepalive_connections=connections,
                        ),
                    )
                )
            )
        started = time.perf_counter()
        await asyncio.gather(
            *(
                run_slot(
                    clients[slot],
                    args,
                    slot,
                    started + args.ramp_seconds + args.duration,
                    metrics,
                )
                for slot in range(args.runs)
            )
        )
    return {
        "test_id": metrics["test_id"],
        "configured_duration_seconds": args.duration,
        "ramp_seconds": args.ramp_seconds,
        "actual_duration_seconds": time.perf_counter() - started,
        "task_slots": args.runs,
        "sse_slots": args.subscribers,
        "runs_submitted": metrics["submitted"],
        "runs_completed": metrics["completed"],
        "terminal_statuses": metrics["terminal_statuses"],
        "errors": dict(metrics["errors"]),
        "submit_latency_seconds": stats(metrics["submit"]),
        "first_event_observed_seconds": stats(metrics["first_event"]),
        "event_persisted_at_lag_seconds": stats(metrics["event_lag"]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8090")
    parser.add_argument("--token", required=True)
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--ramp-seconds", type=float, default=5)
    parser.add_argument("--drain-seconds", type=float, default=120)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--subscribers", type=int, default=500)
    parser.add_argument("--message", default="load test")
    print(json.dumps(asyncio.run(run(parser.parse_args())), indent=2))


if __name__ == "__main__":
    main()
