"""Explicit local demo: real API/Worker, memory storage, mock model and tools.

Run from the repository root with the project interpreter: on macOS/Linux
``.venv/bin/python scripts/server_stream_demo.py``, on Windows
``.venv\Scripts\python.exe scripts\server_stream_demo.py``.
Only binds loopback. Does not execute submitted commands or contact a model.
Data disappears on exit. Production serve commands never select these mocks.
"""

import argparse
import asyncio
import contextlib
import json
import os
import tempfile
import uuid
from pathlib import Path

import httpx
import uvicorn

from qwenpaw.server.api import create_api
from qwenpaw.server.config import AssistantDefinition, ServerConfig
from qwenpaw.server.contracts import RuntimeServices
from qwenpaw.server.storage import MemoryRepository
from qwenpaw.server.worker import Worker

DEMO_TOKEN = "qwenpaw-stream-demo-local-token-32"


def model_factory(_definition):
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel

    class Stream(httpx.AsyncByteStream):
        def __init__(self, final):
            self.final = final

        async def __aiter__(self):
            def event(delta, reason=None):
                return (
                    "data: "
                    + json.dumps(
                        {
                            "id": "demo",
                            "object": "chat.completion.chunk",
                            "model": "mock",
                            "created": 1,
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": reason}
                            ],
                        }
                    )
                    + "\n\n"
                ).encode()

            text = (
                "演示完成。这是模拟输出，没有执行真实命令。"
                if self.final
                else "我将演示一次需要审批的工具调用。"
            )
            for char in text:
                await asyncio.sleep(0.06)
                yield event({"content": char})
            if not self.final:
                yield event(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "demo-" + uuid.uuid4().hex,
                                "type": "function",
                                "function": {
                                    "name": "shell",
                                    "arguments": json.dumps(
                                        {"command": "echo streaming-demo"}
                                    ),
                                },
                            }
                        ]
                    }
                )
            yield event({}, "stop" if self.final else "tool_calls")
            yield b"data: [DONE]\n\n"

    async def handle(request):
        messages = json.loads(request.content)["messages"]
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        final = any(m["role"] == "tool" for m in messages[last_user + 1 :])
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Stream(final)
        )

    return OpenAIChatModel(
        credential=OpenAICredential(api_key="mock", base_url="https://mock.invalid/v1"),
        model="mock",
        max_retries=0,
        client_kwargs={
            "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handle))
        },
    )


class DemoTools:
    def __init__(self, repo):
        self.repo = repo

    async def invoke(self, ctx, call_id, name, arguments, timeout):
        lines = []
        for i in range(5):
            await self.repo.validate_execution(ctx)
            text = f"[模拟输出] step {i + 1}/5\n"
            lines.append(text)
            await self.repo.append(
                ctx,
                [
                    {
                        "type": "tool_output",
                        "tool_call_id": call_id,
                        "stream": "stdout",
                        "text": text,
                    }
                ],
            )
            await asyncio.sleep(0.6)
        return {
            "status": "ok",
            "result": {"stdout": "".join(lines), "stderr": "", "exit_code": 0},
        }

    async def stop_session(self, session_id, run_id=None, epoch=None):
        # Mock operations have no processes or external side effects.
        async with self.repo.sandbox_stop_guard(
            session_id, {"run_id": run_id, "epoch": epoch}
        ):
            pass


async def serve(port):
    with tempfile.TemporaryDirectory(prefix="qwenpaw-stream-demo-") as directory:
        definition = AssistantDefinition(
            version="stream-demo-v1",
            name="流式验收演示",
            model="mock-stream-demo",
            system_prompt="演示流式工具调用。",
            auto_memory=False,
            tools=(
                {
                    "name": "shell",
                    "description": "模拟命令，不执行用户代码",
                    "approval": True,
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            ),
        )
        path = Path(directory) / "assistant.json"
        path.write_text(definition.model_dump_json(), encoding="utf-8")
        config = ServerConfig(
            database_url="memory://",
            service_token=os.environ.get("QWENPAW_SERVER_SERVICE_TOKEN", DEMO_TOKEN),
            internal_token="mock-internal-not-used" * 2,
            definition_path=path,
        )
        repo = MemoryRepository()
        await repo.migrate()
        worker = Worker(config, RuntimeServices(repo, DemoTools(repo), model_factory))
        task = asyncio.create_task(worker.serve())
        try:
            print(
                "Local streaming demo: mock model/tools, volatile memory, no file storage."
            )
            await uvicorn.Server(
                uvicorn.Config(
                    create_api(config, repo, None),
                    host="127.0.0.1",
                    port=port,
                    log_level="warning",
                )
            ).serve()
        finally:
            worker.stopping.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await repo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8090)
    asyncio.run(serve(parser.parse_args().port))
