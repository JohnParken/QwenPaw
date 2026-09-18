"""Small, local-only tool implementation used by the sandbox HTTP service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
from pathlib import Path
from typing import Any

MAX_OUTPUT = 1024 * 1024


class SandboxTools:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._browser = None
        self._playwright = None

    def path(self, value: str = ".") -> Path:
        candidate = (self.root / value).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path escapes sandbox root") from exc
        return candidate

    async def shell(self, command: str, timeout: float = 30) -> dict[str, Any]:
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.root,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:

            async def collect(stream: asyncio.StreamReader) -> bytes:
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = await stream.read(65536)
                    if not chunk:
                        return b"".join(chunks)[:MAX_OUTPUT]
                    if size < MAX_OUTPUT:
                        chunks.append(chunk[: MAX_OUTPUT - size])
                        size += len(chunk)

            out, err = await asyncio.wait_for(
                asyncio.gather(collect(proc.stdout), collect(proc.stderr)), timeout
            )
            await proc.wait()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), 1)
            except Exception:
                pass
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
            raise
        return {
            "exit_code": proc.returncode,
            "stdout": out[:MAX_OUTPUT].decode(errors="replace"),
            "stderr": err[:MAX_OUTPUT].decode(errors="replace"),
        }

    async def read_file(self, path: str) -> dict[str, Any]:
        target = self.path(path)

        def bounded_read() -> bytes:
            with target.open("rb") as stream:
                return stream.read(MAX_OUTPUT + 1)

        data = await asyncio.to_thread(bounded_read)
        return {
            "path": str(target.relative_to(self.root)),
            "content": data[:MAX_OUTPUT].decode(errors="replace"),
            "truncated": len(data) > MAX_OUTPUT,
        }

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        if len(content.encode("utf-8")) > MAX_OUTPUT:
            raise ValueError("content exceeds output limit")
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")
        return {
            "path": str(target.relative_to(self.root)),
            "bytes": len(content.encode("utf-8")),
        }

    async def publish_file(self, path: str) -> dict[str, Any]:
        import base64

        target = self.path(path)

        def read():
            with target.open("rb") as stream:
                content = stream.read(20 * 1024 * 1024 + 1)
            if len(content) > 20 * 1024 * 1024:
                raise ValueError("Artifact exceeds 20 MiB")
            return content

        content = await asyncio.to_thread(read)
        return {
            "name": target.name,
            "base64": base64.b64encode(content).decode("ascii"),
        }

    async def list_files(self, path: str = ".") -> dict[str, Any]:
        target = self.path(path)
        if not target.is_dir():
            raise ValueError("path is not a directory")
        import itertools

        entries = sorted(
            str(p.relative_to(self.root))
            for p in itertools.islice(target.iterdir(), 1001)
        )
        return {
            "path": str(target.relative_to(self.root)) or ".",
            "files": entries[:1000],
            "truncated": len(entries) > 1000,
        }

    async def browser(self, action: str = "snapshot", **kwargs: Any) -> Any:
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._page = await self._browser.new_page()
        page = self._page
        if action == "navigate":
            await page.goto(str(kwargs["url"]))
        elif action == "click":
            await page.locator(str(kwargs["selector"])).click()
        elif action != "snapshot":
            raise ValueError("unsupported browser action")
        return {
            "url": page.url,
            "title": await page.title(),
            "content": (await page.content())[:MAX_OUTPUT],
        }

    async def mcp(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        # MCP transports use AnyIO task-local cancel scopes. Keep connection
        # enter/call/exit in one long-lived task, not separate HTTP request tasks.
        config = json.loads(os.environ.get("QWENPAW_SERVER_MCP_JSON", "{}"))
        servers = config.get("servers", config)
        spec = servers.get(server)
        if not isinstance(spec, dict):
            raise ValueError("unknown MCP server")
        if not hasattr(self, "_mcp_workers"):
            self._mcp_workers = {}
        worker = self._mcp_workers.get(server)
        if worker is None or worker[1].done():
            queue = asyncio.Queue(maxsize=1)
            task = asyncio.create_task(self._mcp_loop(spec, queue))
            worker = (queue, task)
            self._mcp_workers[server] = worker
        future = asyncio.get_running_loop().create_future()
        await worker[0].put((tool, arguments or {}, future))
        return await future

    async def _mcp_loop(self, spec, queue):
        from contextlib import AsyncExitStack
        from mcp import ClientSession

        current = None
        try:
            async with AsyncExitStack() as stack:
                if spec.get("transport", "stdio") == "stdio":
                    from mcp.client.stdio import StdioServerParameters, stdio_client

                    params = StdioServerParameters(
                        command=spec["command"],
                        args=spec.get("args", []),
                        env=spec.get("env"),
                    )
                    streams = await stack.enter_async_context(stdio_client(params))
                elif spec.get("transport") in {"http", "streamable_http"}:
                    from mcp.client.streamable_http import streamable_http_client

                    streams = await stack.enter_async_context(
                        streamable_http_client(spec["url"])
                    )
                else:
                    raise ValueError("unsupported MCP transport")
                session = await stack.enter_async_context(
                    ClientSession(streams[0], streams[1])
                )
                await session.initialize()
                while True:
                    tool, arguments, current = await queue.get()
                    result = await session.call_tool(tool, arguments)
                    if not current.done():
                        current.set_result(result.model_dump(mode="json"))
                    current = None
        except BaseException as exc:
            if current is not None and not current.done():
                current.set_exception(RuntimeError("MCP connection interrupted"))
            while not queue.empty():
                _, _, future = queue.get_nowait()
                if not future.done():
                    future.set_exception(RuntimeError("MCP connection failed"))
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def close(self) -> None:
        workers = [item[1] for item in getattr(self, "_mcp_workers", {}).values()]
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @staticmethod
    def digest(name: str, arguments: dict[str, Any]) -> str:
        payload = json.dumps([name, arguments], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
