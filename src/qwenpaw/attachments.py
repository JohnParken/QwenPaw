"""Bounded, byte-only text extraction for user supplied attachments.

The extractor deliberately accepts bytes rather than paths.  Office archives are
inspected in memory and are never unpacked, while PDF support imports pypdf lazily.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Iterable
from typing import Any
from xml.etree import ElementTree as ET

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_CHARS = 100_000
MAX_ZIP_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ZIP_ENTRIES = 512

_TEXT_FORMATS = {"txt", "md", "csv", "json", "log"}
_OFFICE_FORMATS = {"docx", "xlsx"}
_SUPPORTED_FORMATS = _TEXT_FORMATS | _OFFICE_FORMATS | {"pdf"}

_XML_DTD_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_XML_DTD_TEXT_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_EXTERNAL_FORMULA_RE = re.compile(
    rb"\[[^\]\r\n]{1,512}\][^\r\n<]{1,512}!", re.IGNORECASE
)
_FORBIDDEN_PART_RE = re.compile(
    r"(?:^|/)(?:vbaProject(?:Signature)?\.bin|activeX|embeddings?|externalLinks?|"
    r"oleObject|macrosheets?|dialogsheets?)(?:/|$)",
    re.IGNORECASE,
)


class AttachmentError(ValueError):
    """Raised when an attachment cannot be safely or usefully extracted."""


def extract_attachment(filename: str, content: bytes) -> dict[str, Any]:
    """Extract text from a supported attachment without reading from disk.

    ``content`` is the complete byte payload.  The returned filename is always
    the final path component, and the returned text is capped at
    :data:`MAX_OUTPUT_CHARS` characters.
    """

    safe_filename = _safe_basename(filename)
    if not isinstance(content, bytes):
        raise AttachmentError("attachment content must be bytes")
    if len(content) > MAX_INPUT_BYTES:
        raise AttachmentError("attachment exceeds 10 MiB")

    extension = safe_filename.rsplit(".", 1)[-1].lower() if "." in safe_filename else ""
    if extension not in _SUPPORTED_FORMATS:
        raise AttachmentError(
            f"unsupported attachment format: {extension or 'unknown'}"
        )

    if extension in _TEXT_FORMATS:
        text = _decode_text(content)
        if extension == "json":
            try:
                json.loads(text, parse_constant=_reject_json_constant)
            except (TypeError, ValueError) as exc:
                raise AttachmentError("invalid JSON attachment") from exc
    elif extension in _OFFICE_FORMATS:
        text = _extract_office(extension, content)
    else:
        text = _extract_pdf(content)

    truncated = len(text) > MAX_OUTPUT_CHARS
    if truncated:
        text = text[:MAX_OUTPUT_CHARS]
    return {
        "filename": safe_filename,
        "text": text,
        "truncated": truncated,
        "format": extension,
    }


def _safe_basename(filename: str) -> str:
    if not isinstance(filename, str) or not filename:
        raise AttachmentError("attachment filename must be a non-empty string")
    if "\x00" in filename:
        raise AttachmentError("attachment filename contains NUL")
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if basename in {"", ".", ".."}:
        raise AttachmentError("attachment filename has no basename")
    return basename


def _decode_text(content: bytes) -> str:
    """Decode one of the explicitly supported text encodings.

    GB18030 is intentionally attempted last because it can decode many arbitrary
    byte sequences; the post-decode control-character check keeps binary input
    from being accepted as text merely because that codec is permissive.
    """

    candidates: list[tuple[str, str]] = []
    if content.startswith(b"\xef\xbb\xbf"):
        candidates.append(("utf-8-sig", "UTF-8"))
    elif content.startswith(b"\xff\xfe") or content.startswith(b"\xfe\xff"):
        candidates.append(("utf-16", "UTF-16"))
    else:
        looks_like_utf16 = _looks_like_utf16(content)
        if looks_like_utf16:
            candidates.extend((("utf-16-le", "UTF-16"), ("utf-16-be", "UTF-16")))
        candidates.append(("utf-8", "UTF-8"))
        if looks_like_utf16:
            candidates.extend((("utf-16-le", "UTF-16"), ("utf-16-be", "UTF-16")))
        candidates.append(("gb18030", "GB18030"))

    for codec, _label in candidates:
        try:
            text = content.decode(codec)
        except UnicodeDecodeError:
            continue
        if _looks_binary_text(text):
            continue
        return text
    raise AttachmentError("attachment is not valid UTF-8, UTF-16, or GB18030 text")


def _looks_like_utf16(content: bytes) -> bool:
    if len(content) < 4 or len(content) % 2:
        return False
    sample = content[:4096]
    odd_nuls = sum(byte == 0 for byte in sample[1::2])
    even_nuls = sum(byte == 0 for byte in sample[::2])
    pairs = len(sample) // 2
    return max(odd_nuls, even_nuls) >= max(2, pairs // 5)


def _looks_binary_text(text: str) -> bool:
    if "\x00" in text or "\ufffd" in text:
        return True
    control_count = sum(
        1
        for char in text
        if (ord(char) < 32 and char not in "\t\n\r\f") or 0x7F <= ord(char) <= 0x9F
    )
    return control_count > max(2, len(text) // 100)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _extract_office(kind: str, content: bytes) -> str:
    parts, xml_roots = _read_office_archive(content)
    if kind == "docx":
        document = parts.get("word/document.xml")
        if document is None:
            raise AttachmentError("corrupt DOCX: missing word/document.xml")
        names = [
            "word/document.xml",
            *sorted(
                name
                for name in parts
                if name.startswith("word/")
                and name.endswith(".xml")
                and name not in {"word/document.xml"}
                and any(
                    name.startswith(prefix)
                    for prefix in (
                        "word/header",
                        "word/footer",
                        "word/footnotes",
                        "word/endnotes",
                        "word/comments",
                    )
                )
            ),
        ]
        extracted: list[str] = []
        for name in names:
            root = xml_roots[name]
            paragraphs = [
                element for element in root.iter() if _local_name(element.tag) == "p"
            ]
            if paragraphs:
                extracted.extend(
                    _word_paragraph_text(paragraph) for paragraph in paragraphs
                )
            else:
                fallback = "".join(_xml_text_nodes(root))
                if fallback:
                    extracted.append(fallback)
        return "\n".join(part for part in extracted if part)

    sheet_names = sorted(
        name
        for name in parts
        if name.startswith("xl/worksheets/") and name.endswith(".xml")
    )
    if not sheet_names:
        raise AttachmentError("corrupt XLSX: missing worksheet")

    shared_strings: list[str] = []
    shared_name = "xl/sharedStrings.xml"
    if shared_name in xml_roots:
        shared_strings = [
            "".join(_xml_text_nodes(si))
            for si in xml_roots[shared_name].iter()
            if _local_name(si.tag) == "si"
        ]

    rows: list[str] = []
    for sheet_name in sheet_names:
        root = xml_roots[sheet_name]
        for row in (
            element for element in root.iter() if _local_name(element.tag) == "row"
        ):
            values: list[str] = []
            for cell in (element for element in row if _local_name(element.tag) == "c"):
                value = _xlsx_cell_value(cell, shared_strings)
                reference = cell.attrib.get("r", "")
                column = _xlsx_column_index(reference)
                if column is None:
                    values.append(value)
                    continue
                while len(values) <= column:
                    values.append("")
                values[column] = value
            rows.append("\t".join(values).rstrip("\t"))
    return "\n".join(rows)


def _read_office_archive(
    content: bytes,
) -> tuple[dict[str, bytes], dict[str, ET.Element]]:
    parts: dict[str, bytes] = {}
    roots: dict[str, ET.Element] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
            infos = archive.infolist()
            if not infos:
                raise AttachmentError("corrupt Office attachment: empty archive")
            if len(infos) > MAX_ZIP_ENTRIES:
                raise AttachmentError("Office attachment has too many ZIP entries")
            total_size = 0
            for info in infos:
                name = info.filename.replace("\\", "/")
                _validate_zip_member(name)
                if name in parts:
                    raise AttachmentError("Office attachment has duplicate ZIP entries")
                if info.flag_bits & 0x1:
                    raise AttachmentError(
                        "encrypted Office attachments are unsupported"
                    )
                if _FORBIDDEN_PART_RE.search(name):
                    raise AttachmentError(
                        "Office attachment contains macros or embedded content"
                    )
                total_size += info.file_size
                if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise AttachmentError(
                        "Office attachment exceeds 20 MiB uncompressed"
                    )
                try:
                    data = archive.read(info)
                except (
                    Exception
                ) as exc:  # zip CRC, decompression, and malformed-member errors
                    raise AttachmentError("corrupt Office attachment archive") from exc
                if len(data) != info.file_size:
                    raise AttachmentError("corrupt Office attachment archive")
                parts[name] = data
    except AttachmentError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise AttachmentError("corrupt Office attachment archive") from exc

    for name, data in parts.items():
        lower_name = name.lower()
        if not lower_name.endswith((".xml", ".rels")):
            continue
        root = _parse_xml(data, name)
        roots[name] = root
        _validate_office_xml(name, root)
        if lower_name.startswith("xl/") and _EXTERNAL_FORMULA_RE.search(data):
            raise AttachmentError("Office attachment contains external references")
    return parts, roots


def _validate_zip_member(name: str) -> None:
    if not name or "\x00" in name:
        raise AttachmentError("Office attachment contains an invalid ZIP entry")
    if name.startswith("/"):
        raise AttachmentError("Office attachment contains an unsafe ZIP entry")
    parts = name.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AttachmentError("Office attachment contains an unsafe ZIP entry")


def _parse_xml(data: bytes, name: str) -> ET.Element:
    if _contains_dtd_or_entity_declaration(data):
        raise AttachmentError(f"Office XML {name} contains a DTD or entity")
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError) as exc:
        raise AttachmentError(f"corrupt Office XML: {name}") from exc


def _contains_dtd_or_entity_declaration(data: bytes) -> bool:
    if _XML_DTD_RE.search(data):
        return True
    candidates: list[str] = []
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            candidates.append(data.decode("utf-16"))
        except UnicodeDecodeError:
            pass
    elif data.startswith(b"<\x00"):
        try:
            candidates.append(data.decode("utf-16-le"))
        except UnicodeDecodeError:
            pass
    elif data.startswith(b"\x00<"):
        try:
            candidates.append(data.decode("utf-16-be"))
        except UnicodeDecodeError:
            pass
    return any(_XML_DTD_TEXT_RE.search(candidate) for candidate in candidates)


def _validate_office_xml(name: str, root: ET.Element) -> None:
    for element in root.iter():
        local_name = _local_name(element.tag).lower()
        if local_name in {
            "externalreferences",
            "externallink",
            "externalworkbook",
            "externaldata",
        }:
            raise AttachmentError("Office attachment contains external references")
        if local_name != "relationship":
            continue
        target_mode = element.attrib.get("TargetMode", "").lower()
        target = element.attrib.get("Target", "").strip()
        if target_mode == "external" or _is_external_target(target):
            raise AttachmentError("Office attachment contains external references")


def _is_external_target(target: str) -> bool:
    return bool(
        target.startswith(("//", "\\\\"))
        or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE)
    )


def _word_paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for element in paragraph.iter():
        local_name = _local_name(element.tag)
        if local_name in {"t", "delText", "instrText"} and element.text:
            pieces.append(element.text)
        elif local_name == "tab":
            pieces.append("\t")
        elif local_name in {"br", "cr"}:
            pieces.append("\n")
    return "".join(pieces)


def _xml_text_nodes(element: ET.Element) -> Iterable[str]:
    for node in element.iter():
        if _local_name(node.tag) in {"t", "delText", "v"} and node.text:
            yield node.text


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(_xml_text_nodes(cell))
    value_element = next(
        (child for child in cell if _local_name(child.tag) == "v"), None
    )
    value = (
        value_element.text
        if value_element is not None and value_element.text is not None
        else ""
    )
    if cell_type == "s":
        try:
            index = int(value)
            return shared_strings[index]
        except (TypeError, ValueError, IndexError) as exc:
            raise AttachmentError("corrupt XLSX shared string reference") from exc
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE" if value == "0" else value
    if value:
        return value
    formula = next((child for child in cell if _local_name(child.tag) == "f"), None)
    if formula is not None and formula.text:
        return "=" + formula.text
    return ""


def _xlsx_column_index(reference: str) -> int | None:
    match = re.match(r"^[A-Za-z]+", reference)
    if match is None:
        return None
    value = 0
    for char in match.group(0).upper():
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_pdf(content: bytes) -> str:
    for marker in (
        b"/JavaScript",
        b"/JS",
        b"/Launch",
        b"/EmbeddedFile",
        b"/FileAttachment",
        b"/SubmitForm",
        b"/GoToR",
        b"/URI",
    ):
        if re.search(re.escape(marker) + rb"\b", content, re.IGNORECASE):
            raise AttachmentError("PDF contains unsupported active or external content")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise AttachmentError(
            "PDF support requires the optional pypdf dependency"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise AttachmentError("encrypted PDF attachments are unsupported")
        if len(reader.pages) > 200:
            raise AttachmentError("PDF attachment exceeds the 200 page limit")
        text_parts = [(page.extract_text() or "") for page in reader.pages]
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError("corrupt PDF attachment") from exc

    text = "\n".join(text_parts)
    if not text.strip():
        raise AttachmentError("PDF contains no extractable text; OCR is unsupported")
    return text


__all__ = [
    "AttachmentError",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_CHARS",
    "MAX_ZIP_ENTRIES",
    "MAX_ZIP_UNCOMPRESSED_BYTES",
    "extract_attachment",
]
