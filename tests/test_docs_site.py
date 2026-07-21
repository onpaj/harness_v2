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
