# -*- coding: utf-8 -*-
"""TL configuration, persistence and runtime integration contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from qwenpaw.providers.provider import ModelInfo, ProviderInfo
from qwenpaw.providers.provider_manager import ProviderManager
from qwenpaw.providers.tl_provider import TLProvider


def provider(**kwargs):
    return TLProvider(
        **dict(
            id="tl-local",
            name="TL local",
            is_custom=True,
            base_url="http://localhost:8089/company",
            models=[ModelInfo(id="internal-default", name="Route")],
            **kwargs,
        ),
    )


async def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "qwenpaw.providers.provider_manager.SECRET_DIR", tmp_path
    )
    manager = ProviderManager()
    await manager.add_custom_provider(
        ProviderInfo(
            id="tl-local",
            name="TL local",
            chat_model="TLChatModel",
            base_url="http://localhost:8089/prefix",
            tl_config={
                "app_id": "test",
                "system_prompt_variable_name": "instructions",
            },
            models=[ModelInfo(id="route", name="Route")],
        )
    )
    info = await manager.get_provider_info("tl-local")
    assert info.tl_config.app_id == "test"
    assert info.support_connection_check
    assert not info.require_api_key
    assert not info.support_model_discovery
    await manager.update_provider_async(
        "tl-local", {"tl_config": {"tr_code": "new"}}
    )
    restored = ProviderManager().get_provider("tl-local")
    assert isinstance(restored, TLProvider)
    assert restored.tl_config.app_id == "test"
    assert restored.tl_config.tr_code == "new"
    assert restored.tl_config.system_prompt_variable_name == "instructions"
    assert restored.models[0].supports_image is False


@pytest.mark.parametrize(
    "patch",
    [
        {"generate_kwargs": {"temperature": 1}},
        {"chat_model": "OpenAIChatModel"},
        {"tl_config": {"tool_calling_mode": "native"}},
        {"tl_config": {"timeout_seconds": 0}},
        {"extra_models": [{"id": "another", "name": "Another"}]},
    ],
)
def test_invalid_update_is_atomic(patch):
    original = provider()
    before = original.model_dump()
    with pytest.raises(ValueError):
        original.update_config(patch)
    assert original.model_dump() == before


async def test_no_second_route_or_thinking_overrides():
    original = provider()
    with pytest.raises(ValueError):
        await original.add_model(ModelInfo(id="other", name="Other"))
    assert len(original.configured_models()) == 1
    with pytest.raises(ValueError):
        original.update_model_config(
            "internal-default", {"thinking_enabled": True}
        )
    assert original.get_context_size("internal-default") == 32768
    assert (await original.fetch_models())[0].id == "internal-default"


async def test_unsaved_test_config(monkeypatch):
    from qwenpaw.app.routers.providers import (
        TestProviderRequest,
        test_provider as run_test,
    )

    original = provider()
    seen = []

    async def check(self, timeout=5):
        seen.append(self.tl_config)
        return True, "Session initialization successful; chat was not tested."

    monkeypatch.setattr(TLProvider, "check_connection", check)
    result = await run_test(
        manager=SimpleNamespace(get_provider=lambda _: original),
        provider_id=original.id,
        body=TestProviderRequest(
            tl_config={
                "app_id": "unsaved",
                "system_prompt_variable_name": "custom",
            }
        ),
    )
    assert result.verification == "provider_only"
    assert "chat was not tested" in result.message
    assert seen[0].app_id == "unsaved"
    assert original.tl_config.app_id != "unsaved"
    with pytest.raises(HTTPException):
        await run_test(
            manager=SimpleNamespace(get_provider=lambda _: original),
            provider_id=original.id,
            body=TestProviderRequest(chat_model="OpenAIChatModel"),
        )


async def test_connection_check_uses_only_init(monkeypatch):
    transport = SimpleNamespace(init_session=AsyncMock(return_value="session"))
    monkeypatch.setattr(TLProvider, "_transport", lambda _: transport)
    ok, message = await provider().check_connection()
    assert ok and "chat was not tested" in message
    transport.init_session.assert_awaited_once()


def test_gateway_404_is_not_a_missing_model():
    from qwenpaw.providers.provider_model_availability import (
        classify_model_check,
    )

    result = classify_model_check(
        False, "Gateway path failed", http_status=404, error_kind="tl_error"
    )
    assert result.status == "incompatible_api"
    assert not result.retryable
