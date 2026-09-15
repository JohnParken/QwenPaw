from __future__ import annotations

from pathlib import Path
import shutil
import sys
from zipfile import ZipFile

import pytest

from qwenpaw.office.bundle import SKILL_NAMES, directory_digest, load_bundle, verify_bundle
from qwenpaw.office.tools import (
    PathBoundaryError,
    SkillExecutionContext,
    SkillOutputLimitExceeded,
    SkillScriptDenied,
    SkillScriptTimeout,
    publish_artifact,
    run_skill_script,
)
from qwenpaw.office.verification import CapabilityUnavailable, verify_artifact


def _context(tmp_path: Path, *, output_limit: int = 65536, timeout: float = 2.0):
    # The script is placed in the context work tree so the argv can remain
    # relative to the process cwd while the manifest still validates it.
    skill = tmp_path / "attempt" / "work"
    (skill / "scripts").mkdir(parents=True)
    (skill / "scripts" / "emit.py").write_text(
        "import sys\nprint(sys.argv[1] if len(sys.argv) > 1 else 'ok')\n",
        encoding="utf-8",
    )
    context = SkillExecutionContext(
        tmp_path / "attempt",
        skill="test",
        skill_dir=skill,
        allowed_commands=("python",),
        entrypoints=("scripts/emit.py",),
        timeout_seconds=timeout,
        max_output_bytes=output_limit,
    )
    return context, skill


def _package(path: Path, files: dict[str, bytes]) -> None:
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def test_locked_bundle_has_exact_six_skills_and_matching_digests():
    bundle = load_bundle()
    assert tuple(bundle) == SKILL_NAMES
    assert bundle.available
    for name in SKILL_NAMES:
        skill = bundle[name]
        assert skill.directory is not None
        assert (skill.directory / "SKILL.md").is_file()
        assert (skill.directory / "manifest.json").is_file()
        assert skill.digest == directory_digest(skill.directory)


def test_bundle_rejects_digest_changes_and_symlinks(tmp_path: Path):
    source = Path("src/qwenpaw/office/skills")
    copied = tmp_path / "skills"
    shutil.copytree(source, copied, symlinks=True)
    (copied / "writing" / "SKILL.md").write_text("changed", encoding="utf-8")
    assert not verify_bundle(copied).valid

    copied = tmp_path / "symlink-skills"
    shutil.copytree(source, copied, symlinks=True)
    (copied / "writing" / "escape").symlink_to(Path("/tmp"))
    result = verify_bundle(copied)
    assert not result.valid
    assert any("symlink" in error for error in result.errors)


def test_attempt_paths_are_scoped_and_input_is_read_only(tmp_path: Path):
    context, _ = _context(tmp_path)
    with pytest.raises(PathBoundaryError):
        context.read_text("../secret")
    with pytest.raises(PathBoundaryError):
        context.write_text("input/new.txt", "no")
    (context.root / "input" / "ok.txt").write_text("yes", encoding="utf-8")
    assert context.read_text("input/ok.txt") == "yes"


def test_script_requires_whitelisted_command_and_registered_entrypoint(tmp_path: Path):
    context, _ = _context(tmp_path)
    result = run_skill_script(context, [sys.executable, "scripts/emit.py", "done"])
    assert result.ok and result.stdout.strip() == "done"
    with pytest.raises(SkillScriptDenied, match="SHELL_COMMAND"):
        run_skill_script(context, ["sh", "-c", "echo escaped"])
    with pytest.raises((SkillScriptDenied, PathBoundaryError)):
        run_skill_script(context, [sys.executable, "../outside.py"])


def test_script_timeout_and_output_limit_kill_the_process(tmp_path: Path):
    context, skill = _context(tmp_path, output_limit=16, timeout=0.15)
    (skill / "scripts" / "emit.py").write_text(
        "import sys, time\nprint('x' * 100)\nsys.stdout.flush()\ntime.sleep(1)\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillOutputLimitExceeded):
        run_skill_script(context, [sys.executable, "scripts/emit.py"])

    context, skill = _context(tmp_path / "timeout", output_limit=4096, timeout=0.05)
    (skill / "scripts" / "emit.py").write_text("import time; time.sleep(1)\n", encoding="utf-8")
    with pytest.raises(SkillScriptTimeout):
        run_skill_script(context, [sys.executable, "scripts/emit.py"])


def test_publish_artifact_requires_output_and_calls_callback(tmp_path: Path):
    published: list[tuple[Path, str, str]] = []
    context, _ = _context(tmp_path)
    context.publish_callback = lambda path, title, mime: published.append((path, title, mime))
    context.write_text("output/result.txt", "ready")
    artifact = publish_artifact(context, "output/result.txt", "Result", "text/plain")
    assert artifact.sha256 and published == [(artifact.path, "Result", "text/plain")]
    with pytest.raises(PathBoundaryError):
        publish_artifact(context, "work/result.txt", "bad", "text/plain")


def test_verifier_checks_utf8_ooxml_xlsx_formulas_and_pdf(tmp_path: Path):
    invalid_text = tmp_path / "bad.txt"
    invalid_text.write_bytes(b"\xff")
    assert not verify_artifact(invalid_text).ok

    docx = tmp_path / "ok.docx"
    _package(docx, {
        "[Content_Types].xml": b"<Types/>",
        "word/document.xml": b"<document><p>ok</p></document>",
        "word/_rels/document.xml.rels": b"<Relationships/>",
    })
    assert verify_artifact(docx).ok

    xlsx = tmp_path / "bad.xlsx"
    _package(xlsx, {
        "[Content_Types].xml": b"<Types/>",
        "xl/workbook.xml": b"<workbook/>",
        "xl/_rels/workbook.xml.rels": b"<Relationships/>",
        "xl/worksheets/sheet1.xml": b"<worksheet><sheetData><c><f>#REF!</f></c></sheetData></worksheet>",
    })
    formula_result = verify_artifact(xlsx)
    assert not formula_result.ok and "xlsx_formulas" in formula_result.checks
    recalculated = verify_artifact(xlsx, require_formula_recalculation=True)
    assert not recalculated.ok

    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")
    assert not verify_artifact(pdf).ok
