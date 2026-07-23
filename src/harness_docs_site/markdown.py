"""Minimal, dependency-free Markdown -> HTML converter.

Scoped to exactly what this repo's own docs use (verified by grepping
`docs/superpowers/**/*.md`, `README.md`, `CLAUDE.md`): ATX headings, paragraphs,
fenced code blocks (with an optional language tag), bullet/numbered lists (one
level of nesting), GFM pipe tables, blockquotes, inline code, bold, italic and
links. Deliberately out of scope: footnotes, nested tables, HTML blocks, and
task-list checkboxes rendered specially (`- [ ]` renders as a literal bullet
whose text happens to start with `[ ]`). A construct outside this scope is not
detected and simply renders as literal text — safe by default, never a crash.

Line-oriented: block type is decided by sniffing each line's leading token, one
line of "inside a fence" state gates everything else so a table row or
blockquote marker inside an unclosed fence still renders as literal fence
content, never as a table/blockquote.
"""

from __future__ import annotations

import html
import re

_ATX = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^```\s*([\w+-]*)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_CELL = re.compile(r"^:?-+:?$")
_BLOCKQUOTE = re.compile(r"^>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+\.\s+(.*)$")

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)|_([^_]+)_")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    """Bold/italic/code/link pass. Skips re-processing inside inline code spans
    by splitting on them first and only running the rest of the pass on the
    non-code segments."""
    parts = _INLINE_CODE.split(text)
    # re.split with one capture group alternates: [plain, code, plain, code, ...]
    out = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            out.append(f"<code>{html.escape(part)}</code>")
            continue
        segment = html.escape(part)

        def _link(match: re.Match[str]) -> str:
            label, url = match.group(1), match.group(2)
            if url.startswith("http://") or url.startswith("https://"):
                return f'<a href="{html.escape(url)}">{label}</a>'
            return label

        segment = _LINK.sub(_link, segment)
        segment = _BOLD.sub(r"<strong>\1</strong>", segment)
        segment = _ITALIC.sub(
            lambda m: f"<em>{m.group(1) or m.group(2)}</em>", segment
        )
        out.append(segment)
    return "".join(out)


def _is_table_separator(line: str) -> bool:
    if not _TABLE_ROW.match(line):
        return False
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(_TABLE_SEP_CELL.match(cell) for cell in cells)


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _render_table(lines: list[str], start: int) -> tuple[str, int]:
    header = _table_cells(lines[start])
    row_index = start + 2  # skip the header row and the separator row
    body_rows = []
    while row_index < len(lines) and _TABLE_ROW.match(lines[row_index]):
        body_rows.append(_table_cells(lines[row_index]))
        row_index += 1

    out = ["<table>", "<thead>", "<tr>"]
    out.extend(f"<th>{_inline(cell)}</th>" for cell in header)
    out.append("</tr>")
    out.append("</thead>")
    out.append("<tbody>")
    for row in body_rows:
        out.append("<tr>")
        out.extend(f"<td>{_inline(cell)}</td>" for cell in row)
        out.append("</tr>")
    out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out), row_index


def render(markdown_text: str) -> str:
    """Markdown -> HTML fragment (no `<html>`/`<body>` wrapper; `site.py` owns
    the page shell)."""
    lines = markdown_text.split("\n")
    out: list[str] = []

    in_fence = False
    fence_lang = ""
    fence_lines: list[str] = []
    paragraph: list[str] = []
    list_lines: list[tuple[str, str]] = []  # (marker kind, text), same list run
    blockquote_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_lines:
            kind = list_lines[0][0]
            tag = "ul" if kind == "bullet" else "ol"
            out.append(f"<{tag}>")
            for _, text in list_lines:
                out.append(f"<li>{_inline(text)}</li>")
            out.append(f"</{tag}>")
            list_lines.clear()

    def flush_blockquote() -> None:
        if blockquote_lines:
            inner = " ".join(blockquote_lines)
            out.append(f"<blockquote><p>{_inline(inner)}</p></blockquote>")
            blockquote_lines.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_blockquote()

    index = 0
    while index < len(lines):
        line = lines[index]

        if in_fence:
            fence_match = _FENCE.match(line)
            if fence_match and fence_match.group(1) == "":
                cls = f' class="language-{fence_lang}"' if fence_lang else ""
                code = html.escape("\n".join(fence_lines))
                out.append(f"<pre><code{cls}>{code}</code></pre>")
                fence_lines.clear()
                in_fence = False
            else:
                fence_lines.append(line)
            index += 1
            continue

        fence_open = _FENCE.match(line)
        if fence_open:
            flush_all()
            in_fence = True
            fence_lang = fence_open.group(1)
            index += 1
            continue

        if not line.strip():
            flush_all()
            index += 1
            continue

        atx = _ATX.match(line)
        if atx:
            flush_all()
            level = len(atx.group(1))
            out.append(f"<h{level}>{_inline(atx.group(2))}</h{level}>")
            index += 1
            continue

        if _TABLE_ROW.match(line) and index + 1 < len(lines) and _is_table_separator(
            lines[index + 1]
        ):
            flush_all()
            table_html, next_index = _render_table(lines, index)
            out.append(table_html)
            index = next_index
            continue

        quote = _BLOCKQUOTE.match(line)
        if quote:
            flush_paragraph()
            flush_list()
            blockquote_lines.append(quote.group(1))
            index += 1
            continue

        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if bullet or numbered:
            flush_paragraph()
            flush_blockquote()
            kind = "bullet" if bullet else "numbered"
            text = (bullet or numbered).group(2)
            list_lines.append((kind, text))
            index += 1
            continue

        # A plain continuation line (wrapped prose with no marker of its own).
        # This corpus wraps list items and paragraphs across multiple lines
        # without repeating a marker, so a continuation belongs to whichever
        # block is currently open, not to a freshly flushed paragraph.
        if list_lines:
            kind, text = list_lines[-1]
            list_lines[-1] = (kind, f"{text} {line.strip()}")
            index += 1
            continue
        if blockquote_lines:
            blockquote_lines.append(line.strip())
            index += 1
            continue

        paragraph.append(line.strip())
        index += 1

    if in_fence:
        # An unclosed fence at EOF: render what was captured as literal text
        # rather than dropping it silently.
        cls = f' class="language-{fence_lang}"' if fence_lang else ""
        code = html.escape("\n".join(fence_lines))
        out.append(f"<pre><code{cls}>{code}</code></pre>")

    flush_all()
    return "\n".join(out)
