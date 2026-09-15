"""Local bundle fingerprint from current source and lock."""
import hashlib
from importlib import metadata
from pathlib import Path
import platform
import re
import subprocess
import sys

from .contracts import RuntimeIdentity


def local_identity() -> RuntimeIdentity:
    root = Path(__file__).resolve().parents[2]
    lock = (root / "cloud/requirements.lock").read_bytes()
    for name, expected in re.findall(
        r"^([A-Za-z0-9_.-]+)==([^\s;]+)", lock.decode(), re.MULTILINE
    ):
        if metadata.version(name) != expected:
            raise ValueError("INSTALLED_DEPENDENCY_MISMATCH: " + name)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    changes = hashlib.sha256(
        subprocess.check_output(
            [
                "git",
                "diff",
                "--binary",
                "HEAD",
                "--",
                "src",
                "pyproject.toml",
                ".python-version",
            ],
            cwd=root,
        )
    )
    for path in sorted((root / "src/qwenpaw_cloud").glob("*.py")):
        changes.update(path.name.encode())
        changes.update(path.read_bytes())
    for path in sorted((root / "cloud/migrations").glob("*.sql")):
        changes.update(path.name.encode())
        changes.update(path.read_bytes())
    version = ".".join(map(str, sys.version_info[:3]))
    return RuntimeIdentity(
        commit=commit,
        dirty_diff_digest=changes.hexdigest(),
        dependency_lock_digest=hashlib.sha256(lock).hexdigest(),
        python=version,
        arch=platform.machine(),
    )
