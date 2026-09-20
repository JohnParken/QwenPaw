"""Safe, bounded DEBUG logging for TL wire payloads."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("qwenpaw.providers.tl_wire")

_MAX_PAYLOAD_CHARS = 32_768
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|set-cookie|api[-_]?key|access[-_]?token|"
    r"refresh[-_]?token|token|password|passwd|secret|credential|private[-_]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(\bbearer\s+)[^\s,;]+", re.IGNORECASE)
_JSON_SECRET = re.compile(
    r"([\"'](?:authorization|cookie|api[-_]?key|access[-_]?token|"
    r"refresh[-_]?token|token|password|passwd|secret|credential)[\"']\s*:\s*)"
    r"([\"'])(?:\\.|(?!\2).)*\2",
    re.IGNORECASE,
)
_KEY_VALUE_SECRET = re.compile(
    r"(\b(?:authorization|cookie|api[-_]?key|access[-_]?token|"
    r"refresh[-_]?token|token|password|passwd|secret|credential)\b\s*[=:]\s*)"
    r"([^\s,;&]+)",
    re.IGNORECASE,
)
_URL = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)


def _safe_string(value: str, secrets: tuple[str, ...]) -> str:
    value = _URL.sub(_redact_url, value)
    value = _BEARER.sub(r"\1" + _REDACTED, value)
    value = _JSON_SECRET.sub(_redact_json_secret, value)
    value = _KEY_VALUE_SECRET.sub(r"\1" + _REDACTED, value)
    for secret in secrets:
        if not secret:
            continue
        # Replace both the literal and its JSON-escaped representation.
        for candidate in {secret, json.dumps(secret, ensure_ascii=False)[1:-1]}:
            if candidate:
                value = value.replace(candidate, _REDACTED)
    return value


def _redact_url(match: re.Match[str]) -> str:
    url = match.group(0)
    parsed = urlsplit(url)
    # Keep only the host/port portion; omit credentials and fragments.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    path = parsed.path
    return (
        f"{parsed.scheme}://{netloc}{path}?{_REDACTED}"
        if parsed.query
        else f"{parsed.scheme}://{netloc}{path}"
    )


def _redact_json_secret(match: re.Match[str]) -> str:
    prefix, quote = match.group(1), match.group(2)
    return prefix + quote + _REDACTED + quote


def _redact(value: Any, secrets: tuple[str, ...], seen: set[int]) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_string(value, secrets)
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            text = bytes(value).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return _safe_string(text, secrets)
            return _redact(parsed, secrets, seen)
        except (TypeError, ValueError, RecursionError):
            return "[BINARY]"
    object_id = id(value)
    if object_id in seen:
        return "[CIRCULAR]"
    if isinstance(value, Mapping):
        seen.add(object_id)
        result = {}
        for key, item in value.items():
            key_text = key if isinstance(key, str) else "[NONSTRING_KEY]"
            result[key_text] = (
                _REDACTED
                if _SENSITIVE_KEY.search(key_text)
                else _redact(item, secrets, seen)
            )
        seen.remove(object_id)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(object_id)
        result = [_redact(item, secrets, seen) for item in value]
        seen.remove(object_id)
        return result
    return "[UNSERIALIZABLE]"


def model_debug_enabled() -> bool:
    """Operator opt-in: raw model prompts are private diagnostic data."""
    return os.environ.get("QWENPAW_MODEL_DEBUG", "").lower() in {"1", "true", "yes"}


def log_wire(
    *,
    event: str,
    payload: Any,
    secrets: tuple[str, ...] = (),
    level: str = "DEBUG",
    **context: Any,
) -> None:
    """Emit bounded diagnostics; errors remain visible with DEBUG disabled."""
    from .tl_preview import current_preview_scope

    scope = current_preview_scope()
    capture = bool(
        model_debug_enabled() and scope and scope.model_debug and not scope.closed
    )
    severity = logging.ERROR if level == "ERROR" or event == "error" else logging.DEBUG
    if not capture and not logger.isEnabledFor(severity):
        return
    record = {"event": event, "payload": "[UNSERIALIZABLE]"}
    try:
        safe_secrets = tuple(secret for secret in secrets if isinstance(secret, str))
        record = {"event": _safe_string(event, safe_secrets)}
        record.update(
            {key: _redact(value, safe_secrets, set()) for key, value in context.items()}
        )
        safe_payload = _redact(payload, safe_secrets, set())
        try:
            payload_text = json.dumps(
                safe_payload, ensure_ascii=False, separators=(",", ":")
            )
        except (TypeError, ValueError, RecursionError):
            safe_payload = "[UNSERIALIZABLE]"
            payload_text = json.dumps(safe_payload)
        truncated = len(payload_text) > _MAX_PAYLOAD_CHARS
        if truncated:
            safe_payload = payload_text[:_MAX_PAYLOAD_CHARS] + "…"
        record["payload"] = safe_payload
        record["truncated"] = truncated
        record["original_chars"] = len(payload_text)
        record.update(
            type="model_log",
            object="diagnostic",
            id=uuid4().hex,
            level=logging.getLevelName(severity),
            timestamp=datetime.now(timezone.utc).isoformat(),
            run_id=scope.run_id if scope else "",
            invocation_id=scope.invocation_id if scope else "",
        )
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        record = {
            "type": "model_log",
            "object": "diagnostic",
            "id": uuid4().hex,
            "event": "diagnostic_serialization_error",
            "payload": "[UNSERIALIZABLE]",
            "level": logging.getLevelName(severity),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": scope.run_id if scope else "",
            "invocation_id": scope.invocation_id if scope else "",
        }
        line = json.dumps(record)
    try:
        logger.log(severity, "TL_WIRE %s", line)
        if capture and scope and scope.diagnostic_count < 128:
            # SSE wire records are sampled per HTTP request; complete model
            # responses are emitted separately and must not drown in deltas.
            if event == "sse_event":
                key = str(context.get("request_id", ""))
                if (
                    key not in scope.diagnostic_samples
                    and len(scope.diagnostic_samples) >= 128
                ):
                    return
                sample = scope.diagnostic_samples.get(key, 0) + 1
                scope.diagnostic_samples[key] = sample
                if sample > 8:
                    return
                record["sampled"] = True
                record["sample_limit"] = 8
            if scope.publish(record):
                scope.diagnostic_count += 1
    except Exception:
        # Wire diagnostics must never affect the request/response path.
        return
