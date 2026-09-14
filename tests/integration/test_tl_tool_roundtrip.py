# -*- coding: utf-8 -*-
"""Offline TL wire → real AgentScope → guarded tool → TL final roundtrip."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.message import UserMsg
from agentscope.tool import Toolkit

from qwenpaw.providers.tl_chat_model import TLChatModel
from qwenpaw.providers.tl_config import TLConfig
from qwenpaw.providers.tl_transport import TLTransport
from qwenpaw.runtime.tool_guard import GuardedFunctionTool


@pytest.mark.asyncio
@pytest.mark.parametrize("qwenpaw_agent", [False, True])
async def test_guarded_tool_roundtrip_with_real_sdk(qwenpaw_agent):
    requests, executions = [], []
    bodies = [
        (
            '{"version":1,"type":"tool_calls",'
            '"calls":[{"name":"add","arguments":{"a":2,"b":3}}]}'
        ),
        '{"version":1,"type":"final","content":"2+3=5"}',
    ]

    async def add(a: int, b: int) -> str:
        """Return the sum of two integers without side effects."""
        executions.append((a, b))
        return str(a + b)

    def handle(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        assert "tools" not in payload and "model" not in payload
        assert "tool_choice" not in payload["data"]
        if request.url.path.endswith("init_session"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"session_id": f"session-{len(requests)}"},
                },
            )
        body = bodies.pop(0)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                "event: chunk\ndata: "
                + json.dumps({"content": body}, ensure_ascii=False)
                + '\n\nevent: done\ndata: {"finished":true}\n\n'
            ),
        )

    tool = GuardedFunctionTool(add, request_context={"approval_level": "auto"})
    tool.check_permissions = AsyncMock(wraps=tool.check_permissions)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        transport = TLTransport(
            "https://fake-tl.invalid/company", TLConfig(), client=client
        )
        model = TLChatModel("route-label", transport)
        if qwenpaw_agent:
            from types import SimpleNamespace
            from qwenpaw.agents.react_agent import QwenPawAgent

            agent_class = QwenPawAgent
            options = dict(
                middlewares=[], agent_config=SimpleNamespace(language="en-US")
            )
        else:
            agent_class = Agent
            options = dict(
                injection_config=InjectionConfig(inject_runtime_state=False)
            )
        agent = agent_class(
            name="tl-test",
            model=model,
            system_prompt="Calculate using add.",
            toolkit=Toolkit(tools=[tool]),
            react_config=ReActConfig(max_iters=3),
            **options,
        )
        assert agent.model_config.max_retries == 0
        events = [
            event
            async for event in agent.reply_stream(
                inputs=[UserMsg(name="user", content="计算 2+3")]
            )
        ]

    assert executions == [(2, 3)]
    tool.check_permissions.assert_awaited_once()
    assert len(requests) == 4
    assert len({payload["requestId"] for _, payload in requests}) == 4
    assert (
        requests[1][1]["data"]["session_id"]
        != requests[3][1]["data"]["session_id"]
    )
    assert all(
        payload["data"]["files"] == []
        for path, payload in requests
        if path.endswith("/chat")
    )
    history = json.loads(requests[3][1]["data"]["txt"])["messages"]
    blocks = [block for message in history for block in message["content"]]
    calls = [block for block in blocks if block["type"] == "tool_call"]
    results = [block for block in blocks if block["type"] == "tool_result"]
    assert calls[0]["id"] == results[0]["id"]
    assert results[0]["state"] == "success"
    assert "5" in json.dumps(results[0]["output"])
    types = [str(event.type).lower() for event in events]
    for suffix in ("tool_call_start", "tool_call_delta", "tool_call_end"):
        assert sum(kind.endswith(suffix) for kind in types) == 1, types
    assert any(
        getattr(block, "text", None) == "2+3=5"
        for message in agent.state.context
        for block in message.content
    )
