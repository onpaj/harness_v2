# Design — retire `GithubMergeabilityWatcher`, `github-conflicts` is the only autoresolver path

## Grounding

Same situation `plan-01.md` documented: this worktree (`0c8027b`) is 74 commits
behind `origin/main` and has none of the named files locally. Everything below
was verified by reading the real files off `origin/main` (`git show
origin/main:<path>`) — `drivers/mergeability_watcher.py`,
`drivers/github_conflicts_check.py`, `drivers/scheduled_trigger.py`,
`drivers/fs_processes.py`, `cli.py`, `git_workspace.py`, `ports/source.py`,
`CLAUDE.md`, `README.md`, `docs/adr/0014-*`, the four affected test files, and
the design doc that introduced the check
(`docs/superpowers/specs/2026-07-23-github-conflicts-action-design.md`).
Development must re-verify against whatever `origin/main` looks like once
FR-0's merge actually lands, rather than trusting quoted line numbers here.

No UI is involved: this is a `cli.py` wiring deletion, a driver deletion, a
handful of doc/comment rewrites, and new tests. The task has no visible
surface beyond CLI flags and files on disk, so this design covers only
component boundaries and data shapes — no wireframes.

This design does not restate `plan-01.md`'s reasoning (why the watcher goes,
why no shim, why the template isn't init-seeded) — it takes those decisions as
given and specifies the concrete shapes the development step should produce:
exact wiring diffs, exact prose replacements, exact test bodies, exact
JSON/CLI schemas.

## Component design

### Removed: `drivers/mergeability_watcher.py`

`GithubMergeabilityWatcher(TaskSource)` is deleted whole — no replacement
class. Its responsibilities (list PRs, auto-update `behind`, emit a task per
`dirty` PR, dedup by `repo:pr:head_sha`) are already fully assumed by
`GithubConflictsCheck(Check)` (`drivers/github_conflicts_check.py`), which
ships unchanged. The one behavior the check does **not** reproduce, and isn't
asked to: `report_progress`/`finish` (the `harness:resolving` label swap). That
was the watcher's outbound half; a `Check` has no `report_progress`/`finish` at
all (it isn't a `TaskSource`), and the design doc that introduced the check
already deferred outbound reflection to a future `github-label` sink. So
`test_mergeability_watcher.py::test_report_progress_sets_resolving_label_for_resolve_and_land_steps`,
`::test_finish_removes_resolving_label`, and
`::test_report_progress_and_finish_are_noop_for_a_foreign_task` are deleted
with **no migration target** — this is a deliberate capability drop already
scoped out by the check's own design doc, not a parity gap `plan-01.md`'s FR-4
table missed. Call this out explicitly in the PR body so it doesn't read as an
oversight.

### Changed: `cli.py` wiring

Four independent surfaces, each a small, mechanical edit:

1. **`_mergeability_sources` — deleted whole** (function + its
   `GithubMergeabilityWatcher` import). Nothing else calls it once `_run`'s one
   call site (below) is gone.

2. **`_run`'s source composition — one line collapses to two.**

   Before:
   ```python
   mergeability = _mergeability_sources(args, root, registry) if args.watch_mergeability else []
   github = [] if args.no_github_source else _github_sources(args, root, registry)
   reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
   sources = github + reflectors + mergeability
   ```
   After:
   ```python
   github = [] if args.no_github_source else _github_sources(args, root, registry)
   reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
   sources = github + reflectors
   ```
   `process_sources` (built later in the same function via `_process_sources`)
   is untouched — it's appended to `sources` further down exactly as today.

3. **`resolver_defined` block — comment only, logic untouched.**
   ```python
   # The resolver workflow rides alongside the primary one so its tasks — queued
   # by a `github-conflicts` process — get their own step queues and board
   # columns. Served whenever its definition exists: a process-only detection
   # path still needs a served target (a process targeting an unserved
   # workflow fails to compile).
   resolver_defined = (layout.workflows / f"{args.resolver_workflow}.json").is_file()
   if resolver_defined and args.resolver_workflow not in served_names:
       served_names = [*served_names, args.resolver_workflow]
   ```
   `resolver_defined`, the `.is_file()` check, and the `served_names` append
   are unchanged — invariant: `--resolver-workflow` still exists and is still
   read here, only the watcher clause drops from the comment.

4. **Argparse — `--watch-mergeability` deleted, `--resolver-workflow` kept,
   reworded.**
   ```python
   run.add_argument(
       "--resolver-workflow",
       default=DEFAULT_RESOLVER_WORKFLOW,
       dest="resolver_workflow",
       help="workflow the 'resolver' PR-conflict tasks (e.g. from a "
       "github-conflicts process) are served under",
   )
   ```
   The whole `run.add_argument("--watch-mergeability", ...)` block is removed,
   not defaulted off. `DEFAULT_RESOLVER_WORKFLOW`/`RESOLVER_DEFINITION` (used
   by `_init` to scaffold `workflows/resolver.json` and `agents/resolve.json`)
   are untouched — they serve the process path too.

`_process_sources` itself (the `github-conflicts` registration,
`github_conflicts_factory`, the fail-fast-without-a-token behavior) is **not
touched** — it is already correct and already tested
(`test_process_sources_builds_a_resolve_conflicts_process`,
`test_process_sources_github_conflicts_fails_fast_without_a_client`).

### Changed: doc/comment surfaces (prose only, no behavior change)

Five files, six spots, each a direct find/replace naming the new causal actor.
Exact target text (development should match these verbatim, adjusting only for
whatever the merged `origin/main` tree's exact current wording is if it
drifted):

**`CLAUDE.md` invariant #31** — replace the clause naming the watcher:

> …while every advance of the branch goes through a local commit+push in *some*
> worktree — `GithubConflictsCheck`'s call to `GithubClient.update_branch` (the
> `github-conflicts` process action) breaks that by advancing the branch
> server-side with no local git touch at all. So immediately after…

