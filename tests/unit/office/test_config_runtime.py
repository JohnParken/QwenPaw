from pathlib import Path

import pytest

from qwenpaw.office.config import OfficeSettings
from qwenpaw.office.bundle import SKILL_NAMES, load_bundle
from qwenpaw.office.models import validate_identifier
from qwenpaw.office.runtime import (
    OfficeAgentBuilder,
    OfficeAppServices,
    OfficeRuntimeHost,
    OfficeWorkspace,
    _OfficeSessionAdapter,
    _tenant_id,
    _user_id,
)
from qwenpaw.office.storage import MemoryRepository
from qwenpaw.hooks.session.session_hook import SessionLoadHook, SessionSaveHook
from qwenpaw.runtime.builder import AgentBuilder
from qwenpaw.runtime.runtime import Runtime


def test_identifier_contract() -> None:
    assert validate_identifier("tenant-1", "tenant") == "tenant-1"
    for value in ("", "../tenant", "has space", "x" * 129):
        with pytest.raises(ValueError):
            validate_identifier(value)


def test_production_requires_postgres_and_s3(tmp_path: Path) -> None:
    settings = OfficeSettings(production=True, work_root=tmp_path, skill_bundle_path=tmp_path / "skills", openai_api_key="test")
    failures = settings.static_readiness()
    assert "production requires PostgreSQL" in failures
    assert "production requires the S3 object store" in failures


def test_office_host_uses_native_runtime_chain(tmp_path: Path) -> None:
    host = OfficeRuntimeHost(OfficeSettings(work_root=tmp_path, skill_bundle_path=tmp_path / "skills"), skill_names=(), tool_factory=lambda _ctx: ())
    assert isinstance(host.runtime, Runtime)
    assert host.runtime.builder_factory is OfficeAgentBuilder
    assert issubclass(OfficeAgentBuilder, AgentBuilder)


def test_office_workspace_registers_native_session_hooks(tmp_path: Path) -> None:
    hooks = OfficeWorkspace(tmp_path).plugins.hook_registry._by_phase
    registered = {hook.name for phase_hooks in hooks.values() for hook in phase_hooks}
    assert {SessionLoadHook.name, SessionSaveHook.name} <= registered


@pytest.mark.asyncio
async def test_session_adapter_is_tenant_and_user_scoped() -> None:
    repository = MemoryRepository()
    await repository.create_session("tenant-a", "user-a", "session-1", data={"runtime_state": {"value": "a"}})
    await repository.create_session("tenant-b", "user-a", "session-1", data={"runtime_state": {"value": "b"}})
    adapter = _OfficeSessionAdapter(repository)
    tenant_token = _tenant_id.set("tenant-a")
    user_token = _user_id.set("user-a")
    try:
        loaded = type("State", (), {"data": {}})()
        await adapter.load_session_state(session_id="session-1", user_id="user-a", agent=loaded)
        assert loaded.data == {"value": "a"}
        loaded.data = {"value": "saved"}
        await adapter.save_session_state(session_id="session-1", user_id="user-a", agent=loaded)
        record = await repository.get_session("tenant-a", "user-a", "session-1")
        assert record["data"]["runtime_state"] == {"value": "saved"}
        other = await repository.get_session("tenant-b", "user-a", "session-1")
        assert other["data"]["runtime_state"] == {"value": "b"}
    finally:
        _tenant_id.reset(tenant_token)
        _user_id.reset(user_token)


@pytest.mark.asyncio
async def test_native_toolkit_loads_all_six_locked_skills() -> None:
    bundle = load_bundle()
    settings = OfficeSettings(
        skill_bundle_path=bundle.root,
        openai_api_key="test",
    )
    builder = OfficeAgentBuilder(
        OfficeAppServices(settings, SKILL_NAMES, lambda _ctx: ()),
    )
    toolkit = await builder.build_toolkit(
        builder._profile("openai"),
        agent_id="office",
        request_context={},
        effective_skills=SKILL_NAMES,
        extra_tools=[],
        ctx=None,
        workspace_dir=str(bundle.root.parent),
    )
    assert tuple(await toolkit._get_available_skills()) == SKILL_NAMES
