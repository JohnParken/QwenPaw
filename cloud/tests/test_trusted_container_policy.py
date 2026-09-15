from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw_cloud.office_agent import OfficeAgentBridge, OfficeAgentError
from qwenpaw_cloud.sandbox import (
    PathMap,
    ShellPolicyError,
    TrustedContainerPolicy,
)
from qwenpaw_cloud.skills import directory_digest


def _paths(tmp_path):
    root = tmp_path / "attempt"
    for name in ("input", "workspace", "output", "tmp", "state"):
        (root / name).mkdir(parents=True)
    return PathMap(root)


def _skill(tmp_path, *, commands=("python",)):
    directory = tmp_path / "skill"
    (directory / "scripts").mkdir(parents=True)
    script = directory / "scripts" / "report.py"
    script.write_text("print('ok')")
    return SimpleNamespace(
        directory=directory,
        manifest=SimpleNamespace(id="report", allowed_commands=commands),
        available=True,
        digest=directory_digest(directory),
    )


def test_trusted_policy_allows_only_declared_skill_script(tmp_path):
    paths = _paths(tmp_path)
    skill = _skill(tmp_path)
    policy = TrustedContainerPolicy(paths)

    command = policy.validate(["python", "scripts/report.py", "output/result.csv"], skill)
    assert command.argv[:2] == ("python", "scripts/report.py")
    assert command.cwd == skill.directory.resolve()

    for argv in (
        ["bash", "scripts/report.py"],
        ["sh", "-c", "echo bad"],
        ["python", "-c", "print(1)"],
        ["python", "-m", "module"],
        ["node", "-e", "console.log(1)"],
        ["pip", "install", "x"],
        ["npm", "run", "x"],
        ["curl", "https://example.invalid"],
        ["wget", "https://example.invalid"],
        ["ssh", "host"],
        ["nc", "host", "1"],
        ["python", "other.py"],
        ["python", "../report.py"],
        ["python", "scripts/report.py", "/etc/passwd"],
        ["python", "scripts/report.py", "../output/result.csv"],
        ["/usr/bin/python", "scripts/report.py"],
    ):
        with pytest.raises(ShellPolicyError):
            policy.validate(argv, skill)


@pytest.mark.asyncio
async def test_sandboxed_mode_fails_closed_without_backend(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    skill = _skill(tmp_path)
    bridge = OfficeAgentBridge(
        paths,
        {"provider": "tl", "model": "fake", "base_url": "http://model"},
        model_factory=lambda _: object(),
        shell_mode="sandboxed",
    )
    bridge.catalog = {"data-analysis": skill}
    monkeypatch.setattr("qwenpaw_cloud.office_agent.shutil.which", lambda _: None)
    monkeypatch.setattr("qwenpaw_cloud.office_agent.Path.exists", lambda _: False)
    run = {tool.name: tool for tool in bridge._tools()}["run_skill_script"]

    with pytest.raises(OfficeAgentError, match="SHELL_SANDBOX_UNAVAILABLE"):
        await run(skill="data-analysis", argv=["python", "scripts/report.py"])