Nothing else in the invariant changes — the reset behavior, the "ancestry-aware
on reattach" wording, and the `#86` reference all stay exactly as written.

**`CLAUDE.md` module map** — drop `mergeability_watcher` from the `Drivers`
row's `{...}` list; no other entries move.

**`CLAUDE.md` "What is responsible for what"** — delete the
`drivers/mergeability_watcher.py` bullet outright (the one starting
"`GithubMergeabilityWatcher(TaskSource)`, `kind="mergeability"`: …"). Reword the
`github_conflicts_check.py` bullet's trailing clause from "…the check-based
replacement for the bespoke `mergeability_watcher` detection" to a
forward-looking close:

> …keyed `slug:pr:head_sha` for per-state dedup. Registered as the
> `github-conflicts` action in `cli._process_sources`; the only
> conflict-detection path — an operator opts in by authoring a
> `processes/*.json` naming this action.

**`docs/adr/0014-triggers-produce-tasks-not-placements.md`** — **no change**.
It names "the mergeability watcher" once as historical context at the time
triggers were designed; an ADR is a point-in-time record, not a living doc.
Leaving it is the correct outcome here, not an oversight — noted so review
doesn't flag it as missed.

**`src/harness/drivers/git_workspace.py`** — module docstring:

> …would leave the new worktree stale whenever the branch last advanced
> server-side (`GithubConflictsCheck`'s `GithubClient.update_branch` call,
> which never touches any local ref) rather than through a harness-driven
> commit+push here…

Inline comment in the create/reuse path:

```python
# GithubConflictsCheck's update_branch call advances
# the branch server-side via the GitHub API, touching no
# local git state at all. Reconcile the *new* worktree with
```

Inline comment in the reattach/override path:

```python
# *server-side* (`GithubConflictsCheck`'s `update_branch` call) between
```

**`tests/test_git_workspace.py`** — docstring of
`test_attach_with_branch_override_reconciles_stale_local_ref_with_origin`:

