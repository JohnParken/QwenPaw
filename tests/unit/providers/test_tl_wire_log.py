from __future__ import annotations

import json
import logging

from qwenpaw.providers.tl_wire_log import log_wire


def _record(caplog):
    return json.loads(caplog.records[-1].message.removeprefix("TL_WIRE "))


def test_redacts_secrets_and_keeps_prompt(caplog):
    caplog.set_level(logging.DEBUG, logger="qwenpaw.providers.tl_wire")
    log_wire(
        event="request",
        payload={
            "prompt": "Explain the moon phases",
            "authorization": "Bearer hidden-header",
            "nested": {"api_key": "hidden-key"},
            "url": "https://user:url-password@example.test/path?q=hidden-query",
            "text": "Bearer hidden-bearer password=hidden-password",
        },
        secrets=("literal-secret",),
    )
    record = _record(caplog)
    rendered = json.dumps(record, ensure_ascii=False)
    assert "Explain the moon phases" in rendered
    for secret in ("hidden-header", "hidden-key", "url-password", "hidden-query", "hidden-bearer", "hidden-password"):
        assert secret not in rendered


def test_bounds_payload_and_reports_original_length(caplog):
    caplog.set_level(logging.DEBUG, logger="qwenpaw.providers.tl_wire")
    log_wire(event="response", payload={"text": "x" * 40_000})
    record = _record(caplog)
    assert record["truncated"] is True
    assert record["original_chars"] > 32_768
    assert len(record["payload"]) == 32_769


def test_debug_disabled_does_not_serialize(caplog, monkeypatch):
    logger = logging.getLogger("qwenpaw.providers.tl_wire")
    logger.setLevel(logging.INFO)

    class Explodes:
        def __iter__(self):
            raise AssertionError("payload serialized while DEBUG disabled")

    log_wire(event="ignored", payload=Explodes())
    assert not caplog.records
