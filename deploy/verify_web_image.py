"""Build and exercise the real Web image using an isolated Docker container.

No host ports, user configuration, model credentials or persistent volumes
are mounted. Logs and the result are saved even when a check fails.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import uuid

PROBE = r"""
import json, re, time
from urllib.request import urlopen
from urllib.parse import urljoin
base = "http://127.0.0.1:8088"
deadline = time.monotonic() + 240
last_error = "not ready"
while time.monotonic() < deadline:
    try:
        with urlopen(base + "/api/healthz", timeout=5) as response:
            health = json.load(response)
        assert health["status"] == "ok", health
        assert "default" in health["agents_loaded"], health
        break
    except Exception as exc:
        last_error = str(exc)
        time.sleep(1)
else:
    raise RuntimeError(last_error)
with urlopen(base + "/api/version", timeout=10) as response:
    version = json.load(response)
assert version.get("version"), version
with urlopen(base + "/", timeout=10) as response:
    html = response.read().decode()
scripts = re.findall(r'<script[^>]+src=["\x27]([^"\x27]+)', html)
assert scripts, "Console HTML contains no built JavaScript assets"
for script in scripts:
    with urlopen(urljoin(base + "/", script), timeout=10) as response:
        assert "javascript" in response.headers.get("Content-Type", ""), script
        assert response.read(), script
print(json.dumps({"health": health, "version": version, "scripts": scripts}))
"""

BROWSER_PROBE = r"""
import sys
from playwright.sync_api import sync_playwright
from qwenpaw.config.config import ToolsConfig
assert sys.version_info[:3] == (3, 12, 13), sys.version
assert not ToolsConfig().builtin_tools["desktop_screenshot"].enabled
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path="/usr/bin/chromium", headless=True,
        args=["--no-sandbox"],
    )
    page = browser.new_page()
    page.goto("data:text/html,<title>web-image-ready</title><h1>Ready</h1>")
    assert page.title() == "web-image-ready"
    assert page.screenshot().startswith(b"\x89PNG"), "Screenshot failed"
    browser.close()
print("Python version, tool defaults and headless Chromium passed")
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--build-arg", action="append", default=[])
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    name = "qwenpaw-web-verify-" + uuid.uuid4().hex[:12]
    image = name + ":local"
    result = {"status": "failed", "image": image, "checks": []}
    created = False

    def run(command, log_name, *, timeout=600):
        with (evidence / log_name).open("w") as log:
            subprocess.run(
                command,
                cwd=repo,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=timeout,
            )

    try:
        if not shutil.which("docker"):
            raise RuntimeError(
                "Docker CLI is unavailable; no image was tested"
            )
        run(["docker", "info"], "docker-info.log", timeout=30)
        command = [
            "docker",
            "build",
            "--progress=plain",
            "-f",
            "deploy/Dockerfile.web",
            "-t",
            image,
        ]
        for value in args.build_arg:
            command.extend(["--build-arg", value])
        run(command + ["."], "build.log", timeout=3600)
        result["checks"].append("image_build")
        run(
            [
                "docker",
                "create",
                "--init",
                "--name",
                name,
                "-e",
                "QWENPAW_AUTH_ENABLED=false",
                image,
            ],
            "create.log",
        )
        created = True
        run(["docker", "start", name], "start.log")
        run(["docker", "exec", name, "python", "-c", PROBE], "startup.log")
        result["checks"].append("agent_ready_and_console_assets")
        run(
            ["docker", "exec", name, "python", "-c", BROWSER_PROBE],
            "browser.log",
        )
        result["checks"].append("python_tools_and_browser")
        run(["docker", "stop", "--time", "30", name], "stop.log")
        state = json.loads(
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{json .State}}", name],
                text=True,
            )
        )
        assert not state["OOMKilled"] and state["ExitCode"] in (0, 143), state
        result["shutdown"] = state
        run(["docker", "start", name], "restart.log")
        run(["docker", "exec", name, "python", "-c", PROBE], "restored.log")
        result["checks"].append("graceful_stop_and_existing_config_restart")
        result["status"] = "passed"
    except Exception as exc:
        result["error"] = str(exc)
        raise
    finally:
        if created:
            with (evidence / "container.log").open("w") as log:
                subprocess.run(
                    ["docker", "logs", name],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                )
            subprocess.run(
                ["docker", "stop", "--time", "30", name],
                capture_output=True,
                timeout=45,
            )
            subprocess.run(
                ["docker", "rm", name], capture_output=True, timeout=30
            )
        (evidence / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
