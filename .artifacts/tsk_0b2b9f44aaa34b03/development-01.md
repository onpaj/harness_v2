# Development: ground the architecture in ADRs, refresh README/CLAUDE.md, ship an HTML drill-down

Implements the plan/design/architecture assessment for this task, folding in the
architecture step's two corrections (no `--github-repo` flag; no `conftest.py` to
mirror — both addressed below).

## What was implemented

### 1. ADR set (`docs/adr/`)

- `0000-adr-process.md` — the numbering/status/template convention.
- `0001` through `0012` — one ADR per FR-2 topic (ports-and-adapters, three-way
  decision split, atomic queue `claim()`-by-rename, pure router, UI-never-imports-
  a-driver, worktree-vs-artifact-folder split, agent-persona-as-data,
  `RepositoryRegistry` name→path, landing-proposes-never-touches-main,
  `TaskSource` as the single external port, `TaskControl` as the write-side of
  `BoardView`, `StageOutputView` as a third UI surface). Each cites the concrete
  files/tests/invariants it's grounded in, per the design's table.

### 2. `CLAUDE.md`

- Module map table: added `ports/control`, `ports/logs` to the Ports row;
  `task_control` to the Orchestration row; `composite_events`, `git_remote`,
  `projection_events`, `stage_output` to the Drivers row. Also added a new `UI`
  row (`api/{app,routes}`) — a gap the module-map table had beyond FR-3's stated
  delta (see "Deviation" below).
- Added the `StageOutputView` bullet to "What is responsible for what".
- Appended `See ADR-NNNN.` to every invariant with a grounding ADR (1, 2, 4, 5, 6,
  9, 10, 12, 13, 14, 15, 16, 18, 19, 20, 23). No invariant renumbered or reworded
  beyond the trailing link.

### 3. `README.md`

- Replaced the "Phase 1 is a POC" intro paragraph with a present-tense
  description of the shipped tool (worktrees, real git landing, GitHub
  ingestion, operator board with restart/live output).
- Expanded the Board section to mention artifacts, live stage output and the
  restart control, and to name the three read ports plus `TaskControl`.
- Added a new "GitHub issue ingestion" section, written around the *actual*
  mechanism (per-`repos.json`-entry auto-discovery via each repo's GitHub
  origin, `--github-label`/`--github-workflow`/`--source-poll`, and the
  `harness:todo → harness:queued → <step-label> → harness:pr-open|failed` label
  lifecycle) — not the nonexistent `--github-repo` flag the plan's original
  acceptance bullet named.
- Added the `docs/adr/` pointer sentence after the Architecture table.

### 4. Doc-site generator (`src/harness_docs_site/`, `scripts/build_docs.py`)

- `corpus.py` — `discover_docs(repo_root) -> list[DocEntry]`, globbing
  `docs/adr/*.md`, `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md`,
  then the two literal project docs.
- `markdown.py` — hand-rolled Markdown → HTML fragment converter: headings,
  paragraphs, fenced code blocks, GFM pipe tables, blockquotes, bullet/numbered
  lists, inline bold/italic/code/links (relative links render as plain text,
  `http(s)://` links stay live).
- `site.py` — `build_site(entries, repo_root, out_dir)`: clears and rewrites
  `out_dir`, writing a categorized `index.html` plus one page per document with
  a `« index` back-link.
- `scripts/build_docs.py` — thin CLI (`--out DIR`, default `site`), inserts
  `src/` onto `sys.path` since the package is deliberately excluded from the
  installed wheel.
- `pyproject.toml` — added `exclude = ["harness_docs_site*"]` to
  `tool.setuptools.packages.find`; verified live with `python -m build --wheel`
  that `harness_docs_site` does not appear in the built wheel.
- `.gitignore` — added `site/`.

### 5. Tests

- `tests/conftest.py` — new (first in this repo), inserts `src/` onto
  `sys.path` for `harness_docs_site` import in tests.
- `tests/test_adr_docs.py` — directory exists, filenames match
  `NNNN-slug.md`, every file has a title/status line, every file except
  `0000-adr-process.md` has all three section headings, at least 12 decision
  records exist.
- `tests/test_claude_md_module_map.py` — every `src/harness/**/*.py` module's
  **stem** (not full dotted path — see Deviation below) appears as a substring
  in `CLAUDE.md`.
- `tests/test_docs_site.py` — fixture-driven: `discover_docs`/`build_site`
  against a small tmp tree; asserts index links, per-page titles/back-links,
  ADR heading rendering, idempotent rebuild (renamed doc's stale output is
  gone), and markdown-converter behavior (tables, blockquotes, link handling,
  literal fenced content).

## Deviations from the design (and why)

1. **Module-map completeness test uses each module's bare stem, not its full
   dotted path.** The design's own suggested test compares literal strings like
   `` `ports/control.py` ``, but `CLAUDE.md`'s module map table already lists
   modules via brace notation (`` ports/{queue,workflows,...,control,logs} ``),
   not one fully-qualified path per module — and the architecture assessment
   explicitly said to edit that table in place, not restructure it. A literal
   full-path substring test would therefore fail against the table's existing,
   confirmed-correct shape. The stem-based check (`"control" in text`) is
   permissive enough to match the existing convention while still catching a
   genuinely undocumented module by name.
2. **Added a `UI` row (`api/{app,routes}`) to the module map table.** FR-3's
   acceptance criterion is "every `.py` file under `src/harness/` appears in the
   module map table" — comprehensive, not limited to the stated delta.
   `api/routes.py` was not named anywhere in `CLAUDE.md` (not even the module
   stem), which would have made the new completeness test fail on a pre-existing
   gap unrelated to this task's stated scope. Added the smallest fix that
   satisfies the acceptance criterion without restructuring anything else.
3. **Fixed a markdown-converter bug found during manual verification, not
   anticipated by the design.** The initial line-oriented parser flushed the
   current list (or blockquote) on any wrapped continuation line that lacked its
   own bullet/quote marker — which is exactly how every list item and blockquote
   in this corpus is written (prose wrapped at ~80 columns). This split every
   multi-line bullet into a `<li>` fragment plus a stray following `<p>`. Fixed
   by routing a marker-less non-blank line to the currently open list item or
   blockquote instead of flushing it, only falling through to a fresh paragraph
   when neither is open. Verified by rebuilding the real site and inspecting the
   rendered ADR/plan/spec pages, plus new markdown/table/blockquote unit tests.
4. **Removed a duplicate `<h1>` from generated doc pages.** `site.py`'s first
   draft rendered `<h1>{entry.title}</h1>` above the markdown body, but every
   source document already opens with its own `# Title` line, so the rendered
   body produced a second, redundant `<h1>`. Fixed by letting the rendered
   markdown supply the page's only on-page heading (the wireframe in the design
   also shows exactly one title line per page).

## How to verify

```sh
.venv/bin/pytest -q                     # 486 passed, 1 skipped (unrelated opt-in smoke)
.venv/bin/python scripts/build_docs.py --out /tmp/site   # generates the drill-down
open /tmp/site/index.html               # click through ADRs / specs / plans / project docs
python -m build --wheel && unzip -l dist/*.whl | grep -i docs_site   # confirms no match (excluded)
```

No changes to `dispatcher.py`, `consumer.py`, `router.py`, or any driver's
runtime behavior — this task is docs plus one standalone, non-shipped generator.
