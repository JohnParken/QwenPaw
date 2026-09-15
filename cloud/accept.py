"""P0 acceptance entry, without paid models or production DBs."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # noqa: E402
from qwenpaw_cloud.identity import local_identity  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    if not args.evidence_root.is_absolute() or root.is_relative_to(ROOT):
        parser.error(
            "evidence root must be absolute and outside the repository"
        )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    if not os.getenv("P0_TEST_DSN"):
        parser.error(
            "set P0_TEST_DSN to an explicit dedicated PostgreSQL test database"
        )
    before = local_identity().model_dump()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["P0_NATIVE"] = "1" if args.native else "0"
    # This is the trusted test driver. Harness strips all credentials from
    # Worker children; TaskLauncher uses its own explicit Runner environment.
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "cloud/pytest.ini",
        "cloud/tests",
        "-q",
        "--basetemp",
        str(root / "cases"),
        "--junitxml",
        str(root / "junit.xml"),
    ]
    with (root / "pytest.log").open("w") as log:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    after = local_identity().model_dump()
    tree = ET.parse(root / "junit.xml")
    cases = tree.findall(".//testcase")
    counts = {
        "tests": len(cases),
        "failures": sum(
            c.find("failure") is not None or c.find("error") is not None
            for c in cases
        ),
        "skipped": sum(c.find("skipped") is not None for c in cases),
    }
    counts["passed"] = counts["tests"] - counts["failures"] - counts["skipped"]

    def gate(names):
        selected = [
            c
            for c in cases
            if any(
                n in c.attrib["name"] or n in c.attrib.get("classname", "")
                for n in names
            )
        ]
        if not selected:
            return "BLOCKED"
        if any(
            c.find("failure") is not None or c.find("error") is not None
            for c in selected
        ):
            return "FAIL"
        if any(c.find("skipped") is not None for c in selected):
            return "BLOCKED"
        return "PASS"

    gates = {
        "G1": "PASS"
        if before == after and before["python"] == "3.12.11"
        else "FAIL",
        "G2": gate(["test_configuration", "test_protocol"]),
        "G3": gate(
            [
                "test_same_user_sessions_restore_independently",
                "test_workspace_sessions_single_writer_preserves_refs",
                "test_p0_f04_kill_native",
            ]
        ),
        "G4": gate(["test_sandbox"]),
        "G5": gate(["test_protocol", "test_files", "test_session_restore"]),
    }
    if not args.native:
        gates["G3"] = gates["G4"] = "BLOCKED"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": "macos-dev",
        "runtime_identity": before,
        "identity_unchanged": before == after,
        "os": platform.platform(),
        "counts": counts,
        "gates": gates,
        "status": "PASS"
        if result.returncode == 0 and all(v == "PASS" for v in gates.values())
        else "FAIL",
        "P0-04": (
            "UNSUPPORTED: ReMe consistent snapshot adapter absent; "
            "report supplied"
        ),
        "linux_production_isolation": "UNSUPPORTED (P1)",
        "command": command,
        "evidence_root": str(root),
    }
    (root / "acceptance.json").write_text(json.dumps(report, indent=2))
    (ROOT / "cloud/reports/acceptance.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
