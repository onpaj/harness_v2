"""Discovers the Markdown documents the static site drills down into.

Four fixed locations, in a fixed order: ADRs, phase specs, phase plans, then
the two root project docs. No recursive crawl, no front-matter parsing, no
doc-to-doc link graph — adding a new category later is one more glob line
here, not a change to this function's shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class DocEntry:
    category: str  # "adr" | "spec" | "plan" | "project"
    source_path: Path
    title: str
    sort_key: str
    output_name: str  # relative to site/<category>/


def _title_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _TITLE.search(text)
    if match:
        return match.group(1)
    return path.stem.replace("-", " ")


def _adr_entries(repo_root: Path) -> list[DocEntry]:
    entries = []
    for path in sorted((repo_root / "docs" / "adr").glob("*.md")):
        entries.append(
            DocEntry(
                category="adr",
                source_path=path,
                title=_title_of(path),
                sort_key=path.name,
                output_name=f"{path.stem}.html",
            )
        )
    return entries


def _dated_entries(category: str, directory: Path) -> list[DocEntry]:
    entries = []
    for path in sorted(directory.glob("*.md")):
        entries.append(
            DocEntry(
                category=category,
                source_path=path,
                title=_title_of(path),
                sort_key=path.name,
                output_name=f"{path.stem}.html",
            )
        )
    return entries


def _project_entries(repo_root: Path) -> list[DocEntry]:
    entries = []
    for rank, (name, output_name) in enumerate(
        [("README.md", "readme.html"), ("CLAUDE.md", "claude-md.html")]
    ):
        path = repo_root / name
        if not path.is_file():
            continue
        entries.append(
            DocEntry(
                category="project",
                source_path=path,
                title=_title_of(path),
                sort_key=f"{rank:02d}",
                output_name=output_name,
            )
        )
    return entries


def discover_docs(repo_root: Path) -> list[DocEntry]:
    """Return every document the site should render, in category order.

    Locations, fixed: `docs/adr/*.md`, `docs/superpowers/specs/*.md`,
    `docs/superpowers/plans/*.md`, then `README.md`/`CLAUDE.md` (in that
    order). A missing directory yields no entries for that category rather
    than an error.
    """
    repo_root = Path(repo_root)
    entries: list[DocEntry] = []
    entries.extend(_adr_entries(repo_root))
    entries.extend(_dated_entries("spec", repo_root / "docs" / "superpowers" / "specs"))
    entries.extend(_dated_entries("plan", repo_root / "docs" / "superpowers" / "plans"))
    entries.extend(_project_entries(repo_root))
    return entries
