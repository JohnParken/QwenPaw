import json
from pathlib import Path

import pytest

from qwenpaw_cloud.office_agent import OfficeAgentBridge, OfficeAgentError
from qwenpaw_cloud.sandbox import PathMap


class FakeModel:
    def __new__(cls):
        from agentscope.model import ChatModelBase

        class Model(ChatModelBase):
            def __init__(self):
                from types import SimpleNamespace

                super().__init__(
                    credential=None,
                    model="fake-office",
                    parameters=ChatModelBase.Parameters(),
                    stream=False,
                    max_retries=0,
                    context_size=32768,
                )
                self.pending = True
                self.formatter = SimpleNamespace(supported_input_media_types=())

            async def _call_api(self, model_name, messages, tools=None, **kwargs):
                from agentscope.message import TextBlock, ToolCallBlock
                from agentscope.model import ChatResponse

                if self.pending:
                    self.pending = False
                    return ChatResponse(
                        content=[ToolCallBlock(
                            id="write-1",
                            name="write_text",
                            input=json.dumps({
                                "path": "output/result.txt",
                                "content": "quarterly result",
                            }),
                        )],
                        is_last=True,
                    )
                return ChatResponse(
                    content=[TextBlock(text="Created result.txt")],
                    is_last=True,
                )

        return Model()


def _paths(tmp_path):
    root = tmp_path / "attempt"
    for name in ("input", "workspace", "output", "tmp", "state"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return PathMap(root)


@pytest.mark.asyncio
async def test_real_agent_loop_uses_attempt_scoped_tool(tmp_path):
    bridge = OfficeAgentBridge(
        _paths(tmp_path),
        {"provider": "tl", "model": "fake", "base_url": "http://model"},
        model_factory=lambda _: FakeModel(),
    )
    result = await bridge.run("Create a text report")
    assert (bridge.paths.root / "output/result.txt").read_text() == "quarterly result"
    assert "Created result.txt" in result["text"]
    assert result["conversation"]["state"]["context"]


def test_path_and_skill_boundaries(tmp_path):
    bridge = OfficeAgentBridge(
        _paths(tmp_path),
        {"provider": "tl", "model": "fake", "base_url": "http://model"},
        model_factory=lambda _: object(),
    )
    with pytest.raises(OfficeAgentError, match="PATH_OUTSIDE_ATTEMPT"):
        bridge._path("../secret")
    tools = {tool.name: tool for tool in bridge._tools()}
    assert set(tools) == {
        "list_files", "read_text", "write_text", "copy_file",
        "load_builtin_skill", "run_skill_script",
    }
