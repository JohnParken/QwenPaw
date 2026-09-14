"""Load and initialize the user's default provider configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_TOP_LEVEL_KEYS = {"version", "enabled", "providers", "active_model"}
_PROVIDER_KEYS = {"id", "name", "base_url", "models"}
_MODEL_KEYS = {"id", "name"}
_ACTIVE_MODEL_KEYS = {"provider_id", "model"}
_BUNDLED_CONFIG = Path(__file__).with_name("data") / "tl-provider.json"


def _invalid(message: str) -> ValueError:
    return ValueError(f"Invalid default provider configuration: {message}")


def _validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise _invalid("root must be an object")
    if set(data) != _TOP_LEVEL_KEYS:
        raise _invalid("unknown or missing top-level keys")
    if isinstance(data["version"], bool) or data["version"] != 1:
        raise _invalid("version must be 1")
    if not isinstance(data["enabled"], bool):
        raise _invalid("enabled must be a boolean")
    providers = data["providers"]
    if not isinstance(providers, list):
        raise _invalid("providers must be a list")
    seen: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict) or not _PROVIDER_KEYS <= set(provider):
            raise _invalid("each provider must include id, name, base_url, and models")
        provider_id = provider["id"]
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise _invalid("provider ids must be non-empty strings")
        identity = provider_id.casefold()
        if identity in seen:
            raise _invalid("provider ids must be unique")
        seen.add(identity)
        if not isinstance(provider["name"], str) or not isinstance(provider["base_url"], str):
            raise _invalid("provider name and base_url must be strings")
        models = provider["models"]
        if not isinstance(models, list):
            raise _invalid("provider models must be a list")
        for model in models:
            if not isinstance(model, dict) or not _MODEL_KEYS <= set(model):
                raise _invalid("each model must include id and name")
            if not isinstance(model["id"], str) or not model["id"]:
                raise _invalid("model ids must be non-empty strings")
            if not isinstance(model["name"], str):
                raise _invalid("model names must be strings")
    active = data["active_model"]
    if not isinstance(active, dict) or set(active) != _ACTIVE_MODEL_KEYS:
        raise _invalid("active_model must contain provider_id and model")
    if not all(isinstance(active[key], str) and active[key] for key in _ACTIVE_MODEL_KEYS):
        raise _invalid("active_model values must be non-empty strings")
    if not data["enabled"]:
        return data
    for provider in providers:
        env_name = provider.pop("api_key_env", None)
        if env_name is not None:
            if not isinstance(env_name, str) or not env_name:
                raise _invalid("api_key_env must be a non-empty string")
            secret = os.environ.get(env_name)
            if secret is None:
                raise _invalid("configured api_key_env is unset")
            provider["api_key"] = secret
    return data


def load_default_provider_config(path: Path, *, create: bool = True) -> dict[str, Any]:
    """Read a validated config, optionally creating the bundled default."""
    path = Path(path)
    if not path.exists() and create:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _BUNDLED_CONFIG.read_bytes()
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp_name, path)
            except FileExistsError:
                pass
            else:
                os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    with path.open("r", encoding="utf-8") as handle:
        return _validate(json.load(handle))
