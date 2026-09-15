"""Immutable registry for the built-in Office skills.

The Office runtime deliberately has no install or update path.  A bundle is
the files shipped with QwenPaw, its lock file, and the dependency probes run
at discovery time.  Consumers can use :func:`load_bundle` for a fail-closed
registry or :func:`verify_bundle` when they want structured diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping


SKILL_NAMES = (
    "writing",
    "docx",
    "xlsx",
    "pptx",
    "pdf",
    "bi-analysis",
)
BUNDLE_LOCK_NAME = "bundle.lock.json"
BUNDLE_SCHEMA_VERSION = 1
_SHA256_LENGTH = 64
_NATIVE_SOURCE_SKILLS = frozenset({"docx", "xlsx", "pptx", "pdf"})
OFFICE_TOOL_NAMES = frozenset({
    "list_files", "read_file", "read_text", "write_file", "write_text",
    "copy_file", "create_docx", "create_xlsx", "create_pptx", "create_pdf",
    "read_docx", "read_xlsx", "read_pptx", "read_pdf", "read_table",
    "profile_table", "aggregate_table", "pivot_table", "create_chart",
    "publish_artifact", "run_skill_script",
})


class BundleError(ValueError):
    """Base error raised for an invalid or unavailable skill bundle."""


class BundleIntegrityError(BundleError):
    """Raised when a bundle differs from its immutable lock file."""


class UnknownSkillError(BundleError, KeyError):
    """Raised when a caller asks for a skill outside the fixed allowlist."""


@dataclass(frozen=True)
class DependencyReadiness:
    """Dependency probe result for one skill."""

    ready: bool
    checked: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    disabled_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.ready

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checked": list(self.checked),
            "missing": list(self.missing),
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class SkillManifest:
    """Validated manifest metadata for an immutable skill."""

    id: str
    name: str
    version: str
    description: str = ""
    entrypoints: tuple[str, ...] = ()
    python_packages: tuple[str, ...] = ()
    node_packages: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    supported_mime_types: tuple[str, ...] = ()
    output_patterns: tuple[str, ...] = ()
    runtime: str = "python"
    network: str = "disabled"
    timeout_seconds: float = 60.0
    max_output_bytes: int = 65536
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def required_python_packages(self) -> tuple[str, ...]:
        return self.python_packages

    @property
    def required_node_packages(self) -> tuple[str, ...]:
        return self.node_packages

    @property
    def allowed_scripts(self) -> tuple[str, ...]:
        return self.entrypoints

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "entrypoints": list(self.entrypoints),
            "python_packages": list(self.python_packages),
            "node_packages": list(self.node_packages),
            "allowed_commands": list(self.allowed_commands),
            "allowed_tools": list(self.allowed_tools),
            "supported_mime_types": list(self.supported_mime_types),
            "output_patterns": list(self.output_patterns),
            "runtime": self.runtime,
            "network": self.network,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "metadata": dict(self.metadata),
        }

    to_dict = as_dict

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, skill_name: str | None = None) -> "SkillManifest":
        if not isinstance(value, Mapping):
            raise BundleError("manifest must be a JSON object")

        def text(key: str, default: str = "") -> str:
            raw = value.get(key, default)
            if not isinstance(raw, str) or not raw.strip():
                if default and key not in value:
                    return default
                raise BundleError(f"manifest field {key!r} must be a non-empty string")
            return raw.strip()

        name = text("name", skill_name or "")
        if skill_name is not None and name != skill_name:
            raise BundleError(f"manifest name mismatch: {name!r} != {skill_name!r}")
        identifier = text("id", name)
        if identifier != name:
            raise BundleError(f"manifest id mismatch: {identifier!r} != {name!r}")
        version = text("version")
        if any(ch in version for ch in "\r\n"):
            raise BundleError("manifest version contains a newline")

        def strings(key: str) -> tuple[str, ...]:
            raw = value.get(key, ())
            if raw is None:
                return ()
            if isinstance(raw, str) or not isinstance(raw, Iterable):
                raise BundleError(f"manifest field {key!r} must be an array of strings")
            result: list[str] = []
            for item in raw:
                if not isinstance(item, str) or not item.strip():
                    raise BundleError(f"manifest field {key!r} contains a non-string")
                result.append(item.strip())
            if len(set(result)) != len(result):
                raise BundleError(f"manifest field {key!r} contains duplicates")
            return tuple(result)

        raw_timeout = value.get("timeout_seconds", 60.0)
        raw_output = value.get("max_output_bytes", 65536)
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)) or raw_timeout <= 0:
            raise BundleError("manifest timeout_seconds must be positive")
        if isinstance(raw_output, bool) or not isinstance(raw_output, int) or raw_output <= 0:
            raise BundleError("manifest max_output_bytes must be positive")
        network = value.get("network", "disabled")
        if network not in {"disabled", "none", "off"}:
            raise BundleError("skill network policy must be disabled")

        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise BundleError("manifest metadata must be an object")
        dependencies = value.get("dependencies", {})
        if dependencies is not None and not isinstance(dependencies, Mapping):
            raise BundleError("manifest dependencies must be an object")
        py_fallback = dependencies.get("python", ()) if isinstance(dependencies, Mapping) else ()
        node_fallback = dependencies.get("node", ()) if isinstance(dependencies, Mapping) else ()
        commands_fallback = dependencies.get("commands", ()) if isinstance(dependencies, Mapping) else ()

        def strings_with_fallback(key: str, fallback: Any) -> tuple[str, ...]:
            return strings(key) if key in value else SkillManifest.from_dict({**value, key: fallback}, skill_name=skill_name).as_dict()[key]  # pragma: no cover

        # Parse dependency aliases directly; the recursive helper above is
        # intentionally not used to avoid constructing the object twice.
        def dependency_strings(key: str, fallback: Any) -> tuple[str, ...]:
            raw = value[key] if key in value else fallback
            if raw is None:
                return ()
            if isinstance(raw, str) or not isinstance(raw, Iterable):
                raise BundleError(f"manifest field {key!r} must be an array of strings")
            values = tuple(str(item).strip() for item in raw)
            if any(not item for item in values) or len(set(values)) != len(values):
                raise BundleError(f"manifest field {key!r} contains invalid values")
            return values

        allowed_tools = dependency_strings("allowed_tools", ())
        unknown_tools = set(allowed_tools) - OFFICE_TOOL_NAMES
        if unknown_tools:
            raise BundleError(
                "manifest contains unknown typed tools: "
                + ", ".join(sorted(unknown_tools)),
            )

        return cls(
            id=identifier,
            name=name,
            version=version,
            description=str(value.get("description", "")),
            entrypoints=dependency_strings("entrypoints", ()),
            python_packages=dependency_strings("python_packages", py_fallback),
            node_packages=dependency_strings("node_packages", node_fallback),
            allowed_commands=dependency_strings("allowed_commands", commands_fallback),
            allowed_tools=allowed_tools,
            supported_mime_types=strings("supported_mime_types"),
            output_patterns=strings("output_patterns"),
            runtime=str(value.get("runtime", "python")),
            network="disabled",
            timeout_seconds=float(raw_timeout),
            max_output_bytes=raw_output,
            metadata=dict(metadata),
        )


@dataclass(frozen=True)
class BuiltinSkill:
    """One discovered skill and its immutable validation evidence."""

    name: str
    directory: Path | None
    manifest: SkillManifest
    digest: str = ""
    readiness: DependencyReadiness = field(
        default_factory=lambda: DependencyReadiness(False, disabled_reason="not installed")
    )
    errors: tuple[str, ...] = ()

    @property
    def path(self) -> Path | None:
        return self.directory

    @property
    def available(self) -> bool:
        return self.directory is not None and not self.errors and self.readiness.ready

    @property
    def ready(self) -> bool:
        return self.available

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.name,
            "name": self.name,
            "version": self.manifest.version,
            "digest": self.digest,
            "available": self.available,
            "ready": self.readiness.ready,
            "readiness": self.readiness.as_dict(),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class BundleLock:
    """The version and per-skill digests recorded in ``bundle.lock.json``."""

    schema_version: int
    skills: Mapping[str, str]
    bundle: str = "qwenpaw-office"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bundle": self.bundle,
            "skills": {
                name: {"sha256": digest}
                for name, digest in sorted(self.skills.items())
            },
        }


@dataclass(frozen=True)
class BundleValidation:
    """Structured, non-throwing result from :func:`verify_bundle`."""

    root: Path
    valid: bool
    errors: tuple[str, ...] = ()
    skills: Mapping[str, BuiltinSkill] = field(default_factory=dict)
    lock: BundleLock | None = None

    @property
    def ok(self) -> bool:
        return self.valid

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "valid": self.valid,
            "errors": list(self.errors),
            "skills": {name: skill.as_dict() for name, skill in self.skills.items()},
            "lock": self.lock.as_dict() if self.lock else None,
        }


@dataclass(frozen=True)
class SkillBundle(Mapping[str, BuiltinSkill]):
    """Read-only mapping of the six skills in an immutable bundle."""

    root: Path
    skills: Mapping[str, BuiltinSkill]
    lock: BundleLock
    validation: BundleValidation

    def __getitem__(self, key: str) -> BuiltinSkill:
        return self.skills[key]

    def __iter__(self) -> Iterator[str]:
        return iter(SKILL_NAMES)

    def __len__(self) -> int:
        return len(SKILL_NAMES)

    @property
    def available(self) -> bool:
        return self.validation.valid

    @property
    def ready(self) -> bool:
        return self.validation.valid and all(skill.available for skill in self.skills.values())

    @property
    def runtime_skill_directories(self) -> Mapping[str, Path]:
        """Return the immutable directory used to load each runtime Skill."""
        return MappingProxyType({
            name: skill.directory
            for name, skill in self.items()
            if skill.directory is not None
        })

    def values(self):  # type: ignore[no-untyped-def]
        return (self.skills[name] for name in SKILL_NAMES)

    def items(self):  # type: ignore[no-untyped-def]
        return ((name, self.skills[name]) for name in SKILL_NAMES)


def _default_root() -> Path:
    return Path(__file__).resolve().parent / "skills"


def _path_is_safe(root: Path, candidate: Path, *, must_exist: bool = True) -> bool:
    try:
        root_real = root.resolve(strict=True)
        candidate_real = candidate.resolve(strict=must_exist)
        candidate_real.relative_to(root_real)
    except (OSError, ValueError):
        return False
    try:
        current = root
        for part in candidate.relative_to(root).parts:
            current = current / part
            if current.is_symlink():
                return False
    except (ValueError, OSError):
        return False
    return True


def _iter_regular_files(directory: Path) -> Iterator[Path]:
    if directory.is_symlink():
        raise BundleIntegrityError(f"symlink is not allowed: {directory}")
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise BundleIntegrityError(f"symlink is not allowed: {path}")
        if path.is_file():
            if not _path_is_safe(directory, path):
                raise BundleIntegrityError(f"path escapes skill directory: {path}")
            yield path
        elif not path.is_dir():
            raise BundleIntegrityError(f"unsupported bundle entry: {path}")


def _update_directory_digest(
    digest: "hashlib._Hash",
    directory: Path,
    *,
    prefix: str = "",
) -> None:
    for path in _iter_regular_files(directory):
        relative = path.relative_to(directory).as_posix()
        if prefix:
            relative = f"{prefix}/{relative}"
        encoded_relative = relative.encode("utf-8")
        data = path.read_bytes()
        digest.update(len(encoded_relative).to_bytes(8, "big"))
        digest.update(encoded_relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)


def directory_digest(directory: str | Path) -> str:
    """Compute a deterministic SHA-256 over relative names and file bytes."""
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise BundleIntegrityError(f"not a regular skill directory: {directory}")
    digest = hashlib.sha256()
    _update_directory_digest(digest, directory)
    return digest.hexdigest()


def _skill_digest(policy_directory: Path, runtime_directory: Path) -> str:
    """Digest policy metadata and the distinct runtime source exactly once."""
    policy_directory = policy_directory.resolve(strict=True)
    runtime_directory = runtime_directory.resolve(strict=True)
    if policy_directory == runtime_directory:
        return directory_digest(policy_directory)
    digest = hashlib.sha256()
    _update_directory_digest(digest, policy_directory, prefix="policy")
    _update_directory_digest(digest, runtime_directory, prefix="runtime")
    return digest.hexdigest()


def _load_lock(root: Path) -> BundleLock:
    path = root / BUNDLE_LOCK_NAME
    if not _path_is_safe(root, path):
        raise BundleIntegrityError("bundle lock path is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleIntegrityError(f"invalid {BUNDLE_LOCK_NAME}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise BundleIntegrityError("bundle lock must be a JSON object")
    schema = value.get("schema_version", value.get("version"))
    if schema != BUNDLE_SCHEMA_VERSION:
        raise BundleIntegrityError("unsupported bundle lock schema")
    skills_value = value.get("skills")
    if not isinstance(skills_value, Mapping):
        raise BundleIntegrityError("bundle lock skills must be an object")
    skills: dict[str, str] = {}
    for name, entry in skills_value.items():
        if name not in SKILL_NAMES:
            raise BundleIntegrityError(f"unapproved skill in lock: {name}")
        if isinstance(entry, str):
            digest = entry
        elif isinstance(entry, Mapping):
            digest = entry.get("sha256", entry.get("digest", ""))
        else:
            digest = ""
        if not isinstance(digest, str) or len(digest) != _SHA256_LENGTH:
            raise BundleIntegrityError(f"invalid digest for skill: {name}")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise BundleIntegrityError(f"invalid digest for skill: {name}") from exc
        skills[name] = digest.lower()
    if set(skills) != set(SKILL_NAMES):
        missing = sorted(set(SKILL_NAMES) - set(skills))
        raise BundleIntegrityError(f"bundle lock is missing skills: {', '.join(missing)}")
    return BundleLock(int(schema), skills, str(value.get("bundle", "qwenpaw-office")))


def _load_manifest(skill_dir: Path, name: str) -> SkillManifest:
    path = skill_dir / "manifest.json"
    if not _path_is_safe(skill_dir, path):
        raise BundleIntegrityError("manifest path is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleIntegrityError(f"invalid manifest for {name}: {exc}") from exc
    return SkillManifest.from_dict(value, skill_name=name)


def _native_source_root() -> Path:
    """Return the packaged source directory for native Office skills."""
    return Path(__file__).resolve().parent.parent / "agents" / "skills"


def _native_source_reference(manifest: SkillManifest) -> str | None:
    """Read the explicit native source reference from a policy manifest."""
    for key in ("native_source", "source_dir", "source"):
        value = manifest.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _validated_native_source(candidate: Path, name: str) -> Path:
    """Validate one native source candidate without permitting escapes."""
    if candidate.name == "SKILL.md":
        candidate = candidate.parent
    native_root = _native_source_root()
    if not _path_is_safe(native_root, candidate):
        raise BundleIntegrityError(f"native source path is unsafe: {name}")
    resolved = candidate.resolve(strict=True)
    if resolved.name != f"{name}-zh":
        raise BundleIntegrityError(f"native source name mismatch: {name}")
    if not resolved.is_dir() or resolved.is_symlink():
        raise BundleIntegrityError(f"native source is not a directory: {name}")
    return resolved


def _runtime_skill_directory(
    root: Path,
    policy_dir: Path,
    name: str,
    manifest: SkillManifest,
) -> Path:
    """Resolve the immutable runtime tree, keeping local policies separate."""
    if name not in _NATIVE_SOURCE_SKILLS:
        return policy_dir

    reference = _native_source_reference(manifest)
    candidates: list[Path] = []
    if reference:
        raw = Path(reference)
        if raw.is_absolute():
            candidates.append(raw)
        else:
            if reference.startswith("src/"):
                candidates.append(Path(__file__).resolve().parents[3] / raw)
            candidates.extend((policy_dir / raw, root / raw))
        for candidate in candidates:
            if candidate.exists():
                return _validated_native_source(candidate, name)

    # A copied policy bundle may not retain the repository-relative source
    # reference.  Resolve that case against the packaged native tree while
    # retaining the same name and containment checks above.
    return _validated_native_source(
        _native_source_root() / f"{name}-zh",
        name,
    )


def _probe_dependencies(directory: Path, manifest: SkillManifest) -> DependencyReadiness:
    checked: list[str] = []
    missing: list[str] = []
    for package in manifest.python_packages:
        module = package.split(".", 1)[0].replace("-", "_")
        checked.append(f"python:{package}")
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            found = False
        if not found:
            missing.append(f"python:{package}")
    for package in manifest.node_packages:
        checked.append(f"node:{package}")
        if not (directory / "node_modules" / package).exists():
            missing.append(f"node:{package}")
    required_commands = manifest.metadata.get("required_commands", ())
    if isinstance(required_commands, str):
        required_commands = (required_commands,)
    for command in (*manifest.allowed_commands, *required_commands):
        if not isinstance(command, str) or not command:
            missing.append("command:<invalid>")
            continue
        checked.append(f"command:{command}")
        command_found = shutil.which(command)
        if command in {"libreoffice", "soffice"}:
            command_found = command_found or shutil.which("libreoffice") or shutil.which("soffice")
        if command_found is None and command not in {"python", "python3"}:
            missing.append(f"command:{command}")
        elif command in {"python", "python3"} and shutil.which(command) is None:
            import sys

            if not Path(sys.executable).is_file():
                missing.append(f"command:{command}")
    return DependencyReadiness(
        not missing,
        tuple(checked),
        tuple(missing),
        None if not missing else "missing dependencies: " + ", ".join(missing),
    )


def _find_skill_dir(root: Path, name: str) -> Path:
    path = root / name
    if not _path_is_safe(root, path) or not path.is_dir():
        raise BundleIntegrityError(f"skill directory is missing or unsafe: {name}")
    return path


def _discover(root: Path) -> BundleValidation:
    errors: list[str] = []
    skills: dict[str, BuiltinSkill] = {}
    lock: BundleLock | None = None
    try:
        if not root.is_dir() or root.is_symlink():
            raise BundleIntegrityError(f"bundle root is not a regular directory: {root}")
        lock = _load_lock(root)
    except BundleError as exc:
        errors.append(str(exc))

    if root.is_dir() and not root.is_symlink():
        allowed = set(SKILL_NAMES) | {BUNDLE_LOCK_NAME}
        try:
            for entry in root.iterdir():
                if entry.name not in allowed:
                    errors.append(f"unapproved bundle entry: {entry.name}")
                if entry.is_symlink():
                    errors.append(f"symlink is not allowed: {entry.name}")
        except OSError as exc:
            errors.append(f"cannot enumerate bundle: {exc}")

    for name in SKILL_NAMES:
        directory: Path | None = None
        runtime_directory: Path | None = None
        try:
            directory = _find_skill_dir(root, name)
            manifest = _load_manifest(directory, name)
            # Policies are local to this bundle, while the four Office
            # document skills execute from their immutable native sources.
            # Walk the policy tree for symlink/encoding safety even though
            # its digest is intentionally not the runtime digest.
            for _ in _iter_regular_files(directory):
                pass
            policy_md = directory / "SKILL.md"
            if not policy_md.is_file() or policy_md.is_symlink():
                raise BundleIntegrityError(f"SKILL.md is missing for skill: {name}")
            policy_md.read_text(encoding="utf-8")
            runtime_directory = _runtime_skill_directory(
                root,
                directory,
                name,
                manifest,
            )
            skill_digest = _skill_digest(directory, runtime_directory)
            if lock is not None and lock.skills.get(name) != skill_digest:
                raise BundleIntegrityError(f"digest mismatch for skill: {name}")
            skill_md = runtime_directory / "SKILL.md"
            if not skill_md.is_file() or skill_md.is_symlink():
                raise BundleIntegrityError(f"SKILL.md is missing for skill: {name}")
            skill_md.read_text(encoding="utf-8")
            readiness = _probe_dependencies(runtime_directory, manifest)
            skills[name] = BuiltinSkill(
                name,
                runtime_directory,
                manifest,
                skill_digest,
                readiness,
            )
        except (BundleError, OSError, UnicodeDecodeError) as exc:
            errors.append(str(exc))
            fallback = SkillManifest(name, name, "0.0.0")
            skills[name] = BuiltinSkill(
                name,
                directory,
                fallback,
                readiness=DependencyReadiness(False, disabled_reason=str(exc)),
                errors=(str(exc),),
            )

    valid = not errors and lock is not None and set(skills) == set(SKILL_NAMES)
    return BundleValidation(root, valid, tuple(dict.fromkeys(errors)), skills, lock)


def verify_bundle(root: str | Path | None = None) -> BundleValidation:
    """Validate a bundle without raising for expected integrity failures."""
    bundle_root = Path(root) if root is not None else _default_root()
    try:
        bundle_root = bundle_root.resolve(strict=False)
    except OSError:
        pass
    return _discover(bundle_root)


def validate_bundle(root: str | Path | None = None) -> SkillBundle:
    """Load a bundle, raising :class:`BundleIntegrityError` on tampering."""
    result = verify_bundle(root)
    if not result.valid or result.lock is None:
        detail = "; ".join(result.errors) or "bundle validation failed"
        raise BundleIntegrityError(detail)
    return SkillBundle(result.root, result.skills, result.lock, result)


def load_bundle(root: str | Path | None = None) -> SkillBundle:
    """Alias for the fail-closed immutable bundle loader."""
    return validate_bundle(root)


def discover_builtin_skills(root: str | Path | None = None) -> dict[str, BuiltinSkill]:
    """Return the six registry entries, retaining unavailable diagnostics."""
    return dict(verify_bundle(root).skills)


def _require_skill(name: str, root: str | Path | None = None) -> BuiltinSkill:
    if name not in SKILL_NAMES:
        raise UnknownSkillError(f"unknown built-in skill: {name}")
    skill = discover_builtin_skills(root).get(name)
    if skill is None:
        raise UnknownSkillError(name)
    return skill


def load_skill(
    name: str,
    *,
    root: str | Path | None = None,
    full: bool = False,
    require_ready: bool = False,
) -> dict[str, Any]:
    """Load metadata lazily, optionally returning SKILL.md and file lists."""
    skill = _require_skill(name, root)
    if require_ready and not skill.available:
        raise BundleError(skill.readiness.disabled_reason or f"skill disabled: {name}")
    payload: dict[str, Any] = {
        "id": name,
        "name": name,
        "version": skill.manifest.version,
        "manifest": skill.manifest,
        "digest": skill.digest,
        "readiness": skill.readiness,
        "available": skill.available,
        "errors": list(skill.errors),
    }
    if full and skill.directory is not None and not skill.errors:
        payload["content"] = (skill.directory / "SKILL.md").read_text(encoding="utf-8")
        payload["scripts"] = sorted(
            path.relative_to(skill.directory).as_posix()
            for path in (skill.directory / "scripts").rglob("*")
            if path.is_file() and not path.is_symlink()
        ) if (skill.directory / "scripts").is_dir() else []
        payload["references"] = sorted(
            path.relative_to(skill.directory).as_posix()
            for path in (skill.directory / "references").rglob("*")
            if path.is_file() and not path.is_symlink()
        ) if (skill.directory / "references").is_dir() else []
    return payload


discover_skills = discover_builtin_skills
get_skill = _require_skill
verify_skill_bundle = verify_bundle
bundle_digest = directory_digest


__all__ = [
    "SKILL_NAMES", "BUNDLE_LOCK_NAME", "OFFICE_TOOL_NAMES", "BundleError", "BundleIntegrityError",
    "UnknownSkillError", "DependencyReadiness", "SkillManifest", "BuiltinSkill",
    "BundleLock", "BundleValidation", "SkillBundle", "directory_digest",
    "bundle_digest", "verify_bundle", "verify_skill_bundle", "validate_bundle",
    "load_bundle", "discover_builtin_skills", "discover_skills", "load_skill", "get_skill",
]