> """`GithubConflictsCheck`'s `update_branch` call advances a PR branch
> entirely server-side (merges base into head via the GitHub API) — no local
> git operation touches it. Simulate that by advancing `origin/<branch>`
> through…

Comment further down in the same test file:

```python
# Now the branch advances server-side, independently of any local ref —
# as GithubConflictsCheck's update_branch call does via the GitHub API.
```

**`src/harness/ports/source.py`** — `TaskSource.poll()`'s docstring. This one
needs care: the watcher was a `TaskSource`, the check is a `Check` (a
different ABC, driving a `ScheduledTrigger` which *is* the `TaskSource`). Word
the replacement to be precise about that distinction rather than implying
`GithubConflictsCheck` itself implements `poll()`:

> An implementation may also perform an idempotent, side-effecting action per
> polled item that produces no task (precedent: `GithubTaskSource.poll()` swaps
> a label as part of claiming an issue). The same shape recurs one layer down:
> a `Check` driving a `ScheduledTrigger` may do the same inside `evaluate()` —
> `GithubConflictsCheck.evaluate()` calls GitHub's update-branch API on a
> "behind" PR before `ScheduledTrigger.poll()` ever returns. Any such action
> must be safe to repeat every tick.

After all six edits, `git grep -in mergeab` across the tree must return exactly
one hit: the untouched ADR-0014 line.

### Changed: `README.md` — new section

Processes (`processes/*.json`) are undocumented in the README today (verified:
zero hits for `processes/`, `ScheduledTrigger`, or `Process` as a noun). This
task adds the first README mention, scoped to exactly what FR-5 asks for — a
copy-pasteable autoresolver template — not a full write-up of the process
system. Placed as a new subsection immediately after "## GitHub issue
ingestion" (same repos.json-adjacent territory, same "needs `GITHUB_TOKEN`"
framing the issue-ingestion section already uses):

```markdown
## Autoresolving merge conflicts

Earlier versions of `harness run` watched every registered repo's open PRs and
queued a resolver task for each conflicted ("dirty") one by default
(`--watch-mergeability`). That flag is gone — autoresolution is now opt-in,
authored the same way any other scheduled process is: drop a file under
`processes/`.

```json
// ~/harness-root/processes/autoresolver.json
{
  "trigger": {"interval": "60s"},
  "action": {"check": "github-conflicts", "params": {"head_prefix": "harness/"}},
  "target": {"workflow": "resolver"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

This is not written by `harness init` — a `github-conflicts` process without a
`GITHUB_TOKEN` fails the run fast (`ProcessValidationError`), so seeding it
unconditionally would break every token-less `harness run`. Copy the block
above once a token is configured (see
[Running it as a service](#running-it-as-a-service)); `harness run` then
auto-updates a `behind` PR server-side and queues exactly one resolve→land task
per `dirty` PR, deduped per conflicted head commit.
```

The `--resolver-workflow` flag's role is unchanged by this section — it's
already implicitly covered by the flag's own `--help` text and by the
"Workflow" section further down; this task doesn't add a second explanation of
it.

### Test suite — component boundaries

No production test infrastructure changes (no new fixtures, no new harness
helper module). Three files change shape, one file is untouched:

- **`tests/test_mergeability_watcher.py`** — deleted whole (11 unit tests). All
  poll()-side coverage already exists in `tests/test_github_conflicts_check.py`
  (`test_emits_one_observation_per_dirty_pr_with_provenance`,
  `test_behind_pr_is_updated_and_emits_no_task`,
  `test_clean_and_other_states_are_skipped`,
  `test_seen_ledger_suppresses_a_relisted_conflict_within_the_process`,
  `test_a_new_head_re_emits_after_the_first_was_seen`,
  `test_a_failing_update_branch_does_not_drop_the_rest_of_the_tick`,
  `test_skips_a_repo_without_a_github_origin`). The three
  `report_progress`/`finish` label tests have no replacement (see "Removed:
  `drivers/mergeability_watcher.py`" above) — dropped, not migrated.
- **`tests/test_mergeability_e2e.py`** — deleted whole, but only *after* its
  three scenarios are reproduced against the process path (see below — this is
  the ordering FR-4 already mandates and this design doesn't relitigate).
- **`tests/test_processes_e2e.py`** — gains three new tests (component: same
  file, same `build_harness`/`drive_until_quiet`/`write_process` helpers
  already defined there — no new helper needed beyond what's shown below).
- **`tests/test_cli.py`** — `_mergeability_sources` import,
  `_mergeability_args` helper, all `test_mergeability_sources_*` tests, and
  `test_run_watch_mergeability_defaults_on_and_can_be_disabled` deleted.
  `test_run_serves_resolver_workflow_when_its_file_exists_without_watch_mergeability`
  is kept, renamed to
  `test_run_serves_resolver_workflow_when_its_file_exists`, and its
  `main(["run", ..., "--no-watch-mergeability"])` call becomes
  `main(["run", ...])` (drop the now-nonexistent flag). Its docstring comment
  ("not only when the mergeability watcher is on") loses the watcher clause.
  Every other `main(["run", ...])` test in the file already omits
  `--watch-mergeability` from its arg list, so removing the option is a no-op
  for them — verified by inspection, not by a new assertion.

## Data schemas

### Task shape produced by the `github-conflicts` process (unchanged)

Byte-identical to what the watcher used to produce — this is the whole point
of swapping detection without touching `ResolveConflictBehavior`/
`GitWorkspace.attach`:

```jsonc
{
  "id": "<generated>",
  "workflow_template": null,          // ScheduledTrigger sets step=None, workflow="resolver"
  "repository": "<registry name>",    // from Observation.repository, set by GithubConflictsCheck to the registry key
  "worktree": "<worktree_root>/<task.id>",
  "dedup_key": "scheduled:<process-name>:wf:resolver:<slug>:<pr>:<head_sha>",
  "data": {
    "branch": "<PR head branch>",          // read by GitWorkspace.attach's override path
    "title": "resolve merge conflict on PR #<n>",
    "source": {
      "kind": "mergeability",              // unchanged string — historical, not renamed
      "repo": "<owner/repo slug>",
      "pr": "<number>",
      "url": "<PR url>",
      "base": "<PR base branch>"           // read by ResolveConflictBehavior
    }
  }
}
```

`dedup_key`'s **namespace** changes from the watcher's
`dedup_key("mergeability", repo, pr, head_sha)` to
`ScheduledTrigger`'s `dedup_key(f"scheduled:{name}", f"wf:{workflow}",
f"{slug}:{number}:{head_sha}")` — scoped to the process/trigger `kind` rather
than the source's own `kind`, since a `ScheduledTrigger` (not
`GithubConflictsCheck`) is what stamps `Task.dedup_key`. This is a fact to
state plainly in the PR body, not a compatibility concern: dedup is
per-run-lineage (compared only against tasks already on disk in *this*
install), never compared across the watcher→process cutover.

### `processes/autoresolver.json` — schema (existing, unchanged; this is a new instance of it)

Validated by `compile_process` in `drivers/fs_processes.py`. No schema change —
the reference template is a plain instance of the schema every process file
already uses:

```jsonc
{
  "trigger": {"interval": "<duration string: NNs|NNm|NNh|bare-number-seconds>"},
  "action": {
    "check": "github-conflicts",
    "params": {"head_prefix": "harness/"}   // optional; defaults to "harness/" if omitted
  },
  "target": {"workflow": "resolver"},        // exactly one of {"workflow": ...} / {"step": ...}
  "dedup": "per-state",                      // must be "per-state": state_key is head-SHA-scoped, "per-interval" would suppress every fire after the first for a still-conflicted PR
  "sink": {"kind": "none"}                   // "none" (shipped default) or "slack"
}
```

Filename: `autoresolver.json` (per the issue text), not
`resolve-conflicts.json` (the existing test fixture's name, from the design
doc that introduced the check). Cosmetic — a process's `name` defaults to the
file stem and nothing downstream hardcodes either string — but the two should
not both exist as competing "the" example; the README template uses
`autoresolver.json` and no repo file should also ship
`processes/resolve-conflicts.json` as a second, differently-named copy of the
same idea.

### CLI surface — before/after

| Flag | Before | After |
|---|---|---|
| `--watch-mergeability` / `--no-watch-mergeability` | `BooleanOptionalAction`, default `True`; gates `_mergeability_sources` | **removed** |
| `--resolver-workflow` | default `"resolver"`; passed into `GithubMergeabilityWatcher(resolver_workflow=...)` and read by the `resolver_defined` serve-check | default `"resolver"`; read **only** by the `resolver_defined` serve-check — reworded `help=` text (above) |

No new flags. No change to `harness init`'s argument surface — `_init` still
creates an empty `processes/` directory and does not write
`autoresolver.json`.

### New e2e test bodies (parity for FR-4, added to `tests/test_processes_e2e.py`)

These three replace `test_mergeability_e2e.py`'s three scenarios, built from a
`processes/autoresolver.json`-shaped process compiled via
`FilesystemProcessRepository`, not a hand-built `GithubMergeabilityWatcher` —
proving the same outcomes purely through the process path. Same
`build_harness`/`write_process`/`drive_until_quiet` shape the file already has;
the only addition each test needs beyond what's already imported is
`FakeGithubClient`/`PullRequestInfo` (already used by
`test_github_conflicts_check.py`, same import path) and a `client=` parameter
threaded into `build_harness` so `FilesystemProcessRepository.build(...,
checks={**BUILTIN_CHECKS, "github-conflicts": <factory closing over client
+ a MemoryRepositoryRegistry>})` is used instead of `build(clock=clock)` alone
— mirroring `_process_sources`' `github_conflicts_factory` in `cli.py`, not
reinventing a second wiring path.

1. **`test_dirty_pr_via_autoresolver_process_flows_through_resolver_to_a_single_pr_on_the_same_branch`**
   — write an `autoresolver.json`-shaped process targeting `resolver`; seed a
   `FakeGithubClient` with one `dirty` PR; drive to quiescence; assert exactly
   one `done` task with `status == "end"`, `data.source.kind == "mergeability"`,
   `data.source.pr == <n>`; assert `workspace.handles[task.id].branch` equals
   the PR's own head branch (proves the branch-override path fired, not a
   fresh `harness/<id>` branch) — same assertions as the deleted
   `test_dirty_pr_flows_through_resolver_to_a_single_pr_on_the_same_branch`,
   sourced from a compiled process instead of a hand-built watcher.

2. **`test_behind_pr_via_autoresolver_process_is_auto_updated_with_no_task_created`**
   — same setup, PR state `behind`; assert `client.updated_branches == [(slug,
   number)]`, `done/` and `tasks/` both empty.

3. **`test_restart_does_not_duplicate_the_autoresolver_task_for_the_same_conflict`**
   — drive one `dirty` PR to `done`; construct a **fresh** `SourcePoller`
   seeded from every task now on disk (mirroring the deleted
   `test_restart_does_not_duplicate_the_resolver_task_for_the_same_conflict`'s
   approach, but seeding from a freshly-built `ScheduledTrigger` compiled from
   the same `processes/autoresolver.json`, not a re-used watcher instance —
   the check's own in-process `_seen` is irrelevant here on purpose, since the
   point is proving the **cross-restart** path: `ScheduledTrigger`'s
   `per-state` `dedup_key` + `SourcePoller._seen` seeded from disk); assert the
   fresh poller's next `tick()` is `False` for the same PR/head_sha.

All three assert through the same public surface `test_mergeability_e2e.py`
did (`done/`/`tasks/` glob contents, `workspace.handles[...].branch`,
`client.updated_branches`, `SourcePoller.tick()`'s return) — no new assertion
vocabulary, so a reviewer can diff old-test-body against new-test-body
line-for-line to confirm parity.

## Interfaces summary

- **CLI**: one flag removed (`--watch-mergeability`), one flag's help text
  reworded (`--resolver-workflow`), no other CLI change.
- **Filesystem**: no schema change; one new documented (not code-written)
  example file path, `processes/autoresolver.json`, described in README only.
- **No API/UI change** — the process admin UI (`ProcessAdmin`,
  `process_form.html`) already handles any process naming `github-conflicts`;
  this task touches none of it.
- **Commit**: a single commit (or small stack) carrying a `BREAKING CHANGE:`
  footer per `plan-01.md`'s FR-7 — `--watch-mergeability` and
  `--resolver-workflow`'s watcher-specific kwarg are gone; autoresolution is
  now opt-in via `processes/autoresolver.json` (point at the new README
  section). No deprecation shim, per the plan's already-settled decision.
