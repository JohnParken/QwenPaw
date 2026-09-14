# -*- coding: utf-8 -*-
"""Prompt contracts for explicit paid-media call ceilings."""

from __future__ import annotations

import pytest

from services.file_agent_runtime.prompts import load_file_agent_prompt

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("prompt_name", "required_contracts"),
    (
        (
            "creator_agent.system",
            (
                "显式媒体预算覆盖默认资产拆分",
                "共享设计图数 + R2V Element 数",
                "不能用共享设计 Artifact 冒充",
                "单张共享设计图预算例外",
                "不得建立 cast lineup 或创建第二个可调度视觉节点",
                "全部图片调用上限”不足以覆盖这些 storyboard",
            ),
        ),
    ),
)
def test_prompts_preserve_explicit_paid_image_ceiling(
    prompt_name: str,
    required_contracts: tuple[str, ...],
) -> None:
    prompt = load_file_agent_prompt(prompt_name)
    assert all(contract in prompt for contract in required_contracts)
