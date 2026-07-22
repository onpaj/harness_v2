# Plan: ground the architecture in ADRs, refresh README/CLAUDE.md, ship an HTML drill-down

## Summary

The harness's design decisions currently live only as prose invariants inside
`CLAUDE.md` and as a trail of phase specs/plans under `docs/superpowers/`. There is no
standalone, numbered record of *why* each decision was made, `README.md` describes a
phase-1-era tool (no GitHub ingestion, no operator restart, no live stage output), and
`CLAUDE.md`'s own module map has drifted behind `src/harness/`. This task (a) writes a
set of Architecture Decision Records (ADRs) that ground the existing invariants in
context/decision/consequences form, (b) brings `README.md` and `CLAUDE.md` back in
sync with what the code actually does, and (c) ships a static, dependency-free HTML
page that lets a reader drill down from an overview into any ADR or phase doc without
running the harness.

## Context

The project has been built phase by phase (1 → 4, plus at least one further round that
added operator control and live output) and each phase's spec/plan is preserved under
`docs/superpowers/`. That is a good historical record but a poor entry point: a new
contributor (or a later phase's design step) has to reconstruct *why* e.g. artifacts
moved from a separate store (phase 2) into the worktree (phase 3), or why the router
must stay a pure function, by reading prose invariants and cross-referencing dated
files. `CLAUDE.md`'s "Invariants — do not break" section (23 entries) is effectively an
undated, unnumbered decision log already — this task turns the durable subset of it
into proper ADRs, one decision per file, so future changes can supersede a specific one
instead of editing a shared wall of text.

Two concrete staleness bugs motivate the refresh, found by diffing `CLAUDE.md`'s module
map against `src/harness/`:

- **Module map gaps.** `ports/control.py` and `ports/logs.py` are missing from the
  Ports row; `drivers/composite_events.py`, `drivers/git_remote.py`,
  `drivers/projection_events.py` and `drivers/stage_output.py` are missing from the
  Drivers row; `task_control.py` (a core service alongside `dispatcher`/`consumer`/
  `source_poller`) is missing from the Orchestration row.
- **Undocumented capability.** The board's task detail (`api/templates/_task.html`)
  already renders a "live output" panel streamed via `StageOutputView`/SSE
  (`api/routes.py:52-69`, `/api/tasks/{id}/output/events`) and a "Restart" button
  wired to `TaskControl.restart` — neither the `StageOutputView` port nor the
  restart/live-output UI is mentioned anywhere in `CLAUDE.md` or `README.md`. `grep`
  for `StageOutputView`/`TaskControl`/`stage_output`/`task_control` against
  `CLAUDE.md` turns up nothing except one incidental match of the word "restart" in
  invariant 23.

`README.md`'s "Board" section still says a click "shows metadata and history" (true
but incomplete — it omits artifacts, live output and restart) and the tool's framing
throughout is "Phase 1 is a POC" language with no mention of GitHub issue ingestion
(`harness run --github-repo ...`) or operator control, both of which ship today.

## Functional requirements

**FR-1 — ADR set under `docs/adr/`.**
Create `docs/adr/` with one Markdown file per decision, numbered
`0001-<slug>.md`, `0002-<slug>.md`, … (zero-padded, sequential, never renumbered —
a superseded ADR gets a new number and a `Superseded-by` link, not a deletion).
Each file follows a fixed template: `# ADR-NNNN: <Title>`, `Status` (`Accepted`
unless stated otherwise), `Context`, `Decision`, `Consequences`. A short
`docs/adr/README.md` (or `0000-...`) explains the numbering/status convention.
*Acceptance:* every file present validates against the template (four required
sections in order); a lightweight test (e.g. `tests/test_adr_docs.py`) asserts the
directory exists, every file matches the numbering pattern, and every file contains
all four section headers.

