import re
from pathlib import Path

ADR_ROOT = Path(__file__).resolve().parents[1] / "docs" / "adr"
FILENAME = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")
TITLE = re.compile(r"^# ADR-\d{4}: .+$", re.MULTILINE)
STATUS = re.compile(r"^Status:.+$", re.MULTILINE)


def test_adr_directory_exists():
    assert ADR_ROOT.is_dir()


def _adr_files() -> list[Path]:
    files = sorted(ADR_ROOT.glob("*.md"))
    assert files, "docs/adr/ has no ADR files"
    return files


def test_every_adr_filename_matches_numbering_pattern():
    for path in _adr_files():
        assert FILENAME.match(path.name), f"{path.name} doesn't match NNNN-slug.md"


def test_every_adr_has_title_and_status():
    for path in _adr_files():
        text = path.read_text(encoding="utf-8")
        assert TITLE.search(text), f"{path.name} is missing a '# ADR-NNNN: ...' title"
        assert STATUS.search(text), f"{path.name} is missing a 'Status:' line"


def test_every_adr_except_process_doc_has_the_three_sections():
    for path in _adr_files():
        if path.name == "0000-adr-process.md":
            continue
        text = path.read_text(encoding="utf-8")
        for heading in ("## Context", "## Decision", "## Consequences"):
            assert heading in text, f"{path.name} is missing '{heading}'"


def test_at_least_twelve_decision_records_besides_the_process_doc():
    decision_records = [p for p in _adr_files() if p.name != "0000-adr-process.md"]
    assert len(decision_records) >= 12
