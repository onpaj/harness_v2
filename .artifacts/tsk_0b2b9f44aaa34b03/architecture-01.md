# Architecture assessment: ground the architecture in ADRs, refresh README/CLAUDE.md, ship an HTML drill-down

Reviewed against the current tree (`src/harness/`, `docs/`, `tests/`, `pyproject.toml`,
`README.md`, `CLAUDE.md` as they exist on disk today, not as summarized in the plan).
The plan and design are sound in shape and unusually well-grounded — most FR-2/FR-3
claims check out verbatim against the code. This assessment exists to catch the
handful of places the design's stated *rationale* doesn't survive contact with the
tree, and to turn its remaining open decisions into instructions a developer can
follow without re-deriving them.

**Verdict: proceed, with three corrections below (§1–§3) folded into FR-2/FR-4/the
generator's plumbing before writing code.** Everything else in the design — the ADR
template, the corpus/markdown/site split, the file layout, the test plan — is
confirmed against the tree and should be built as specified.

## 1. Alignment check — what the design got right

Verified directly against `src/harness/`:

- **FR-3's module-map delta is exactly right.** `CLAUDE.md`'s current Ports row
  (line 112) is missing `control`/`logs`; Orchestration (113) is missing
  `task_control`; Drivers (115) is missing `composite_events`, `git_remote`,
  `projection_events`, `stage_output`. All seven files exist under `src/harness/`
  and nowhere else in the table. No eighth gap, no false positive.
- **The ADR groundings in FR-2 name real files and real tests.** Spot-checked
  `tests/test_architecture.py`: `test_orchestration_does_not_import_control`,
  `test_router_only_knows_models`, `test_api_reads_artifacts_only_through_view`,
  `test_consumer_has_no_branch_on_outcome_value` all exist verbatim as named.
  `ports/control.py` and `ports/logs.py` read exactly as ADR-0011/0012 describe them
  (`TaskControl.restart`, `StageOutputView.tail`/`subscribe`) — ADR-0012 is correctly
  flagged as the first place this decision gets written down; no existing invariant
  names it before this task.
- **README staleness is worse than the plan states, not better.** The plan says the
  Board section is "true but incomplete." In fact `README.md` today has zero mentions
  of GitHub ingestion, worktrees, artifacts, restart, or live output anywhere, and
  still opens with "Phase 1 is a POC ... Real agents, persistent storage, and git
  arrive in later phases" — flatly wrong for the shipped tool. FR-4's rewrite is not
  optional polish; treat the intro paragraph replacement as the highest-value single
  edit in this task.
- **The Markdown converter's corrected scope (tables + blockquotes) is confirmed.**
  `grep` across `docs/superpowers/**/*.md` + `README.md` + `CLAUDE.md`: 10 files use
  pipe tables (not 8 as the design estimated — even more reason to keep them in
  scope), 7 files use `>` blockquotes (the plans' "For agentic workers" callout line).
  Build the converter for both from the start; do not treat them as a stretch goal.
- **The `pyproject.toml` exclusion is necessary, not defensive-programming.**
  `[tool.setuptools.packages.find]` currently has `where = ["src"]` with no
  `include`/`exclude` — i.e. auto-discovery. Adding `src/harness_docs_site/__init__.py`
  without excluding it *will* ship it in the wheel. The design's
  `exclude = ["harness_docs_site*"]` fix is required, not optional hardening.

## 2. Correction — there is no `--github-repo` flag; don't invent one

The design's FR-4 guidance ("documents `--github-repo`/`--github-label`/
`--github-workflow`... verify exact flag names against `cli.py`'s `run` subparser
during development") correctly deferred the verification — but the plan's own
acceptance criterion ("README mentions `--github-repo`... at least once") assumes a
flag that **does not exist**. Reading `cli.py`'s `run` subparser (lines 756–789)
directly:

- There is no per-repo `--github-repo` flag at all. GitHub ingestion is **automatic
  per entry in `repos.json`**: `_github_sources()` (cli.py:339) builds one
  `GithubTaskSource` for every repository already registered via
  `RepositoryRegistry` whose git origin resolves to a GitHub slug
  (`drivers/git_remote.py:github_slug`); a repo with no GitHub origin is skipped
  with a warning, not opted out via a flag.
- The real flags are `--github-label` (default `"harness:todo"`, the select label),
  `--github-workflow` (default `"default"`, which workflow a newly ingested issue
  starts on), and `--source-poll` (default `30.0`s, the poll interval — deliberately
  coarser than `--poll` to respect rate limits).
- The label vocabulary `github_source.py` manages: `harness:todo` (select) →
  `harness:queued` (claimed) → per-step labels from `DEFAULT_STEP_LABELS`
  (`development` → `harness:in-progress`, `review` → `harness:in-review`, `land` →
  `harness:landing`) → `harness:pr-open` on success or `harness:failed` on failure.
  Foreign labels (e.g. `bug`, `priority`) are untouched — only labels in this managed
  set are ever added/removed.

**Development-step instruction:** write the new README "GitHub issue ingestion"
section around *this* mechanism — "the harness watches every repo in `repos.json`
that has a GitHub origin; select which issues to pull with `--github-label`" — not
around a `--github-repo` flag. Treat the plan's acceptance bullet as superseded by
this finding: require the section to mention `--github-label` and the label
lifecycle instead of a nonexistent flag; still require `restart` and "live output"
each mentioned once, per FR-4, both of which are real (`ports/control.py`,
`ports/logs.py`, wired into `api/routes.py:141` and `:158`).

## 3. Correction — there is no existing sys.path mechanism for `scripts/` or `tests/` to "mirror"

The design tells the generator to import `harness_docs_site` "the same way `tests/`
already resolves `harness` in editable-install dev environments... confirm the
existing test `conftest.py`/`pytest.ini` path-setup approach and mirror it." That
mechanism doesn't exist to mirror: **there is no `conftest.py` anywhere in this repo**
(root or `tests/`), and `pytest.ini`-equivalent config (`[tool.pytest.ini_options]` in
`pyproject.toml`) does no path manipulation either. `tests/` imports `harness.*`
successfully only because `pip install -e ".[dev]"` installs the `harness` package
itself in editable mode — and `harness_docs_site` is deliberately *excluded* from
that same package discovery (§1), so it will not be import-reachable through the
editable install no matter how `harness` currently resolves.

Concretely, that means two independent call sites need their own explicit
`sys.path` entry for `src/`, since neither can lean on packaging:

- **`scripts/build_docs.py`** — a standalone script invoked directly (`python
  scripts/build_docs.py`), not through pytest or an installed entry point. It must
  do `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))` (or
  equivalent) before `import harness_docs_site`.
- **`tests/test_docs_site.py`** — needs the same import and currently has nothing to
  inherit it from. **Recommendation:** rather than duplicating the `sys.path` hack
  inside the test file, add `tests/conftest.py` — the first one in this repo — whose
  sole content is inserting `src/` onto `sys.path` for the test session. This keeps
  the one-off ahead of `harness_docs_site`'s exclusion contained to a single,
  obviously-named file instead of two independent hacks that could drift apart, and
  it does not affect how `harness` itself is imported (that keeps going through the
  editable install, unchanged).

This is a small addition, not a redesign — flagging it only because the design's
stated justification for "just do what already exists" was itself wrong, and a
developer who trusted that sentence at face value would go looking for a
`conftest.py` that isn't there.

## 4. Proposed architecture (confirmed, with the two corrections above folded in)

No change to the design's shape. Components, in dependency order:

```
docs/adr/0000-adr-process.md            (process doc, numbered like any ADR)
docs/adr/0001-...0012-...md             (the 12 ADRs, FR-2)
CLAUDE.md                                (module map + invariant cross-refs, edited in place)
README.md                                (intro/Board/GitHub-ingestion sections, edited in place)

src/harness_docs_site/                   (new sibling package to src/harness/, excluded from the wheel)
├── corpus.py     discover_docs(repo_root) -> list[DocEntry]
├── markdown.py   render(markdown_text) -> str          (headings, paragraphs, fences,
│                                                          GFM tables, blockquotes, lists,
│                                                          inline bold/italic/code/links)
└── site.py       build_site(entries, repo_root, out_dir) -> None

scripts/build_docs.py                    (thin argparse entry point, sys.path-inserts src/)

tests/conftest.py                        (new — sys.path insert of src/, session-scoped)
tests/test_adr_docs.py                   (FR-1 acceptance)
tests/test_claude_md_module_map.py       (FR-3 acceptance, substring check — see §5)
tests/test_docs_site.py                  (FR-5 acceptance, fixture-driven)

pyproject.toml                           (+ tool.setuptools.packages.find.exclude)
.gitignore                               (+ site/)
```

`harness_docs_site` has **zero dependency on `harness`** — it only reads Markdown
files and writes HTML files, so it must not `import harness` for anything (not even
`artifacts_layout` or `models`). This keeps it genuinely standalone and means it
would still work if pointed at a fixture tree that has no relationship to the real
`src/harness/` package, which is exactly what `tests/test_docs_site.py` does.

Data flow is one-directional and generation-time only: `corpus.discover_docs()` walks
four fixed glob locations → returns `DocEntry` records → `site.build_site()` reads
each `source_path`, calls `markdown.render()` on its text, wraps the fragment in a
shared page-shell string template, writes it under `out_dir/<category>/`, and writes
`out_dir/index.html` grouping all entries by category. There is no second pass, no
link-graph resolution, no incremental build — every invocation clears and rewrites
`out_dir` from scratch (idempotent, per the design).

## 5. Implementation guidance

**ADRs (FR-1/FR-2).** Write `docs/adr/0000-adr-process.md` first — it fixes the
template every other file copies. Then the 12 ADRs in the design's table order
(0001 ports-and-adapters ... 0012 stageoutputview). For each, open the file(s) it's
grounded in before writing prose — the design's citations all resolved to real files
in this review, but the *content* of e.g. `behaviors/landing.py`'s idempotency
handling or `ports/repos.py`'s exact contract should come from reading them, not from
re-deriving them from the invariant text a second time. Keep Context/Decision/
Consequences terse — three short paragraphs, not an essay; these are meant to be
skimmed from the HTML drill-down.

**`CLAUDE.md` edits.** Follow the design's table verbatim (§"Module map table",
"What is responsible for what", "Invariant cross-references") — all three were
re-verified against the live file in this review and are correct as specified. Edit
existing lines in place; do not renumber or restructure the 23 invariants.

**`README.md` edits.** Follow FR-4/the design's section list, with §2's correction
applied to the GitHub-ingestion section: describe the per-`repos.json`-entry
auto-discovery mechanism and the `harness:*` label lifecycle, not a `--github-repo`
flag. Keep the intro rewrite factual and present-tense; it's the section most likely
to be read by someone deciding whether to trust this project.

**Doc-site generator.** Build `corpus.py` → `markdown.py` → `site.py` in that order
(each is testable in isolation before the next depends on it). In `markdown.py`,
write the fence-state-tracking line parser first (it gates correct handling of
everything else — a table row or blockquote line inside an unclosed fence must still
render as literal text), then layer in block dispatch (heading / table / blockquote
/ list / paragraph by leading-token sniffing) and the inline pass (bold/italic/code/
link, skipped inside code spans) last. Apply §3's `sys.path` fix in
`scripts/build_docs.py` and add `tests/conftest.py` as described.

**Tests.** Build in the order FR-1 → FR-3 → FR-5 test files, matching the design's
three specs one-to-one; each is independent of the others and of any production
code, so they can be written and run before the doc-site package is finished (the
FR-1/FR-3 tests only need the ADRs/CLAUDE.md edits to exist).

## 6. Risks and mitigations

- **Silent drift resuming right after this task ships.** FR-3's own motivation was a
  module-map that had already drifted once. The `tests/test_claude_md_module_map.py`
  substring check (design §5) is the right mitigation and must land in the same
  commit as the module-map edit, not as a follow-up — otherwise this task repeats the
  exact failure mode it was written to fix.
- **ADR prose contradicting `CLAUDE.md`'s invariant text.** Twelve independently
  written files describing rules that already have canonical wording risk drifting
  from that wording over time. Mitigation: when writing each ADR's Decision section,
  quote (not paraphrase) the relevant invariant sentence from `CLAUDE.md`, then add
  the *why* around it — this makes disagreement structurally visible (a diff) rather
  than a subtle rewording someone has to notice by re-reading both files side by side.
- **The Markdown converter silently mis-rendering a doc it wasn't designed for.**
  Scope is fixed to the corpus's current subset (§1 confirms tables+blockquotes are
  in, footnotes/nested tables/HTML blocks are out). If a future doc introduces one of
  the excluded constructs, the renderer will emit it as literal escaped text rather
  than crash — safe by default, but silently ugly. No action needed now; worth a
  one-line comment in `markdown.py` stating the deliberate non-goals so a future
  contributor doesn't read the omission as an oversight.
- **`sys.path` hack duplicating between `scripts/build_docs.py` and the tests.**
  Addressed in §3 by centralizing the test-side insert into a new `tests/conftest.py`
  rather than repeating it.
- **Wheel bloat / accidental shipping of `harness_docs_site`.** Addressed by the
  `pyproject.toml` exclude (§1); worth a one-line assertion in
  `tests/test_docs_site.py` or a manual `python -m build && unzip -l dist/*.whl`
  check during development to confirm the exclude actually works, since a typo in the
  glob pattern (`harness_docs_site*` vs `harness_docs_site`) would fail silently —
  `packages.find` exclude patterns are fnmatch-style and `harness_docs_site*` is
  correct (matches the package name and any submodules setuptools might otherwise
  discover under it), but this is cheap enough to verify once, live, rather than
  trust by inspection.

## 7. Prerequisites before development starts

None outside this repo. Development can start directly from this assessment plus
the design's file-by-file contents — no further investigation is needed except the
in-flight reads (behavior/driver file contents for ADR prose) that are normal part
of writing each ADR, not a blocking dependency.
