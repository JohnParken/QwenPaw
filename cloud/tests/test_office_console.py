import importlib.util
from pathlib import Path
import sys

from fastapi.testclient import TestClient


def _server_module():
    path = Path(__file__).parents[2] / "test-tools/office-agent-console/server.py"
    spec = importlib.util.spec_from_file_location("office_agent_console_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_console_upload_multiturn_and_download(tmp_path, monkeypatch):
    module = _server_module()
    monkeypatch.setenv("OFFICE_MODEL_PROVIDER", "tl")
    monkeypatch.setenv("OFFICE_MODEL_ID", "fake")
    monkeypatch.setenv("OFFICE_MODEL_BASE_URL", "http://model.invalid")
    monkeypatch.setenv("OFFICE_TEST_ROOT", str(tmp_path / "attempts"))

    class FakeBridge:
        def __init__(self, paths, _config, shell_mode):
            self.paths = paths
            self.shell_mode = shell_mode

        async def run(self, message, restored_state=None):
            turn = (restored_state or {}).get("turn", 0) + 1
            target = self.paths.root / "output" / f"turn-{turn}.txt"
            target.write_text(message, encoding="utf-8")
            return {"conversation": {"turn": turn}, "text": f"done {turn}"}

    monkeypatch.setattr(module, "OfficeAgentBridge", FakeBridge)
    with TestClient(module.create_app()) as client:
        created = client.post(
            "/api/sessions",
            files={"files": ("source.docx", b"input", "application/octet-stream")},
        )
        assert created.status_code == 200
        session_id = created.json()["id"]
        for turn in (1, 2):
            response = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"message": f"request {turn}"},
            )
            assert response.status_code == 200
            assert response.json()["message"] == f"done {turn}"
        artifact = client.get(
            f"/api/sessions/{session_id}/artifacts/turn-2.txt"
        )
        assert artifact.status_code == 200
        assert artifact.content == b"request 2"


def test_console_requires_explicit_model_configuration(monkeypatch):
    module = _server_module()
    for name in (
        "OFFICE_MODEL_PROVIDER",
        "OFFICE_MODEL_ID",
        "OFFICE_MODEL_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    try:
        module.create_app()
    except RuntimeError as exc:
        assert "OFFICE_MODEL_PROVIDER" in str(exc)
    else:
        raise AssertionError("console started without a model configuration")
