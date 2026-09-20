# -*- coding: utf-8 -*-
"""Tests for QwenPawAgent media-block classification and stripping.

Covers _is_media_block (dict/object/data-block detection) and
_strip_media_blocks_from_memory (top-level media removal, nested
tool_result output filtering, empty-content placeholder insertion),
which previously had no coverage.
"""
# pylint: disable=protected-access,redefined-outer-name,unused-argument
from __future__ import annotations

from types import SimpleNamespace

import pytest
from agentscope.message import (
    Msg,
    TextBlock,
    ToolResultBlock,
    ToolResultState,
)

from qwenpaw.agents.react_agent import QwenPawAgent
from qwenpaw.constant import MEDIA_UNSUPPORTED_PLACEHOLDER


def _agent_with_context(context: list) -> QwenPawAgent:
    """Build a bare agent with only a state.context — no model needed."""
    agent = object.__new__(QwenPawAgent)
    agent.state = SimpleNamespace(context=context)
    return agent


def _msg_with(blocks: list) -> Msg:
    """Build a Msg whose content is assigned raw (bypassing block
    validation) so dict media blocks can be inspected by the strip logic."""
    msg = Msg(
        name="assistant",
        role="assistant",
        content=[TextBlock(type="text", text="seed")],
    )
    msg.content = blocks
    return msg


# ---------------------------------------------------------------------------
# _is_media_block
# ---------------------------------------------------------------------------


class TestIsMediaBlock:
    def test_dict_image_is_media(self):
        agent = _agent_with_context([])
        assert agent._is_media_block({"type": "image"}) is True
        assert agent._is_media_block({"type": "audio"}) is True
        assert agent._is_media_block({"type": "video"}) is True
        assert agent._is_media_block({"type": "file"}) is True

    def test_dict_text_not_media(self):
        agent = _agent_with_context([])
        assert agent._is_media_block({"type": "text"}) is False

    def test_object_block_by_type(self):
        agent = _agent_with_context([])
        assert agent._is_media_block(SimpleNamespace(type="image")) is True
        assert agent._is_media_block(SimpleNamespace(type="text")) is False

    def test_data_block_image_mime_is_media(self):
        agent = _agent_with_context([])
        source = SimpleNamespace(media_type="image/png")
        assert (
            agent._is_media_block(
                SimpleNamespace(type="data", source=source),
            )
            is True
        )

    def test_data_block_non_media_mime_not_media(self):
        agent = _agent_with_context([])
        source = SimpleNamespace(media_type="application/json")
        assert (
            agent._is_media_block(
                SimpleNamespace(type="data", source=source),
            )
            is False
        )

    def test_data_block_without_source_not_media(self):
        agent = _agent_with_context([])
        assert (
            agent._is_media_block(
                SimpleNamespace(type="data", source=None),
            )
            is False
        )


# ---------------------------------------------------------------------------
# _strip_media_blocks_from_memory
# ---------------------------------------------------------------------------


