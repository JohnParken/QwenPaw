"""Configuration surface for the single general-purpose office Agent."""
from __future__ import annotations

import argparse
import json
from typing import Any

from .skills import SKILL_NAMES, discover_builtin_skills

PROVIDERS = ("tl", "openai")
DEFAULT_PROVIDER = "tl"


def runtime_inventory() -> dict[str, Any]:
    """Return the deployable inventory; no legacy provider is discoverable."""
    catalog = discover_builtin_skills()
    return {
        "providers": list(PROVIDERS),
        "default_provider": DEFAULT_PROVIDER,
        "skills": [
            {
                "id": name,
                "version": catalog[name].manifest.version,
                "digest": catalog[name].digest,
                "ready": catalog[name].readiness.ready,
                "checked": list(catalog[name].readiness.checked),
                "missing": list(catalog[name].readiness.missing),
                "required_python_packages": list(
                    catalog[name].manifest.required_python_packages
                ),
                "required_node_packages": list(
                    catalog[name].manifest.required_node_packages
                ),
                "allowed_commands": list(
                    catalog[name].manifest.allowed_commands
                ),
            }
            for name in SKILL_NAMES
        ],
        "skill_count": len(SKILL_NAMES),
        "office_renderer": False,
    }


def skill_summaries() -> list[dict[str, str]]:
    """Small model prompt payload; full instructions remain lazy-loaded."""
    return [
        {
            "id": item.manifest.id,
            "version": item.manifest.version,
            "digest": item.digest,
            "description": item.manifest.description,
        }
        for item in discover_builtin_skills().values()
        if item.available
    ]


def validate_runtime_inventory() -> dict[str, Any]:
    """Return inventory or raise when a production image is incomplete."""
    inventory = runtime_inventory()
    failures: list[str] = []
    if inventory["providers"] != list(PROVIDERS):
        failures.append("provider inventory mismatch")
    if inventory["skill_count"] != len(SKILL_NAMES):
        failures.append("skill inventory mismatch")
    for skill in inventory["skills"]:
        if not skill["digest"] or len(skill["digest"]) != 64:
            failures.append(f"{skill['id']}: invalid digest")
        if not skill["ready"]:
            missing = ", ".join(skill["missing"]) or "skill files"
            failures.append(f"{skill['id']}: {missing}")
    inventory["ready"] = not failures
    inventory["failures"] = failures
    if failures:
        raise RuntimeError("OFFICE_RUNTIME_NOT_READY: " + "; ".join(failures))
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect office Worker inventory")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    if args.require_ready:
        inventory = validate_runtime_inventory()
    else:
        inventory = runtime_inventory()
        inventory["ready"] = all(item["ready"] for item in inventory["skills"])
    print(json.dumps(inventory, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
