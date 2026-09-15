from __future__ import annotations

from dataclasses import replace
from http.server import HTTPServer
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from qwenpaw.office.api import create_app
from qwenpaw.office.bundle import SKILL_NAMES, load_bundle
from qwenpaw.office.config import OfficeSettings
from qwenpaw.office.runtime import OfficeRuntimeHost
from qwenpaw.office.service import OfficeService
from qwenpaw.office.storage import MemoryObjectStore, MemoryRepository
from tests.integration.helpers import MockLLMHandler


_CASES: dict[str, dict[str, Any]] = {
    "writing": {
        "filename": "result.md",
        "mime_type": "text/markdown",
        "tool_name": "write_text",
        "tool_args": {
            "path": "output/result.md",
            "content": "# Office result\n\nThe writing artifact is ready.",
        },
    },
    "docx": {
        "filename": "result.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "tool_name": "create_docx",
        "tool_args": {
            "path": "output/result.docx",
            "title": "Office document",
            "paragraphs": ["The document artifact is ready."],
        },
    },
    "xlsx": {
        "filename": "result.xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "tool_name": "create_xlsx",
        "tool_args": {
            "path": "output/result.xlsx",
            "columns": ["region", "revenue"],
            "rows": [["East", 15], ["West", 20]],
            "sheet_name": "Summary",
        },
    },
    "pptx": {
        "filename": "result.pptx",
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "tool_name": "create_pptx",
        "tool_args": {
            "path": "output/result.pptx",
            "title": "Office presentation",
            "slides": [{"title": "Results", "bullets": ["Revenue is ready."]}],
        },
    },
    "pdf": {
        "filename": "result.pdf",
        "mime_type": "application/pdf",
        "tool_name": "create_pdf",
        "tool_args": {
            "path": "output/result.pdf",
            "title": "Office PDF",
            "paragraphs": ["The PDF artifact is ready."],
        },
    },
    "bi-analysis": {
        "filename": "analysis.md",
        "mime_type": "text/markdown",
        "tool_name": "write_text",
        "tool_args": {
            "path": "output/analysis.md",
            "content": "# BI analysis\n\nEast: 15; West: 20.",
        },
    },
}


def _content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict)
        )
    return ""


class _OfficeModelHandler(MockLLMHandler):
    """Drive a real OfficeRuntimeHost through create, publish, and final."""

    def _stream_office_tool_call(
        self,
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        chunks = (
            {
                "id": f"chatcmpl-{call_id}",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "office-e2e-model",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": ""},
                        }],
                    },
                    "finish_reason": None,
                }],
            },
            {
                "id": f"chatcmpl-{call_id}",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "office-e2e-model",
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": [{
                        "index": 0,
                        "function": {
                            "arguments": json.dumps(
                                arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }]},
                    "finish_reason": None,
                }],
            },
            {
                "id": f"chatcmpl-{call_id}",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "office-e2e-model",
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "tool_calls",
                }],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 10,
                    "total_tokens": 25,
                },
            },
        )
        for chunk in chunks:
            self.wfile.write(
                f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode(),
            )
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _stream_completion(self):  # noqa: ANN001
        body = self._read_body()
        server = self.server
        server.office_requests.append(body)
        case = server.office_case
        if not isinstance(case, dict):
            self._respond_error(500)
            return

        tool_results = [
            message
            for message in body.get("messages", [])
            if message.get("role") == "tool"
        ]
        stage = len(tool_results)
        if stage == 0:
            self._stream_office_tool_call(
                call_id="call_office_create",
                name=case["tool_name"],
                arguments=case["tool_args"],
            )
        elif stage == 1:
            self._stream_office_tool_call(
                call_id="call_office_publish",
                name="publish_artifact",
                arguments={
                    "path": case["tool_args"]["path"],
                    "title": f"{case['filename']} artifact",
                    "mime_type": case["mime_type"],
                },
            )
        elif stage == 2:
            self._stream_text("The office artifact is ready.")
        else:
            self._respond_error(409)


@pytest.fixture
def office_model_server() -> Iterator[tuple[HTTPServer, str]]:
    server = HTTPServer(("127.0.0.1", 0), _OfficeModelHandler)
    server.office_case = None
    server.office_requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.e2e
