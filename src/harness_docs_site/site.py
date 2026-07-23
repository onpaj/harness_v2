"""Writes the generated Architecture Explorer: an animated explorer index, one
restyled page per document, and the copied static assets.

Idempotent — `build_site` clears `out_dir` first, so re-running the generator
(or rebuilding after a doc rename) never leaves a stale page behind.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from harness_docs_site.architecture import MODEL, model_to_dict
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


def _svg_diagram() -> str:
    parts = {p.id: p for p in MODEL.parts}
    out = ['<svg id="hexmap" viewBox="0 0 100 100" role="img" '
           'aria-label="Architecture map">']
    out.append('<g class="edges">')
    for edge in MODEL.edges:
        a, b = parts[edge.src], parts[edge.dst]
        out.append(
            f'<line class="edge" x1="{a.x}" y1="{a.y}" x2="{b.x}" y2="{b.y}" '
            f'data-src="{edge.src}" data-dst="{edge.dst}"></line>'
        )
    out.append("</g>")
    out.append('<g class="parts">')
    for part in MODEL.parts:
        out.append(
            f'<g class="part" data-part-id="{html.escape(part.id)}" '
            f'data-kind="{html.escape(part.kind)}" tabindex="0" role="button" '
            f'aria-label="{html.escape(part.name)}">'
        )
        out.append(f'<circle class="node" cx="{part.x}" cy="{part.y}" r="4.2"></circle>')
        out.append(
            f'<text class="label" x="{part.x}" y="{part.y + 7.5}" '
            f'text-anchor="middle">{html.escape(part.name)}</text>'
        )
        out.append("</g>")
    out.append("</g>")
    out.append('<circle id="token" r="1.8"></circle>')
    out.append("</svg>")
    return "\n".join(out)


def _adr_html_map(repo_root: Path) -> dict[str, str]:
    """Rendered HTML for every ADR any part cites. A cited slug whose file is
    absent (e.g. under a test fixture) is skipped, never fatal."""
    slugs: list[str] = []
    for part in MODEL.parts:
        for slug in part.adrs:
            if slug not in slugs:
                slugs.append(slug)
    mapping: dict[str, str] = {}
    for slug in slugs:
        path = repo_root / "docs" / "adr" / f"{slug}.md"
        if path.is_file():
            mapping[slug] = render(path.read_text(encoding="utf-8"))
    return mapping


def _json_block(element_id: str, payload: object) -> str:
    # `</` cannot appear inside a <script>; escape the slash defensively.
    text = json.dumps(payload).replace("</", "<\\/")
    return f'<script type="application/json" id="{element_id}">{text}</script>'


def _index_page(entries: list[DocEntry], repo_root: Path) -> str:
    by_category: dict[str, list[DocEntry]] = {key: [] for key in _CATEGORY_ORDER}
    for entry in entries:
        by_category.setdefault(entry.category, []).append(entry)

    body = ['<header class="topbar">',
            '<span class="brand">harness</span>',
            '<button id="theme-toggle" type="button" aria-label="Toggle theme">◑</button>',
            '</header>',
            '<main class="explorer">',
            '<section class="stage">',
            '<div class="controls">',
            '<button id="play" type="button">▶ Play</button>',
            '<button id="step" type="button">Step ›</button>',
            '<span id="caption" class="caption"></span>',
            '</div>',
            _svg_diagram(),
            '<ul class="legend">',
            ]
    for kind in ("port", "driver", "core", "ui", "store"):
        body.append(f'<li data-kind="{kind}"><span class="swatch"></span>{kind}</li>')
    body.append("</ul>")
    body.append("</section>")

    body.append('<aside id="drawer" class="drawer" hidden></aside>')
    body.append("</main>")

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

    body.append(_json_block("model-data", model_to_dict(MODEL)))
    body.append(_json_block("adr-html", _adr_html_map(repo_root)))
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
