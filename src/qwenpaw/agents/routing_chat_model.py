# -*- coding: utf-8 -*-
"""ChatModel router for local/cloud model selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Literal, Type

from agentscope.formatter import FormatterBase
from agentscope.message import Msg, TextBlock
from agentscope.model import ChatModelBase
from agentscope.model._model_response import ChatResponse

from ..config.config import AgentsLLMRoutingConfig
from ..providers.tl_utils import is_tl_formatter

logger = logging.getLogger(__name__)


Route = Literal["local", "cloud"]


def _extract_user_text(message: Msg | dict[str, Any]) -> str:
    """Read user text from SDK blocks or legacy dictionary messages."""
    if isinstance(message, dict):
        role, content = message.get("role"), message.get("content")
    else:
        role, content = message.role, message.content
    if role != "user":
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") in {
            "text",
            "text/plain",
        }:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return " ".join(parts)


@dataclass
class RoutingDecision:
    route: Route
    reasons: list[str] = field(default_factory=list)


class RoutingPolicy:
    """Select a route using the configured default mode."""

    def __init__(self, cfg: AgentsLLMRoutingConfig):
        self.cfg = cfg

    def decide(
        self,
        *,
        text: str = "",
        channel: str = "",
        tools_available: bool = True,
    ) -> RoutingDecision:
        del text, channel, tools_available

        if getattr(self.cfg, "mode", "local_first") == "cloud_first":
            return RoutingDecision(
                route="cloud",
                reasons=["mode:cloud_first"],
            )

        return RoutingDecision(
            route="local",
            reasons=["mode:local_first"],
        )


@dataclass(frozen=True)
class RoutingEndpoint:
    provider_id: str
    model_name: str
    model: ChatModelBase
    formatter: FormatterBase
    formatter_family: Type[FormatterBase]


class RoutingChatModel(ChatModelBase):
    """A ChatModelBase that routes between local and cloud slots."""

    def __init__(
        self,
        *,
        local_endpoint: RoutingEndpoint,
        cloud_endpoint: RoutingEndpoint,
        routing_cfg: AgentsLLMRoutingConfig,
    ) -> None:
        # agentscope 2.0 ChatModelBase requires credential/model/parameters;
        # the router doesn't issue calls itself (it delegates to endpoints),
        # so reuse the local endpoint's metadata for base-class attributes.
        local_model = local_endpoint.model
        super().__init__(
            credential=getattr(local_model, "credential", None),
            model="routing",
            parameters=getattr(local_model, "parameters", None)
            or ChatModelBase.Parameters(),
            stream=bool(getattr(local_model, "stream", True)),
        )
        self.local_endpoint = local_endpoint
        self.cloud_endpoint = cloud_endpoint
        self.routing_cfg = routing_cfg
        self.policy = RoutingPolicy(routing_cfg)

    @property
    def formatter(self):
        endpoint = (
            self.local_endpoint
            if self.policy.decide().route == "local"
            else self.cloud_endpoint
        )
        return endpoint.formatter

    async def count_tokens(self, messages, tools=None):
        endpoint = (
            self.local_endpoint
            if self.policy.decide().route == "local"
            else self.cloud_endpoint
        )
        if is_tl_formatter(endpoint.formatter):
            return await endpoint.model.count_tokens(messages, tools)
        return await super().count_tokens(messages, tools)

    async def generate_structured_output(
        self, messages, structured_model, **kwargs
    ):
        endpoint = (
            self.local_endpoint
            if self.policy.decide().route == "local"
            else self.cloud_endpoint
        )
        return await endpoint.model.generate_structured_output(
            messages, structured_model, **kwargs
        )

    async def __call__(
        self,
        messages: list[Msg | dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "required"] | str | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        # agentscope 2.0 doesn't pass ``structured_model`` through ``__call__``
        # (it goes via ``generate_structured_output``); drop any 1.x leftover.
        kwargs.pop("structured_model", None)
        text = " ".join(filter(None, map(_extract_user_text, messages)))
        decision = self.policy.decide(
            text=text,
            tools_available=tools is not None,
        )
        endpoint = (
            self.local_endpoint
            if decision.route == "local"
            else self.cloud_endpoint
        )

        logger.debug(
            "LLM routing decision: route=%s provider=%s model=%s reasons=%s",
            decision.route,
            endpoint.provider_id,
            endpoint.model_name,
            ",".join(decision.reasons),
        )

        return await endpoint.model(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )
