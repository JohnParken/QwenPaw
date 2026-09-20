import json
import socket

from click.testing import CliRunner

from qwenpaw.cli.main import cli


def _env(monkeypatch, definition_path):
    values = {
        "DATABASE_URL": "mysql://user:secret@db.example/app",
        "SERVICE_TOKEN": "s" * 32,
        "INTERNAL_TOKEN": "i" * 32,
        "DEFINITION_PATH": str(definition_path),
    }
    for key, value in values.items():
        monkeypatch.setenv(f"QWENPAW_SERVER_{key}", value)


def _definition(path, **overrides):
    value = {
        "version": "v1",
        "system_prompt": "You are helpful.",
        "model": "demo-model",
        "tools": [
            {
                "name": "shell",
                "description": "run a command",
                "input_schema": {"type": "object"},
                "execution": "sandbox",
            },
            {
                "name": "search",
                "description": "search service",
                "input_schema": {"type": "object"},
                "execution": "service",
            },
        ],
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_check_validates_config_offline_and_reports_safe_summary(tmp_path, monkeypatch):
    definition = tmp_path / "definition.json"
    _definition(definition)
    _env(monkeypatch, definition)

    def no_network(*args, **kwargs):
        raise AssertionError("serve check must not open a network socket")

    monkeypatch.setattr(socket, "create_connection", no_network)

    result = CliRunner().invoke(cli, ["serve", "check", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["connectivity_checked"] is False
    assert payload["sandbox_tools"] == 1
    assert payload["service_tools"] == 1


def test_check_rejects_missing_environment_without_secret_values(tmp_path, monkeypatch):
    definition = tmp_path / "definition.json"
    _definition(definition)
    monkeypatch.setenv("QWENPAW_SERVER_SERVICE_TOKEN", "secret-service-token")

    result = CliRunner().invoke(cli, ["serve", "check", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {"ok": False, "stage": "environment"}
    assert "secret-service-token" not in result.output


def test_check_rejects_memory_and_invalid_database_urls(tmp_path, monkeypatch):
    definition = tmp_path / "definition.json"
    _definition(definition)
    _env(monkeypatch, definition)
    runner = CliRunner()

    monkeypatch.setenv("QWENPAW_SERVER_DATABASE_URL", "memory://")
    memory = runner.invoke(cli, ["serve", "check", "--json"])
    assert memory.exit_code == 1
    assert json.loads(memory.output)["stage"] == "database_url"

    monkeypatch.setenv("QWENPAW_SERVER_DATABASE_URL", "mysql://user:secret@db/app?bad=x")
    invalid = runner.invoke(cli, ["serve", "check"])
    assert invalid.exit_code == 1
    assert "secret" not in invalid.output
    assert "Invalid database_url" in invalid.output


def test_check_rejects_invalid_definition_without_rendering_contents(tmp_path, monkeypatch):
    definition = tmp_path / "definition.json"
    definition.write_text('{"version": "bad", "unexpected_secret": "dont-print"}')
    _env(monkeypatch, definition)

    result = CliRunner().invoke(cli, ["serve", "check", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {"ok": False, "stage": "assistant_definition"}
    assert "dont-print" not in result.output
