# -*- coding: utf-8 -*-
"""AgentScope message formatter for the standalone TL prompt protocol.

The TL gateway accepts one system variable and one text payload.  This
formatter deliberately stops before any provider wire conversion: it turns
typed AgentScope messages into the small, JSON-safe history vocabulary owned
by :mod:`tl_prompt_codec`.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from agentscope.formatter import FormatterBase
from agentscope.message import (
    DataBlock,
    HintBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from pydantic import Field

from .tl_errors import TLError


def _tl_error(stage: str, message: str, kind: str | None = None) -> TLError:
    """Build a typed protocol error."""
    return TLError(stage, message, kind=kind)


def _raise_tl_error(
    stage: str,
    message: str,
    kind: str | None = None,
) -> None:
    raise _tl_error(stage, message, kind)


def _block_state(block: Any) -> str:
    state = getattr(block, "state", None)
    value = getattr(state, "value", state)
    if value is None:
        return "unknown"
    if not isinstance(value, str):
        return str(value)
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON number {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_historical_input(raw_input: Any) -> dict[str, Any] | None:
    """Decode a historical ToolCallBlock input without repairing it.

    ``None`` means the original value is retained under ``raw_input``.  The
    formatter never turns malformed history into executable empty arguments.
    """

    if not isinstance(raw_input, str):
        return None
    try:
        value = json.loads(
            raw_input,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _format_text_blocks(blocks: list[Any]) -> str:
    texts: list[str] = []
    for sub_block in blocks:
        if isinstance(sub_block, TextBlock):
            texts.append(sub_block.text)
            continue
        if isinstance(sub_block, DataBlock):
            _raise_tl_error(
                "prompt_encode",
                "TL prompt protocol accepts text-only Hint and "
                "tool output blocks",
                "unsupported_media",
            )
        _raise_tl_error(
            "prompt_encode",
            f"unsupported block in text-only nested "
            f"content: {type(sub_block).__name__}",
            "unsupported_block",
        )
    return "\n".join(texts)


class TLChatFormatter(FormatterBase):
    """Preserve AgentScope history for the standalone TL prompt codec."""

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        description="The standalone TL adapter accepts text input only.",
    )
    _qwenpaw_tl_formatter: ClassVar[bool] = True

    @classmethod
    def validate_text_input(cls, messages: list[Msg]) -> None:
        """Reject raw media before QwenPaw's generic media stripping.

        This is intentionally synchronous so the model/reasoning boundary can
        perform the preflight before any destructive cleanup or file lookup.
        """

        cls.assert_list_of_msgs(messages)
        for message in messages:
            for block in message.content:
                cls._validate_block(block)

    @classmethod
    def _validate_block(cls, block: Any) -> None:
        if isinstance(block, DataBlock):
            _raise_tl_error(
                "prompt_encode",
                "TL provider supports text input only; "
                "media blocks are unsupported",
                "unsupported_media",
            )

        if isinstance(block, HintBlock):
            if isinstance(block.hint, str):
                return
            _format_text_blocks(block.hint)
            return

        if isinstance(block, ToolResultBlock):
            output = block.output
            if isinstance(output, str):
                return
            _format_text_blocks(output)
            return

        if isinstance(
            block,
            (TextBlock, ThinkingBlock, ToolCallBlock),
        ):
            return

        _raise_tl_error(
            "prompt_encode",
            f"unsupported AgentScope content block: {type(block).__name__}",
            "unsupported_block",
        )

    @classmethod
    def _format_block(cls, block: Any) -> dict[str, Any] | None:
        cls._validate_block(block)

        if isinstance(block, ThinkingBlock):
            # Reasoning is deliberately excluded from the visible TL history.
            return None

        if isinstance(block, TextBlock):
            return {"type": "text", "text": block.text}

        if isinstance(block, HintBlock):
            text = (
                block.hint
                if isinstance(block.hint, str)
                else _format_text_blocks(block.hint)
            )
            formatted: dict[str, Any] = {
                "type": "hint",
                "text": text,
            }
            if block.source is not None:
                formatted["source"] = block.source
            return formatted

        if isinstance(block, ToolCallBlock):
            formatted = {
                "type": "tool_call",
                "id": block.id,
                "name": block.name,
                "state": _block_state(block),
            }
            arguments = _decode_historical_input(block.input)
            if arguments is None:
                formatted["raw_input"] = block.input
                formatted["input_status"] = "invalid_json"
            else:
                formatted["arguments"] = arguments
            return formatted

        if isinstance(block, ToolResultBlock):
            if isinstance(block.output, str):
                output: Any = block.output
            else:
                output = [
                    {"type": "text", "text": item.text}
                    for item in block.output
                    if isinstance(item, TextBlock)
                ]
            return {
                "type": "tool_result",
                "id": block.id,
                "name": block.name,
                "output": output,
                "state": _block_state(block),
            }

        # _validate_block keeps this branch unreachable, but retaining an
        # explicit failure prevents a future AgentScope block from vanishing.
        _raise_tl_error(
            "prompt_encode",
            f"unsupported AgentScope content block: {type(block).__name__}",
            "unsupported_block",
        )
        return None

    @classmethod
    def _format_message(cls, message: Msg) -> list[dict[str, Any]]:
        """Split mixed typed blocks into protocol records in original order."""

        if message.role == "system":
            content: list[dict[str, Any]] = []
            for block in message.content:
                formatted = cls._format_block(block)
                if formatted is not None:
                    if formatted["type"] != "text":
                        _raise_tl_error(
                            "prompt_encode",
                            "system history may contain text blocks only",
                            "invalid_system_history",
                        )
                    content.append(formatted)
            if not content:
                return []
            return [
                {"role": "system", "name": message.name, "content": content}
            ]

        records: list[dict[str, Any]] = []
        for block in message.content:
            formatted = cls._format_block(block)
            if formatted is None:
                continue
            block_type = formatted["type"]
            # Hint and tool results have their own data roles even when the
            # AgentScope runtime stores them in an assistant Msg alongside
            # text and calls.
            role = message.role
            if block_type == "hint":
                role = "user"
            elif block_type == "tool_call":
                role = "assistant"
            elif block_type == "tool_result":
                role = "tool"
            records.append(
                {
                    "role": role,
                    "name": message.name,
                    "content": [formatted],
                },
            )
        return records

    @classmethod
    def format_messages(cls, messages: list[Msg]) -> list[dict[str, Any]]:
        """Normalize messages for callers outside an event loop."""

        cls.assert_list_of_msgs(messages)
        cls.validate_text_input(messages)
        formatted: list[dict[str, Any]] = []
        for message in messages:
            formatted.extend(cls._format_message(message))
        return formatted

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Return protocol history records for TL payload transport."""

        return self.format_messages(msgs)


__all__ = ["TLChatFormatter", "TLError"]
