"""Single-turn office Agent used by the cloud Worker.

This bridge intentionally bypasses the legacy Workspace tool registry.  Only
Attempt-scoped file operations and immutable built-in Skills are exposed.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from .sandbox import (
    PathMap,
    ShellPolicy,
    TrustedContainerPolicy,
    seatbelt_profile,
)
from .skills import (
    SKILL_NAMES,
    directory_digest,
    discover_builtin_skills,
    load_skill,
)


class OfficeAgentError(RuntimeError):
    pass


def create_model(config: dict[str, str]):
    provider_name = config.get("provider")
    model_id = config.get("model", "")
    base_url = config.get("base_url", "")
    if provider_name not in {"tl", "openai"} or not model_id or not base_url:
        raise OfficeAgentError("INVALID_MODEL_CONFIG")
    from qwenpaw.providers.provider import ModelInfo

    common = {
        "id": "office-" + provider_name,
        "name": "Office " + provider_name.upper(),
        "base_url": base_url,
        "models": [ModelInfo(id=model_id, name=model_id)],
        "is_custom": True,
    }
    if provider_name == "tl":
        from qwenpaw.providers.tl_provider import TLProvider

        provider = TLProvider(**common, api_key=config.get("api_key", ""))
    else:
        from qwenpaw.providers.openai_provider import OpenAIProvider

        api_key = config.get("api_key", "")
        if not api_key:
            raise OfficeAgentError("MODEL_CREDENTIAL_REQUIRED")
        provider = OpenAIProvider(**common, api_key=api_key)
    return provider.get_chat_model_instance(model_id)


class OfficeAgentBridge:
    def __init__(
        self,
        paths: PathMap,
        model_config: dict[str, str],
        *,
        model_factory: Callable[[dict[str, str]], Any] = create_model,
        on_text: Callable[[str], Awaitable[None]] | None = None,
        shell_mode: str = "sandboxed",
    ):
        self.paths = paths
        self.model = model_factory(model_config)
        self.on_text = on_text
        if shell_mode not in {"sandboxed", "trusted_container"}:
            raise OfficeAgentError("INVALID_SHELL_MODE")
        self.shell_mode = shell_mode
        self.catalog = discover_builtin_skills()

    def _path(self, value: str, *, writable: bool = False) -> Path:
        if not value or "\\" in value:
            raise OfficeAgentError("INVALID_PATH")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise OfficeAgentError("PATH_OUTSIDE_ATTEMPT")
        allowed = {"workspace", "output", "tmp"} if writable else {
            "input", "workspace", "output", "tmp"
        }
        if not relative.parts or relative.parts[0] not in allowed:
            raise OfficeAgentError("PATH_OUTSIDE_ATTEMPT")
        target = self.paths.root.joinpath(*relative.parts)
        root = self.paths.root.resolve(strict=True)
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise OfficeAgentError("PATH_OUTSIDE_ATTEMPT")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise OfficeAgentError("SYMLINK_NOT_ALLOWED")
        return target

    def _tools(self):
        from agentscope.tool import FunctionTool

        def list_files(directory: str = "input") -> str:
            base = self._path(directory)
            if not base.is_dir():
                raise OfficeAgentError("NOT_A_DIRECTORY")
            return json.dumps([
                p.relative_to(self.paths.root).as_posix()
                for p in sorted(base.rglob("*")) if p.is_file() and not p.is_symlink()
            ])

        def read_text(path: str) -> str:
            target = self._path(path)
            if not target.is_file() or target.stat().st_size > 1_048_576:
                raise OfficeAgentError("FILE_NOT_READABLE")
            return target.read_text(encoding="utf-8")

        def write_text(path: str, content: str) -> str:
            target = self._path(path, writable=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return path

        def copy_file(source: str, destination: str) -> str:
            src = self._path(source)
            dst = self._path(destination, writable=True)
            if not src.is_file():
                raise OfficeAgentError("SOURCE_NOT_FILE")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            return destination

        def load_builtin_skill(name: str) -> str:
            if name not in SKILL_NAMES:
                raise OfficeAgentError("SKILL_NOT_ALLOWED")
            item = self.catalog[name]
            if not item.available:
                raise OfficeAgentError(item.readiness.disabled_reason or "SKILL_DISABLED")
            value = load_skill(name, full=True)
            return json.dumps(value, default=str, ensure_ascii=False)

        async def run_skill_script(
            skill: str, argv: list[str], timeout: int = 60
        ) -> str:
            if skill not in SKILL_NAMES:
                raise OfficeAgentError("SKILL_NOT_ALLOWED")
            item = self.catalog[skill]
            if not item.available or item.directory is None:
                raise OfficeAgentError("SKILL_DISABLED")
            try:
                current_digest = directory_digest(item.directory)
            except (OSError, ValueError) as exc:
                raise OfficeAgentError("SKILL_DIGEST_MISMATCH") from exc
            if current_digest != item.digest:
                raise OfficeAgentError("SKILL_DIGEST_MISMATCH")
            if self.shell_mode == "trusted_container":
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    raise OfficeAgentError("TRUSTED_CONTAINER_REQUIRES_NON_ROOT")
                command = TrustedContainerPolicy(self.paths).validate(argv, item)
            else:
                policy = ShellPolicy(
                    self.paths, item.directory, item.manifest.allowed_commands
                )
                command = policy.validate(argv, item.directory, skill)
            actual = list(command.argv)
            if Path("/usr/bin/sandbox-exec").exists():
                actual = [
                    "/usr/bin/sandbox-exec", "-p", seatbelt_profile(self.paths),
                    *actual,
                ]
            elif shutil.which("bwrap"):
                mounts: list[str] = []
                for public in (
                    "/usr", "/bin", "/lib", "/lib64", "/etc/ld.so.cache",
                    str(Path(sys.prefix).resolve()), str(item.directory),
                ):
                    if Path(public).exists():
                        mounts.extend(["--ro-bind", public, public])
                actual = [
                    "bwrap", "--unshare-all",
                    "--die-with-parent", "--new-session", *mounts,
                    "--bind", str(self.paths.root), str(self.paths.root),
                    "--proc", "/proc", "--dev", "/dev", "--", *actual,
                ]
            elif self.shell_mode == "sandboxed":
                raise OfficeAgentError("SHELL_SANDBOX_UNAVAILABLE")
            env = self.paths.environment()
            proc = await asyncio.create_subprocess_exec(
                *actual, cwd=command.cwd, env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), min(timeout, 120))
            except (asyncio.TimeoutError, asyncio.CancelledError):
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
                raise OfficeAgentError("SKILL_SCRIPT_TIMEOUT") from None
            combined = (out + b"\n" + err)[:65536].decode(errors="replace")
            if proc.returncode:
                raise OfficeAgentError(f"SKILL_SCRIPT_EXIT_{proc.returncode}: {combined}")
            return combined

        def tool(fn, description):
            return FunctionTool(fn, name=fn.__name__, description=description)

        return [
            tool(list_files, "List files inside this private Attempt."),
            tool(read_text, "Read a small UTF-8 text file inside the Attempt."),
            tool(write_text, "Write UTF-8 text under workspace/output/tmp."),
            tool(copy_file, "Copy one Attempt file to workspace/output/tmp."),
            tool(load_builtin_skill, "Load one approved built-in office Skill."),
            tool(run_skill_script, "Run an approved Skill command without network."),
        ]

    async def run(self, message: str, restored_state: dict | None = None) -> dict:
        from agentscope.agent import ReActConfig
        from agentscope.message import Msg, TextBlock
        from agentscope.tool import Toolkit
        from qwenpaw.agents.react_agent import QwenPawAgent

        summaries = [
            f"{s.manifest.id}@{s.manifest.version}: {s.manifest.description}"
            for s in self.catalog.values() if s.available
        ]
        config = SimpleNamespace(
            language="en", tools=None,
            running=SimpleNamespace(light_context_config=SimpleNamespace(
                context_compact_config=SimpleNamespace(enabled=False)
            )),
        )
        agent = QwenPawAgent(
            name="OfficeAgent", model=self.model,
            system_prompt=(
                "You are a single office-document and data-analysis agent. "
                "Use only the provided Attempt-scoped tools. Load a Skill before "
                "specialized work. Publish deliverables under output/. Available:\n"
                + "\n".join(summaries)
            ),
            toolkit=Toolkit(tools=self._tools()),
            react_config=ReActConfig(max_iters=24), middlewares=[],
            agent_config=config, workspace_dir=self.paths.root / "workspace",
            request_context={"channel": "office", "agent_id": "office"},
            effective_skills=[], governor=None,
        )
        if restored_state:
            agent.load_state_dict(restored_state, strict=True)
        text = ""
        async for event in agent.reply_stream(
            inputs=[Msg(
                name="user", role="user", content=[TextBlock(text=message)]
            )]
        ):
            for block in getattr(event, "content", []) or []:
                value = getattr(block, "text", None)
                if value:
                    text += value
                    if self.on_text:
                        await self.on_text(text)
        state = agent.state_dict()
        if not text:
            for message_item in reversed(state.get("state", {}).get("context", [])):
                if message_item.get("role") != "assistant":
                    continue
                text = "".join(
                    block.get("text", "")
                    for block in message_item.get("content", [])
                    if block.get("type") == "text"
                )
                if text:
                    break
        if self.on_text and text:
            await self.on_text(text)
        return {"conversation": state, "text": text}