**FR-2 — ADR content covers the load-bearing invariants, grounded in current code.**
At minimum, one ADR each for: (1) ports-and-adapters — no driver import in
`dispatcher.py`/`consumer.py`, enforced by `test_architecture.py`; (2) the three-way
split of decision-making (behavior/dispatcher/consumer); (3) the filesystem queue's
atomic `claim()`-by-rename and per-queue `.processing/`; (4) the router as a pure
function; (5) the UI (`api/`, `projection.py`) never importing a driver; (6) the
worktree-versus-artifact-folder split introduced in phase 2 and its phase-3
consequence (artifacts moved into the worktree, versioned, attempt-indexed); (7) the
agent-as-persona-data model (`AgentSpec`/`AgentCatalog`, no branch on agent name);
(8) `RepositoryRegistry` as the name→path indirection; (9) landing as an idempotent
step that only proposes, never touches `main`; (10) `TaskSource` as the single
external-world port (`poll`/`report_progress`/`finish`) with task origin carried in
`task.data.source`; (11) `TaskControl` as the write-side counterpart of `BoardView`,
and restart as a reset rather than a routing decision; (12) `StageOutputView` as a
third, read-only UI surface distinct from `BoardView`/`ArtifactView`. Each ADR's
Context/Decision cites the actual file(s)/invariant number(s) it grounds, so it reads
as documentation of a real decision, not a rephrasing exercise.
*Acceptance:* each of the 12 topics above has exactly one ADR; each ADR names at
least one concrete file or test it is grounded in.

**FR-3 — `CLAUDE.md` module map and invariants brought current.**
Add the six missing modules (`ports/control.py`, `ports/logs.py`,
`drivers/composite_events.py`, `drivers/git_remote.py`,
`drivers/projection_events.py`, `drivers/stage_output.py`, `task_control.py` — seven,
see note below) to the module map table in their correct layer row. Add a
`StageOutputView` bullet to "What is responsible for what" alongside the existing
`BoardView`/`ArtifactStore` bullets. Add each new ADR file as a cross-reference where
the corresponding invariant already lives (e.g. a `See ADR-000N.` aside), without
deleting or renumbering the existing 23 invariants.
*Acceptance:* every `.py` file under `src/harness/` (excluding `__init__.py`) appears
in the module map table; a test or a documented manual check confirms this (a simple
diff between `find src/harness -name '*.py'` and the table's contents is enough — see
Non-functional requirements on keeping this cheap to re-check).

**FR-4 — `README.md` refreshed to describe the shipped tool.**
Replace the "Phase 1 is a POC" framing in the intro with a description of what the
harness does today (worktrees, real git landing, GitHub issue ingestion, an operator
board with restart and live output). Expand the "Board" section to mention artifacts,
history, live stage output and the restart control. Add a short "GitHub issue
ingestion" section documenting `--github-repo`/`--github-label`/`--github-workflow`
and the label states it manages (mirroring what `github_source.py` actually does).
Add a pointer to `docs/adr/` for readers who want the *why* behind the architecture
table already in the README.
*Acceptance:* README mentions `--github-repo`, `restart`, and "live output" at least
once each; a link to `docs/adr/` resolves to an existing file.

**FR-5 — Static HTML drill-down over the docs.**
Ship a generated, dependency-free static HTML page (or small set of pages) that lets
a reader browse: an index grouped by category (ADRs / phase specs / phase plans) →
click through to a rendered page for one document → a link back to the index. This
is a *documentation* view, deliberately separate from the live operational board
(`api/`) — it must work with the harness not running, e.g. opened straight from the
filesystem or served as a static artifact in CI. It is generated from the Markdown
sources (`docs/adr/*.md`, `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md`,
plus `README.md`/`CLAUDE.md` as top-level entries) — not hand-maintained HTML — so it
cannot drift the way `README.md`/`CLAUDE.md` just did.
*Acceptance:* a single command (e.g. `python -m harness.docs_site` or
`harness docs build`, exact name is a design-step decision — see Open questions)
produces a self-contained output directory; opening its index in a browser and
clicking through reaches every ADR and every phase spec/plan without a running
server; a test renders it against a small fixture set of Markdown files and asserts
the expected links exist in the output.

