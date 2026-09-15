from zipfile import ZipFile

import pytest

from qwenpaw_cloud.verification import CapabilityUnavailable, verify_artifact


def _package(path, files):
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def test_docx_structure_and_safe_xml(tmp_path):
    path = tmp_path / "ok.docx"
    _package(path, {
        "[Content_Types].xml": b"<Types/>",
        "word/document.xml": b"<document><p>Hello</p></document>",
        "word/_rels/document.xml.rels": b"<Relationships/>",
    })
    result = verify_artifact(path)
    assert result.level == "structural_verified"
    assert "safe_package" in result.checks


def test_bad_zip_and_unresolved_placeholder(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    assert verify_artifact(bad).level == "unverified"
    csv = tmp_path / "values.csv"
    csv.write_text("name,value\n{{TODO}},[ERROR]\n", encoding="utf-8")
    result = verify_artifact(csv)
    assert result.level == "unverified"
    assert any("placeholder" in item for item in result.limitations)


def test_requested_capabilities_are_unverified(tmp_path):
    path = tmp_path / "values.tsv"
    path.write_text("name\tvalue\na\t1\n", encoding="utf-8")
    with pytest.raises(CapabilityUnavailable, match="OFFICE_RENDERER_UNAVAILABLE"):
        verify_artifact(path, require_render=True, require_formula_recalculation=True)
