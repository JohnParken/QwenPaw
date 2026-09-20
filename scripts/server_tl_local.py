"""Run a loopback, single-process server against a real TL model service."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import secrets
from pathlib import Path

import uvicorn

from qwenpaw.server.agent import create_model_factory
from qwenpaw.server.api import create_api
from qwenpaw.server.config import AssistantDefinition, ServerConfig
from qwenpaw.server.contracts import RuntimeServices
from qwenpaw.server.memory import MemoryService
from qwenpaw.server.objects import MemoryObjects
from qwenpaw.server.storage import MemoryRepository
from qwenpaw.server.worker import Worker


class FailClosedTools:
    def __init__(self, repository):
        self.repository = repository

    async def invoke(self, *args, **kwargs):
        raise RuntimeError(
            "Sandbox tools require production serve with TDSQL and Controller"
        )

    async def stop_session(self, *args, **kwargs):
        session_id = args[0] if args else kwargs["session_id"]
        run_id = kwargs.get("run_id", args[1] if len(args) > 1 else None)
        epoch = kwargs.get("epoch", args[2] if len(args) > 2 else None)
        async with self.repository.sandbox_stop_guard(
            session_id,
            {"run_id": run_id, "epoch": epoch},
        ):
            pass

    async def close(self):
        return None


def load_config(definition_path: Path) -> ServerConfig:
    definition = AssistantDefinition.model_validate_json(
        definition_path.read_text(encoding="utf-8")
    )
    if definition.model_protocol != "tl":
        raise ValueError("server_tl_local requires definition model_protocol=tl")
    if any(tool.execution == "sandbox" for tool in definition.tools):
        raise ValueError(
            "server_tl_local cannot run sandbox tools; use production serve with TDSQL"
        )
    token = os.environ.get("QWENPAW_SERVER_SERVICE_TOKEN") or secrets.token_urlsafe(32)
    internal = os.environ.get("QWENPAW_SERVER_INTERNAL_TOKEN") or secrets.token_urlsafe(
        32
    )
    if len(token) < 32 or len(internal) < 32:
        raise ValueError("Local service tokens must contain at least 32 characters")
    values = {
        "database_url": "memory://",
        "service_token": token,
        "internal_token": internal,
        "definition_path": definition_path,
        "model_api_key": os.environ.get("QWENPAW_SERVER_MODEL_API_KEY", ""),
    }
    return ServerConfig.model_validate(values)


async def serve(definition_path: Path, port: int) -> None:
    from qwenpaw.providers.tl_wire_log import model_debug_enabled
    from qwenpaw.utils.logging import setup_logger

    setup_logger("debug" if model_debug_enabled() else "info")
    config = load_config(definition_path)
    repository = MemoryRepository()
    await repository.migrate()
    definition = config.definition()
    await repository.put_definition(
        definition.version, definition.model_dump(mode="json")
    )
    tools = FailClosedTools(repository)
    factory = create_model_factory(config)
    memory = MemoryService(config, repository)
    await memory.initialize()
    worker = Worker(config, RuntimeServices(repository, tools, factory, memory))
    task = asyncio.create_task(worker.serve())
    memory_task = asyncio.create_task(memory.serve())
    print("Local TL server: http://127.0.0.1:%d" % port)
    print("Use Authorization: Bearer <service token> and X-QwenPaw-User: local.")
    if not os.environ.get("QWENPAW_SERVER_SERVICE_TOKEN"):
        print(
            "Service token (local setup only): "
            + config.service_token.get_secret_value()
        )
    try:
        await uvicorn.Server(
            uvicorn.Config(
                create_api(config, repository, MemoryObjects()),
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        ).serve()
    finally:
        worker.stopping.set()
        task.cancel()
        memory_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(asyncio.CancelledError):
            await memory_task
        await tools.close()
        await factory.close()
        await repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    asyncio.run(serve(args.definition, args.port))


if __name__ == "__main__":
    main()
