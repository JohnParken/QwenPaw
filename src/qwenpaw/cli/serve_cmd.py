"""Independent multi-user service entry points."""

import asyncio
import os
from pathlib import Path

import click


@click.group("serve")
def serve_cmd():
    """Run the BFF API, shared worker, controller, or sandbox service."""


@serve_cmd.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit a safe JSON summary.")
def check(as_json):
    """Validate production configuration offline; does not test connectivity."""
    import json
    from urllib.parse import parse_qs, urlsplit

    from ..server.config import ServerConfig

    # Validation exceptions may contain credentials; never render their inputs.
    stage = "environment"
    try:
        config = ServerConfig.from_env()
        stage = "database_url"
        url = urlsplit(config.database_url.get_secret_value())
        if (
            url.scheme not in {"mysql", "mariadb", "tdsql"}
            or not url.hostname
            or not url.path.strip("/")
            or url.fragment
        ):
            raise ValueError()
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError()
        options = parse_qs(url.query, strict_parsing=True)
        if set(options) - {"ssl_ca", "ssl_cert", "ssl_key", "connect_timeout"}:
            raise ValueError()
        if int(options.get("connect_timeout", ["10"])[0]) <= 0:
            raise ValueError()
        stage = "assistant_definition"
        definition = config.definition()
    except Exception:
        if as_json:
            click.echo(json.dumps({"ok": False, "stage": stage}))
            raise click.exceptions.Exit(1) from None
        raise click.ClickException(
            f"Invalid {stage}; check configuration (values withheld)."
        ) from None
    result = {
        "ok": True,
        "profile": "v1-production",
        "connectivity_checked": False,
        "model_protocol": definition.model_protocol,
        "controller_required": True,
        "sandbox_tools": sum(t.execution == "sandbox" for t in definition.tools),
        "service_tools": sum(t.execution == "service" for t in definition.tools),
        "storage": "tdsql",
        "redis_enabled": bool(config.redis_url),
        "concurrency": config.concurrency,
        "per_user_concurrency": config.per_user_concurrency,
    }
    if as_json:
        click.echo(json.dumps(result))
    else:
        click.echo("/v1 production configuration valid (offline only).")
        click.echo(
            "Required: TDSQL, model service, API, Worker, Controller; files use S3."
        )
        click.echo("The production Controller uses Kubernetes and session storage.")
        click.echo(
            "No database, model, Controller or object-storage connection was tested."
        )


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
