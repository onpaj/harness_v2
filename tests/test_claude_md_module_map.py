from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "src" / "harness"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


def _module_stems() -> list[tuple[str, str]]:
    """(relative path, stem) for every module, excluding __init__.py.

    The module map's rows use brace notation (`ports/{queue,control,...}`), not
    one literal dotted path per module, so the check is against each module's
    bare stem (`control`, not `ports/control.py`) — that's what actually
    appears in the table today, and it's still enough to catch a genuinely
    undocumented module."""
    entries = []
    for path in SOURCE.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        entries.append((str(path.relative_to(SOURCE)).replace("\\", "/"), path.stem))
    assert entries, "no modules found under src/harness"
    return entries


def test_every_source_module_is_named_in_claude_md():
    """Cheap substring check against the raw file, not a parsed-table check —
    a new src/harness/<x>.py whose stem appears nowhere in CLAUDE.md fails this
    by name, without the test needing to understand the table's structure."""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    missing = [path for path, stem in _module_stems() if stem not in text]
    assert not missing, f"CLAUDE.md doesn't mention: {missing}"
