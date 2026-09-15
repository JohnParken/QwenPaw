"""Startup defaults integrate with persisted provider/model choices."""

import json

import pytest

from qwenpaw.config.config import ModelSlotConfig
from qwenpaw.providers import provider_manager as module
from qwenpaw.providers.provider_manager import ProviderManager
from qwenpaw.providers.tl_provider import TLProvider


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "WORKING_DIR", tmp_path)
    monkeypatch.setattr(module, "SECRET_DIR", tmp_path / "secrets")
    monkeypatch.delenv("QWENPAW_PROVIDER_CONFIG", raising=False)
    return tmp_path


def test_fresh_manager_selects_tl_and_creates_editable_file(storage):
    manager = ProviderManager()
    assert isinstance(manager.get_provider("tlproxy"), TLProvider)
    assert manager.get_active_model().model == "deepseek-v4-flash"
    assert (storage / "tl-provider.json").is_file()
    assert manager.get_provider("tlproxy").models[0].max_input_length == 1048576


def test_saved_provider_and_selection_win(storage):
    manager = ProviderManager()
    provider = manager.get_provider("tlproxy").model_copy(deep=True)
    provider.base_url = "http://127.0.0.1:9999"
    manager._save_provider(provider)
    slot = ModelSlotConfig(provider_id="openai", model="saved-label")
    manager.save_active_model(slot)
    restarted = ProviderManager()
    assert restarted.get_provider("tlproxy").base_url.endswith(":9999")
    assert restarted.get_active_model() == slot


def test_file_can_select_other_backend_and_generation_options(storage):
    ProviderManager()
    path = storage / "tl-provider.json"
    data = json.loads(path.read_text())
    data["providers"] = [{
        "id": "my-openai", "name": "My OpenAI", "is_custom": True,
        "chat_model": "OpenAIChatModel", "base_url": "http://127.0.0.1:9999/v1",
        "models": [{"id": "other-model", "name": "Other model",
                    "max_input_length": 65536}],
        "generate_kwargs": {"temperature": 0.2, "max_tokens": 2048},
    }]
    data["active_model"] = {"provider_id": "my-openai", "model": "other-model"}
    path.write_text(json.dumps(data))
    manager = ProviderManager()
    assert manager.active_model.model == "other-model"
    assert manager.get_provider("my-openai").generate_kwargs["temperature"] == 0.2


def test_disabled_file_does_not_select_default(storage):
    ProviderManager()
    path = storage / "tl-provider.json"
    data = json.loads(path.read_text())
    data["enabled"] = False
    path.write_text(json.dumps(data))
    manager = ProviderManager()
    assert manager.get_provider("tlproxy") is None
    assert manager.active_model is None
