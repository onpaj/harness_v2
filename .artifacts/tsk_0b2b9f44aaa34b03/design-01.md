# Design: ground the architecture in ADRs, refresh README/CLAUDE.md, ship an HTML drill-down

Resolves the plan's open questions with concrete decisions (marked **Decision:**
throughout) and gives the development step exact filenames, section contents, and
module contracts. No production code (`dispatcher.py`, `consumer.py`, `router.py`,
drivers) changes — this is docs plus one small standalone generator.

## UX/UI — the static doc site

This is the only user-facing surface this task adds (the operator board in `api/`
is untouched). It is a set of generated static HTML files, opened directly from
disk (`file://.../site/index.html`) or served by any static file server — no JS
framework, no build step beyond the generator itself.

### Wireframe — index page (`site/index.html`)

```
+--------------------------------------------------------------------+
| harness docs                                                       |
+--------------------------------------------------------------------+
| Architecture Decision Records                                      |
|   0000 · ADR process and numbering                                  |
|   0001 · Ports and adapters                                         |
|   0002 · Three-way split of decision-making                         |
|   0003 · Atomic queue claim() by rename                             |
|   ...                                                                |
|   0012 · StageOutputView, a third read-only UI surface               |
|                                                                       |
| Phase specs                                                         |
|   2026-07-19 · Orchestration phase 1 — design                       |
|   2026-07-19 · Board UI — design                                    |
|   2026-07-20 · Orchestration phase 2 — design                       |
|   ...                                                                |
|                                                                       |
| Phase plans                                                         |
|   2026-07-19 · Orchestration phase 1                                |
|   2026-07-19 · Board UI                                             |
|   ...                                                                |
|                                                                       |
| Project docs                                                        |
|   README                                                             |
|   CLAUDE.md                                                          |
+--------------------------------------------------------------------+
```

Each row is a single `<a href="...">`; there is no search box, no JS, no client
routing — a directory listing rendered as HTML, grouped and titled. Rows within a
category are sorted the way that category is naturally ordered: ADRs numerically
by their filename prefix, specs/plans chronologically by their filename's leading
date, project docs in a fixed order (`README`, `CLAUDE.md`).

### Wireframe — one document page (`site/adr/0006-worktree-vs-artifact-folder-split.html`)

```
+--------------------------------------------------------------------+
| « index                                                             |
+--------------------------------------------------------------------+
| ADR-0006: Worktree vs. artifact-folder split                        |
|                                                                       |
| Status: Accepted                                                     |
|                                                                       |
| ## Context                                                           |
| ...rendered markdown body...                                        |
|                                                                       |
| ## Decision                                                          |
| ...                                                                  |
|                                                                       |
| ## Consequences                                                      |
| ...                                                                  |
+--------------------------------------------------------------------+
```

Every generated page carries exactly one navigation element: a `« index` link
back to `index.html` (relative, so the whole `site/` directory is relocatable).
Cross-references between docs (e.g. an invariant's `See ADR-0006` in `CLAUDE.md`,
or an ADR citing `docs/superpowers/specs/...`) render as **plain inline text**,
not links — **Decision:** the generator does not resolve cross-document
Markdown links into site-relative hyperlinks in this pass (see Component design
→ Markdown converter, "what it deliberately does not do"). This keeps FR-5's
scope to rendering + one index, matching the plan's "drill-down," not a full
wiki with backlinks.

### Component hierarchy (generation-time, not runtime — there is no browser JS)

```
scripts/build_docs.py                  (entry point, argparse: --out DIR)
└─ harness_docs_site/                  (new standalone package, see below)
   ├─ corpus.py     — discover_docs() -> list[DocEntry]
   ├─ markdown.py   — render(markdown_text: str) -> str  (HTML fragment)
   └─ site.py       — build_site(entries, out_dir) -> None (writes index.html + one file per entry)
```

There's no client-side component tree because there's no client-side code —
this is server-side-rendered-once-and-frozen output.

## Component design

### 1. ADR corpus (`docs/adr/`)

**Decision:** `docs/adr/0000-adr-process.md` (not a bare `README.md`) is the
process document — keeping it in the same numbered sequence means the doc-site
corpus discovery (which globs `docs/adr/*.md`) picks it up automatically as
just another entry, instead of needing a special case for "the one ADR file
without a number." It renders in the site like any other ADR, titled "ADR-0000:
ADR process and numbering."

