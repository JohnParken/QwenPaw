# -*- coding: utf-8 -*-
"""Real AgentScope block/stream contracts with an offline TL transport."""

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from agentscope.message import SystemMsg, UserMsg, TextBlock, ToolCallBlock
from pydantic import BaseModel

from qwenpaw.providers.tl_chat_model import TLChatModel
from qwenpaw.providers.tl_config import TLConfig
from qwenpaw.providers.tl_errors import TLError

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Return the sum",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        },
    }
]
CALL = (
    '{"version":1,"type":"tool_calls",'
    '"calls":[{"name":"add","arguments":{"a":2,"b":3}}]}'
)
FINAL = '{"version":1,"type":"final","content":"2+3=5"}'
DSML_CALL = (
    '<｜｜DSML｜｜ calls>\n'
    '<｜｜DSML｜｜ invoke name="add">{"a":2,"b":3}</｜｜DSML｜｜ invoke>\n'
    '</｜｜DSML｜｜ calls>'
)


class FakeTransport:
    config = TLConfig()

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = 0

    async def iter_text(self, system_prompt, user_payload, stream=True):
        self.calls.append((system_prompt, user_payload, stream))
        response = self.responses.pop(0)
        try:
            for chunk in (
                response if isinstance(response, list) else [response]
            ):
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
        finally:
            self.closed += 1


def messages():
    return [
        SystemMsg(name="system", content="SYSTEM_ONLY"),
        UserMsg(name="user", content="计算 2+3"),
    ]


async def test_text_deltas_and_final_snapshot():
    transport = FakeTransport(["A", "B"])
    model = TLChatModel("label", transport)
    chunks = [r async for r in await model(messages())]
    assert [r.content[0].text for r in chunks] == ["A", "B", "AB"]
    assert [r.is_last for r in chunks] == [False, False, True]
    assert len({r.id for r in chunks}) == 1
    assert len({r.content[0].id for r in chunks}) == 1
    assert all(r.usage is None for r in chunks)
    assert "SYSTEM_ONLY" not in transport.calls[0][1]


async def test_tool_batch_commit_and_semantic_failure():
    transport = FakeTransport(
        [CALL[:30], CALL[30:]], CALL.replace('"a":2', '"a":"bad"')
    )
    model = TLChatModel("label", transport)
    chunks = [r async for r in await model(messages(), TOOLS)]
    assert [r.is_last for r in chunks] == [False, True]
    assert chunks[0] is not chunks[1]
    assert isinstance(chunks[-1].content[0], ToolCallBlock)
    assert json.loads(chunks[-1].content[0].input) == {"a": 2, "b": 3}
    assert chunks[0].content[0].id == chunks[1].content[0].id
    emitted = []
    with pytest.raises(TLError):
        async for result in await model(messages(), TOOLS):
            emitted.append(result)
    assert not emitted
    assert len(transport.calls) == 2


async def test_tail_error_never_emits_or_corrects():
    transport = FakeTransport([CALL, TLError("sse_decode", "No done", "eof")])
    model = TLChatModel("label", transport)
    emitted = []
    with pytest.raises(TLError):
        async for result in await model(messages(), TOOLS):
            emitted.append(result)
    assert not emitted and len(transport.calls) == 1


async def test_single_syntax_correction_reuses_system():
    transport = FakeTransport(
        CALL.replace('"version":1,', '"version":1,,', 1), CALL
    )
    model = TLChatModel("label", transport, stream=False)
    result = await model(messages(), TOOLS)
    assert isinstance(result.content[0], ToolCallBlock)
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == transport.calls[1][0]
    assert transport.calls[0][1] != transport.calls[1][1]


