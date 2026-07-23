# Development — retire `GithubMergeabilityWatcher`

## Prerequisite (FR-0): merged `origin/main`

This worktree's branch tip (`0c8027b`) was 74 commits behind `origin/main` and
contained none of the files this task names — confirmed on disk (no
`drivers/mergeability_watcher.py`, no `drivers/github_conflicts_check.py`, no
`processes/`), exactly as `plan-01.md`/`design-01.md`/`architecture-01.md`
flagged and mandated as FR-0/prerequisite #1.

Ran `git merge origin/main` (merge commit `453066c`, merging `e4485d6`). Full
suite green on the merged tree (1221 passed, 1 skipped) *before* touching
anything else, so any later red would be attributable to this task, not the
merge. **Note for whoever lands this**: `453066c` is a real merge commit,
already present in this branch's history — the harness's own commit step will
land on top of it, not replace it.

Re-ran `git grep -in "mergeab"` after the merge and found zero drift from what
`plan-01.md`/`design-01.md`/`architecture-01.md` had quoted from `origin/main`.

One site none of the three planning documents had checked against: this repo
also ships a docs-site architecture model (`src/harness_docs_site/architecture.py`)
with a `test_shipped_model_is_coherent_with_the_repo` test that validates every
listed driver `sources=(...)` path actually exists on disk. It carried its own
`mergeability-watcher` `Driver` entry pointing at the now-deleted file. Removed
it (see below) — this would otherwise have been an immediate red after FR-1's
deletion that no planning document anticipated.

## Sequencing followed (per `architecture-01.md`'s mandated order)

1. **FR-4 first, before any deletion** — added three new e2e tests to
   `tests/test_processes_e2e.py` proving the `github-conflicts` process path
   reproduces the watcher's three e2e scenarios (full resolve→land on the
   PR's own branch, behind-PR no-op, cross-restart dedup), built against the
   *existing*, unmodified `GithubConflictsCheck`/`FilesystemProcessRepository`
   machinery. Got them green first.
2. Added the one coverage gap `architecture-01.md` identified — no test
   proved `GithubConflictsCheck` threads its own `head_prefix` constructor
   argument through rather than dropping it — as
   `test_skips_a_pr_outside_head_prefix` in `test_github_conflicts_check.py`.
3. **Only then** deleted `drivers/mergeability_watcher.py`,
   `tests/test_mergeability_watcher.py`, `tests/test_mergeability_e2e.py`,
   `cli._mergeability_sources`, the `--watch-mergeability` flag, and the
   `test_cli.py` tests that exercised them.
4. Swept the prose sites (CLAUDE.md, `git_workspace.py`,
   `test_git_workspace.py`, `ports/source.py`) plus two more leftover
   references to the watcher-as-actor that the grep sweep surfaced but the
   planning docs hadn't enumerated (`github_client.py`'s `PullRequestInfo`
   docstring, and a section-header comment in `test_github_client.py`).
5. Added the README's "Autoresolving merge conflicts" section.
6. Full suite green, plus explicit runs of `test_architecture.py`,
   `test_architecture_model.py`, `test_docs_site.py`,
   `test_claude_md_module_map.py`.

## Files changed

**Deleted**
- `src/harness/drivers/mergeability_watcher.py`
- `tests/test_mergeability_watcher.py`
- `tests/test_mergeability_e2e.py`

**`src/harness/cli.py`**
- Removed the `GithubMergeabilityWatcher` import.
- Removed `_mergeability_sources()` whole.
- `_run`'s source composition: `sources = github + reflectors + mergeability`
  → `sources = github + reflectors`.
- `resolver_defined` block's comment reworded to drop the watcher clause; the
  logic (`--resolver-workflow` still read, `resolver_defined` still computed,
  `served_names` still appended) is byte-for-byte unchanged — `--resolver-workflow`
  still exists, since the resolver workflow still needs to be *served* for a
  `github-conflicts` process to have somewhere to route into.
- `--watch-mergeability`/`--no-watch-mergeability` argparse option removed
  entirely (not defaulted off). `--resolver-workflow`'s `help=` text reworded
  to describe the process-only path.

