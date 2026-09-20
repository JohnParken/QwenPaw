# -*- coding: utf-8 -*-
"""Configuration for the standalone TL chat transport."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TLConfig(BaseModel):
    """Local settings for the fixed ``chatbbc`` wire protocol.

    The fields in this model are deliberately limited to protocol metadata
    and client-side resource limits.  Model routing and generation settings
    are owned by the TL service and therefore do not belong here.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # The internal service accepts empty metadata values for deployments that
    # do not require them.  Keep them as configured instead of inventing
    # credentials or routing identity on the client's behalf.
    app_id: str = ""
    tr_code: str = ""
    tr_version: str = ""
    system_prompt_variable_name: str = Field(
        default="system_prompt",
        min_length=1,
    )
    tool_calling_mode: Literal["system_prompt"] = "system_prompt"
    json_correction_max_attempts: int = Field(default=1, ge=0, le=1)

    # A gateway reached directly must not be re-routed by HTTP_PROXY /
    # HTTPS_PROXY environment variables or by the platform proxy settings: a
    # forward proxy would rewrite, fail, or disclose an internal request.  The
    # default keeps every TL attempt on a direct socket; deployments that
    # genuinely need a forward proxy for the gateway can opt back in.
    trust_env: bool = False

    timeout_seconds: float = Field(default=150.0, gt=0)
    stream_idle_timeout_seconds: float = Field(default=0.0, ge=0)

    max_request_bytes: int = Field(default=1_048_576, gt=0)
    max_response_bytes: int = Field(default=4_194_304, gt=0)
    max_wire_response_bytes: int = Field(default=67_108_864, gt=0)
    max_sse_event_bytes: int = Field(default=1_048_576, gt=0)

    @field_validator(
        "system_prompt_variable_name",
    )
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        """Reject aliases that are empty or contain only whitespace."""
        if not value.strip():
            raise ValueError("TL metadata values must not be blank")
        return value

    @field_validator(
        "json_correction_max_attempts",
        "timeout_seconds",
        "stream_idle_timeout_seconds",
        "max_request_bytes",
        "max_response_bytes",
        "max_wire_response_bytes",
        "max_sse_event_bytes",
        mode="before",
    )
    @classmethod
    def _reject_bool_limits(cls, value: float | int) -> float | int:
        """Prevent Python's bool-as-int coercion for resource limits."""
        if isinstance(value, bool):
            raise ValueError("TL resource limits must be numeric")
        return value

    @field_validator("timeout_seconds", "stream_idle_timeout_seconds")
    @classmethod
    def _require_finite_timeout(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("TL timeouts must be finite")
        return value
