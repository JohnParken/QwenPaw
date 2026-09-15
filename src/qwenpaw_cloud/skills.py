"""Safe, read-only catalog for the six built-in office skills.

The catalog deliberately has no installation or execution side effects.  A
caller can use the short description for discovery and request the complete
instruction file only after checking the immutable directory digest.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from qwenpaw.security.skill_scanner import scan_skill_directory

SKILL_NAMES = ("writing", "docx", "xlsx", "pptx", "pdf", "data-analysis")
_CANDIDATES = {
    "docx": "docx-en", "xlsx": "xlsx-en", "pptx": "pptx-en", "pdf": "pdf-en",
}


@dataclass(frozen=True)
class DependencyReadiness:
    ready: bool
    checked: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    disabled_reason: str | None = None


@dataclass(frozen=True)
class SkillManifest:
    id: str
    name: str
    version: str
    digest: str = ""
    required_python_packages: tuple[str, ...] = ()
    required_node_packages: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    supported_mime_types: tuple[str, ...] = ()
    output_patterns: tuple[str, ...] = ()
    description: str = ""
    license: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillExecutionAudit:
    skill: str
    action: str
    digest: str
    allowed: bool
    reason: str = ""
    actor: str = "system"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {"skill": self.skill, "action": self.action, "digest": self.digest,
                "allowed": self.allowed, "reason": self.reason, "actor": self.actor,
                "created_at": self.created_at}


@dataclass(frozen=True)
class BuiltinSkill:
    name: str
    directory: Path | None
    manifest: SkillManifest
    digest: str = ""
    scan: Any = None
    readiness: DependencyReadiness = field(default_factory=lambda: DependencyReadiness(False, disabled_reason="not installed"))

    @property
    def available(self) -> bool:
        return self.directory is not None and self.readiness.ready


def _safe_path(root: Path, candidate: Path) -> Path | None:
    """Resolve a candidate while rejecting symlinks and traversal."""
    root = root.resolve()
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if candidate.is_symlink() or any(part.is_symlink() for part in [candidate, *candidate.parents] if part.exists()):
        return None
    return resolved if resolved.is_dir() else None


def directory_digest(directory: Path) -> str:
    """Digest every regular file, including relative names and metadata."""
    h = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed: {path}")
        if path.is_file():
            rel = path.relative_to(directory).as_posix().encode()
            data = path.read_bytes()
            h.update(len(rel).to_bytes(8, "big")); h.update(rel)
            h.update(len(data).to_bytes(8, "big")); h.update(data)
    return h.hexdigest()


def _frontmatter(text: str) -> SkillManifest:
    block = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    values: dict[str, Any] = {}
    if block:
        for line in block.group(1).splitlines():
            if ":" in line and not line[:1].isspace():
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return SkillManifest(
        id=values.get("id", values.get("name", "")),
        name=values.get("name", ""),
        version=values.get("version", ""),
        description=values.get("description", ""),
        license=values.get("license", ""), metadata=values,
    )


_BUILTIN_MANIFESTS: dict[str, dict[str, Any]] = {
    "writing": {"version": "1.0.0", "required_python_packages": (), "required_node_packages": (), "allowed_commands": (), "supported_mime_types": ("text/plain", "text/markdown"), "output_patterns": ("*.md", "*.txt")},
    "docx": {"version": "1.0.0", "required_python_packages": ("docx",), "required_node_packages": ("docx",), "allowed_commands": ("python", "node"), "supported_mime_types": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",), "output_patterns": ("*.docx",)},
    "xlsx": {"version": "1.0.0", "required_python_packages": ("openpyxl", "pandas", "xlsxwriter"), "required_node_packages": (), "allowed_commands": ("python",), "supported_mime_types": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv", "text/tab-separated-values"), "output_patterns": ("*.xlsx", "*.csv", "*.tsv")},
    "pptx": {"version": "1.0.0", "required_python_packages": ("pptx",), "required_node_packages": ("pptxgenjs",), "allowed_commands": ("python", "node"), "supported_mime_types": ("application/vnd.openxmlformats-officedocument.presentationml.presentation",), "output_patterns": ("*.pptx",)},
    "pdf": {"version": "1.0.0", "required_python_packages": ("pypdf", "pdfplumber", "reportlab"), "required_node_packages": (), "allowed_commands": ("python",), "supported_mime_types": ("application/pdf",), "output_patterns": ("*.pdf",)},
    "data-analysis": {"version": "1.0.0", "required_python_packages": ("pandas", "matplotlib"), "required_node_packages": (), "allowed_commands": ("python",), "supported_mime_types": ("text/csv", "text/tab-separated-values", "application/json", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), "output_patterns": ("*.csv", "*.json", "*.png")},
}


def _manifest(name: str, frontmatter: SkillManifest | None = None, *, digest: str = "") -> SkillManifest:
    values = dict(_BUILTIN_MANIFESTS[name])
    values.update(id=name, name=name, digest=digest)
    if frontmatter:
        values["description"] = frontmatter.description
        values["license"] = frontmatter.license
        values["metadata"] = frontmatter.metadata
    return SkillManifest(**values)


def _readiness(directory: Path, manifest: SkillManifest) -> DependencyReadiness:
    checked: list[str] = []; missing: list[str] = []
    for module in manifest.required_python_packages:
        checked.append(f"python:{module}")
        if importlib.util.find_spec(module.split(".")[0].replace("-", "_")) is None: missing.append(f"python:{module}")
    for package in manifest.required_node_packages:
        checked.append(f"node:{package}")
        node = shutil.which("node")
        if node is None:
            missing.append(f"node:{package}")
            continue
        probe = subprocess.run(
            [node, "-e", f"require.resolve({package!r})"],
            cwd=directory,
            check=False,
            capture_output=True,
            env={
                **os.environ,
                "NODE_PATH": os.environ.get(
                    "QWENPAW_OFFICE_NODE_PATH",
                    os.environ.get("NODE_PATH", ""),
                ),
            },
            timeout=10,
        )
        if probe.returncode:
            missing.append(f"node:{package}")
    for command in manifest.allowed_commands:
        checked.append(f"command:{command}")
        if shutil.which(command) is None: missing.append(f"command:{command}")
    return DependencyReadiness(not missing, tuple(checked), tuple(missing),
                               None if not missing else "missing dependencies: " + ", ".join(missing))


def _find_skill(name: str, roots: Iterable[Path]) -> Path | None:
    candidate_name = _CANDIDATES.get(name, name)
    for root in roots:
        for candidate in (root / candidate_name, root / name):
            safe = _safe_path(root, candidate)
            if safe and (safe / "SKILL.md").is_file(): return safe
    return None


def discover_builtin_skills(root: str | Path | None = None) -> dict[str, BuiltinSkill]:
    roots = [Path(root)] if root else [
        Path(__file__).resolve().parent / "builtin_skills",
        Path(__file__).resolve().parents[1] / "qwenpaw" / "agents" / "skills",
    ]
    result: dict[str, BuiltinSkill] = {}
    for name in SKILL_NAMES:
        directory = _find_skill(name, roots)
        if directory is None:
            result[name] = BuiltinSkill(name, None, _manifest(name), readiness=DependencyReadiness(False, disabled_reason="skill files are not bundled"))
            continue
        try:
            digest = directory_digest(directory)
            frontmatter = _frontmatter((directory / "SKILL.md").read_text(encoding="utf-8"))
            if frontmatter.name != name:
                raise ValueError(f"frontmatter name mismatch: {frontmatter.name!r}")
            manifest = _manifest(name, frontmatter, digest=digest)
            scan = scan_skill_directory(directory, skill_name=name, block=False)
            readiness = _readiness(directory, manifest)
            result[name] = BuiltinSkill(name, directory, manifest, digest, scan, readiness)
        except (OSError, ValueError) as exc:
            result[name] = BuiltinSkill(name, directory, _manifest(name), readiness=DependencyReadiness(False, disabled_reason=str(exc)))
    return result


def load_skill(name: str, *, root: str | Path | None = None, full: bool = False) -> dict[str, Any]:
    if name not in SKILL_NAMES: raise KeyError(f"unknown built-in skill: {name}")
    skill = discover_builtin_skills(root)[name]
    payload = {"name": name, "manifest": skill.manifest, "digest": skill.digest,
               "readiness": skill.readiness, "available": skill.available}
    if full and skill.directory:
        payload["content"] = (skill.directory / "SKILL.md").read_text(encoding="utf-8")
        payload["scripts"] = sorted(p.relative_to(skill.directory).as_posix() for p in (skill.directory / "scripts").rglob("*") if p.is_file()) if (skill.directory / "scripts").exists() else []
        payload["references"] = sorted(p.relative_to(skill.directory).as_posix() for p in (skill.directory / "references").rglob("*") if p.is_file()) if (skill.directory / "references").exists() else []
    return payload


__all__ = ["SKILL_NAMES", "DependencyReadiness", "SkillManifest", "SkillExecutionAudit", "BuiltinSkill", "directory_digest", "discover_builtin_skills", "load_skill"]
