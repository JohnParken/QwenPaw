# -*- coding: utf-8 -*-
"""AgentScope adapter for TL's system-prompt tool protocol.

Only fully validated batches become model blocks. Tool execution belongs to
the existing AgentScope/ToolGuard loop, never to this adapter.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from copy import deepcopy
from uuid import uuid4

from agentscope.message import TextBlock
from agentscope.model import ChatModelBase
from agentscope.model._model_response import ChatResponse, StructuredResponse
from pydantic import BaseModel, ValidationError

from .tl_config import TLConfig
from .tl_errors import TLError
from .tl_formatter import TLChatFormatter
from .tl_preview import begin_preview
from .tl_prompt_codec import (
    build_correction_payload,
    compile_prompt,
    estimate_tokens,
    is_correctable_json_error,
    parse_response,
)
from .tl_transport import TLTransport


class TLChatModel(ChatModelBase):
    """A reusable model with invocation-local history and correction budget."""

    def __init__(
        self,
        model: str,
        transport: TLTransport,
        config: TLConfig | None = None,
        stream: bool = True,
        context_size: int = 32768,
        output_reserve: int = 1024,
        token_counter=None,
    ):
        super().__init__(
            credential=None,
            model=model,
            parameters=ChatModelBase.Parameters(),
            stream=stream,
            max_retries=0,
            context_size=context_size,
        )
        self.transport = transport
        self.config = (config or transport.config).model_copy(deep=True)
        self.formatter = TLChatFormatter()
        if isinstance(output_reserve, bool) or output_reserve < 0:
            raise TLError(
                "configuration", "output_reserve must be non-negative"
            )
        self.output_reserve = output_reserve
        self._token_counter = token_counter

    async def _compile(
        self, messages, tools=None, tool_choice=None, structured_schema=None
    ):
        # Freeze the actual middleware output, never reconstruct agent memory.
        records = await self.formatter.format(deepcopy(messages))
        return compile_prompt(
            records,
            tools=deepcopy(tools),
            tool_choice=deepcopy(tool_choice),
            structured_schema=deepcopy(structured_schema),
            token_counter=self._token_counter,
        )

    def _check_budget(self, compiled, payload=None):
        estimated = estimate_tokens(
            compiled, user_payload=payload, token_counter=self._token_counter
        )
        if estimated + self.output_reserve > self.context_size:
            raise TLError(
                "prompt_encode",
                "Estimated compiled input exceeds maximum context window.",
                kind="context_overflow",
            )

    async def count_tokens(self, messages, tools=None):
        compiled = await self._compile(messages, tools)
        return compiled.input_token_estimate + self.output_reserve

    @staticmethod
    def _reject_options(kwargs):
        if kwargs:
            raise TLError(
                "configuration",
                "TL does not support generation parameter overrides.",
            )

    async def __call__(self, messages, tools=None, tool_choice=None, **kwargs):
        # Bypass the SDK base retry/accumulator, which converts cancellation
        # into an interrupted response and accumulates stream blocks itself.
        stream = kwargs.pop("stream", self.stream)
        if not isinstance(stream, bool):
            raise TLError("configuration", "stream must be a boolean")
        self._reject_options(kwargs)
        compiled = await self._compile(messages, tools, tool_choice)
        self._check_budget(compiled)
        if stream:
            return self._stream_response(compiled)
        blocks = await self._validated_response(compiled, stream=False)
        return ChatResponse(content=blocks, is_last=True)

    async def _call_api(
        self, model_name, messages, tools=None, tool_choice=None, **kwargs
    ):
        return await self(messages, tools, tool_choice, **kwargs)

    async def _validated_response(self, compiled, *, stream):
        payload = compiled.user_payload
        first_error = None
        invocation_id = "tl_invocation_" + uuid4().hex
        for attempt in range(self.config.json_correction_max_attempts + 1):
            try:
                self._check_budget(compiled, payload)
            except TLError:
                if first_error is not None:
                    raise TLError(
                        "response_parse",
                        "Syntax correction exceeds the input budget.",
                        "correction_budget",
                    ) from first_error
                raise
            chunks = []
            preview = (
                begin_preview(invocation_id=invocation_id)
                if stream and compiled.mode == "tools"
                else None
            )
            try:
                async with aclosing(
                    self.transport.iter_text(
                        compiled.system_prompt, payload, stream=stream
                    )
                ) as source:
                    async for chunk in source:
                        chunks.append(chunk)
                        if preview is not None:
                            preview.feed(chunk)
            except (asyncio.CancelledError, GeneratorExit):
                if preview is not None:
                    preview.clear("cancel")
                raise
            except TLError as exc:
                if preview is not None:
                    preview.clear("error")
                if first_error is not None:
                    raise exc from first_error
                raise
            except BaseException:
                if preview is not None:
                    preview.clear("error")
                raise
            text = "".join(chunks)
            try:
                parsed = parse_response(text, compiled=compiled)
                if preview is not None:
                    preview.clear("commit")
                return (
                    parsed.structured
                    if compiled.mode == "structured"
                    else parsed.blocks
                )
            except TLError as exc:
                if first_error is not None:
                    if preview is not None:
                        preview.clear("invalid")
                    raise exc from first_error
                if (
                    attempt >= self.config.json_correction_max_attempts
                    or not is_correctable_json_error(
                        exc, text, compiled=compiled
                    )
                ):
                    if preview is not None:
                        preview.clear("invalid")
                    raise
                if preview is not None:
                    preview.clear("correction")
                first_error = exc
                payload = build_correction_payload(compiled, text, exc)
            finally:
                if preview is not None:
                    preview.clear("error")
        raise AssertionError("unreachable TL correction state")

    async def _stream_response(self, compiled):
        response_id = "tl_response_" + uuid4().hex
        if compiled.mode != "text":
            blocks = await self._validated_response(compiled, stream=True)
            yield ChatResponse(
                id=response_id, content=deepcopy(blocks), is_last=False
            )
            yield ChatResponse(
                id=response_id, content=deepcopy(blocks), is_last=True
            )
            return
        block_id = "tl_text_" + uuid4().hex
        chunks = []
        async with aclosing(
            self.transport.iter_text(
                compiled.system_prompt, compiled.user_payload, stream=True
            )
        ) as source:
            async for chunk in source:
                chunks.append(chunk)
                yield ChatResponse(
                    id=response_id,
                    content=[TextBlock(id=block_id, text=chunk)],
                    is_last=False,
                )
        yield ChatResponse(
            id=response_id,
            content=[TextBlock(id=block_id, text="".join(chunks))],
            is_last=True,
        )

    async def generate_structured_output(
        self, messages, structured_model, **kwargs
    ):
        tools = kwargs.pop("tools", None)
        choice = kwargs.pop("tool_choice", None)
        if tools or choice not in (None, "none"):
            raise TLError(
                "configuration",
                "TL structured output cannot be combined with tools.",
            )
        self._reject_options(kwargs)
        if isinstance(structured_model, type) and issubclass(
            structured_model, BaseModel
        ):
            schema = structured_model.model_json_schema()
        elif isinstance(structured_model, dict):
            schema = deepcopy(structured_model)
        else:
            raise TLError(
                "configuration",
                "Expected a Pydantic model class or JSON schema.",
            )
        compiled = await self._compile(messages, structured_schema=schema)
        result = await self._validated_response(compiled, stream=False)
        if isinstance(structured_model, type):
            # Validation only: return the SDK's documented dict content.
            try:
                structured_model.model_validate(result, strict=True)
            except ValidationError:
                raise TLError(
                    "response_parse",
                    "Structured result failed model validation.",
                    "invalid_output",
                ) from None
        return StructuredResponse(content=result)
