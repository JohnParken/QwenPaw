from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qwenpaw.office.api import create_app
from qwenpaw.office.bundle import SKILL_NAMES, load_bundle
from qwenpaw.office.config import OfficeSettings
from qwenpaw.office.runtime import RuntimeSignal
from qwenpaw.office.service import OfficeService
from qwenpaw.office.storage import MemoryObjectStore, MemoryRepository
from qwenpaw.office.tools import (
    SkillExecutionContext,
    aggregate_table,
    create_chart,
    create_docx,
    create_pdf,
    create_pptx,
    create_xlsx,
)


@pytest.mark.e2e
def test_six_skills_create_verify_and_publish_real_files(tmp_path: Path) -> None:
    bundle = load_bundle()
    assert bundle.ready
    assert tuple(bundle) == SKILL_NAMES
    context = SkillExecutionContext(root=tmp_path / "attempt")

    context.write_text("output/brief.md", "# Quarterly brief\n\nRevenue increased.")
    create_docx(context, "output/report.docx", "Quarterly report", ["Revenue increased."])
    create_xlsx(context, "output/data.xlsx", ["region", "revenue"], [["East", 10], ["West", 20]])
    create_pptx(context, "output/deck.pptx", "Quarterly review", [{"title": "Results", "bullets": ["Revenue increased", "Costs stable"]}])
    create_pdf(context, "output/summary.pdf", "Quarterly summary", ["Revenue increased."])

    (context.input_dir / "source.csv").write_text(
        "region,revenue\nEast,10\nWest,20\nEast,5\n",
        encoding="utf-8",
    )
    analysis = aggregate_table(context, "input/source.csv", "region", "revenue")
    context.write_text("output/analysis.json", json.dumps(analysis, ensure_ascii=False))
    create_chart(
        context,
        "output/revenue.png",
        list(analysis),
        list(analysis.values()),
        "Revenue by region",
    )

    import pandas as pd

    pd.DataFrame(
        {"region": ["East", "West", "East"], "revenue": [10, 20, 5]},
    ).to_parquet(context.input_dir / "source.parquet", index=False)
    parquet_analysis = aggregate_table(
        context,
        "input/source.parquet",
        "region",
        "revenue",
    )

    artifacts = [
        context.publish("output/brief.md"),
        context.publish("output/report.docx"),
        context.publish("output/data.xlsx"),
        context.publish("output/deck.pptx"),
        context.publish("output/summary.pdf"),
        context.publish("output/analysis.json"),
        context.publish("output/revenue.png"),
    ]
    assert len(artifacts) == 7
    assert all(item.verification and item.verification.ok for item in artifacts)
    assert analysis == {"East": 15.0, "West": 20.0}
    assert parquet_analysis == analysis
    assert all(item.path.read_bytes() for item in artifacts)


class _PublishingRuntime:
    def __init__(self) -> None:
        self.service: OfficeService | None = None

    async def stream(self, **kwargs):
        assert self.service is not None
        request_context = {
            **kwargs,
            "work_dir": str(kwargs["work_dir"]),
        }
        ctx = SimpleNamespace(
            request=SimpleNamespace(request_context=request_context),
        )
        tools = {tool.__name__: tool for tool in self.service._build_tools(ctx)}
        tools["write_text"]("output/result.md", "# Verified result\n\nReady for download.")
        tools["publish_artifact"]("output/result.md", "Verified result", "text/markdown")
        yield RuntimeSignal("message.delta", {"delta": "done"})
        yield RuntimeSignal("runtime.response", {"usage": {"input_tokens": 1}})


@pytest.mark.e2e
def test_trusted_bff_can_publish_and_download_artifact(tmp_path: Path) -> None:
    settings = OfficeSettings(work_root=tmp_path, openai_api_key="test")
    runtime = _PublishingRuntime()
    service = OfficeService(
        settings,
        load_bundle(),
        repository=MemoryRepository(),
        object_store=MemoryObjectStore(),
        runtime_host=runtime,
    )
    runtime.service = service
    headers = {
        "X-Tenant-Id": "tenant-e2e",
        "X-User-Id": "user-e2e",
        "X-Request-Id": "session-create",
    }
    with TestClient(create_app(settings, service=service)) as client:
        session_response = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"metadata": {}},
        )
        assert session_response.status_code == 201
        session_id = session_response.json()["session_id"]
        message_response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={**headers, "X-Request-Id": "turn-publish"},
            json={"content": "Create a result"},
        )
        assert message_response.status_code == 200
        artifact = message_response.json()["artifacts"][0]
        download = client.get(
            f"/api/v1/artifacts/{artifact['artifact_id']}",
            headers={**headers, "X-Request-Id": "artifact-download"},
        )
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("text/markdown")
        assert download.content == b"# Verified result\n\nReady for download."