@pytest.mark.parametrize("wrapper", [
    {"name": "add", "parameters": {"a": 2, "b": 3}},
    {"id": "call1", "type": "function", "function": {
        "name": "add", "arguments": {"a": 2, "b": 3}}},
    {"name": "add", "arguments": {"a": 2, "b": 3}, "id": "call1"},
])
async def test_call_wrapper_corrects_once_with_original_contract(wrapper):
    broken = json.dumps({"version": 1, "type": "tool_calls", "calls": [wrapper]})
    transport = FakeTransport(broken, CALL)
    model = TLChatModel("label", transport, stream=False)
    result = await model(messages(), TOOLS)
    assert isinstance(result.content[0], ToolCallBlock)
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == transport.calls[1][0]
    correction = json.loads(transport.calls[1][1])["correction"]
    assert correction["parse_error"]["type"] == "call_shape"
    assert correction["failed_assistant_content"] == broken


@pytest.mark.parametrize("corrected", [
    CALL.replace('"add"', '"unknown"'),
    CALL.replace('"a":2', '"a":"2"'),
    CALL.replace('"arguments"', '"parameters"'),
])
async def test_bad_wrapper_correction_never_emits_or_retries_again(corrected):
    broken = CALL.replace('"arguments"', '"parameters"')
    transport = FakeTransport(broken, corrected)
    model = TLChatModel("label", transport)
    emitted = []
    with pytest.raises(TLError):
        async for result in await model(messages(), TOOLS):
            emitted.append(result)
    assert not emitted
    assert len(transport.calls) == 2


async def test_call_shape_respects_disabled_correction():
    transport = FakeTransport(CALL.replace('"arguments"', '"parameters"'))
    model = TLChatModel("label", transport,
                        config=TLConfig(json_correction_max_attempts=0))
    with pytest.raises(TLError):
        async for _ in await model(messages(), TOOLS):
            pass
    assert len(transport.calls) == 1


async def test_syntax_and_wrapper_share_one_correction_budget():
    transport = FakeTransport('{bad', CALL.replace('"arguments"', '"parameters"'))
    model = TLChatModel("label", transport, stream=False)
    with pytest.raises(TLError):
        await model(messages(), TOOLS)
    assert len(transport.calls) == 2


async def test_dsml_is_retried_once_and_failed_body_is_untrusted_json():
    transport = FakeTransport(DSML_CALL, CALL)
    model = TLChatModel("label", transport, stream=False)
    result = await model(messages(), TOOLS)
    assert isinstance(result.content[0], ToolCallBlock)
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == transport.calls[1][0]
    correction = json.loads(transport.calls[1][1])
    assert correction["correction"]["failed_assistant_content"] == DSML_CALL
    assert "untrusted" in correction["correction"]["instruction"]
    assert correction["correction"]["original_user_payload"] == json.loads(
        transport.calls[0][1]
    )


async def test_dsml_valid_call_succeeds_without_retry():
    model = TLChatModel("label", FakeTransport(CALL), stream=False)
    result = await model(messages(), TOOLS)
    assert isinstance(result.content[0], ToolCallBlock)
    assert result.content[0].name == "add"
    assert json.loads(result.content[0].input) == {"a": 2, "b": 3}


async def test_repeated_dsml_has_exactly_two_attempts():
    transport = FakeTransport(DSML_CALL, DSML_CALL)
    model = TLChatModel("label", transport, stream=False)
    with pytest.raises(TLError):
        await model(messages(), TOOLS)
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "body, choice",
    [
        (CALL.replace('"add"', '"unknown"'), None),
        (CALL.replace('"a":2', '"a":"2"'), None),
        (
            CALL.replace(
                '"calls":[{"name":"add","arguments":{"a":2,"b":3}}]',
                '"calls":[{"name":"add","arguments":{"a":2,"b":3}},{"name":"unknown","arguments":{"a":2,"b":3}}]',
            ),
            None,
        ),
        (FINAL, "required"),
    ],
)
async def test_dsml_invalid_or_required_final_emits_no_blocks(body, choice):
    transport = FakeTransport(DSML_CALL, body)
    model = TLChatModel("label", transport)
    emitted = []
    with pytest.raises(TLError):
        async for result in await model(messages(), TOOLS, tool_choice=choice):
            emitted.append(result)
    assert not emitted
    assert len(transport.calls) == 2


