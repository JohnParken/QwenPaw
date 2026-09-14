# -*- coding: utf-8 -*-
"""A text-only provider for the internal two-stage chatbbc protocol."""

from __future__ import annotations

import asyncio
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .provider import ModelConnectionResult, ModelInfo, Provider
from .tl_config import TLConfig
from .tl_transport import TLTransport


class TLProvider(Provider):
    """One endpoint binds one server route; model IDs are local labels."""

    chat_model: Literal["TLChatModel"] = "TLChatModel"
    tl_config: TLConfig = Field(default_factory=TLConfig)
    require_api_key: bool = False
    support_connection_check: bool = True
    support_model_discovery: bool = False

    @model_validator(mode="before")
    @classmethod
    def _default_config(cls, data):
        if isinstance(data, dict) and data.get("tl_config") is None:
            data = {**data, "tl_config": {}}
        return data

    @model_validator(mode="after")
    def _validate_route(self):
        if self.base_url:
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.query
                or url.fragment
                or url.username
                or url.password
            ):
                raise ValueError(
                    "TL base_url must be an HTTP(S) service root."
                )
        if self.generate_kwargs:
            raise ValueError("TL does not support generate_kwargs overrides.")
        visible = {m.id for m in self.configured_models()}
        if len(visible) > 1:
            raise ValueError(
                "TL supports one model label per endpoint; "
                "create another provider."
            )
        for model in (
            *self.models,
            *self.extra_models,
            *self.discovered_models,
        ):
            if not model.id.strip():
                raise ValueError("TL model label must not be empty.")
            self._validate_model_options(model.model_dump())
            for field in (
                "supports_image",
                "supports_video",
                "supports_multimodal",
            ):
                if getattr(model, field) is True:
                    raise ValueError("TL v1 supports text input only.")
                setattr(model, field, False)
        self.support_model_discovery = False
        self.require_api_key = False
        self.model_sync_mode = "disabled"
        self.discovery_strategy = "unsupported"
        self.support_connection_check = True
        return self

    @staticmethod
    def _validate_model_options(config):
        if config.get("generate_kwargs") or any(
            config.get(key) is not None
            for key in (
                "thinking_enabled",
                "thinking_budget",
                "reasoning_effort",
            )
        ):
            raise ValueError(
                "TL does not support sampling or thinking overrides."
            )

    def update_config(self, config):
        # Validate a detached candidate before making any in-memory change.
        candidate = self.model_copy(deep=True)
        Provider.update_config(candidate, config)
        if config.get("tl_config") is not None:
            patch = config["tl_config"]
            if isinstance(patch, TLConfig):
                patch = patch.model_dump()
            candidate.tl_config = TLConfig.model_validate(
                {**self.tl_config.model_dump(), **patch},
            )
        if config.get("models") is not None:
            candidate.models = [
                ModelInfo.model_validate(m) for m in config["models"]
            ]
        candidate = type(self).model_validate(candidate.model_dump())
        for field in type(self).model_fields:
            setattr(self, field, getattr(candidate, field))

    def update_model_config(self, model_id, config):
        self._validate_model_options(config)
        return super().update_model_config(model_id, config)

    async def add_model(self, model_info, target="extra_models", timeout=10):
        candidate = self.model_copy(deep=True)
        result = await Provider.add_model(
            candidate, model_info.model_copy(deep=True), target, timeout
        )
        if not result[0]:
            return result
        candidate = type(self).model_validate(candidate.model_dump())
        self.models = candidate.models
        self.extra_models = candidate.extra_models
        self.removed_model_ids = candidate.removed_model_ids
        return result

    def _transport(self):
        return TLTransport(
            self.base_url,
            self.tl_config,
            api_key=self.api_key,
            custom_headers=self.custom_headers,
        )

    async def fetch_models(self, timeout=5):
        return [
            model.model_copy(deep=True) for model in self.configured_models()
        ]

    async def check_connection(self, timeout=5):
        try:
            async with asyncio.timeout(timeout):
                await self._transport().init_session(
                    "Reply briefly in plain text."
                )
            return (
                True,
                "Session initialization successful; chat was not tested.",
            )
        except Exception as exc:
            return False, self.connection_error_message(exc)

    async def check_model_connection(self, model_id, timeout=5):
        if not self.has_model(model_id):
            return ModelConnectionResult(
                success=False,
                message="Unknown local TL route label.",
                verification="unverified",
            )
        try:
            async with asyncio.timeout(timeout):
                async for _ in self._transport().iter_text(
                    "Reply briefly in plain text.",
                    (
                        '{"version":1,"messages":[{"role":"user",'
                        '"content":"Reply OK"}]}'
                    ),
                    stream=True,
                ):
                    pass
            return ModelConnectionResult(
                success=True,
                message=(
                    "Configured TL route completed chat; "
                    "upstream identity is not reported."
                ),
                verification="live",
            )
        except Exception as exc:
            return ModelConnectionResult(
                success=False,
                message=self.connection_error_message(exc),
                http_status=getattr(exc, "status_code", None),
                error_kind="tl_error",
                verification="unverified",
            )

    def get_chat_model_cls(self):
        from .tl_chat_model import TLChatModel

        return TLChatModel

    def get_chat_model_instance(self, model_id):
        type(self).model_validate(self.model_dump())
        if not self.has_model(model_id):
            raise ValueError("Unknown local TL route label.")
        info = self.get_model_info(model_id)
        return self.get_chat_model_cls()(
            model=model_id,
            transport=self._transport(),
            config=self.tl_config,
            context_size=self.get_context_size(model_id),
            output_reserve=info.max_output_length or 1024,
        )

    def _context_catalog_enabled(self):
        return False

    def get_context_size(self, model_id):
        info = self.get_model_info(model_id)
        if info is not None and info.max_input_length_configured:
            return info.max_input_length
        return 32768

    def supports_agent_thinking(self, model_id):
        return False