**`src/harness/drivers/git_workspace.py`** — module docstring + 2 inline
comments renamed the causal actor from `GithubMergeabilityWatcher.update_branch`
to `GithubConflictsCheck`'s call to `GithubClient.update_branch`. No logic
change — a diff of `attach()`'s actual git operations before/after is empty.

**`src/harness/ports/source.py`** — `TaskSource.poll()`'s docstring reworded
to cite `GithubConflictsCheck.evaluate()` (a `Check` driving a `ScheduledTrigger`,
not a `TaskSource` itself) as the precedent for a side-effecting, task-less
poll action, replacing the watcher citation. Worded precisely to not imply
`GithubConflictsCheck` itself implements `poll()`.

**`src/harness/drivers/github_client.py`** — `PullRequestInfo`'s docstring
("as the mergeability watcher sees it") renamed to `GithubConflictsCheck`
(not called out by name in any planning doc, but a real leftover reference to
the removed actor, caught by the post-sweep grep).

**`src/harness_docs_site/architecture.py`** — removed the `mergeability-watcher`
`Driver` entry from the docs-site architecture model (the coverage gap noted
above). No other entry touched.

**`CLAUDE.md`**
- Invariant #31: the clause naming `GithubMergeabilityWatcher.update_branch`
  now names `GithubConflictsCheck`'s call to `GithubClient.update_branch`. The
  reset behavior described afterward — hard-reset on create-path reuse,
  ancestry-aware reconciliation on override-reattach, the `#86` reference — is
  unchanged wording, only the causal actor's name changed.
- Module map: dropped `mergeability_watcher` from the `Drivers` row.
- "What is responsible for what": deleted the `drivers/mergeability_watcher.py`
  bullet outright; reworded `github_conflicts_check.py`'s trailing clause from
  "the check-based replacement for the bespoke `mergeability_watcher` detection"
  to "the only conflict-detection path — an operator opts in by authoring a
  `processes/*.json` naming this action".
- `docs/adr/0014-triggers-produce-tasks-not-placements.md` deliberately left
  untouched (point-in-time historical record, per the plan's explicit
  non-change AC).

**`README.md`** — new "Autoresolving merge conflicts" section immediately
after "GitHub issue ingestion", documenting the breaking change
(`--watch-mergeability` is gone) and providing the copy-pasteable
`processes/autoresolver.json` template. This is documentation only — no file
is written by `harness init`, since a `github-conflicts` process with no
`GITHUB_TOKEN` fails the run fast (`ProcessValidationError`), and several
existing `test_cli.py` tests call `main(["run", ...])` with no token set and
assert a `0` return code.

**`tests/test_github_conflicts_check.py`** — added
`test_skips_a_pr_outside_head_prefix`: builds a `dirty` PR with the default
`harness/` head but constructs the check with `head_prefix="release/"`, and
asserts `evaluate()` returns `[]` — proving the check threads its own
`head_prefix` argument through to `list_pull_requests` rather than silently
dropping it (the parity gap `architecture-01.md` identified; no prior test
constructed the check with a non-default `head_prefix`).

**`tests/test_processes_e2e.py`** — added three parity tests replacing
`test_mergeability_e2e.py`'s scenarios, built from a
`processes/autoresolver.json`-shaped process compiled via
`FilesystemProcessRepository.build(checks={**BUILTIN_CHECKS, "github-conflicts":
<factory>})` (mirroring `cli._process_sources`'s `github_conflicts_factory`,
not a hand-built watcher):
- `test_dirty_pr_via_autoresolver_process_flows_through_resolver_to_a_single_pr_on_the_same_branch`
  — full resolve→land flow; asserts exactly one `done` task with
  `status == "end"`, correct `data.source`, and that `GitWorkspace`'s
  branch-override fired (`workspace.handles[task.id].branch` equals the PR's
  own head branch, not a fresh `harness/<id>` branch).
- `test_behind_pr_via_autoresolver_process_is_auto_updated_with_no_task_created`
  — behind PR → `client.update_branch` called, no task, `done/`/`tasks/` empty.
