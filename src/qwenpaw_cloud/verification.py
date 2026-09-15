"""Conservative, dependency-light verification for generated office artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import io
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Iterable


OFFICE_RENDERER_UNAVAILABLE = "OFFICE_RENDERER_UNAVAILABLE"
_EXTENSIONS = {".doc", ".docx", ".xlsx", ".pptx", ".pdf", ".csv", ".tsv"}
_ERROR_RE = re.compile(r"(?:traceback \(most recent call last\)|\[\s*(?:error|failed)\s*\]|\b(?:error|failed|exception):?\b)", re.I)
_PLACEHOLDER_RE = re.compile(r"(?:\{\{[^{}]+\}\}|\$\{[^{}]+\}|<\s*(?:todo|placeholder)[^>]*>|\b(?:todo|placeholder)\b)", re.I)
_ZIP_LIMIT = 256 * 1024 * 1024


@dataclass
class VerificationResult:
    """The evidence produced by :func:`verify_artifact`.

    ``structural_verified`` means all structural checks passed;
    ``unverified`` deliberately means that evidence was unavailable.
    """

    level: str
    checks: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.level in {"structural_verified", "content_checked"}

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "checks": list(self.checks),
            "limitations": list(self.limitations),
        }


class CapabilityUnavailable(RuntimeError):
    code = OFFICE_RENDERER_UNAVAILABLE

    def __init__(self, capability: str):
        super().__init__(f"{self.code}: {capability}")
        self.capability = capability


def _zip_xml(path: Path, required: Iterable[str]) -> tuple[dict[str, bytes], list[str]]:
    errors: list[str] = []
    found: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            total = 0
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in Path(name).parts:
                    errors.append("unsafe ZIP member path")
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    errors.append("symlink ZIP member is not allowed")
                    continue
                total += info.file_size
                if total > _ZIP_LIMIT:
                    errors.append("ZIP contents exceed safety limit")
                    break
                if name in required:
                    found[name] = archive.read(info)
            errors.extend(f"missing ZIP member: {name}" for name in required if name not in found)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        errors.append(f"invalid ZIP package: {exc}")
    return found, errors


def _xml_checks(files: dict[str, bytes]) -> list[str]:
    errors: list[str] = []
    for name, data in files.items():
        if len(data) > _ZIP_LIMIT:
            errors.append(f"XML member exceeds safety limit: {name}")
            continue
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            errors.append(f"unsafe XML declaration: {name}")
            continue
        try:
            ET.fromstring(data)
        except ET.ParseError as exc:
            errors.append(f"invalid XML: {name}: {exc}")
    return errors


def _text_checks(data: bytes, *, delimiter: str) -> list[str]:
    errors: list[str] = []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ["text is not valid UTF-8"]
    if not text.strip():
        errors.append("file is empty")
    if _ERROR_RE.search(text):
        errors.append("error string found in artifact")
    if _PLACEHOLDER_RE.search(text):
        errors.append("unresolved placeholder found in artifact")
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if not rows or not any(row for row in rows):
            errors.append("no tabular rows found")
        elif len({len(row) for row in rows if row}) > 1:
            errors.append("inconsistent column count")
    except csv.Error as exc:
        errors.append(f"invalid delimited text: {exc}")
    return errors


def verify_artifact(
    path: str | Path,
    *,
    requirements: dict[str, Any] | None = None,
    require_formula_recalculation: bool = False,
    require_render: bool = False,
    require_visual: bool = False,
    require_revision: bool = False,
    require_doc: bool = False,
    require_macros: bool = False,
) -> VerificationResult:
    """Verify structure and obvious failure residue without claiming visual QA."""
    path = Path(path)
    requirements = requirements or {}
    require_formula_recalculation |= bool(requirements.get("formula_recalculation"))
    require_render |= bool(requirements.get("render"))
    require_visual |= bool(requirements.get("visual"))
    require_revision |= bool(requirements.get("revisions"))
    require_doc |= bool(requirements.get("doc"))
    require_macros |= bool(requirements.get("macros"))
    checks: list[str] = []
    limitations: list[str] = []
    errors: list[str] = []
    suffix = path.suffix.lower()
    if suffix not in _EXTENSIONS:
        errors.append(f"unsupported artifact type: {suffix or 'none'}")
    elif not path.is_file():
        errors.append("artifact does not exist")
    else:
        data = path.read_bytes()
        if suffix == ".doc":
            limitations.append(".doc is not supported; OFFICE_RENDERER_UNAVAILABLE")
        elif suffix in {".docx", ".xlsx", ".pptx"}:
            roots = {
                ".docx": ("[Content_Types].xml", "word/document.xml", "word/_rels/document.xml.rels"),
                ".xlsx": ("[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"),
                ".pptx": ("[Content_Types].xml", "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"),
            }[suffix]
            files, zip_errors = _zip_xml(path, roots)
            xml_errors = _xml_checks(files)
            errors.extend(zip_errors + xml_errors)
            if not zip_errors:
                checks.append("safe_package")
            if not xml_errors:
                checks.append("xml_structure")
            text = b"\n".join(files.values())
            if _ERROR_RE.search(text.decode("utf-8", "ignore")):
                errors.append("error string found in XML")
            if _PLACEHOLDER_RE.search(text.decode("utf-8", "ignore")):
                errors.append("unresolved placeholder found in XML")
        elif suffix == ".pdf":
            if data.startswith(b"%PDF-"):
                checks.append("pdf_signature")
            else:
                errors.append("invalid PDF signature")
            if b"%%EOF" not in data[-1024:]:
                errors.append("PDF trailer is incomplete")
        else:
            text_errors = _text_checks(data, delimiter="\t" if suffix == ".tsv" else ",")
            errors.extend(text_errors)
            if not text_errors:
                checks.append("tabular_text")
    unavailable = []
    if require_doc or suffix == ".doc":
        unavailable.append("legacy .doc conversion")
    if require_render:
        unavailable.append("Office/WPS final rendering")
    if require_visual:
        unavailable.append("rendered visual acceptance")
    if require_formula_recalculation:
        unavailable.append("Excel formula recalculation")
    if require_revision:
        unavailable.append("accepting Word revisions")
    if require_macros:
        unavailable.append("Office/WPS macros")
    if unavailable:
        raise CapabilityUnavailable(", ".join(unavailable))
    if errors:
        level = "unverified"
    elif limitations:
        level = "unverified"
    else:
        level = "structural_verified"
    if not checks:
        if not errors:
            checks.append("structure")
    return VerificationResult(level, checks, list(dict.fromkeys(errors + limitations)))


verify_office_artifact = verify_artifact
verify_generated_artifact = verify_artifact