@pytest.mark.p0
@pytest.mark.parametrize("skill_name", tuple(_CASES))
def test_office_model_creates_verifies_and_downloads_artifact(
    tmp_path: Path,
    office_model_server: tuple[HTTPServer, str],
    skill_name: str,
) -> None:
    server, base_url = office_model_server
    case = _CASES[skill_name]
    server.office_case = case
    server.office_requests.clear()

    bundle = load_bundle()
    assert bundle.ready
    assert tuple(bundle) == SKILL_NAMES
    assert all(bundle[name].directory is not None for name in SKILL_NAMES)

    settings = OfficeSettings(
        work_root=tmp_path,
        openai_api_key="local-office-e2e",
        openai_base_url=base_url,
        openai_model="office-e2e-model",
    )
    repository = MemoryRepository()
    service = OfficeService(
        settings,
        bundle,
        repository=repository,
        object_store=MemoryObjectStore(),
    )
    assert isinstance(service.runtime_host, OfficeRuntimeHost)

    user_id = f"user-{skill_name}"
    headers = {
        "X-Tenant-Id": "tenant-office-e2e",
        "X-User-Id": user_id,
        "X-Request-Id": "session-create",
    }
    with TestClient(create_app(settings, service=service)) as client:
        session_response = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"metadata": {"skill": skill_name}},
        )
        assert session_response.status_code == 201, session_response.text
        session_id = session_response.json()["session_id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={**headers, "X-Request-Id": f"turn-{skill_name}"},
            json={"content": f"Create the {skill_name} artifact.", "provider": "openai"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "completed"
        assert result["message"]
        assert len(result["artifacts"]) == 1

        artifact = result["artifacts"][0]
        assert artifact["filename"] == case["filename"]
        assert artifact["content_type"] == case["mime_type"]
        assert artifact["size"] > 0
        assert artifact["verified"] is True
        verification = artifact["verification"]
        assert verification["level"] in {"structural_verified", "content_checked"}
        assert verification["errors"] == []

        download = client.get(
            f"/api/v1/artifacts/{artifact['artifact_id']}",
            headers={**headers, "X-Request-Id": f"download-{skill_name}"},
        )
        assert download.status_code == 200, download.text
        assert download.content
        assert download.headers["content-type"].startswith(case["mime_type"])
        assert case["filename"] in download.headers["content-disposition"]

        session = client.get(
            f"/api/v1/sessions/{session_id}",
            headers={**headers, "X-Request-Id": f"session-read-{skill_name}"},
        )
        assert session.status_code == 200, session.text
        session_data = session.json().get("data") or session.json()
        runtime_state = session_data.get("runtime_state")
        assert isinstance(runtime_state, dict) and runtime_state
        assert isinstance(runtime_state.get("state"), dict)

    requests = server.office_requests
    assert len(requests) == 3
    exposed_tools = {
        tool.get("function", {}).get("name")
        for tool in requests[0].get("tools", [])
    }
    assert "run_skill_script" in exposed_tools
    assert "approved_skill_script" not in exposed_tools
    system_text = "\n".join(
        _content_text(message)
        for message in requests[0].get("messages", [])
        if message.get("role") == "system"
    )
    for loaded_skill in SKILL_NAMES:
        assert f"<name>{loaded_skill}</name>" in system_text

    assistant_calls = []
    for request in requests:
        calls = []
        for message in request.get("messages", []):
            if message.get("role") != "assistant":
                continue
            calls.extend(
                call.get("function", {}).get("name")
                for call in message.get("tool_calls", [])
            )
        assistant_calls.append(calls)
    assert assistant_calls == [[], [case["tool_name"]], [case["tool_name"], "publish_artifact"]]
    assert [
        sum(message.get("role") == "tool" for message in request.get("messages", []))
        for request in requests
    ] == [0, 1, 2]


def _real_model_smoke_enabled() -> bool:
    return os.environ.get("QWENPAW_OFFICE_REAL_MODEL_SMOKE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@pytest.mark.e2e
@pytest.mark.manual_real
@pytest.mark.skipif(
    not _real_model_smoke_enabled(),
    reason="set QWENPAW_OFFICE_REAL_MODEL_SMOKE=1 to use paid credentials",
)
@pytest.mark.parametrize("skill_name", tuple(_CASES))
def test_office_real_model_artifact_smoke(
    tmp_path: Path,
    skill_name: str,
) -> None:
    settings = replace(
        OfficeSettings.from_env(),
        production=False,
        object_store_backend="memory",
        work_root=tmp_path,
        default_provider="openai",
        allowed_providers=("openai",),
    )
    assert settings.openai_api_key
    service = OfficeService(
        settings,
        load_bundle(),
        repository=MemoryRepository(),
        object_store=MemoryObjectStore(),
    )
    assert isinstance(service.runtime_host, OfficeRuntimeHost)
    headers = {
        "X-Tenant-Id": "tenant-office-real",
        "X-User-Id": f"user-office-real-{skill_name}",
        "X-Request-Id": "real-session",
    }
    case = _CASES[skill_name]
    with TestClient(create_app(settings, service=service)) as client:
        session = client.post("/api/v1/sessions", headers=headers, json={"metadata": {}})
        assert session.status_code == 201, session.text
        response = client.post(
            f"/api/v1/sessions/{session.json()['session_id']}/messages",
            headers={**headers, "X-Request-Id": f"real-turn-{skill_name}"},
            json={
                "content": (
                    f"Load the {skill_name} skill, then call {case['tool_name']} "
                    "with exactly these JSON arguments: "
                    f"{json.dumps(case['tool_args'], ensure_ascii=False)}. "
                    f"Publish {case['tool_args']['path']} with MIME "
                    f"{case['mime_type']}, then confirm completion."
                ),
                "provider": "openai",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "completed", result
        assert len(result["artifacts"]) == 1, result
        artifact = result["artifacts"][0]
        assert artifact["filename"] == case["filename"]
        assert artifact["content_type"] == case["mime_type"]
        assert artifact["verified"] is True
