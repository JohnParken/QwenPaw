"""Independent multi-user service entry points."""

import asyncio
import os
from pathlib import Path

import click


@click.group("serve")
def serve_cmd():
    """Run the BFF API, shared worker, controller, or sandbox service."""


async def _components():
    from ..server.config import ServerConfig
    from ..server.storage import create_repository

    config = ServerConfig.from_env()
    dsn = config.database_url.get_secret_value()
    if dsn == "memory://":
        raise click.ClickException(
            "memory:// is only for in-process tests; service processes require TDSQL"
        )
    repository = create_repository(
        dsn, prefix=config.storage_table_prefix, max_size=config.database_pool_size
    )
    await repository.open()
    return config, repository


@serve_cmd.command("migrate")
def migrate():
    """Apply versioned service schema migrations before rollout."""

    async def run():
        config, repo = await _components()
        try:
            await repo.migrate()
            from ..server.memory import MemoryService

            await MemoryService(config, repo).initialize()
            definition = config.definition()
            await repo.put_definition(
                definition.version, definition.model_dump(mode="json")
            )
        finally:
            await repo.close()

    asyncio.run(run())


@serve_cmd.command("api")
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8090, type=int)
def api(host, port):
    """Serve authenticated BFF APIs and durable SSE."""

    async def run():
        import uvicorn
        from ..server.api import create_api
        from ..server.objects import S3Objects
        from ..server.notifications import Notifications

        config, repo = await _components()
        notifications = Notifications(config.redis_url)
        await notifications.start()
        try:
            app = create_api(config, repo, S3Objects(config), notifications)
            await uvicorn.Server(uvicorn.Config(app, host=host, port=port)).serve()
        finally:
            await notifications.close()
            await repo.close()

    asyncio.run(run())


@serve_cmd.command("worker")
def worker():
    """Run shared Agent workers; never executes local user tools."""

    async def run():
        from ..server.agent import create_model_factory
        from ..server.contracts import RuntimeServices
        from ..server.notifications import Notifications
        from ..server.tools import RemoteTools
        from ..server.worker import Worker
        from ..server.memory import MemoryService

        config, repo = await _components()
        tools = RemoteTools(config, repo)
        notifier = Notifications(config.redis_url)
        await notifier.start()
        model_factory = create_model_factory(config)
        try:
            memory = MemoryService(config, repo)
            await memory.initialize()
            async with asyncio.TaskGroup() as group:
                group.create_task(
                    Worker(
                        config,
                        RuntimeServices(repo, tools, model_factory, memory),
                        notifier,
                    ).serve()
                )
                group.create_task(memory.serve())
        finally:
            await tools.close()
            await model_factory.close()
            await notifier.close()
            await repo.close()

    asyncio.run(run())


@serve_cmd.command("controller")
@click.option("--port", default=8091, type=int)
def controller(port):
    """Run the Kubernetes sandbox controller (requires Pod RBAC)."""

    async def run():
        import uvicorn
        from ..server.controller import (
            Kubernetes,
            SandboxController,
            create_controller_app,
        )
        from ..server.objects import S3Objects

        config, repo = await _components()
        controller = SandboxController(
            config, repo, Kubernetes(config), S3Objects(config)
        )
        try:
            app = create_controller_app(controller, config)
            await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port)).serve()
        finally:
            await repo.close()

    asyncio.run(run())


@serve_cmd.command("sandbox")
@click.option("--port", default=8092, type=int)
def sandbox(port):
    """Run only inside an isolated tool Pod. No database/model credentials."""
    import uvicorn
    from ..server.sandbox_app import create_sandbox_app

    token = os.environ["QWENPAW_SANDBOX_TOKEN"]
    if len(token) < 32:
        raise click.ClickException("Sandbox token must contain at least 32 characters")
    root = Path(os.environ.get("QWENPAW_SANDBOX_ROOT", "/workspace"))
    app = create_sandbox_app(root, token)
    uvicorn.run(app, host="0.0.0.0", port=port)
