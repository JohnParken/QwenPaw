# -*- coding: utf-8 -*-
"""Agent execution driver.

Drives ``agent.reply_stream(inputs=msgs)`` with heartbeat wrapping
and delegates each ``EventType`` event to ``Envelope.translate_event()``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncGenerator

from .envelope import Envelope
from .heartbeat import (
    _iter_with_heartbeat,
    _HEARTBEAT_TICK,
    HEARTBEAT_INTERVAL_SECONDS,
)
from ..providers.tl_preview import current_preview_scope, drain_preview_events

logger = logging.getLogger(__name__)


class AgentExecutor:
    """Execute the agent's reply stream and translate
    events into SSE envelopes.

    One instance per ``Runtime.run()`` invocation.  The executor owns the
    heartbeat wrapper but not the agent itself (that belongs to the
    ``HookContext``).
    """

    def __init__(self, agent: Any, envelope: Envelope) -> None:
        self._agent = agent
        self._envelope = envelope

    async def run(
        self,
        msgs: list[Any],
    ) -> AsyncGenerator[Any, None]:
        """Drive ``agent.reply_stream`` and yield SSE envelope objects.

        Wraps the raw event stream with ``_iter_with_heartbeat`` so long
        idle periods (e.g. tool-guard approval waits) emit keep-alive
        envelopes instead of letting the connection drop.
        """
        agent_iter = self._agent.reply_stream(inputs=msgs).__aiter__()
        preview = current_preview_scope()
        if preview is None or not preview.enabled:
            async for event in _iter_with_heartbeat(
                agent_iter,
                HEARTBEAT_INTERVAL_SECONDS,
            ):
                if event is _HEARTBEAT_TICK:
                    async for obj in self._envelope.heartbeat():
                        yield obj
                    continue

                self._maybe_stamp_finished_at(event)

                async for obj in self._envelope.translate_event(event):
                    yield obj
            return

        # A TL provider can be silent while it buffers and validates a full
        # JSON response. Keep the preview queue and agent stream independent
        # so the UI receives updates during that wait. Preview objects are
        # already typed local events and deliberately bypass Envelope's
        # durable state machine.
        agent_task = asyncio.ensure_future(agent_iter.__anext__())
        preview_task = asyncio.ensure_future(preview.queue.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {agent_task, preview_task},
                    timeout=HEARTBEAT_INTERVAL_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    async for obj in self._envelope.heartbeat():
                        yield obj
                    continue

                # Give lifecycle/update events priority when both sources
                # become ready in the same event-loop turn. If the agent is
                # also ready, its event remains done and is handled on the
                # next pass after all queued preview snapshots are drained.
                if preview_task in done:
                    try:
                        preview_event = preview_task.result()
                    except asyncio.CancelledError:
                        raise
                    preview_task = asyncio.ensure_future(preview.queue.get())
                    yield preview_event
                    continue

                try:
                    event = agent_task.result()
                except StopAsyncIteration:
                    preview.clear_active("error")
                    async for item in drain_preview_events(
                        preview.queue, preview_task
                    ):
                        yield item
                    return

                # A provider publishes preview_clear(commit) immediately
                # before yielding its validated model response. Drain those
                # synchronous queue entries before translating the durable
                # event so the clear always precedes formal content.
                async for item in drain_preview_events(
                    preview.queue, preview_task
                ):
                    yield item

                self._maybe_stamp_finished_at(event)
                async for obj in self._envelope.translate_event(event):
                    yield obj
                agent_task = asyncio.ensure_future(agent_iter.__anext__())
                preview_task = asyncio.ensure_future(preview.queue.get())
        except asyncio.CancelledError:
            preview.clear_active("cancel")
            raise
        except BaseException:
            preview.clear_active("error")
            raise
        finally:
            for task in (agent_task, preview_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                agent_task, preview_task, return_exceptions=True
            )
            close = getattr(agent_iter, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:  # pylint: disable=broad-except
                    logger.debug(
                        "executor: failed to close agent stream", exc_info=True
                    )

    def _maybe_stamp_finished_at(self, event: Any) -> None:
        """Backfill ``finished_at`` on the reply's assistant message.

        agentscope only stamps ``Msg.finished_at`` via
        ``Msg.append_event(REPLY_END)``, a path that only agentscope's own
        app service invokes.  QwenPaw persists context through
        ``_save_to_context``, which never writes ``finished_at`` and pins
        ``created_at`` at the first saved segment of the reply.  The
        session snapshot therefore records no real completion time, and
        history rebuilt from the API falls back to ``created_at`` for the
        assistant completion time — under-reporting turns that include
        long tool calls (issue #6826).

        ``REPLY_END`` is emitted after every ``_save_to_context`` call of
        the reply, so stamping here lands in the session snapshot that is
        persisted at turn end.  Best-effort by design: any failure is
        logged and swallowed so the SSE stream is never affected.
        """
        try:
            from agentscope.event import EventType

            if getattr(event, "type", None) != EventType.REPLY_END.value:
                return
            context = getattr(
                getattr(self._agent, "state", None),
                "context",
                None,
            )
            if not context:
                return
            reply_id = getattr(event, "reply_id", None)
            target = None
            if reply_id is not None:
                for msg in reversed(context):
                    if getattr(msg, "id", None) == reply_id:
                        target = msg
                        break
            if target is None:
                last = context[-1]
                if getattr(last, "role", None) == "assistant":
                    target = last
            if target is None or getattr(target, "finished_at", None):
                return
            target.finished_at = (
                getattr(event, "created_at", None)
                or datetime.now().isoformat()
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "executor: failed to stamp finished_at on REPLY_END",
                exc_info=True,
            )


__all__ = ["AgentExecutor"]
