"""Writes the generated static site: one index page plus one page per document.

Idempotent — `build_site` clears `out_dir` first, so re-running the generator
twice (or after a doc was renamed) never leaves a stale page behind.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from harness_docs_site.corpus import DocEntry
from harness_docs_site.markdown import render

_CATEGORY_TITLES = {
    "adr": "Architecture Decision Records",
    "spec": "Phase specs",
    "plan": "Phase plans",
    "project": "Project docs",
}
_CATEGORY_ORDER = ["adr", "spec", "plan", "project"]

_PAGE_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
</head>
<body>
{body}
</body>
</html>
"""


def _page(title: str, body: str) -> str:
    return _PAGE_SHELL.format(title=html.escape(title), body=body)


def _index_page(entries: list[DocEntry]) -> str:
    by_category: dict[str, list[DocEntry]] = {key: [] for key in _CATEGORY_ORDER}
    for entry in entries:
        by_category.setdefault(entry.category, []).append(entry)

    body = ["<h1>harness docs</h1>"]
    for category in _CATEGORY_ORDER:
        group = sorted(by_category.get(category, []), key=lambda e: e.sort_key)
        if not group:
            continue
        body.append(f"<h2>{html.escape(_CATEGORY_TITLES[category])}</h2>")
        body.append("<ul>")
        for entry in group:
            href = f"{entry.category}/{entry.output_name}"
            body.append(f'<li><a href="{html.escape(href)}">{html.escape(entry.title)}</a></li>')
        body.append("</ul>")
    return _page("harness docs", "\n".join(body))


def _doc_page(entry: DocEntry) -> str:
    # Every source document already opens with its own `# Title` heading, so
    # the rendered body supplies the on-page <h1> — adding a second one here
    # would duplicate it.
    text = entry.source_path.read_text(encoding="utf-8")
    body = [
        '<p><a href="../index.html">&laquo; index</a></p>',
        render(text),
    ]
    return _page(entry.title, "\n".join(body))


def build_site(entries: list[DocEntry], repo_root: Path, out_dir: Path) -> None:
    """Write `out_dir/index.html` and, for every entry,
    `out_dir/<category>/<output_name>`. Clears and recreates `out_dir` first."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    (out_dir / "index.html").write_text(_index_page(entries), encoding="utf-8")

    for entry in entries:
        category_dir = out_dir / entry.category
        category_dir.mkdir(parents=True, exist_ok=True)
        (category_dir / entry.output_name).write_text(_doc_page(entry), encoding="utf-8")
