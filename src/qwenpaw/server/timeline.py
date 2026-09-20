"""Compact replayable history, independent of the seven-day SSE journal."""

from copy import deepcopy


def compact_timeline(events: list[dict], run_id: str, status: str) -> dict | None:
    items: dict[str, dict] = {}
    for event in events:
        kind = event.get("type")
        obj = event.get("object")
        if kind == "tool":
            key = "tool:" + event["tool_call_id"]
            items[key] = {**items.get(key, {}), **deepcopy(event)}
        elif kind == "tool_output":
            key = "tool:" + event["tool_call_id"]
            tool = items.setdefault(
                key, {"type": "tool", "tool_call_id": event["tool_call_id"]}
            )
            tool["log"] = (tool.get("log", "") + event.get("text", ""))[-65536:]
        elif obj == "message" and event.get("id"):
            key = "message:" + event["id"]
            old = items.get(key, {})
            # Starting a message must not erase already received partial text.
            items[key] = {
                **deepcopy(event),
                "content": deepcopy(event.get("content") or old.get("content", [])),
            }
        elif obj == "content" and event.get("msg_id"):
            key = "message:" + event["msg_id"]
            message = items.setdefault(
                key,
                {
                    "object": "message",
                    "id": event["msg_id"],
                    "role": "assistant",
                    "type": "message",
                    "content": [],
                },
            )
            content = message["content"]
            index = event.get("index") or 0
            if not isinstance(index, int) or not 0 <= index < 10000:
                continue
            while len(content) <= index:
                content.append({})
            block = deepcopy(event)
            if event.get("delta") and isinstance(event.get("text"), str):
                block["text"] = content[index].get("text", "") + event["text"]
            # Tool arguments are reconstructed by final envelopes / tool records.
            block["delta"] = False
            content[index] = block
    if not items:
        return None
    for item in items.values():
        if item.get("type") == "tool" and item.get("status") in {
            None,
            "preparing",
            "awaiting_approval",
            "running",
        }:
            item["status"] = (
                "unknown" if item.get("status") == "running" else "cancelled"
            )
    return {
        "role": "assistant",
        "type": "run_timeline",
        "run_id": run_id,
        "status": status,
        "events": list(items.values()),
    }
