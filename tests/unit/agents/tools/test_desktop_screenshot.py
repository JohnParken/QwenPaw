# -*- coding: utf-8 -*-
"""Tests for desktop_screenshot cancel cleanup."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentscope.message import Base64Source
from PIL import Image

from qwenpaw.runtime.tool_registry import ToolRegistry
from qwenpaw.agents.tools.desktop_screenshot import (
    _tool_ok,
    _capture_macos_screencapture,
    desktop_screenshot,
)


@pytest.mark.asyncio
async def test_macos_screencapture_kills_proc_on_cancel(tmp_path):
    """Cancel/timeout must terminate the interactive screencapture process."""
    proc = MagicMock()
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=0)

    async def _raise_cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    with (
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        patch(
            "qwenpaw.tool_calls.cancellable_wait",
            new=_raise_cancelled,
        ),
    ):
        result = await _capture_macos_screencapture(
            str(tmp_path / "shot.png"),
            capture_window=True,
        )

    assert "timed out" in result.content[0].text.lower()
    proc.kill.assert_called_once()
    proc.wait.assert_awaited()


@pytest.mark.asyncio
async def test_desktop_screenshot_freezes_local_image(tmp_path):
    """A captured screenshot is immutable before tool return."""
    image_path = tmp_path / "desktop.png"

    def capture(path):
        Image.new("RGB", (2, 2), color="red").save(path)
        return _tool_ok(path, f"Desktop screenshot saved to {path}")

    with (
        patch("platform.system", return_value="Linux"),
        patch(
            "qwenpaw.agents.tools.desktop_screenshot._capture_mss",
            side_effect=capture,
        ),
    ):
        result = await desktop_screenshot(str(image_path))

    assert isinstance(result.content[0].source, Base64Source)
    first_data = result.content[0].source.data
    Image.new("RGB", (2, 2), color="blue").save(image_path)
    assert result.content[0].source.data == first_data


def test_desktop_screenshot_not_enabled_by_default() -> None:
    """Screenshot tool is opt-in and not surfaced without explicit allow."""
    registry = ToolRegistry()
    registry.register(desktop_screenshot._tool_descriptor)

    default_names = {desc.name for desc in registry.filter()}
    assert "desktop_screenshot" not in default_names

    allowed_names = {
        desc.name for desc in registry.filter(allowed={"desktop_screenshot"})
    }
    assert "desktop_screenshot" in allowed_names
