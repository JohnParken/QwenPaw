from pathlib import Path

import pytest

from qwenpaw_cloud.skills import SKILL_NAMES, SkillExecutionAudit, directory_digest, discover_builtin_skills, load_skill
from qwenpaw_cloud.office_runtime import (
    runtime_inventory,
    validate_runtime_inventory,
)


def test_catalog_is_exactly_allowlisted_and_safe():
    catalog = discover_builtin_skills(Path(__file__).parents[2] / "src" / "qwenpaw" / "agents" / "skills")
    assert tuple(catalog) == SKILL_NAMES
    assert all(skill.name in SKILL_NAMES for skill in catalog.values())
    assert all(not skill.directory or not any(p.is_symlink() for p in skill.directory.rglob("*")) for skill in catalog.values())


def test_full_load_exposes_digest_and_auxiliary_files():
    payload = load_skill("pdf", root=Path(__file__).parents[2] / "src" / "qwenpaw" / "agents" / "skills", full=True)
    assert len(payload["digest"]) == 64
    assert "content" in payload and "references" in payload


def test_digest_rejects_symlinks(tmp_path):
    (tmp_path / "SKILL.md").write_text("ok", encoding="utf-8")
    (tmp_path / "link").symlink_to(tmp_path / "SKILL.md")
    with pytest.raises(ValueError, match="symlink"):
        directory_digest(tmp_path)


def test_execution_audit_is_serializable():
    audit = SkillExecutionAudit("xlsx", "load", "a" * 64, False, "disabled")
    assert audit.to_dict()["allowed"] is False


def test_runtime_inventory_only_exposes_two_providers_and_six_skills():
    inventory = runtime_inventory()
    assert inventory["providers"] == ["tl", "openai"]
    assert inventory["default_provider"] == "tl"
    assert [item["id"] for item in inventory["skills"]] == list(SKILL_NAMES)


def test_production_inventory_gate_rejects_missing_dependency(monkeypatch):
    inventory = {
        "providers": ["tl", "openai"],
        "skill_count": 6,
        "skills": [
            {"id": name, "digest": "a" * 64, "ready": name != "pptx",
             "missing": ["node:pptxgenjs"] if name == "pptx" else []}
            for name in SKILL_NAMES
        ],
    }
    monkeypatch.setattr(
        "qwenpaw_cloud.office_runtime.runtime_inventory", lambda: inventory
    )
    with pytest.raises(RuntimeError, match="pptx: node:pptxgenjs"):
        validate_runtime_inventory()
