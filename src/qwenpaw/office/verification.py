"""Fail-closed structural verification for Office and BI artifacts.

The verifier intentionally does not claim that a file looks good in a desktop
client. It checks UTF-8 text, safe OOXML ZIP members/XML, workbook formula
residue, and parseable PDFs without requiring a renderer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import csv
import io
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Iterable


OFFICE_RENDERER_UNAVAILABLE = "OFFICE_RENDERER_UNAVAILABLE"
PDF_PARSER_UNAVAILABLE = "PDF_PARSER_UNAVAILABLE"
_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".csv", ".tsv"}
_OOXML_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
_EXTENSIONS = _TEXT_EXTENSIONS | _OOXML_EXTENSIONS | {".pdf", ".doc", ".parquet", ".png"}
_ZIP_LIMIT = 256 * 1024 * 1024
_ZIP_MEMBER_LIMIT = 64 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 2000
_ERROR_RE = re.compile(
    r"(?:traceback\s*\(most recent call last\)|\[\s*(?:error|failed)\s*\]|"
    r"\b(?:error|failed|exception):?\b)",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(
    r"(?:\{\{[^{}]+\}\}|\$\{[^{}]+\}|<\s*(?:todo|placeholder)[^>]*>|"
    r"\b(?:todo|placeholder)\b)",
    re.IGNORECASE,
)
_FORMULA_ERROR_RE = re.compile(
    r"#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|SPILL!|CALC!|GETTING_DATA)",
    re.IGNORECASE,
)


@dataclass
class VerificationResult:
    """Evidence returned by :func:`verify_artifact`."""

    level: str
    checks: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.level in {"structural_verified", "content_checked"}

    @property
    def valid(self) -> bool:
        return self.ok

    @property
    def unverified(self) -> bool:
        return self.level == "unverified"

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "checks": list(self.checks),
            "limitations": list(self.limitations),
            "errors": list(self.errors),
            "details": dict(self.details),
        }


class ArtifactVerificationError(ValueError):
    """Raised by :func:`validate_artifact` for an invalid artifact."""


class CapabilityUnavailable(RuntimeError):
    """Raised when a caller explicitly requires an unavailable capability."""

    code = OFFICE_RENDERER_UNAVAILABLE

    def __init__(self, capability: str, *, code: str | None = None):
        self.capability = capability
        self.code = code or self.code
        super().__init__(f"{self.code}: {capability}")


def _read_data(value: str | Path | bytes | bytearray) -> tuple[Path | None, bytes]:
    if isinstance(value, (bytes, bytearray)):
        return None, bytes(value)
    path = Path(value)
    if path.is_symlink():
        raise ArtifactVerificationError("artifact symlink is not allowed")
    if not path.is_file():
        raise ArtifactVerificationError("artifact does not exist")
    try:
        return path, path.read_bytes()
    except OSError as exc:
        raise ArtifactVerificationError(f"artifact is not readable: {exc}") from exc


def is_utf8(value: str | Path | bytes | bytearray) -> bool:
    """Return whether a path or byte string is valid UTF-8."""
    try:
        _, data = _read_data(value)
        data.decode("utf-8-sig")
    except (ArtifactVerificationError, UnicodeDecodeError):
        return False
    return True


def validate_utf8(value: str | Path | bytes | bytearray) -> None:
    """Raise when *value* is not valid UTF-8."""
    if not is_utf8(value):
        raise ArtifactVerificationError("text is not valid UTF-8")


def _safe_zip(path: Path, required: Iterable[str]) -> tuple[dict[str, bytes], list[str], list[str]]:
    found: dict[str, bytes] = {}
    errors: list[str] = []
    checks: list[str] = []
    required = tuple(required)
    try:
        with zipfile.ZipFile(path) as archive:
            names: set[str] = set()
            total = 0
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                if name in names:
                    errors.append(f"duplicate ZIP member: {name}")
                names.add(name)
                parts = Path(name).parts
                if not name or name.startswith("/") or ".." in parts:
                    errors.append(f"unsafe ZIP member path: {name!r}")
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    errors.append(f"symlink ZIP member is not allowed: {name}")
                    continue
                if info.flag_bits & 0x1:
                    errors.append(f"encrypted ZIP member is not allowed: {name}")
                    continue
                if info.file_size > _ZIP_MEMBER_LIMIT:
                    errors.append(f"ZIP member exceeds safety limit: {name}")
                    continue
                if info.compress_size and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
                    errors.append(f"ZIP compression ratio is unsafe: {name}")
                    continue
                total += info.file_size
                if total > _ZIP_LIMIT:
                    errors.append("ZIP contents exceed safety limit")
                    break
                if name in required:
                    found[name] = archive.read(info)
            errors.extend(f"missing ZIP member: {name}" for name in required if name not in found)
            if not errors:
                checks.append("safe_package")
    except (OSError, zipfile.BadZipFile, RuntimeError, zipfile.LargeZipFile) as exc:
        errors.append(f"invalid ZIP package: {exc}")
    return found, errors, checks


def _xml_checks(files: dict[str, bytes]) -> list[str]:
    errors: list[str] = []
    for name, data in files.items():
        if len(data) > _ZIP_MEMBER_LIMIT:
            errors.append(f"XML member exceeds safety limit: {name}")
            continue
        upper = data.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            errors.append(f"unsafe XML declaration: {name}")
            continue
        try:
            ET.fromstring(data)
        except ET.ParseError as exc:
            errors.append(f"invalid XML: {name}: {exc}")
    return errors


def _formula_checks(path: Path) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    details: dict[str, Any] = {"formula_count": 0, "formula_errors": []}
    formula_count = 0
    formula_errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                info.filename.replace("\\", "/")
                for info in archive.infolist()
                if info.filename.replace("\\", "/").startswith("xl/worksheets/")
                and info.filename.lower().endswith(".xml")
            ]
            for name in names:
                data = archive.read(name)
                if _FORMULA_ERROR_RE.search(data.decode("utf-8", "ignore")):
                    formula_errors.append(f"formula error residue in {name}")
                try:
                    root = ET.fromstring(data)
                except ET.ParseError:
                    continue
                for formula in root.iter():
                    if formula.tag.rsplit("}", 1)[-1] == "f":
                        formula_count += 1
                        expression = "".join(formula.itertext()).strip()
                        if not expression:
                            errors.append(f"empty formula in {name}")
                        if _FORMULA_ERROR_RE.search(expression):
                            formula_errors.append(f"formula error in {name}")
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"could not inspect XLSX formulas: {exc}")
    if formula_errors:
        errors.extend(formula_errors)
    details["formula_count"] = formula_count
    details["formula_errors"] = formula_errors
    return errors, details


def _text_checks(data: bytes, *, delimiter: str | None = None) -> list[str]:
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
    if delimiter is not None:
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
            nonempty = [row for row in rows if row]
            if not nonempty:
                errors.append("no tabular rows found")
            elif len({len(row) for row in nonempty}) > 1:
                errors.append("inconsistent column count")
        except csv.Error as exc:
            errors.append(f"invalid delimited text: {exc}")
    return errors


def _pdf_checks(path: Path, data: bytes) -> tuple[list[str], list[str], dict[str, Any]]:
    checks: list[str] = []
    limitations: list[str] = []
    details: dict[str, Any] = {}
    errors: list[str] = []
    if not data.startswith(b"%PDF-"):
        errors.append("invalid PDF signature")
    else:
        checks.append("pdf_signature")
    if b"%%EOF" not in data[-4096:]:
        errors.append("PDF trailer is incomplete")
    try:
        from pypdf import PdfReader
    except (ImportError, ModuleNotFoundError):
        limitations.append(PDF_PARSER_UNAVAILABLE)
        return errors, limitations, details
    try:
        reader = PdfReader(str(path), strict=False)
        page_count = len(reader.pages)
        details["page_count"] = page_count
        if page_count <= 0:
            errors.append("PDF has no pages")
        else:
            checks.append("pdf_parse")
            extracted = 0
            for page in reader.pages:
                try:
                    extracted += len(page.extract_text() or "")
                except Exception:
                    continue
            details["extracted_text_bytes"] = extracted
    except Exception as exc:
        errors.append(f"PDF parse failed: {exc}")
    return errors, limitations, details


def _parquet_checks(path: Path) -> tuple[list[str], dict[str, Any]]:
    try:
        import pyarrow.parquet as parquet

        source = parquet.ParquetFile(path)
        fields = [str(name) for name in source.schema_arrow.names]
        if not fields:
            return ["Parquet schema has no fields"], {}
        return [], {
            "columns": fields,
            "row_groups": source.num_row_groups,
            "rows": source.metadata.num_rows if source.metadata else None,
        }
    except ImportError:
        return ["Parquet verification requires pyarrow"], {}
    except Exception as exc:
        return [f"Parquet parse failed: {exc}"], {}


def _png_checks(path: Path, data: bytes) -> tuple[list[str], dict[str, Any]]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ["invalid PNG signature"], {}
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
        if width <= 0 or height <= 0:
            return ["PNG has invalid dimensions"], {}
        return [], {"width": width, "height": height}
    except ImportError:
        return ["PNG verification requires Pillow"], {}
    except Exception as exc:
        return [f"PNG parse failed: {exc}"], {}


def _render_checks(path: Path) -> tuple[list[str], dict[str, Any]]:
    """Render one Office/PDF artifact with LibreOffice and Poppler."""
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise CapabilityUnavailable("Poppler pdftoppm is unavailable")
    with tempfile.TemporaryDirectory(prefix="qwenpaw-office-verify-") as raw_tmp:
        temp_dir = Path(raw_tmp)
        source_pdf = path
        if path.suffix.lower() in _OOXML_EXTENSIONS:
            office = shutil.which("libreoffice") or shutil.which("soffice")
            if office is None:
                raise CapabilityUnavailable("LibreOffice is unavailable")
            converted = subprocess.run(
                [office, "--headless", "--convert-to", "pdf", "--outdir", str(temp_dir), str(path)],
                check=False,
                capture_output=True,
                timeout=90,
            )
            source_pdf = temp_dir / f"{path.stem}.pdf"
            if converted.returncode != 0 or not source_pdf.is_file():
                detail = converted.stderr.decode("utf-8", "replace")[-1000:]
                return [f"LibreOffice rendering failed: {detail}"], {}
        preview = temp_dir / "preview"
        rendered = subprocess.run(
            [pdftoppm, "-f", "1", "-singlefile", "-png", str(source_pdf), str(preview)],
            check=False,
            capture_output=True,
            timeout=60,
        )
        image = preview.with_suffix(".png")
        if rendered.returncode != 0 or not image.is_file() or image.stat().st_size == 0:
            detail = rendered.stderr.decode("utf-8", "replace")[-1000:]
            return [f"Poppler rendering failed: {detail}"], {}
        if not image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            return ["rendered preview is not a PNG"], {}
        return [], {"rendered_preview_bytes": image.stat().st_size}


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
    expected_mime_type: str | None = None,
) -> VerificationResult:
    """Check artifact structure and obvious failure residue."""
    requirements = requirements or {}
    require_formula_recalculation |= bool(requirements.get("formula_recalculation"))
    require_render |= bool(requirements.get("render"))
    require_visual |= bool(requirements.get("visual"))
    require_revision |= bool(requirements.get("revisions"))
    require_doc |= bool(requirements.get("doc"))
    require_macros |= bool(requirements.get("macros"))
    try:
        path_obj, data = _read_data(path)
    except ArtifactVerificationError as exc:
        return VerificationResult("unverified", errors=[str(exc)], limitations=[str(exc)])
    suffix = path_obj.suffix.lower() if path_obj is not None else ""
    checks: list[str] = []
    limitations: list[str] = []
    errors: list[str] = []
    details: dict[str, Any] = {"size_bytes": len(data)}
    if suffix not in _EXTENSIONS:
        errors.append(f"unsupported artifact type: {suffix or 'none'}")
    elif suffix == ".doc":
        limitations.append("legacy .doc is not supported")
    elif suffix in _OOXML_EXTENSIONS:
        required = {
            ".docx": ("[Content_Types].xml", "word/document.xml", "word/_rels/document.xml.rels"),
            ".xlsx": ("[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"),
            ".pptx": ("[Content_Types].xml", "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"),
        }[suffix]
        if path_obj is None:
            errors.append("OOXML validation requires a file path")
        else:
            files, zip_errors, zip_checks = _safe_zip(path_obj, required)
            errors.extend(zip_errors)
            checks.extend(zip_checks)
            xml_errors = _xml_checks(files)
            errors.extend(xml_errors)
            if not xml_errors and files:
                checks.append("xml_structure")
            xml_text = b"\n".join(files.values()).decode("utf-8", "ignore")
            if _ERROR_RE.search(xml_text):
                errors.append("error string found in XML")
            if _PLACEHOLDER_RE.search(xml_text):
                errors.append("unresolved placeholder found in XML")
            if suffix == ".xlsx" and not zip_errors:
                formula_errors, formula_details = _formula_checks(path_obj)
                errors.extend(formula_errors)
                details.update(formula_details)
                checks.append("xlsx_formulas")
    elif suffix == ".pdf":
        if path_obj is None:
            errors.append("PDF validation requires a file path")
        else:
            pdf_errors, pdf_limits, pdf_details = _pdf_checks(path_obj, data)
            errors.extend(pdf_errors)
            limitations.extend(pdf_limits)
            details.update(pdf_details)
    elif suffix == ".parquet":
        if path_obj is None:
            errors.append("Parquet validation requires a file path")
        else:
            parquet_errors, parquet_details = _parquet_checks(path_obj)
            errors.extend(parquet_errors)
            details.update(parquet_details)
            if not parquet_errors:
                checks.append("parquet_schema")
    elif suffix == ".png":
        if path_obj is None:
            errors.append("PNG validation requires a file path")
        else:
            png_errors, png_details = _png_checks(path_obj, data)
            errors.extend(png_errors)
            details.update(png_details)
            if not png_errors:
                checks.append("png_image")
    elif suffix in _TEXT_EXTENSIONS:
        delimiter = "\t" if suffix == ".tsv" else "," if suffix == ".csv" else None
        text_errors = _text_checks(data, delimiter=delimiter)
        errors.extend(text_errors)
        if not text_errors:
            checks.append("utf8_text")
            if delimiter is not None:
                checks.append("tabular_text")

    if expected_mime_type:
        expected_by_suffix = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".pdf": "application/pdf",
            ".csv": "text/csv",
            ".tsv": "text/tab-separated-values",
            ".json": "application/json",
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".parquet": "application/vnd.apache.parquet",
            ".png": "image/png",
        }
        if expected_by_suffix.get(suffix) != expected_mime_type:
            errors.append("MIME type does not match artifact extension")

    if require_render or require_visual or require_formula_recalculation:
        if path_obj is None:
            raise CapabilityUnavailable("rendering requires a file path")
        if require_formula_recalculation and suffix != ".xlsx":
            errors.append("formula recalculation applies only to XLSX")
        render_errors, render_details = _render_checks(path_obj)
        errors.extend(render_errors)
        details.update(render_details)
        if not render_errors:
            checks.append("libreoffice_poppler_render" if suffix in _OOXML_EXTENSIONS else "poppler_render")
            if require_formula_recalculation:
                checks.append("libreoffice_formula_recalculation")

    unavailable: list[str] = []
    if require_doc or suffix == ".doc":
        unavailable.append("legacy .doc conversion")
    if require_revision:
        unavailable.append("accepting Word revisions")
    if require_macros:
        unavailable.append("Office/WPS macros")
    if unavailable:
        raise CapabilityUnavailable(", ".join(unavailable))

    all_limitations = list(dict.fromkeys(limitations))
    all_errors = list(dict.fromkeys(errors))
    level = "structural_verified" if not all_errors and not all_limitations else "unverified"
    if not checks and not all_errors:
        checks.append("structure")
    return VerificationResult(level, checks, all_limitations + all_errors, all_errors, details)


def validate_artifact(path: str | Path, **kwargs: Any) -> VerificationResult:
    """Verify an artifact and raise if the result is not publishable."""
    result = verify_artifact(path, **kwargs)
    if not result.ok:
        detail = "; ".join(result.errors or result.limitations) or "artifact verification failed"
        raise ArtifactVerificationError(detail)
    return result


verify_office_artifact = verify_artifact
verify_generated_artifact = verify_artifact


__all__ = [
    "OFFICE_RENDERER_UNAVAILABLE", "PDF_PARSER_UNAVAILABLE", "VerificationResult",
    "ArtifactVerificationError", "CapabilityUnavailable", "is_utf8", "validate_utf8",
    "verify_artifact", "validate_artifact", "verify_office_artifact", "verify_generated_artifact",
]
