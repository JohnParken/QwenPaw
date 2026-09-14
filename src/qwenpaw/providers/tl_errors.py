# -*- coding: utf-8 -*-
"""Typed, safe diagnostics emitted by the standalone TL transport."""

from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERN = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|"
    r"[\"']?(?:authorization|proxy-authorization|cookie|set-cookie|"
    r"(?:x[-_])?api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|"
    r"password|secret)[\"']?\s*[:=]\s*"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\r\n,;]+))",
)
_URL_PATTERN = re.compile(r"(?i)https?://[^\s<>\"']+")
_MAX_MESSAGE_LENGTH = 512


def _safe_message(value: Any) -> str:
    """Return a bounded diagnostic without common credential values."""
    text = _URL_PATTERN.sub("[redacted URL]", str(value))
    text = _SECRET_PATTERN.sub("[redacted]", text)
    text = " ".join(
        "".join(c for c in text if c.isprintable() or c.isspace()).split()
    )
    if len(text) > _MAX_MESSAGE_LENGTH:
        text = f"{text[:_MAX_MESSAGE_LENGTH - 1]}…"
    return text


class TLError(ValueError):
    """An error with a stable transport phase and safe user-facing detail."""

    def __init__(
        self,
        stage: str,
        message: str,
        kind: str | None = None,
        *,
        status_code: int | None = None,
        attempt_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.stage = str(stage)
        self.message = _safe_message(message)
        self.kind = kind
        self.status_code = status_code
        self.attempt_id = attempt_id
        self.request_id = request_id
        self.session_id = session_id

        detail = f"[{self.stage}] {self.message}"
        if self.kind:
            detail = f"{detail} ({self.kind})"
        super().__init__(detail)

    @property
    def diagnostic(self) -> str:
        """Return the sanitized message for logs or API response."""
        return self.message