Fixed template every ADR file follows, in order:

```markdown
# ADR-NNNN: <Title>

Status: Accepted

## Context

...

## Decision

...

## Consequences

...
```

- `NNNN` is a zero-padded four-digit sequence number, matching the filename
  prefix (`0001-<slug>.md`). Numbers are never reused or renumbered; a
  superseded decision gets ADR `0000+k`'s `Status` line replaced with
  `Status: Superseded by ADR-00xx`, and the new ADR's `Context` says what it
  supersedes and why.
- `Status` is one line, one of `Accepted` / `Proposed` / `Superseded by ADR-NNNN`.
  All 12 ADRs from FR-2 ship as `Accepted` — they document decisions already
  load-bearing in the shipped code, not proposals.
- `<slug>` is lowercase, hyphenated, derived from the title.

The twelve ADRs (topics fixed by the plan's FR-2; filenames and grounding below
are this design's contribution):

| # | Slug | Grounded in |
|---|---|---|
| 0001 | `ports-and-adapters` | `ports/*`, `tests/test_architecture.py::test_ports_do_not_import_drivers`, `test_orchestration_does_not_import_drivers`, `test_only_app_and_cli_wire_drivers`; invariant #1 |
| 0002 | `three-way-decision-split` | `ConsumerBehavior` (outcome), `dispatcher.py` (routing), `consumer.py` (delivery only); `tests/test_architecture.py::test_consumer_has_no_branch_on_outcome_value`; invariant #2 |
| 0003 | `atomic-queue-claim-by-rename` | `drivers/fs_queue.py`'s `claim()` (`os.replace` into `.processing/`); invariant #6 (recover-before-hydrate) and the "Gotchas" note on per-queue `.processing/` |
| 0004 | `pure-router` | `router.py`, `tests/test_architecture.py::test_router_only_knows_models`; invariant #4 |
| 0005 | `ui-never-imports-a-driver` | `api/*`, `projection.py`, `tests/test_architecture.py::test_api_does_not_import_drivers`, `test_projection_does_not_import_drivers`, `test_api_reads_artifacts_only_through_view`; invariant #5, #11 |
| 0006 | `worktree-vs-artifact-folder-split` | `ports/workspace.py`, `artifacts_layout.py`, `drivers/worktree_artifacts.py`; invariants #9, #10, #16; phase 2→3 transition (separate `ArtifactStore` → artifacts inside the worktree, versioned, attempt-indexed) |
| 0007 | `agent-persona-as-data` | `ports/agent.py` (`AgentSpec`/`AgentCatalog`), `behaviors/agent.py`, `drivers/fs_agents.py`; invariants #13, #14 |
| 0008 | `repository-registry-name-to-path` | `ports/repos.py`, `drivers/fs_repos.py`; invariant #15 |
| 0009 | `landing-proposes-never-touches-main` | `behaviors/landing.py`, `ports/forge.py`, `drivers/github_forge.py`, `drivers/fake_forge.py`; invariant #12; the "Landing is idempotent" / "Landing needs a pushable remote" / "A failed PR fails the task" gotchas |
| 0010 | `tasksource-single-external-port` | `ports/source.py`, `source_poller.py`, `drivers/source_reflector.py`, `drivers/github_source.py`; invariants #18, #19, #20; the "`poll()` claims... `_claimed` ledger" gotcha |
| 0011 | `taskcontrol-write-side-of-boardview` | `ports/control.py`, `task_control.py`, `tests/test_architecture.py::test_orchestration_does_not_import_control`; invariant #23 |
| 0012 | `stageoutputview-third-ui-surface` | `ports/logs.py`, `drivers/stage_output.py`, `api/routes.py`'s `/api/tasks/{id}/output/events`; no existing invariant names this — the ADR itself is where this decision first gets written down (see FR-3 below, which points invariant text at it) |

Each ADR's **Context** section states the problem in the terms the code actually
uses (e.g. ADR-0003's Context describes the crash-recovery problem `claim()`
solves, not a generic "we need thread safety" framing), **Decision** states the
rule as this repo enforces it today (quoting the guarding test where one exists),
**Consequences** notes at least one thing the decision forecloses (e.g. ADR-0009:
"the harness itself can never merge — a human always reviews the PR").

### 2. `CLAUDE.md` updates

**Module map table** — add seven rows (matches the plan's reconciled count):

| Layer | New row content |
|---|---|
| Ports | `ports/control.py`, `ports/logs.py` added to the existing Ports row |
| Orchestration | `task_control.py` added alongside `dispatcher`, `consumer`, `source_poller` |
| Drivers | `drivers/composite_events.py`, `drivers/git_remote.py`, `drivers/projection_events.py`, `drivers/stage_output.py` added to the existing Drivers row |

**Decision:** these are edits to the existing table's cell contents (appending
to the comma-separated lists in the Ports/Orchestration/Drivers rows), not new
rows — the table's row axis is the layer, not the module, matching its current
shape.

**"What is responsible for what"** — add one bullet after the existing
`ArtifactStore`/`BoardView` bullets:

> - **`StageOutputView`** is a third, read-only UI surface alongside `BoardView`
>   and `ArtifactView`: where `BoardView` shows *where* a task is and
>   `ArtifactView` shows *what it produced*, `StageOutputView` shows *what the
>   running stage is doing right now* — a bounded, in-memory, live-only tail
>   (`drivers/stage_output.py`), gone once the stage ends. See ADR-0012.

**Invariant cross-references** — append `See ADR-NNNN.` to the end of the
invariant line (not a new paragraph) for every invariant that now has a
grounding ADR:

| Invariant # | Append |
|---|---|
| 1 | `See ADR-0001.` |
| 2 | `See ADR-0002.` |
| 4 | `See ADR-0004.` |
| 5 | `See ADR-0005.` |
| 9, 10, 16 | `See ADR-0006.` (all three describe the same worktree/artifact split) |
| 12 | `See ADR-0009.` |
| 13, 14 | `See ADR-0007.` |
| 15 | `See ADR-0008.` |
| 18, 19, 20 | `See ADR-0010.` |
| 23 | `See ADR-0011.` and `See ADR-0012.` (it covers both `TaskControl`/restart and, implicitly, the live-output surface added in the same round — split the sentence if one link reads awkwardly attached to both halves) |

Invariants #3, #6, #7, #8, #11, #17, #21, #22 keep no ADR link — nothing in FR-2's
twelve topics grounds them individually; **Decision:** don't force a mapping,
leave them as invariant-only per the plan's "more can be added later" scope note.
Invariant #6 (recover-before-hydrate) is the *consequence* of the atomic-claim
decision in ADR-0003 covered by its Consequences section, not a separate ADR —
**Decision:** link it too: `See ADR-0003.`

No invariant is renumbered, reworded beyond the trailing link, or deleted.

### 3. `README.md` updates

- **Intro** (currently "Phase 1 is a POC..."): replace with a present-tense
  description — worktrees, real git landing, GitHub issue ingestion, an
  operator board with restart and live output are all shipped, not phase-1
  stand-ins. Keep the same two opening sentences (task/workflow definition);
  replace only the "Phase 1 is a POC" paragraph.
- **Board section**: expand the one sentence ("a click shows metadata and
  history") to also name artifacts, live stage output, and the restart control,
  matching what `_task.html` actually renders.
- **New section, "GitHub issue ingestion"**, placed after "Board" and before
  "How work flows": documents `--github-repo`, `--github-label`,
  `--github-workflow` (`harness run` flags — verify exact flag names against
  `cli.py`'s `run` subparser during development, since this design doesn't
  reproduce argparse's exact strings) and the label states `github_source.py`
  manages (todo/in-progress/done/failed-equivalent labels — development step
  reads `drivers/github_source.py` for the exact label vocabulary rather than
  guessing here).
- **Architecture section**: after the existing port table, add one sentence:
  "See `docs/adr/` for the *why* behind each of these — one Architecture
  Decision Record per load-bearing rule."

**Decision:** no new "Board" subsections are needed structurally — one
expanded paragraph plus the new ADR pointer sentence satisfies FR-4's
acceptance criteria (`--github-repo`, `restart`, "live output" each mentioned
once, link to `docs/adr/` resolves).

### 4. Doc-site generator

**Decision:** lives at `scripts/build_docs.py` (a thin CLI entry) plus a new
importable package `src/harness_docs_site/` (sibling to `src/harness/`, **not**
`src/harness/docs_site.py`) — keeping it a separate top-level package, rather
than a module inside `harness`, means it is trivially excluded from
`tool.setuptools.packages.find`'s `where = ["src"]` scope by... actually
`packages.find` under `where=["src"]` would pick up any top-level package
found there. **Decision:** exclude it explicitly by adding
`exclude = ["harness_docs_site*"]` to `tool.setuptools.packages.find` in
`pyproject.toml`, so `pip install harness` never ships this dev-only code —
satisfying the plan's non-functional "no new production dependency" spirit
(no new *code path* ships either). This also means it needs no entry in
`[project.scripts]`; `scripts/build_docs.py` imports it directly via a
`sys.path` insert of `src/`, the same way `tests/` already resolves `harness`
in editable-install dev environments (confirm the existing test `conftest.py`/
`pytest.ini` path-setup approach and mirror it — do not invent a second
mechanism if one already exists for `tests/`).

Three modules, matching the wireframe's component hierarchy:

**`harness_docs_site/corpus.py`**

```python
@dataclass(frozen=True)
class DocEntry:
    category: str        # "adr" | "spec" | "plan" | "project"
    source_path: Path    # e.g. docs/adr/0006-worktree-vs-artifact-folder-split.md
    title: str           # first "# " heading's text, ADR/heading prefix stripped for display
    sort_key: str        # filename for adr/spec/plan; fixed rank string for project docs
    output_name: str     # e.g. "0006-worktree-vs-artifact-folder-split.html"

def discover_docs(repo_root: Path) -> list[DocEntry]: ...
```

- Globs, in this fixed order: `docs/adr/*.md` (category `adr`), then
  `docs/superpowers/specs/*.md` (category `spec`), then
  `docs/superpowers/plans/*.md` (category `plan`), then the two literal paths
  `README.md`, `CLAUDE.md` (category `project`, in that fixed order — not
  globbed, since there are exactly two and their relative order matters more
  than alphabetical).
- `title` is extracted from the source file's first line matching `^# (.+)$`;
  if none is found the file's stem (with hyphens turned to spaces) is used as a
  fallback so the generator never crashes on a malformed doc.
- Within `adr`, `spec`, `plan`, entries sort by `source_path.name` (which is
  why ADRs are zero-padded and specs/plans are dated `YYYY-MM-DD-...` — both
  schemes sort correctly as plain strings, no date parsing needed).
- **What it deliberately does not do:** no recursive directory walk beyond
  these three fixed locations, no front-matter parsing, no doc-to-doc link
  graph. Adding a new category later (the plan's own Open Questions flag CI/
  publishing as a possible follow-up) means adding one glob line here — the
  function stays a flat list-builder, not a general site-map crawler.

**`harness_docs_site/markdown.py`**

**Decision on converter scope**, grounded in what the actual corpus uses (verified
by grepping `docs/superpowers/**/*.md`, `README.md`, `CLAUDE.md` during this
design step): the source docs use ATX headings (`#`...`######`), paragraphs,
fenced code blocks (```` ``` ````, sometimes with a language tag, e.g. `sh`,
`python`, `jsonc`), bullet lists (`-`) with one level of nesting, numbered lists,
GFM pipe tables (`| a | b |` / `|---|---|` — **the plan under-scoped this**: 8 of
the 14 existing spec/plan files use tables, so the converter must support them,
not just "headings, paragraphs, fences, lists, links" as FR-5's non-functional
note assumed), blockquotes (`>`, used for the plans' "For agentic workers" callout),
inline code (`` `x` ``), bold (`**x**`), italic (`*x*` / `_x_`), and inline links
(`[text](url)`). No footnotes, no nested tables, no HTML blocks, no task-list
checkboxes rendered specially (`- [ ]` renders as a literal bullet with literal
text "[ ] ...", which is acceptable — these appear only inside plan files as
prose about a *different* checkbox convention, not as content this site needs to
make interactive).

```python
def render(markdown_text: str) -> str:
    """Markdown -> HTML *fragment* (no <html>/<body> wrapper — site.py owns the
    page shell). Line-oriented block parser (headings / fences / tables /
    blockquotes / lists / paragraphs by leading-token sniffing), each block's
    text run through a regex-based inline pass (bold, italic, code, links) that
    stops recursing inside a fenced code span or `` inline code ``, so a
    literal `[not a link](...)`-shaped string inside a code sample renders
    unlinked."""
```

- Hand-rolled, no dependency (matches the plan's stated default; confirmed
  correct given the table finding above only changes *scope*, not the
  *approach* — a table row is still line-oriented text, no different in kind
  from a heading line).
- Code fences pass their content through `html.escape` and wrap in
  `<pre><code class="language-{tag}">`; the language tag is cosmetic only
  (no syntax highlighting is added — Decision: out of scope, this is a
  drill-down reader, not a code-hosting UI).
- Fenced blocks containing literal backtick-triples-as-content (the plans'
  meta-examples, e.g. a fence showing another fence) are handled by matching
  fence delimiters by line, not by a global regex — track "inside fence"
  state per line, matching only a line that is *exactly* ```` ``` ```` (plus
  optional language tag on the opening one) at column 0.
- Cross-document links (`[text](../specs/foo.md)`-shaped, or bare invariant
  references like "See ADR-0006") render as **plain text with the markdown
  link syntax stripped** — i.e. `[text](url)` becomes the anchor text `text`
  only, not an `<a href>`, *unless* `url` is an absolute `http(s)://` URL, in
  which case it renders as a normal external `<a href="url">`. **Decision:**
  this is the simplest rule that (a) never produces a broken relative link
  when the target file's `.md` extension doesn't match its `.html` output name
  one-for-one across categories, (b) still makes genuinely external links
  (e.g. README's link to `https://docs.astral.sh/uv/`) clickable, (c) keeps
  the generator's job "render this file's content," not "resolve every other
  file's future output path" (that resolution would need `corpus.py`'s full
  entry list threaded into every single render call, and prose in the plan's
  own Open Questions doesn't ask for backlink resolution — only "drill-down"
  from index to doc, one level).

**`harness_docs_site/site.py`**

```python
def build_site(entries: list[DocEntry], repo_root: Path, out_dir: Path) -> None:
    """Writes out_dir/index.html (categorized links) and, for every entry,
    out_dir/<category>/<output_name> (rendered page with a '« index' back-link).
    Clears and recreates out_dir first (idempotent — re-running the generator
    twice produces the same tree, no stale leftover pages from a renamed doc)."""
```

- Page shell is a single small string template embedded in `site.py` (no
  Jinja dependency — `harness.api` already depends on `jinja2` for the
  *installed* board, but this generator is dev-only and explicitly must not
  pull that dependency into scope; a tiny f-string template is enough for one
  repeated shell).
- Output layout: `site/index.html`, `site/adr/000N-*.html`,
  `site/spec/*.html`, `site/plan/*.html`, `site/project/{readme,claude-md}.html`
  — one subdirectory per category so `output_name` collisions across
  categories are structurally impossible (an ADR and a spec could otherwise
  coincidentally share a stem).
- **Decision: output directory is `site/`** at the repo root (plan's stated
  default), added to `.gitignore`.

**Entry point** — `scripts/build_docs.py`:

```
usage: build_docs.py [--out DIR]   # default DIR = "site"
```

Not wired into `harness`'s installed CLI (`cli.py` / `[project.scripts]`) —
**Decision: confirms the plan's stated default** (a maintainer/CI script, not
an end-user `harness docs build` subcommand), because every one of `cli.py`'s
existing subcommands (`init`, `submit`, `run`, `service`, `update`) acts on a
*running deployment* (`--root`, a workflow, a task queue); this generator acts
on *this repository's own source tree* and has no meaningful `--root` — adding
it to `cli.py` would be the first subcommand with no relationship to a
deployed harness instance, which is exactly the kind of surface growth the
plan's Open Questions flagged as worth avoiding.

### 5. Tests

Three new test files/functions, matching FR-1/FR-3/FR-5's acceptance criteria
one-to-one:

- **`tests/test_adr_docs.py`** — walks `docs/adr/*.md`; for each file asserts
  the filename matches `^\d{4}-[a-z0-9-]+\.md$`, and the file's text contains,
  in order, a line matching `^# ADR-\d{4}: .+`, a `Status:` line, and the three
  headings `## Context`, `## Decision`, `## Consequences` (`0000-adr-process.md`
  is exempted from the three-heading check — it's the process doc, not a
  decision record, though it does still need the numbered-filename and a
  `# ADR-0000: ...` title line so `corpus.py` titles it consistently).
- **`tests/test_claude_md_module_map.py`** — enumerates
  `Path("src/harness").rglob("*.py")` excluding `__init__.py`, and asserts every
  module's dotted path (e.g. `ports/control.py` → `` `ports/control.py` ``)
  appears as a literal substring somewhere in `CLAUDE.md`'s text. **Decision:**
  a substring check against the raw file, not a parsed-table check — cheap,
  matches the plan's non-functional "cheap to keep in sync" requirement, and
  self-updating: the next contributor who adds `src/harness/ports/new_thing.py`
  without touching `CLAUDE.md` gets a failing test naming the exact missing
  path, without this test needing to understand the table's markdown structure.
- **`tests/test_docs_site.py`** — builds a small fixture set (3-4 tiny `.md`
  files under a `tmp_path`, one per category plus one ADR) via
  `harness_docs_site.corpus.discover_docs` pointed at the fixture root and
  `harness_docs_site.site.build_site`, then asserts: `index.html` exists and
  contains an `<a href=...>` for each fixture's expected output path; each
  per-doc page exists, contains its title, and contains a `« index`-style
  back-link; a fixture ADR's `## Context`/`## Decision`/`## Consequences`
  headings appear as rendered HTML headings in its output page.

None of these three tests touches `dispatcher.py`/`consumer.py`/`router.py` or
any driver — satisfying the plan's "no behavior change" non-functional
requirement structurally (they can't regress runtime behavior; they only read
`docs/`, `CLAUDE.md`, and the new standalone package).

## Data schemas

No runtime/domain schema changes (task, queue, event, workflow shapes are
untouched). The schemas introduced are documentation-corpus shapes, all
in-process Python dataclasses used only by the generator and its tests — none
of this is persisted, served over HTTP, or read by `harness run`.

### ADR file (on disk, `docs/adr/NNNN-<slug>.md`)

```
# ADR-NNNN: <Title>
Status: Accepted | Proposed | Superseded by ADR-NNNN

## Context
<prose, cites concrete file(s)/test(s)/invariant number(s)>

## Decision
<prose, states the rule as currently enforced>

## Consequences
<prose, at least one thing this forecloses>
```

Field types, for the test's purposes: `number: str` (4-digit, from filename),
`title: str`, `status: Literal["Accepted", "Proposed"] | str` (a `"Superseded by
ADR-NNNN"` string, not a separate `superseded_by` field — keeping `Status` a
single free-text line, matching how the plan's data model describes it, means
the structure test only ever needs to check for the literal prefix `Status:`,
not parse a second field out of it).

### `DocEntry` (in-memory, `harness_docs_site/corpus.py`)

```python
@dataclass(frozen=True)
class DocEntry:
    category: str        # one of: "adr", "spec", "plan", "project"
    source_path: Path
    title: str
    sort_key: str
    output_name: str      # relative to site/<category>/
```

No `__init__`-time validation beyond what `discover_docs` itself guarantees
(every entry it returns has a `category` in the fixed set and a
`source_path` that exists) — there is no external caller that could hand it
malformed data, so no defensive checks are added (matches this repo's stated
preference against validating internal invariants that can't actually be
violated).

### Generated site tree (on disk, output of `build_site`)

```
site/
  index.html
  adr/
    0000-adr-process.html
    0001-ports-and-adapters.html
    ...
    0012-stageoutputview-third-ui-surface.html
  spec/
    2026-07-19-orchestration-phase1-design.html
    ...
  plan/
    2026-07-19-orchestration-phase1.html
    ...
  project/
    readme.html
    claude-md.html
```

Not committed (`.gitignore`d, per the plan's non-functional stance that this is
a generator, not a published artifact).

## Summary of decisions this design makes (answers to the plan's Open questions)

1. **12 ADRs, one file each** — plan's default confirmed; exact filenames and
   groundings fixed above (table in "ADR corpus").
2. **Generator location:** `scripts/build_docs.py` + new sibling package
   `src/harness_docs_site/`, excluded from the installed wheel via
   `tool.setuptools.packages.find.exclude` — not a `harness docs build`
   subcommand, not `src/harness/docs_site.py`.
3. **Markdown approach:** hand-rolled, no dependency — but scope corrected to
   include GFM pipe tables and blockquotes, which the plan's own non-functional
   note under-counted; verified by direct grep of the existing corpus.
4. **Output directory:** `site/` at repo root, git-ignored, one subdirectory
   per category.
5. **Cross-document links:** rendered as plain text (external `http(s)://`
   links stay live); no link-graph resolution in this pass — out of scope,
   matching FR-5's "drill-down" framing rather than a full wiki.
6. **CI/publishing:** left out of scope, per the plan; nothing in this design
   blocks a later task from pointing a GitHub Pages workflow at `site/`.