- `test_restart_does_not_duplicate_the_autoresolver_task_for_the_same_conflict`
  — drives one dirty PR to `done`, then builds a **fresh** `ScheduledTrigger`
  (compiled from the same process file, fresh `GithubConflictsCheck` instance
  with an empty in-process `_seen`) and a fresh `SourcePoller` seeded from
  every task now on disk; asserts the next `tick()` is `False` — proving the
  cross-restart dedup rides `ScheduledTrigger`'s `per-state` `dedup_key` +
  `SourcePoller._seen`, not the check's own in-process ledger.

**`tests/test_cli.py`** — removed the `_mergeability_sources` import,
`_mergeability_args` helper, and the three `test_mergeability_sources_*`
tests (their coverage is superseded by `test_github_conflicts_check.py`'s
unit tests plus the new e2e parity tests). Removed
`test_run_watch_mergeability_defaults_on_and_can_be_disabled` (the flag it
exercised no longer exists). Renamed
`test_run_serves_resolver_workflow_when_its_file_exists_without_watch_mergeability`
to `test_run_serves_resolver_workflow_when_its_file_exists` and dropped its
`"--no-watch-mergeability"` argument — the resolver-served-whenever-its-file-exists
behavior is now the only behavior, not a flag-off special case. Reworded one
other test's comment that referenced "the mergeability watcher flag".

**`tests/test_git_workspace.py`** / **`tests/test_github_client.py`** —
2 docstring/comment rewrites each, same actor-renaming as above, no logic
change.

## Verification

```sh
.venv/bin/pytest -q
```

Full suite: **1207 passed, 1 skipped** (unchanged skip — `test_smoke_claude.py`,
opt-in only with `HARNESS_SMOKE_CLAUDE=1`). Ran repeatedly at each checkpoint
(pre-merge, post-merge, post-parity-tests, post-deletion, post-doc-sweep,
final) — green throughout, per the sequencing the plan/architecture mandated
("coverage never dips below what existed before the task started").

Explicitly re-ran the architecture/docs-model guard tests, since this task
touches CLAUDE.md's module map and a docs-site model:
```sh
.venv/bin/pytest -q tests/test_architecture.py tests/test_architecture_model.py \
  tests/test_docs_site.py tests/test_claude_md_module_map.py
```
56 passed.

Final sweep: `git grep -in "mergeability_watcher\|GithubMergeabilityWatcher\|watch.mergeability"`
across `src/`, `tests/`, `CLAUDE.md`, `README.md`, `docs/adr`,
`src/harness_docs_site` returns exactly two hits, both deliberate: the
README's own note that the flag is gone, and a `test_processes_e2e.py`
section-comment explaining what the new tests replace. `docs/adr/0014-*`'s
historical mention of "the mergeability watcher" is untouched, as specified.

## Commit message (for the harness's landing step)

Recommend a single commit (on top of the `453066c` merge already in this
branch) with a `BREAKING CHANGE:` footer, no deprecation shim — per
`plan-01.md`'s FR-7, ratified by `architecture-01.md`:

```
feat!: retire GithubMergeabilityWatcher, github-conflicts is the only autoresolver path

Conflict resolution existed twice: a bespoke TaskSource wired on by default
(--watch-mergeability) and a Check-shaped process action (github-conflicts)
that already reproduced the same detection but required an operator to
hand-author a processes/*.json. The watcher is now gone; the process is the
only detection path.

BREAKING CHANGE: --watch-mergeability/--no-watch-mergeability is removed.
Autoresolution is now opt-in: drop a processes/autoresolver.json naming the
github-conflicts action (see the README's new "Autoresolving merge
conflicts" section for the template). --resolver-workflow is unchanged and
still controls which workflow those tasks are served under.
```

## Open items for review

- The merge commit `453066c` already sits in this branch's history as a
  prerequisite for any of this task's edits to apply — flagging explicitly in
  case the landing/review step expects a linear, single-purpose commit
  history.
- `docs/adr/0014-triggers-produce-tasks-not-placements.md` still says "the
  mergeability watcher spots" — deliberately untouched, per the plan's
  explicit non-change AC (ADRs are point-in-time records).
- No change to `GithubConflictsCheck`, `ResolveConflictBehavior`, or
  `GitWorkspace.attach`'s actual logic — verified via the full test suite and
  by reading `attach()`'s git-operation sequence before/after (identical).
