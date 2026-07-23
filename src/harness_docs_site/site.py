"""Writes the generated Architecture Explorer: a port-first explorer index,
one restyled page per document, and the copied static assets.

The index shows just the harness's ports, grouped by area. Clicking a port
expands it in place, revealing the drivers available behind it, each with its
own documentation — no graph, no animation, nothing to decode.

Idempotent — `build_site` clears `out_dir` first, so re-running the generator
(or rebuilding after a doc rename) never leaves a stale page behind.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from harness_docs_site.architecture import MODEL, Driver, Port
from harness_docs_site.corpus import DocEntry
from harness_docs_site.markdown import render

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

_CATEGORY_TITLES = {
    "adr": "Architecture Decision Records",
    "spec": "Phase specs",
    "plan": "Phase plans",
    "project": "Project docs",
}
_CATEGORY_ORDER = ["adr", "spec", "plan", "project"]

_PAGE_SHELL = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body class="{body_class}">
{body}
</body>
</html>
"""


def _shell(title: str, body: str, css_href: str, body_class: str) -> str:
    return _PAGE_SHELL.format(
        title=html.escape(title),
        body=body,
        css_href=css_href,
        body_class=body_class,
    )


def _refs_html(adrs: tuple[str, ...], sources: tuple[str, ...],
               adr_titles: dict[str, str]) -> str:
    """The 'Grounded in' block: source paths plus ADR links (only ADRs whose
    file exists — a fixture tree may lack one a real repo has)."""
    items = []
    for src in sources:
        items.append(f"<li><code>{html.escape(src)}</code></li>")
    for slug in adrs:
        if slug not in adr_titles:
            continue
        items.append(
            f'<li><a href="adr/{html.escape(slug)}.html">'
            f"{html.escape(adr_titles[slug])}</a></li>"
        )
    if not items:
        return ""
    return '<ul class="refs">' + "".join(items) + "</ul>"


def _driver_html(driver: Driver, adr_titles: dict[str, str]) -> str:
    out = [f'<details class="driver" id="driver-{html.escape(driver.id)}">']
    out.append(
        "<summary>"
        f'<span class="driver-name">{html.escape(driver.name)}</span>'
        f'<span class="driver-tagline">{html.escape(driver.tagline)}</span>'
        "</summary>"
    )
    out.append('<div class="driver-body">')
    out.append(f"<p>{html.escape(driver.description)}</p>")
    out.append(_refs_html(driver.adrs, driver.sources, adr_titles))
    out.append("</div>")
    out.append("</details>")
    return "\n".join(out)


def _port_html(port: Port, adr_titles: dict[str, str]) -> str:
    count = len(port.drivers)
    noun = "driver" if count == 1 else "drivers"
    out = [f'<details class="port" id="port-{html.escape(port.id)}">']
    out.append(
        "<summary>"
        f'<span class="port-name">{html.escape(port.name)}</span>'
        f'<span class="port-tagline">{html.escape(port.tagline)}</span>'
        f'<span class="driver-count">{count} {noun}</span>'
        "</summary>"
    )
    out.append('<div class="port-body">')
    out.append(f"<p>{html.escape(port.description)}</p>")
    out.append(_refs_html(port.adrs, port.sources, adr_titles))
    out.append(f'<h3 class="drivers-heading">Available {noun}</h3>')
    for driver in port.drivers:
        out.append(_driver_html(driver, adr_titles))
    out.append("</div>")
    out.append("</details>")
    return "\n".join(out)


def _port_catalogue(adr_titles: dict[str, str]) -> str:
    out = ['<section class="ports">']
    out.append(
        '<p class="ports-intro">Every moving part of the harness sits behind '
        "a <strong>port</strong> — a small interface the core depends on. "
        "What actually runs is a <strong>driver</strong> plugged into that "
        "port, wired at the edges. Click a port to see the drivers available "
        "behind it.</p>"
    )
    for group in MODEL.groups:
        ports = [p for p in MODEL.ports if p.group == group]
        if not ports:
            continue
        out.append(f'<h2 class="group-title">{html.escape(group)}</h2>')
        out.append('<div class="port-grid">')
        for port in ports:
            out.append(_port_html(port, adr_titles))
        out.append("</div>")
    out.append("</section>")
    return "\n".join(out)


def _adr_titles(repo_root: Path) -> dict[str, str]:
    """slug → link text for every ADR the model cites. A cited slug whose file
    is absent (e.g. under a test fixture) is skipped, never fatal."""
    slugs: list[str] = []
    for port in MODEL.ports:
        for slug in port.adrs:
            if slug not in slugs:
                slugs.append(slug)
        for driver in port.drivers:
            for slug in driver.adrs:
                if slug not in slugs:
                    slugs.append(slug)
    titles: dict[str, str] = {}
    for slug in slugs:
        if (repo_root / "docs" / "adr" / f"{slug}.md").is_file():
            titles[slug] = f"ADR {slug.split('-', 1)[0]}"
    return titles


def _index_page(entries: list[DocEntry], repo_root: Path) -> str:
    by_category: dict[str, list[DocEntry]] = {key: [] for key in _CATEGORY_ORDER}
    for entry in entries:
        by_category.setdefault(entry.category, []).append(entry)

    body = ['<header class="topbar">',
            '<span class="brand">harness</span>',
            '<button id="theme-toggle" type="button" aria-label="Toggle theme">◑</button>',
            '</header>',
            '<main class="explorer">',
            _port_catalogue(_adr_titles(repo_root)),
            "</main>",
            ]

    body.append('<section class="doc-index">')
    for category in _CATEGORY_ORDER:
        group = sorted(by_category.get(category, []), key=lambda e: e.sort_key)
        if not group:
            continue
        body.append(f"<h2>{html.escape(_CATEGORY_TITLES[category])}</h2>")
        body.append("<ul>")
        for entry in group:
            href = f"{entry.category}/{entry.output_name}"
            body.append(
                f'<li><a href="{html.escape(href)}">{html.escape(entry.title)}</a></li>'
            )
        body.append("</ul>")
    body.append("</section>")

    body.append('<script src="assets/app.js" defer></script>')

    return _shell("harness — architecture explorer", "\n".join(body),
                  css_href="assets/app.css", body_class="page-explorer")


def _doc_page(entry: DocEntry) -> str:
    text = entry.source_path.read_text(encoding="utf-8")
    body = [
        '<header class="topbar">',
        '<a class="brand" href="../index.html">← harness</a>',
        '<button id="theme-toggle" type="button" aria-label="Toggle theme">◑</button>',
        '</header>',
        '<article class="doc">',
        render(text),
        '</article>',
        '<script src="../assets/app.js" defer></script>',
    ]
    return _shell(entry.title, "\n".join(body),
                  css_href="../assets/app.css", body_class="page-doc")


def build_site(entries: list[DocEntry], repo_root: Path, out_dir: Path) -> None:
    """Write the explorer index, a page per entry under `<category>/`, and the
    copied `assets/` dir. Clears and recreates `out_dir` first."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    shutil.copytree(_ASSETS_DIR, out_dir / "assets")

    (out_dir / "index.html").write_text(
        _index_page(entries, repo_root), encoding="utf-8"
    )

    for entry in entries:
        category_dir = out_dir / entry.category
        category_dir.mkdir(parents=True, exist_ok=True)
        (category_dir / entry.output_name).write_text(
            _doc_page(entry), encoding="utf-8"
        )
