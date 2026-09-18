"""Routing sees user text consistently across SDK and legacy histories."""

import pytest
from agentscope.message import UserMsg, AssistantMsg, TextBlock

from qwenpaw.agents.routing_chat_model import _extract_user_text


@pytest.mark.parametrize(
    "message, expected",
    [
        (UserMsg(name="u", content="你好"), "你好"),
        (
            UserMsg(
                name="u",
                content=[TextBlock(text="one"), TextBlock(text="two")],
            ),
            "one two",
        ),
        (AssistantMsg(name="a", content="ignore"), ""),
        ({"role": "user", "content": "legacy"}, "legacy"),
        (
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "first"},
                    {
                        "type": "image",
                        "text": "ignore",
                        "url": "https://invalid",
                    },
                    {"type": "text/plain", "text": "second"},
                    {"type": "text", "text": 1},
                ],
            },
            "first second",
        ),
        ({"role": "tool", "content": "ignore"}, ""),
        ({"role": "user"}, ""),
    ],
)
def test_extract_user_text(message, expected):
    assert _extract_user_text(message) == expected
