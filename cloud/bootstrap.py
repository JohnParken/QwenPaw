"""Create private P0 role configs and synthetic test capabilities."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # noqa: E402
from qwenpaw_cloud.auth import Auth  # noqa: E402
from qwenpaw_cloud.__main__ import Config  # noqa: E402
from qwenpaw_cloud.identity import local_identity  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--executor", choices=["fixture", "native"], default="fixture"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if (
        not args.root.is_absolute()
        or root.is_relative_to(ROOT)
        or root == Path.home()
        or root == Path("/")
    ):
        parser.error(
            "choose a new private absolute root outside source and real HOME"
        )
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    dsn = os.environ.get("P0_TEST_DSN")
    if not dsn:
        parser.error("set P0_TEST_DSN; no database is guessed")
    key = secrets.token_hex(32)
    keypath = root / "service-key"
    keypath.write_text(key)
    keypath.chmod(0o600)
    auth = Auth(key)
    identity = local_identity().model_dump()
    for role in ("core", "files"):
        value = {
            "role": role,
            "port": 18080 if role == "core" else 18081,
            "root": str(root / role),
            "dsn": dsn,
            "signing_key_file": str(keypath),
            "runtime_identity": identity,
        }
        Config.model_validate(value)
        (root / (role + ".json")).write_text(json.dumps(value, indent=2))
    for wid in ("worker-a", "worker-b"):
        token = root / (wid + ".token")
        token.write_text(auth.issue("worker", wid, "core"))
        token.chmod(0o600)
        workroot = root / wid
        workroot.mkdir(mode=0o700)
        value = {
            "role": "worker",
            "worker_id": wid,
            "root": str(workroot),
            "token_file": str(token),
            "runtime_identity": identity,
            "executor": args.executor,
        }
        Config.model_validate(value)
        (root / (wid + ".json")).write_text(json.dumps(value, indent=2))
    for role, claims in [
        ("bff", {"tenant_id": "t1", "user_id": "u1"}),
        ("coordinator", {}),
    ]:
        token = root / (role + ".token")
        token.write_text(auth.issue(role, "p0-test", "core", **claims))
        token.chmod(0o600)
    print("Private role configs created at " + str(root))


if __name__ == "__main__":
    main()