async def test_dsml_correction_budget_zero_does_not_retry():
    transport = FakeTransport(DSML_CALL)
    model = TLChatModel(
        "label",
        transport,
        config=TLConfig(json_correction_max_attempts=0),
        stream=False,
    )
    with pytest.raises(TLError):
        await model(messages(), TOOLS)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        "<calls><invoke name=\"add\">{\"a\":2,\"b\":3}</invoke></calls>",
        "Quoted text: " + DSML_CALL,
        DSML_CALL[:-12],
    ],
)
async def test_non_full_or_truncated_dsml_is_not_corrected(body):
    transport = FakeTransport(body)
    model = TLChatModel("label", transport, stream=False)
    with pytest.raises(TLError):
        await model(messages(), TOOLS)
    assert len(transport.calls) == 1


async def test_text_mode_returns_dsml_as_text():
    model = TLChatModel("label", FakeTransport(DSML_CALL), stream=False)
    result = await model(messages())
    assert isinstance(result.content[0], TextBlock)
    assert result.content[0].text == DSML_CALL


async def test_none_returns_tool_looking_text_without_execution():
    model = TLChatModel("label", FakeTransport(CALL), stream=False)
    result = await model(messages(), TOOLS, tool_choice="none")
    assert isinstance(result.content[0], TextBlock)
    assert result.content[0].text == CALL


async def test_structured_output_and_context_budget():
    class Result(BaseModel):
        answer: int

    transport = FakeTransport('{"answer":5}')
    model = TLChatModel("label", transport, stream=False)
    result = await model.generate_structured_output(messages(), Result)
    assert result.content == {"answer": 5}
    assert "properties" in transport.calls[0][0]
    assert "properties" not in transport.calls[0][1]
    tiny = TLChatModel("label", FakeTransport(), context_size=10)
    with pytest.raises(TLError) as error:
        await tiny(messages(), TOOLS)
    assert error.value.kind == "context_overflow"
    assert not tiny.transport.calls


async def test_cancellation_propagates_and_closes():
    entered, closed = asyncio.Event(), asyncio.Event()

    class Waiting(FakeTransport):
        async def iter_text(self, *args, **kwargs):
            try:
                entered.set()
                yield CALL
                await asyncio.Event().wait()
            finally:
                closed.set()

    model = TLChatModel("label", Waiting())
    stream = await model(messages(), TOOLS)
    pending = asyncio.create_task(anext(stream))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert closed.is_set()


async def test_wrapper_counting_and_watchdogs():
    from qwenpaw.agents.model_factory import (
        _provider_retry_options,
        _create_formatter_instance,
    )
    from qwenpaw.providers.retry_chat_model import RetryChatModel
    from qwenpaw.providers.fallback_chat_model import FallbackChatModel
    from qwenpaw.token_usage.model_wrapper import TokenRecordingModelWrapper

    model = TLChatModel("label", FakeTransport())
    model.count_tokens = AsyncMock(return_value=6789)
    wrapped = RetryChatModel(
        TokenRecordingModelWrapper("tl", model),
        **_provider_retry_options(model, None),
    )
    assert not wrapped._retry_config.enabled
    assert wrapped._stream_first_content_timeout_override == 0
    assert wrapped._stream_idle_timeout_override == 0
    assert _create_formatter_instance(model) is model.formatter
    assert (
        await FallbackChatModel([wrapped]).count_tokens(messages(), TOOLS)
        == 6789
    )
    model.count_tokens.assert_awaited_once()


