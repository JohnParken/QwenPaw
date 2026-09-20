from __future__ import annotations

import io
import sys
import zipfile

import pytest

from qwenpaw.attachments import (
    AttachmentError,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_CHARS,
    MAX_ZIP_UNCOMPRESSED_BYTES,
    extract_attachment,
)

pytestmark = pytest.mark.unit


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


def test_text_formats_decode_supported_encodings_and_strip_path() -> None:
    cases = {
        "utf8.txt": "hello 世界".encode("utf-8"),
        "utf16.md": "hello 世界".encode("utf-16"),
        "report.csv": "姓名,值\n小明,42".encode("gb18030"),
    }

    for filename, content in cases.items():
        result = extract_attachment(
            f"https://example.invalid/uploads/{filename}", content
        )
        assert result["filename"] == filename
        assert result["text"] == content.decode(
            "utf-16"
            if filename.endswith(".md")
            else "gb18030" if filename.endswith(".csv") else "utf-8"
        )
        assert result["format"] == filename.rsplit(".", 1)[-1]
        assert result["truncated"] is False


def test_json_is_validated_and_binary_is_rejected() -> None:
    result = extract_attachment("data.JSON", b'{"ok": true, "items": [1, 2]}')
    assert result["format"] == "json"
    assert '"ok": true' in result["text"]

    with pytest.raises(AttachmentError, match="invalid JSON"):
        extract_attachment("broken.json", b"{not json}")
    with pytest.raises(AttachmentError, match="not valid"):
        extract_attachment("payload.log", b"\x00\x01\xff\x00")


def test_input_and_output_limits_are_enforced() -> None:
    with pytest.raises(AttachmentError, match="10 MiB"):
        extract_attachment("large.txt", b"x" * (MAX_INPUT_BYTES + 1))

    result = extract_attachment("long.txt", b"x" * (MAX_OUTPUT_CHARS + 1))
    assert result["text"] == "x" * MAX_OUTPUT_CHARS
    assert result["truncated"] is True


def test_docx_extracts_paragraphs_without_unpacking() -> None:
    document = b"""<?xml version='1.0' encoding='UTF-8'?>
    <w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>
      <w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p>
      <w:p><w:r><w:t>World</w:t></w:r></w:p></w:body>
    </w:document>"""
    result = extract_attachment(
        "/private/inbox/report.docx", _zip_bytes({"word/document.xml": document})
    )
    assert result["filename"] == "report.docx"
    assert result["format"] == "docx"
    assert result["text"] == "Hello\nWorld"


def test_xlsx_extracts_shared_and_inline_cell_values() -> None:
    shared = b"""<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>
      <si><t>Greeting</t></si>
    </sst>"""
    sheet = b"""<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>
      <sheetData><row r='1'><c r='A1' t='s'><v>0</v></c><c r='B1'><v>42</v></c></row>
      <row r='2'><c r='A2' t='inlineStr'><is><t>world</t></is></c></row></sheetData>
    </worksheet>"""
    result = extract_attachment(
        "table.xlsx",
        _zip_bytes({"xl/sharedStrings.xml": shared, "xl/worksheets/sheet1.xml": sheet}),
    )
    assert result["text"] == "Greeting\t42\nworld"


@pytest.mark.parametrize(
    "entries, message",
    [
        (
            {"word/document.xml": b"<!DOCTYPE x [<!ENTITY y 'boom'>]><document/>"},
            "DTD",
        ),
        (
            {
                "word/document.xml": b"<document><p>safe</p></document>",
                "_rels/.rels": b"<Relationships><Relationship Target='https://example.invalid/x' /></Relationships>",
            },
            "external references",
        ),
        (
            {
                "word/document.xml": b"<document/>",
                "word/vbaProject.bin": b"macro",
            },
            "macros",
        ),
        (
            {"../word/document.xml": b"<document/>"},
            "unsafe ZIP entry",
        ),
    ],
)
def test_office_adversarial_parts_are_rejected(
    entries: dict[str, bytes], message: str
) -> None:
    with pytest.raises(AttachmentError, match=message):
        extract_attachment("unsafe.docx", _zip_bytes(entries))


def test_utf16_office_dtd_is_rejected() -> None:
    document = "<!DOCTYPE document [<!ENTITY x 'blocked'>]><document/>".encode("utf-16")
    with pytest.raises(AttachmentError, match="DTD"):
        extract_attachment("utf16.docx", _zip_bytes({"word/document.xml": document}))


def test_office_zip_entry_and_uncompressed_limits() -> None:
    many_entries = {"word/document.xml": b"<document/>"}
    many_entries.update({f"word/extra-{index}.xml": b"<x/>" for index in range(512)})
    with pytest.raises(AttachmentError, match="too many ZIP entries"):
        extract_attachment("many.docx", _zip_bytes(many_entries))

    oversized = b"x" * (MAX_ZIP_UNCOMPRESSED_BYTES + 1)
    with pytest.raises(AttachmentError, match="20 MiB"):
        extract_attachment("bomb.docx", _zip_bytes({"word/document.xml": oversized}))


def test_corrupt_office_archive_and_optional_pdf_dependency_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AttachmentError, match="corrupt Office"):
        extract_attachment("broken.xlsx", b"not a zip")

    monkeypatch.setitem(sys.modules, "pypdf", None)
    with pytest.raises(AttachmentError, match="optional pypdf"):
        extract_attachment("scan.pdf", b"%PDF-1.7\n%%EOF")


def test_pdf_active_content_is_rejected_before_optional_import() -> None:
    with pytest.raises(AttachmentError, match="active or external"):
        extract_attachment("linked.pdf", b"%PDF-1.7 /JavaScript (app.alert) %%EOF")


def test_pdf_text_is_extracted_and_scanned_pdf_requests_ocr() -> None:
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = writer._add_object(
        DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 10 Td (Hello PDF) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    text_pdf = io.BytesIO()
    writer.write(text_pdf)

    result = extract_attachment("hello.pdf", text_pdf.getvalue())
    assert result["text"] == "Hello PDF"
    assert result["format"] == "pdf"

    blank_writer = pypdf.PdfWriter()
    blank_writer.add_blank_page(width=100, height=100)
    blank_pdf = io.BytesIO()
    blank_writer.write(blank_pdf)
    with pytest.raises(AttachmentError, match="OCR is unsupported"):
        extract_attachment("scan.pdf", blank_pdf.getvalue())