## Non-functional requirements

- **No new production dependency**, matching the project's established stance (see
  phase-4 plan notes: "No new production dependency... don't reach for `requests`").
  The Markdown → HTML step should either use a minimal hand-rolled converter
  (headings, paragraphs, fenced code blocks, links, lists — sufficient for this
  project's own doc style) or land as a **dev-only** dependency declared in
  `[project.optional-dependencies]` (e.g. an `docs` extra), never in
  `[project.dependencies]`. This is a design-step decision; either is acceptable as
  long as `pip install harness` (no extras) keeps working unchanged.
- **Cheap to keep in sync.** The module-map-vs-`src/` drift that motivated FR-3
  should not recur silently — prefer a test that fails when a new top-level module
  has no table entry, over a comment asking humans to remember.
- **No behavior change.** This is a docs-only task; it must not touch
  `dispatcher.py`, `consumer.py`, `router.py`, or any driver's runtime behavior.
  `.venv/bin/pytest -q` must stay green throughout.
- **English only**, per this repo's standing instruction — ADR prose, template,
  generator code and commit messages are all English.

## Data model

No task/queue/domain data model changes. The only "data" introduced is the ADR
corpus itself:

- **ADR file** — `docs/adr/NNNN-<slug>.md`: `number` (from filename), `title`,
  `status` (`Accepted` | `Superseded` | `Proposed`), `context`, `decision`,
  `consequences`, optional `superseded_by` (an ADR number).
- **Doc corpus (for the HTML drill-down)** — a flat list of `{category, path, title}`
  discovered by globbing `docs/adr/*.md`, `docs/superpowers/specs/*.md`,
  `docs/superpowers/plans/*.md`, plus the two root docs; `category` is derived from
  the containing directory, `title` from the file's first `# ` heading.

## Interfaces

- **CLI (new, optional):** a `harness docs build [--out DIR]` subcommand (or a plain
  `python -m` entry point if the design step prefers not to grow the CLI surface for
  a docs-only concern — open question below) that writes the static site to `DIR`
  (default e.g. `site/` or `.artifacts/docs-site/`, git-ignored).
- **Generated site (static, no server):** `index.html` (categorized list) + one HTML
  file per source document, cross-linked; plain `<a href>` navigation, no JS
  framework required (the existing board already shows the project's comfort with
  a little vanilla JS/htmx if the design step wants live-search or a sidebar, but
  that is not required for "drill-down").
- No changes to `api/routes.py`'s existing endpoints — the operational board and the
  documentation site are separate surfaces and must not be wired together (mixing
  them would let `api/` start knowing about `docs/`, which nothing today requires and
  which would blur "the UI must not know what the harness runs on").

## Dependencies and scope

**Depends on:** nothing outside this repo; purely additive.

**In scope:**
- New `docs/adr/` directory and its contents (FR-1, FR-2).
- Edits to `CLAUDE.md` (module map, invariant cross-references, new bullet) and
  `README.md` (FR-3, FR-4).
- A new, small doc-site generator (module + optional CLI subcommand) and its
  generated-but-not-committed output (FR-5).
- Tests covering ADR structure and the generator's output.

**Out of scope:**
- Any change to runtime behavior, ports, or drivers.
- Publishing/hosting the generated site anywhere (GitHub Pages, CI artifact
  upload) — this task ships the *generator* and a locally-openable result, not a
  deployment. A follow-up task can wire CI if wanted.
- Rewriting or renumbering the existing 23 invariants in `CLAUDE.md` — they stay the
  source of truth for "don't break this"; ADRs add *why*, they don't replace the
  invariant list.
- Retroactively documenting every historical decision — only the load-bearing
  invariants enumerated in FR-2 need an ADR in this pass; more can be added later,
  one file at a time, without touching this task's files.

## Rough plan

1. **Inventory** — enumerate every `.py` under `src/harness/` and diff against
   `CLAUDE.md`'s module map to get the exact FR-3 delta (this plan already did a first
   pass; the design/development step should re-run it against HEAD in case the tree
   moved).
