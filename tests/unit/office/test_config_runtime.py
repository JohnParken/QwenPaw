from pathlib import Path

import pytest

from qwenpaw.office.config import OfficeSettings
from qwenpaw.office.bundle import SKILL_NAMES, load_bundle
from qwenpaw.office.models import validate_identifier
from qwenpaw.office.runtime import OfficeAgentBuilder, OfficeAppServices, OfficeRuntimeHost
from qwenpaw.runtime.builder import AgentBuilder
from qwenpaw.runtime.runtime import Runtime


def test_identifier_contract() -> None:
    assert validate_identifier("tenant-1", "tenant") == "tenant-1"
    for value in ("", "../tenant", "has space", "x" * 129):
        with pytest.raises(ValueError):
            validate_identifier(value)


def test_office_defaults_to_tlprovider_and_allows_provider_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QWENPAW_OFFICE_DEFAULT_PROVIDER", raising=False)
    assert OfficeSettings().default_provider == "tlprovider"

    monkeypatch.setenv("QWENPAW_OFFICE_DEFAULT_PROVIDER", "openai")
    assert OfficeSettings.from_env().default_provider == "openai"


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
