"""Small, local-only tool implementation used by the sandbox HTTP service."""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import json
import os
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable

MAX_OUTPUT = 1024 * 1024
_READ_CHUNK_BYTES = 16 * 1024


class _BoundedOutput:
    """Capture one process stream while optionally forwarding decoded text."""

    def __init__(
        self,
        stream: str,
        on_output: Callable[[str, str], Awaitable[None]] | None,
    ) -> None:
        self.stream = stream
        self.on_output = on_output
        self.data = bytearray()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.emitted = 0

    async def feed(self, chunk: bytes) -> None:
        remaining = MAX_OUTPUT - len(self.data)
        if remaining <= 0:
            return
        accepted = chunk[:remaining]
        self.data.extend(accepted)
        text = self.decoder.decode(accepted, final=False)
        await self._emit(text)

    async def finish(self) -> None:
        await self._emit(self.decoder.decode(b"", final=True))

    async def _emit(self, text: str) -> None:
        if not text or self.on_output is None or self.emitted >= MAX_OUTPUT:
            return
        encoded = text.encode("utf-8")
        remaining = MAX_OUTPUT - self.emitted
        if len(encoded) > remaining:
            text = encoded[:remaining].decode("utf-8", errors="ignore")
            if not text:
                return
            encoded = text.encode("utf-8")
        self.emitted += len(encoded)
        await self.on_output(self.stream, text)

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


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

    async def shell(
        self,
        command: str,
        timeout: float = 30,
        on_output: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
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
            deadline = asyncio.get_running_loop().time() + timeout

            async def collect(stream: asyncio.StreamReader, name: str) -> str:
                captured = _BoundedOutput(name, on_output)
                while True:
                    chunk = await stream.read(_READ_CHUNK_BYTES)
                    if not chunk:
                        await captured.finish()
                        return captured.text()
                    await captured.feed(chunk)

            readers = asyncio.gather(
                collect(proc.stdout, "stdout"), collect(proc.stderr, "stderr")
            )
            try:
                out, err = await asyncio.wait_for(readers, timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if not readers.done():
                    readers.cancel()
                await asyncio.gather(readers, return_exceptions=True)
                raise
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            await asyncio.wait_for(proc.wait(), remaining)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await self._terminate(proc)
            raise
        return {
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
        }

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        """Terminate the tool process and make a best effort to reap it.

        POSIX hosts signal the whole process group created by
        ``start_new_session=True`` so a shell's descendants die with it.
        Windows has neither ``os.killpg`` nor ``signal.SIGKILL`` -- merely
        touching them raises ``AttributeError`` -- so it falls back to
        terminating the direct child.  Never let that platform difference
        replace the caller's timeout/cancellation with a new exception.
        """
        killpg = getattr(os, "killpg", None)
        if killpg is not None:
            try:
                killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        else:
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass

        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), 1)
        except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
            pass

        sigkill = getattr(signal, "SIGKILL", None)
        if killpg is not None and sigkill is not None:
            try:
                killpg(proc.pid, sigkill)
            except (ProcessLookupError, OSError):
                pass
        else:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

        try:
            await asyncio.shield(proc.wait())
        except (asyncio.CancelledError, OSError):
            pass

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