class TestStripMediaBlocksFromMemory:
    def test_strips_top_level_media(self):
        msg = _msg_with(
            [
                TextBlock(type="text", text="keep"),
                {"type": "image", "source": {}},
            ],
        )
        agent = _agent_with_context([msg])
        removed = agent._strip_media_blocks_from_memory()
        assert removed == 1
        assert len(msg.content) == 1
        assert msg.content[0].text == "keep"

    def test_non_list_content_skipped(self):
        msg = _msg_with([])
        msg.content = "plain string"
        agent = _agent_with_context([msg])
        assert agent._strip_media_blocks_from_memory() == 0

    def test_empty_content_gets_placeholder(self):
        msg = _msg_with([{"type": "image", "source": {}}])
        agent = _agent_with_context([msg])
        removed = agent._strip_media_blocks_from_memory()
        assert removed == 1
        assert len(msg.content) == 1
        assert msg.content[0].text == MEDIA_UNSUPPORTED_PLACEHOLDER

    def test_no_media_returns_zero(self):
        msg = _msg_with([TextBlock(type="text", text="plain")])
        agent = _agent_with_context([msg])
        assert agent._strip_media_blocks_from_memory() == 0
        assert len(msg.content) == 1

    def test_nested_media_in_tool_result_stripped(self):
        tool_result = ToolResultBlock(
            id="t1",
            name="fetch",
            state=ToolResultState.SUCCESS,
            output="seed",
        )
        # Assign raw nested output (bypassing block validation) so a
        # dict media item can be inspected by the strip logic.
        tool_result.output = [
            TextBlock(type="text", text="kept"),
            {"type": "image", "source": {}},
        ]
        msg = _msg_with([tool_result])
        agent = _agent_with_context([msg])
        removed = agent._strip_media_blocks_from_memory()
        assert removed == 1
        # the media item removed from nested output
        assert len(tool_result.output) == 1
        assert tool_result.output[0].text == "kept"

    def test_nested_all_media_replaced_with_placeholder(self):
        tool_result = ToolResultBlock(
            id="t1",
            name="fetch",
            state=ToolResultState.SUCCESS,
            output="seed",
        )
        tool_result.output = [{"type": "image", "source": {}}]
        msg = _msg_with([tool_result])
        agent = _agent_with_context([msg])
        removed = agent._strip_media_blocks_from_memory()
        assert removed == 1
        assert tool_result.output == MEDIA_UNSUPPORTED_PLACEHOLDER

    def test_nested_output_not_list_untouched(self):
        tool_result = ToolResultBlock(
            id="t1",
            name="fetch",
            state=ToolResultState.SUCCESS,
            output="plain string output",
        )
        msg = _msg_with([tool_result])
        agent = _agent_with_context([msg])
        removed = agent._strip_media_blocks_from_memory()
        assert removed == 0
        assert tool_result.output == "plain string output"

    def test_counts_across_messages(self):
        msg1 = _msg_with([{"type": "image", "source": {}}])
        msg2 = _msg_with(
            [
                TextBlock(type="text", text="ok"),
                {"type": "video", "source": {}},
            ],
        )
        agent = _agent_with_context([msg1, msg2])
        removed = agent._strip_media_blocks_from_memory()
        assert removed == 2


@pytest.mark.asyncio
async def test_tl_file_result_uses_text_receipt_without_mutating_attachment(
    monkeypatch,
):
    """TL receives a text-only copy while the frontend keeps the file block."""
    from agentscope.message import DataBlock, URLSource
    import qwenpaw.agents.react_agent as react_agent_module

    tool_result = ToolResultBlock(
        id="call-send-file",
        name="send_file_to_user",
        state=ToolResultState.SUCCESS,
        output="seed",
    )
    attachment = DataBlock(
        source=URLSource(
            url="file:///tmp/report.pdf",
            media_type="application/pdf",
        ),
        name="report.pdf",
    )
    tool_result.output = [
        attachment,
        TextBlock(text="File sent successfully."),
    ]
    agent = object.__new__(QwenPawAgent)
    agent.name = "test-agent"
    agent.model = SimpleNamespace(count_tokens=_count_one_token)
    agent.context_config = SimpleNamespace(tool_result_limit=100)
    agent._get_active_formatter = lambda: object()
    monkeypatch.setattr(react_agent_module, "is_tl_formatter", lambda _: True)

    reserved, offloaded = await agent._split_tool_result_for_compression(
        tool_result,
    )

    assert offloaded is None
    assert all(
        getattr(block, "type", None) == "text"
        for block in reserved.output
    )
    receipt = reserved.output[0].text
    assert "File sent successfully." in receipt
    assert "report.pdf" in receipt
    assert "file:///tmp/report.pdf" in receipt
    assert tool_result.output[0] is attachment
    assert isinstance(tool_result.output[0], DataBlock)


async def _count_one_token(*_args, **_kwargs):
    return 1