def test_tl_candidate_errors_stop_fallback():
    from qwenpaw.providers.fallback_chat_model import FallbackChatModel
    from qwenpaw.providers.model_error_policy import classify_model_error
    from qwenpaw.providers.retry_chat_model import (
        _enable_reasoning_content_fallback,
    )

    model = TLChatModel("label", FakeTransport())
    fallback = FallbackChatModel([model, model, model])
    error = TLError("chat_http", "HTTP error", status_code=404)
    assert not fallback._can_try_next(1, error)
    decision = classify_model_error(error)
    assert not decision.retryable and not decision.fallback_eligible
    assert decision.kind != "model_not_found"
    history = [{"role": "assistant", "content": "previous"}]
    assert not _enable_reasoning_content_fallback(
        model, (), {"messages": history}
    )
    assert "reasoning_content" not in history[0]


@pytest.mark.parametrize(
    "body",
    [
        "",
        "   ",
        "[" * 2000 + "0" + "]" * 2000,
        "```json\n" + CALL + "\n```",
        CALL + CALL,
        CALL.replace('"version":1', '"version":true'),
        CALL.replace('"version":1', '"version":1,"version":1'),
        CALL.replace('"a":2', '"a":NaN'),
        CALL.replace('"add"', '"unknown"'),
        CALL.replace('"a":2', '"a":"2"'),
        '{"version":1,"type":"tool_calls","calls":[]}',
        '{"version":1,"type":"final","content":"ok","extra":true}',
    ],
)
async def test_invalid_output_never_replays_or_emits(body):
    transport = FakeTransport(body)
    model = TLChatModel("label", transport)
    emitted = []
    with pytest.raises(TLError):
        async for result in await model(messages(), TOOLS):
            emitted.append(result)
    assert not emitted
    assert len(transport.calls) == 1


async def test_correction_has_no_third_attempt_and_retains_first_error():
    broken = CALL.replace('"version":1,', '"version":1,,', 1)
    transport = FakeTransport(
        broken, TLError("chat_http", "failed", "http_error")
    )
    model = TLChatModel("label", transport, stream=False)
    with pytest.raises(TLError) as error:
        await model(messages(), TOOLS)
    assert error.value.__cause__.kind == "json_syntax"
    assert len(transport.calls) == 2


async def test_correction_overflow_cannot_restart_context_recovery():
    broken = CALL.replace('"version":1,', '"version":1,,', 1)
    transport = FakeTransport(broken)
    model = TLChatModel("label", transport, stream=False)
    model.context_size = await model.count_tokens(messages(), TOOLS)
    with pytest.raises(TLError) as error:
        await model(messages(), TOOLS)
    assert error.value.kind == "correction_budget"
    assert len(transport.calls) == 1


async def test_unresolved_or_remote_schema_rejected_before_http():
    for reference in (
        "#/$defs/missing",
        "https://invalid.example/schema.json",
    ):
        tools = deepcopy(TOOLS)
        tools[0]["function"]["parameters"]["properties"]["a"] = {
            "$ref": reference
        }
        model = TLChatModel("label", FakeTransport())
        with pytest.raises(TLError):
            await model(messages(), tools)
        assert not model.transport.calls


async def test_input_and_tool_snapshots_are_invocation_local():
    tools = deepcopy(TOOLS)
    transport = FakeTransport(CALL, "plain second round")
    model = TLChatModel("label", transport)
    history = messages()
    history.append(UserMsg(name="memory", content="DYNAMIC_MEMORY"))
    stream = await model(history, tools)
    tools[0]["function"]["name"] = "changed_after_call"
    history[-1].content[0].text = "changed_after_call"
    chunks = [response async for response in stream]
    assert chunks[-1].content[0].name == "add"
    assert "DYNAMIC_MEMORY" in transport.calls[0][1]
    assert "changed_after_call" not in transport.calls[0][0]
    assert "DYNAMIC_MEMORY" not in transport.calls[0][0]
    assert (await model(messages(), [], stream=False)).content[
        0
    ].text == "plain second round"
    assert '"name":"add"' not in transport.calls[1][0]