2. **ADR template + numbering convention** — write `docs/adr/0000-...` (or a
   `docs/adr/README.md`) defining Status values and the numbering rule.
3. **Write the 12 ADRs from FR-2**, each grounded in the specific file/test it
   documents; cross-check each one's "Decision" against the corresponding
   `CLAUDE.md` invariant so they don't contradict each other.
4. **Refresh `CLAUDE.md`** — module map rows, the new `StageOutputView`
   responsibility bullet, and `See ADR-000N` pointers next to the invariants that now
   have one.
5. **Refresh `README.md`** — intro framing, Board section, new GitHub-ingestion
   section, link to `docs/adr/`.
6. **Build the doc-site generator** — small Markdown→HTML pass (or `docs` extra
   dependency), an index page grouped by category, cross-linked pages; a
   `harness docs build` subcommand or `python -m` entry point (design step picks
   the exact name/wiring); `.gitignore` the generated output directory.
7. **Tests** — ADR structure test (FR-1 acceptance), module-map-completeness test
   (FR-3 acceptance), generator output test against a small fixture set (FR-5
   acceptance).
8. **Full suite green** — `.venv/bin/pytest -q`; manually open the generated
   `index.html` and click through to confirm every doc is reachable.
9. **Commit(s)** — conventional-commit messages (`docs: ...` for the ADRs/README/
   CLAUDE.md changes; `feat: ...` if the `harness docs build` subcommand is judged
   feature-worthy enough to warrant a minor bump, otherwise fold the generator under
   a `docs:`-prefixed commit if it's framed as tooling in service of documentation —
   design step's call, see Open questions).

## Open questions

- **Exact ADR list beyond the 12 in FR-2** — is 12 the right granularity, or should
  some be split/merged (e.g. "ports-and-adapters" and "UI never imports a driver" are
  closely related)? *Default taken:* keep them as 12 separate, single-topic ADRs —
  finer-grained ADRs age better (one can be superseded without disturbing its
  neighbors).
- **Where the doc-site generator's code lives and how it's invoked** — a new
  `src/harness/docs_site.py` + `harness docs build` CLI subcommand (discoverable,
  consistent with the rest of the tool) versus a standalone `scripts/build_docs.py`
  outside the installed package (keeps a docs-only concern out of the shipped CLI
  surface entirely). *Default taken:* a `scripts/build_docs.py` dev script, not a new
  CLI subcommand — this is a maintainer/CI concern, not something an end user running
  `harness run` needs, and it avoids growing the installed tool's surface (and its
  `--version`/argparse help text) for a docs-only feature. The design step should
  confirm or override this.
- **Markdown→HTML approach** — hand-rolled minimal converter vs. a `docs` extra
  dependency (e.g. `markdown` or `markdown-it-py`). *Default taken:* hand-rolled,
  given this repo's explicit "don't reach for a new dependency" precedent from phase
  4 and the fact that the source docs use a small, consistent subset of Markdown
  (headings, paragraphs, fences, lists, links) — no tables-in-tables or footnotes to
  worry about. If the design step finds the source docs use richer Markdown than
  expected, revisit.
- **Output directory location and `.gitignore`** — proposed `site/` at the repo root
  (short, conventional for static-site generators) vs. nesting under `docs/`. *Default
  taken:* `site/`, git-ignored, kept out of `docs/` so `docs/` stays pure Markdown
  source.
- **CI wiring / publishing** — explicitly out of scope per this plan, but worth
  flagging: if the intent behind "ship" is "make this browsable for the team" rather
  than "make it locally buildable," a follow-up task should add a GitHub Pages
  workflow. *Default taken:* not this task's job; ask the user if the HTML drill-down
  needs to be *hosted* rather than just generatable, since that changes FR-5's
  acceptance criteria materially.
