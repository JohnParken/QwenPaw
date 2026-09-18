"""Deterministic OpenAI-compatible model for service tests, never production.

Run: python scripts/server_mock_model.py --port 8093
Use an assistant definition with model=mock, base_url=http://127.0.0.1:8093/v1,
auto_memory=false, tools=[]; no external model credentials are consumed.
"""

import argparse
import asyncio
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()

    async def chunks():
        tool = getattr(app.state, "tool_name", None)
        if tool and body.get("messages", [{}])[-1].get("role") == "user":
            chunk = {
                "id": "mock-tool",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "mock",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "mock-call",
                                    "type": "function",
                                    "function": {
                                        "name": tool,
                                        "arguments": app.state.tool_arguments,
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
            yield "data: " + json.dumps(chunk) + "\n\n"
            chunk["choices"][0].update(delta={}, finish_reason="tool_calls")
            yield "data: " + json.dumps(chunk) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        for index in range(20):
            await asyncio.sleep(0.05)
            chunk = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "mock",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"token{index} "},
                        "finish_reason": None,
                    }
                ],
            }
            yield "data: " + json.dumps(chunk) + "\n\n"
        yield 'data: {"id":"mock","object":"chat.completion.chunk","created":1,"model":"mock","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield "data: [DONE]\n\n"

    if not body.get("stream"):
        return {
            "id": "mock",
            "object": "chat.completion",
            "created": 1,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"facts":[]}'},
                    "finish_reason": "stop",
                }
            ],
        }
    return StreamingResponse(chunks(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8093)
    parser.add_argument(
        "--tool-name",
        help="Optional fixed tool call once per turn; benchmark environments only",
    )
    parser.add_argument("--tool-arguments", default='{"command":"sleep 1"}')
    args = parser.parse_args()
    json.loads(args.tool_arguments)
    app.state.tool_name = args.tool_name
    app.state.tool_arguments = args.tool_arguments
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
