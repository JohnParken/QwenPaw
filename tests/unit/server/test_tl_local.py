import json
import subprocess
import sys

import pytest

from scripts.server_tl_local import FailClosedTools, load_config


def definition(protocol="tl"):
    return {
        "version": "test-v1",
        "system_prompt": "test",
        "model": "test-model",
        "model_protocol": protocol,
        "base_url": "http://127.0.0.1:9000",
    }


def test_load_config_requires_tl_definition(tmp_path):
    path = tmp_path / "definition.json"
    path.write_text(json.dumps(definition("openai")), encoding="utf-8")
    with pytest.raises(ValueError, match="model_protocol=tl"):
        load_config(path)


def test_load_config_rejects_sandbox_tools(tmp_path):
    item = definition()
    item["tools"] = [
        {
            "name": "shell",
            "description": "shell",
            "execution": "sandbox",
            "input_schema": {},
        }
    ]
    path = tmp_path / "definition.json"
    path.write_text(json.dumps(item), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot run sandbox tools"):
        load_config(path)


@pytest.mark.asyncio
async def test_tools_fail_closed_without_controller():
    with pytest.raises(RuntimeError, match="production serve"):
        await FailClosedTools(None).invoke(None, "id", "shell", {}, 1)


def test_help_exposes_definition_and_port():
    result = subprocess.run(
        [sys.executable, "scripts/server_tl_local.py", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--definition" in result.stdout
    assert "--port" in result.stdout
