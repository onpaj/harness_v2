import re
from pathlib import Path

from harness_docs_site.corpus import discover_docs
from harness_docs_site.markdown import render
from harness_docs_site.site import build_site


def _write_fixture_tree(root: Path) -> None:
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "superpowers" / "specs").mkdir(parents=True)
    (root / "docs" / "superpowers" / "plans").mkdir(parents=True)

    (root / "docs" / "adr" / "0001-example-decision.md").write_text(
        "# ADR-0001: Example decision\n"
        "Status: Accepted\n\n"
        "## Context\n\nSome context.\n\n"
        "## Decision\n\nSome decision.\n\n"
        "## Consequences\n\nSome consequence.\n",
        encoding="utf-8",
    )
    (root / "docs" / "superpowers" / "specs" / "2026-01-01-example-design.md").write_text(
        "# Example spec\n\nA spec body.\n", encoding="utf-8"
    )
    (root / "docs" / "superpowers" / "plans" / "2026-01-01-example-plan.md").write_text(
        "# Example plan\n\nA plan body.\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Example Readme\n\nHello.\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Example Claude doc\n\nHi.\n", encoding="utf-8")


def test_discover_docs_finds_one_entry_per_fixture_category(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    categories = {entry.category for entry in entries}
    assert categories == {"adr", "spec", "plan", "project"}
    assert len(entries) == 5  # 1 adr + 1 spec + 1 plan + readme + claude.md


def test_build_site_writes_index_with_links_to_every_entry(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    index = (out_dir / "index.html").read_text(encoding="utf-8")
    for entry in entries:
        expected_href = f"{entry.category}/{entry.output_name}"
        assert f'href="{expected_href}"' in index


def test_build_site_writes_a_page_per_entry_with_title_and_back_link(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    for entry in entries:
        page = (out_dir / entry.category / entry.output_name).read_text(encoding="utf-8")
        assert entry.title in page
        assert "index.html" in page  # the "« index" back-link


def test_build_site_renders_adr_headings(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    adr_entry = next(entry for entry in entries if entry.category == "adr")
    page = (out_dir / "adr" / adr_entry.output_name).read_text(encoding="utf-8")
    assert "<h2>Context</h2>" in page
    assert "<h2>Decision</h2>" in page
    assert "<h2>Consequences</h2>" in page


def test_build_site_is_idempotent_and_clears_stale_pages(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    out_dir = tmp_path / "site"
    build_site(discover_docs(tmp_path), tmp_path, out_dir)

    # Rename a doc, discover again, rebuild: the old output must be gone.
    old_plan = tmp_path / "docs" / "superpowers" / "plans" / "2026-01-01-example-plan.md"
    new_plan = tmp_path / "docs" / "superpowers" / "plans" / "2026-02-02-renamed-plan.md"
    old_plan.rename(new_plan)

    build_site(discover_docs(tmp_path), tmp_path, out_dir)
    assert not (out_dir / "plan" / "2026-01-01-example-plan.html").exists()
    assert (out_dir / "plan" / "2026-02-02-renamed-plan.html").exists()


def test_markdown_render_supports_tables_and_blockquotes():
    text = (
        "# Title\n\n"
        "| a | b |\n"
        "|---|---|\n"
        "| 1 | 2 |\n\n"
        "> a callout line\n"
    )
    html = render(text)
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<td>1</td>" in html
    assert "<blockquote>" in html
    assert "a callout line" in html


def test_markdown_render_does_not_linkify_relative_links_but_keeps_external_ones():
    text = "[internal](../specs/foo.md) and [external](https://example.com)"
    html = render(text)
    assert "<a href" not in html.split("and")[0]
    assert '<a href="https://example.com">external</a>' in html


def test_markdown_render_keeps_fenced_content_literal():
    text = "```python\n| not | a | table |\n```\n"
    html = render(text)
    assert "<pre><code" in html
    assert "<table>" not in html


def test_build_site_renders_every_port_and_its_drivers(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    index = (out_dir / "index.html").read_text(encoding="utf-8")
    from harness_docs_site.architecture import MODEL

    for port in MODEL.ports:
        assert f'id="port-{port.id}"' in index
        for driver in port.drivers:
            assert f'id="driver-{driver.id}"' in index
            assert driver.name in index


def test_build_site_groups_ports_in_model_order(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    from harness_docs_site.architecture import MODEL

    import html as _html

    positions = [
        index.index(f'class="group-title">{_html.escape(g)}<') for g in MODEL.groups
    ]
    assert positions == sorted(positions)


def test_build_site_copies_assets_and_links_them(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    assert (out_dir / "assets" / "app.css").is_file()
    assert (out_dir / "assets" / "app.js").is_file()
    index = (out_dir / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/app.css"' in index
    assert 'src="assets/app.js"' in index
    # Doc pages reference the asset one level up.
    adr_entry = next(e for e in entries if e.category == "adr")
    page = (out_dir / "adr" / adr_entry.output_name).read_text(encoding="utf-8")
    assert 'href="../assets/app.css"' in page


def test_build_site_output_has_no_external_urls(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    for path in out_dir.rglob("*"):
        if path.suffix in {".html", ".css", ".js"}:
            text = path.read_text(encoding="utf-8")
            assert "http://" not in text, f"external URL in {path}"
            assert "https://" not in text, f"external URL in {path}"


def test_index_links_only_existing_adr_pages(tmp_path: Path):
    # The fixture tree has only ADR 0001; the shipped model cites others.
    # A missing ADR must be silently skipped, never rendered as a dead link.
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    for href in re.findall(r'href="(adr/[^"]+)"', index):
        assert (tmp_path / "site" / href).parent.name == "adr"
        assert href == "adr/0001-example-decision.html", href


def test_app_css_defines_theme_and_port_styles(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    css = (tmp_path / "site" / "assets" / "app.css").read_text(encoding="utf-8")
    assert '[data-theme="light"]' in css
    assert "prefers-reduced-motion" in css
    for selector in ("details.port", "details.driver", ".port-grid", ".group-title"):
        assert selector in css, selector


def test_app_js_is_an_enhancement_with_no_external_calls(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    js = (tmp_path / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "details.port" in js  # binds to the server-rendered catalogue
    assert "fetch(" not in js  # fully client-side, no network


def test_explorer_index_structure_snapshot(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    # Containers the CSS/JS bind to must all be present.
    for needle in ('id="theme-toggle"', 'class="ports"', 'class="ports-intro"',
                   'class="port-grid"', "<details class=\"port\"",
                   "<details class=\"driver\"", 'class="doc-index"'):
        assert needle in index, needle
    # The catalogue renders ports only at the top level — no graph remnants.
    for gone in ('id="hexmap"', 'id="token"', 'id="play"', 'id="drawer"'):
        assert gone not in index, gone
