import json
from pathlib import Path

import pytest

from qwenpaw.providers.default_provider_config import (
    load_default_provider_config,
)


def test_missing_config_is_created_and_existing_is_preserved(tmp_path: Path):
    path = tmp_path / "nested" / "providers.json"
    created = load_default_provider_config(path)
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text(json.dumps({"version": 1, "enabled": False}), encoding="utf-8")
    assert path.read_text(encoding="utf-8") == json.dumps({"version": 1, "enabled": False})


def test_disabled_config_is_returned(tmp_path: Path):
    path = tmp_path / "providers.json"
    data = {
        "version": 1,
        "enabled": False,
        "providers": [],
        "active_model": {"provider_id": "unused", "model": "unused"},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_default_provider_config(path)["enabled"] is False


def test_disabled_config_does_not_resolve_api_key_env(tmp_path: Path):
    path = tmp_path / "providers.json"
    data = load_default_provider_config(tmp_path / "seed.json")
    data["enabled"] = False
    data["providers"][0]["api_key_env"] = "UNSET_DEFAULT_PROVIDER_KEY"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "api_key_env" in load_default_provider_config(path)["providers"][0]


@pytest.mark.parametrize("mutator", [
    lambda d: d.update(extra=True),
    lambda d: d.update(enabled="yes"),
    lambda d: d.update(providers={}),
    lambda d: d["providers"].append({"id": "TLPROXY", "name": "x", "base_url": "", "models": []}),
])
def test_invalid_shapes_are_rejected(tmp_path: Path, mutator):
    path = tmp_path / "providers.json"
    data = load_default_provider_config(tmp_path / "seed.json")
    mutator(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_default_provider_config(path)


def test_api_key_env_is_resolved_without_persisting_secret(tmp_path, monkeypatch):
    path = tmp_path / "providers.json"
    data = load_default_provider_config(tmp_path / "seed.json")
    data["providers"][0]["api_key_env"] = "TEST_DEFAULT_PROVIDER_KEY"
    monkeypatch.setenv("TEST_DEFAULT_PROVIDER_KEY", "secret-value")
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_default_provider_config(path)
    assert loaded["providers"][0]["api_key"] == "secret-value"
    assert "api_key_env" not in loaded["providers"][0]
    assert "secret-value" not in path.read_text(encoding="utf-8")
