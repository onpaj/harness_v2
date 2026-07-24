# Plan — retire `GithubMergeabilityWatcher`, leave `github-conflicts` as the only autoresolver path

## ⚠️ Grounding note (read first)

This worktree's branch tip (`0c8027b`) is **74 commits behind `origin/main`**
(`git rev-list --count HEAD..origin/main` → 74). None of the files this task
names — `drivers/mergeability_watcher.py`, `drivers/github_conflicts_check.py`,
`processes/`, `ports/triggers.py` (`Check`/`Observation`), the `resolver`
workflow, invariants #28–#41 — exist on this branch's checkout; they all live
on `origin/main` only. This is a known, recurring situation in this repo (see
e.g. commits `66dd350`, `25e291b`, `7c2b8ac`, `83f961b` in this same repo's
history, each a prior plan/design/architecture step hitting the identical
staleness and grounding itself by reading `origin/main` directly via
`git show origin/main:<path>`).

Everything below was verified by reading the real files off `origin/main`
(fetched into this repo's object DB — `git fetch origin main` — no working-tree
checkout needed to read them). **FR-0 makes merging `origin/main` the
mandatory first implementation step**, exactly as the precedent commits did.
Design/architecture/development must re-verify against `origin/main` (or the
merged tree) rather than trusting this document's quoted line numbers blindly,
in case main moves further before development starts.

## Summary

Conflict resolution for harness-authored PRs exists twice on `origin/main`:
a bespoke `TaskSource` (`GithubMergeabilityWatcher`, wired by a `--watch-mergeability`
flag defaulting **on**) and a `Check`-shaped process action (`GithubConflictsCheck`,
registered as `github-conflicts`, only wired if the operator hand-authors a
`processes/*.json`). The design doc that introduced the check
(`docs/superpowers/specs/2026-07-23-github-conflicts-action-design.md`)
explicitly deferred deleting the watcher to "a later pass" — this task is that
pass. The result: `github-conflicts` is the only detection path, the watcher
and its flag are gone, and an operator gets identical resolve→land behavior by
authoring a `processes/*.json` from a documented template.

## Context

- The watcher (`drivers/mergeability_watcher.py`, `GithubMergeabilityWatcher`,
  `kind="mergeability"`) is a `TaskSource`: `poll()` lists open harness PRs,
  auto-updates a `"behind"` one server-side (`client.update_branch`, no task),
  and queues exactly one resolver task per `"dirty"` PR, deduped by
  `dedup_key("mergeability", repo, pr, head_sha)`. Wired per-repo in
  `cli._mergeability_sources`, gated by `--watch-mergeability`
  (`BooleanOptionalAction`, **default `True`**), with `--resolver-workflow`
  choosing the target workflow (default `"resolver"`).
- The check (`drivers/github_conflicts_check.py`, `GithubConflictsCheck(Check)`)
  does the identical detection — same `list_pull_requests` scan, same
  behind→update/dirty→emit split, same `data.branch`/`data.source` shape — but
  as a `Check.evaluate() -> list[Observation]`, registered as the
  `"github-conflicts"` action in `cli._process_sources` and compiled by
  `FilesystemProcessRepository` into a `ScheduledTrigger` (dedup `"per-state"`,
  key `f"{slug}:{number}:{head_sha}"`) whenever an operator writes a
  `processes/*.json` naming it. **No such process file ships today** —
  `harness init` only creates an empty `processes/` directory (`_init` in
  `cli.py`) — so today the check is dead code until someone hand-authors the
  file, while the watcher fires unconditionally whenever `GITHUB_TOKEN` is set.
- Both paths target the same `resolver` workflow (`resolve → land`), and both
  produce tasks whose `data.branch` / `data.source.base` are read identically
  downstream by `ResolveConflictBehavior` and `GitWorkspace.attach`'s
  branch-override reuse path (CLAUDE.md invariants #28, #31).
- CLAUDE.md invariant #31 currently justifies a hard reset in
  `GitWorkspace.attach`'s branch-reuse path by naming
  `GithubMergeabilityWatcher.update_branch` as the one caller that advances a
  shared branch server-side with no local git touch. That same fact is true of
  `GithubConflictsCheck` (it calls the identical `GithubClient.update_branch`).
  The reset must survive untouched; only the *name of the caller* in the
  reasoning changes. The same rewrite is needed in three more spots that quote
  the watcher for the same reason: `drivers/git_workspace.py`'s module
  docstring and two inline comments, and `tests/test_git_workspace.py`'s two
  comments, plus one line in `ports/source.py`'s `TaskSource.poll()` docstring.

## Functional requirements

**FR-0 — Merge `origin/main` into this branch.**
Mandatory prerequisite; every FR below is expressed against the real files on
`origin/main`, not the empty/pre-phase-4 state currently checked out here.
- AC: `git merge origin/main` (or equivalent) completes with the resulting
  tree containing `drivers/mergeability_watcher.py`,
  `drivers/github_conflicts_check.py`, `processes/`-related drivers, and
  CLAUDE.md invariants up to #41.
- AC: full suite green on the merged tree *before* touching anything else, so
  any later red is attributable to this change, not the merge.

**FR-1 — Delete the watcher driver and its dedicated tests.**
- AC: `src/harness/drivers/mergeability_watcher.py` deleted.
- AC: `tests/test_mergeability_watcher.py` and `tests/test_mergeability_e2e.py`
  deleted, but not before FR-4 below reproduces the behavior they proved (see
  FR-4's explicit mapping of each deleted test to its replacement/subsumption).

**FR-2 — Remove the watcher's wiring and flags from `cli.py`.**
- AC: `cli._mergeability_sources` deleted; the `GithubMergeabilityWatcher`
  import removed.
- AC: `--watch-mergeability` argparse option removed entirely (not defaulted
  off — removed, per the issue).
- AC: in `_run`, the line
  `mergeability = _mergeability_sources(...) if args.watch_mergeability else []`
  and the `+ mergeability` term in `sources = github + reflectors + mergeability`
  are removed; `sources = github + reflectors`.
- AC: `--resolver-workflow` **stays** — it is still read at the
  `resolver_defined = (layout.workflows / f"{args.resolver_workflow}.json").is_file()`
  / "resolver rides alongside the served workflows" site in `_run`, which is
  independent of the watcher (a process-only detection path still needs the
  resolver workflow *served*, i.e. given its own step queues, for a
  `github-conflicts` process to have somewhere to route into). Only its
  watcher-specific plumbing (the `resolver_workflow=args.resolver_workflow`
  kwarg passed into the now-deleted `GithubMergeabilityWatcher(...)` call)
  disappears along with the function that read it.
- AC: reword the flag's `help=` text (currently "workflow template used for
  tasks the mergeability watcher queues") and the two comments around the
  `resolver_defined` block that say "queued by the mergeability watcher *or*
  the `github-conflicts` process" — the watcher clause is dropped, the process
  clause stands alone.
- AC: `DEFAULT_RESOLVER_WORKFLOW` / `RESOLVER_DEFINITION` (used by `_init` to
  scaffold `workflows/resolver.json`) are untouched — they serve the process
  path too and are not watcher-specific.

**FR-3 — Fix up the surviving tests in `tests/test_cli.py`.**
- AC: `_mergeability_sources` import, `_mergeability_args` helper, and every
  `test_mergeability_sources_*` test deleted (their coverage is superseded by
  the existing `test_github_conflicts_check.py` unit tests plus FR-4's new
  e2e coverage — confirm no unique assertion is lost; see FR-4's mapping
  table).
- AC: `test_run_watch_mergeability_defaults_on_and_can_be_disabled` deleted
  (the flag it exercises no longer exists).
- AC: `test_run_serves_resolver_workflow_when_its_file_exists_without_watch_mergeability`
  is kept but renamed (e.g.
  `test_run_serves_resolver_workflow_when_its_file_exists`) and stops passing
  `"--no-watch-mergeability"` to `main(["run", ...])` — the decoupling it
  proves ("resolver served whenever its definition file exists, independent of
  any watcher flag") is now the *only* behavior, not a flag-off special case.
- AC: every other test that calls `main(["run", ...])` without `GITHUB_TOKEN`
  set (there are several — e.g. `test_run_serves_multiple_workflows_with_repeated_flag`,
  `test_run_with_no_workflow_flag_serves_default_and_resolver`,
  `test_run_all_workflows_serves_every_definition_found`) keeps returning `0`
  after the change — they must not start failing because `--watch-mergeability`
  no longer parses (it's simply absent from the arg list in all of them
  already, so removing the option should be a no-op for them; this AC is a
  regression check, not a code change).

**FR-4 — Prove parity for the Process path before deleting the watcher's tests.**
The watcher's deleted tests proved three things the check-based path must
still prove, verified against real files as they exist on `origin/main` today:

| Deleted watcher test | What it proved | Where the replacement lives |
|---|---|---|
| `test_mergeability_watcher.py::test_dirty_pr_yields_exactly_one_resolver_task` | dirty PR → one task, `data.branch`/`data.source` shape | Already covered at the unit level by `test_github_conflicts_check.py::test_emits_one_observation_per_dirty_pr_with_provenance` — no gap. |
| `test_mergeability_watcher.py::test_behind_pr_is_updated_and_yields_no_task` | behind PR → `update_branch`, no task | Already covered by `test_github_conflicts_check.py::test_behind_pr_is_updated_and_emits_no_task` — no gap. |
| `test_mergeability_e2e.py::test_dirty_pr_flows_through_resolver_to_a_single_pr_on_the_same_branch` | **full** resolve→land flow: a process-sourced task's `data.branch`/`data.source.base` actually drive `GitWorkspace.attach`'s branch-override and `ResolveConflictBehavior`, landing exactly one PR on the *original* branch | **Gap — no equivalent exists today.** `test_processes_e2e.py` has e2e coverage for `github-issues` but none for `github-conflicts` driving the `resolver` workflow end to end. **New test required** (see below). |
| `test_mergeability_e2e.py::test_behind_pr_is_auto_updated_with_no_task_created` | same, full-loop, for the behind case | **Gap** — same reasoning; add alongside the above. |
| `test_mergeability_e2e.py::test_restart_does_not_duplicate_the_resolver_task_for_the_same_conflict` | dedup survives a **process restart** (`SourcePoller.seed()` from tasks on disk, not just the check's in-process `_seen`) | **Gap.** `test_github_conflicts_check.py::test_seen_ledger_suppresses_a_relisted_conflict_within_the_process` only proves in-process dedup (the check's own `_seen` set), which does *not* survive a restart (a fresh `GithubConflictsCheck` has an empty `_seen`). Cross-restart dedup for the process path rides `ScheduledTrigger`'s `dedup_key` (`per-state`, keyed on `state_key`) + `SourcePoller._seen` seeded from disk — the *same* mechanism GitHub-issue ingestion already relies on, but there is no test exercising it for `github-conflicts` specifically. **New test required.** |

- AC: add an e2e test (natural home: `tests/test_processes_e2e.py`, following
  its existing `drive_until_quiet`/`build_harness` pattern and
  `test_mergeability_e2e.py`'s `MemoryWorkspace`/fake-forge setup) that builds
  a `processes/autoresolver.json`-shaped process (`action.check =
  "github-conflicts"`, `target.workflow = "resolver"`), feeds a `FakeGithubClient`
  a `"dirty"` PR, drives the loop to quiescence, and asserts: exactly one task
  reaches `done` with `status == "end"`; `workspace.handles[task.id].branch`
  equals the PR's own head branch (proving the branch-override path fired, not
  a fresh `harness/<id>` branch); the resolver actually ran a merge or agent
  call (mirroring the original's fixture).
- AC: add the behind-PR sibling of the above (no task created, `update_branch`
  called) in the same harness shape.
- AC: add a restart-dedup test: build the process source via
  `FilesystemProcessRepository(...).build(...)`, drive one dirty PR to `done`,
  then construct a **fresh** `SourcePoller` (mirroring the deleted test's
  approach) seeded from every task now on disk, and assert its next `tick()`
  for the same PR/head_sha is `False` — i.e., no re-queue.
- AC: only once all three new tests are green does FR-1's test deletion
  proceed; the plan explicitly sequences "add parity coverage" before "delete
  the old coverage" so there is never a window with neither.

**FR-5 — Ship a reference process template, documented rather than auto-seeded.**
- AC: a `processes/autoresolver.json` example — `action: {"check":
  "github-conflicts", "params": {"head_prefix": "harness/"}}`, `target:
  {"workflow": "resolver"}`, a `sink` slot (recommend `{"kind": "none"}` as the
  shipped default, matching the design doc's own example and every other
  process example in the repo) — is added to **README.md** as a copy-pasteable
  block (a new short section, e.g. under wherever `repos.json`/`processes/`
  are currently introduced), **not** written by `harness init`.
- AC (the reasoning this rests on, verify during development): `harness init`
  seeding this file unconditionally would be a regression, not a convenience —
  `FilesystemProcessRepository.build()`/`compile_process` **fails fast**
  (`ProcessValidationError`) when a `github-conflicts`/`github-issues` process
  exists but no `GITHUB_TOKEN` is configured (see
  `_process_sources`'s `github_conflicts_factory`, which raises rather than
  degrading to a no-op). Several existing `test_cli.py` tests already call
  `main(["init", ...])` then `main(["run", ...])` with `GITHUB_TOKEN` deleted
  from the environment and assert a `0` return code (e.g.
  `test_run_watch_mergeability_defaults_on_and_can_be_disabled` — being
  deleted anyway, but also `test_run_with_no_workflow_flag_serves_default_and_resolver`
  and siblings, which are *not* being deleted). Auto-seeding the process file
  would flip all of those to a crash. This is exactly why `harness init` has
  never actually seeded a process example for `github-issues` either (only an
  empty `processes/` dir), despite an earlier design doc's stated intent to do
  so — confirm this reasoning still holds against whatever `origin/main` looks
  like by the time development starts, rather than trusting it blind.
- AC: the README section states plainly that dropping the file in requires
  `GITHUB_TOKEN` to be set, and that this replaces the previous default-on
  `--watch-mergeability` behavior — i.e. this is where the breaking-change note
  from FR-7 gets its user-facing home.

**FR-6 — Update CLAUDE.md, and the code comments its invariants describe.**
- AC: invariant **#31** — rewrite so it names `GithubConflictsCheck`'s call to
  `GithubClient.update_branch` (not `GithubMergeabilityWatcher.update_branch`)
  as the thing that advances a shared branch server-side with no local git
  touch. The reset behavior described afterward (hard-reset on create-path
  reuse, ancestry-aware reconciliation on override-reattach) is **unchanged
  wording** — only the causal actor's name changes.
- AC: module-map driver table — drop `mergeability_watcher` from the `Drivers`
  row's list.
- AC: delete the standalone `drivers/mergeability_watcher.py` bullet under
  "What is responsible for what" (the one starting "`GithubMergeabilityWatcher(TaskSource)`,
  `kind="mergeability"`: …").
- AC: the `drivers/github_conflicts_check.py` bullet currently ends "…the
  check-based replacement for the bespoke `mergeability_watcher` detection" —
  reword to drop the dangling reference to a class that no longer exists (e.g.
  end the sentence at "for per-state dedup" and drop the trailing clause, or
  replace it with a forward-looking description of what the check *is* rather
  than what it replaced).
- AC: `src/harness/drivers/git_workspace.py` — reword the module docstring's
  "`GithubMergeabilityWatcher.update_branch`, which never touches any local
  ref" and the two inline comments in the branch-override create/reattach
  paths ("`GithubMergeabilityWatcher's update_branch (FR-2)`" and
  "`GithubMergeabilityWatcher.update_branch`") to name `GithubConflictsCheck`
  instead. No behavior change — comments only.
- AC: `tests/test_git_workspace.py` — same rewrite for its two comments
  quoting `GithubMergeabilityWatcher.update_branch`.
- AC: `src/harness/ports/source.py` — `TaskSource.poll()`'s docstring cites
  `GithubMergeabilityWatcher.poll()` as the precedent for a side-effecting,
  task-less poll action; reword to cite `GithubConflictsCheck.evaluate()`
  instead (a `Check`, not a `TaskSource`, but the same "idempotent side effect,
  no task" shape — word this precisely, since the two are different ABCs).
- AC (explicit non-change): `docs/adr/0014-triggers-produce-tasks-not-placements.md`
  mentions "the mergeability watcher" once, as historical motivating context
  written at the time triggers were designed. **Leave it untouched** — ADRs are
  point-in-time decision records, not living docs; rewriting one to erase a
  since-removed class would misrepresent what was actually true when the
  decision was made. Called out here so it isn't mistaken for a missed
  reference during review.

**FR-7 — Commit as a breaking change, no deprecation shim.**
- AC: the commit message uses a `BREAKING CHANGE:` footer (conventional
  commits; per `CLAUDE.md`'s release process this is what
  `python-semantic-release` needs to cut a major bump) stating that
  `--watch-mergeability`/`--resolver-workflow`'s watcher plumbing is gone, and
  that autoresolution is now opt-in via a hand-authored
  `processes/autoresolver.json` (pointing at the FR-5 README section).
- AC: **no** deprecation shim (a flag that prints a pointer and exits
  non-zero). Rationale to record in the commit body: the parent repo's
  `~/CLAUDE.md`-style guidance and this repo's own conventions
  ("Avoid backwards-compatibility hacks…" — global instructions) argue against
  reintroducing dead flag-parsing machinery whose only job is to print a
  redirect; a clear `BREAKING CHANGE:` note plus the README template is a
  cleaner signal for the one-operator audience this project currently has.
  Development should treat this as the plan's decision, not re-open it, unless
  architecture surfaces a concrete reason (e.g. a known external caller of
  `--watch-mergeability`) to prefer the shim.

## Non-functional requirements

- **No orchestration-core change.** This is entirely a `cli.py` wiring
  deletion + a `drivers/` deletion + doc/test updates. `dispatcher.py`,
  `consumer.py`, `router.py`, `source_poller.py` are untouched — invariants
  #1–#4, #17, #20 continue to hold trivially (nothing there imports the
  watcher today either, so there is nothing to un-import).
- **`test_architecture.py` must stay green unmodified.** It has no
  watcher-specific assertion (`git grep` confirms zero hits), so its
  glob-based guards should pass without edits — treat any failure there as a
  signal something drifted outside this task's intended blast radius.
- **No data/schema migration.** The task shape produced by `github-conflicts`
  is already byte-for-byte identical to the watcher's (`data.branch`,
  `data.source = {kind: "mergeability", repo, pr, url, base}`) — deliberately
  unchanged so `ResolveConflictBehavior`/`GitWorkspace.attach` need zero
  changes.

## Data model

No new entities. Confirms the existing shape carries over unchanged:

```
Task.data = {
  "branch": <PR head branch>,             # read by GitWorkspace.attach's override path
  "title": "resolve merge conflict on PR #<n>",
  "source": {
    "kind": "mergeability",               # unchanged — still "mergeability", not "github-conflicts"
    "repo": <owner/repo slug>,
    "pr": <number>,
    "url": <PR url>,
    "base": <PR base branch>,             # read by ResolveConflictBehavior
  },
}
Task.dedup_key = dedup_key("scheduled:<process-name>", "wf:resolver", "<slug>:<pr>:<head_sha>")
# (was dedup_key("mergeability", repo, pr, head_sha) under the watcher — the
# key's *namespace* changes because it's now scoped to the process/trigger
# `kind`, not the source's own kind; this does not break anything since dedup
# is per-run-lineage, not compared across the cutover, but call it out to
# architecture/development as a fact worth stating plainly, not silently
# changing.)
```

## Interfaces

- **CLI**: `harness run` loses `--watch-mergeability`/`--no-watch-mergeability`;
  keeps `--resolver-workflow` (reworded help text). No other CLI surface
  changes.
- **Filesystem**: no new file format. The reference template
  (`processes/autoresolver.json`) uses the existing `processes/*.json` schema
  verbatim — no schema change, just a documented example.
- **No API/UI change** — `ProcessAdmin`/the process editor already handle any
  process naming `github-conflicts`; nothing about this task touches `api/`.

## Dependencies and scope

**Depends on** (already present on `origin/main`, not built by this task):
`GithubConflictsCheck`, its registration in `_process_sources`, the `resolver`
workflow scaffold, `FilesystemProcessRepository`/`ScheduledTrigger`, the
existing `test_process_sources_builds_a_resolve_conflicts_process` /
`test_process_sources_github_conflicts_fails_fast_without_a_client` coverage
in `test_cli.py`.

**In scope**: everything in FR-0 through FR-7 above — deleting the watcher,
its flag, its tests; adding the three parity tests; the doc/comment rewrites;
the README template; the breaking-change commit.

**Out of scope (deliberate)**:
- Folding `triggers/*.json` into `processes/` — CLAUDE.md already documents
  the two surfaces coexisting as intentional; a separate, later cleanup.
- The outbound `harness:resolving` label reflection the watcher used to do in
  `report_progress`/`finish` — the design doc that introduced the check
  already deferred this to a future `github-label` sink; this task does not
  reintroduce it or invent one.
- Rewriting ADR-0014's historical mention of the watcher (see FR-6's explicit
  non-change AC).
- Any change to `GithubConflictsCheck`, `ResolveConflictBehavior`, or
  `GitWorkspace.attach` themselves — they are correct and tested today; this
  task only removes their now-redundant sibling and points documentation at
  them.
- A GitHub-label or Slack sink for the resolver process — `sink: {"kind":
  "none"}` in the shipped template is deliberate and sufficient.

## Rough plan

1. **FR-0**: merge `origin/main`; confirm full suite green before any edit.
2. **FR-4 first** (parity before deletion): write the three new e2e tests
   against the *existing* `GithubConflictsCheck`/process machinery (no
   production code change needed for this step — the machinery already
   exists). Get them green.
3. **FR-1/FR-2/FR-3**: delete `mergeability_watcher.py`, `_mergeability_sources`,
   the flag, its wiring line, and every test that exercised it; fix the
   surviving `resolver`-served test's flag argument.
4. **FR-6**: sweep CLAUDE.md (invariant #31, module map, two bullets),
   `git_workspace.py` (module docstring + 2 comments), `test_git_workspace.py`
   (2 comments), `ports/source.py` (1 docstring line). Grep for
   `[Mm]ergeab` across the whole tree afterward and confirm the only surviving
   hit is the deliberately-untouched ADR-0014 line.
5. **FR-5**: add the README template section.
6. Run the full suite (`.venv/bin/pytest -q`) plus `test_architecture.py`
   explicitly; confirm the token-less `main(["run", ...])` tests in
   `test_cli.py` still return `0`.
7. **FR-7**: commit with the `BREAKING CHANGE:` footer.

## Open questions

- **Filename**: the issue names the template `processes/autoresolver.json`;
  the design doc / existing `test_cli.py` fixture that already builds a
  `github-conflicts` process uses the filename `resolve-conflicts.json`.
  Since a process's `name` defaults to the file stem and nothing downstream
  hardcodes either string, this is cosmetic — **default chosen: `autoresolver.json`**,
  matching the issue's explicit text, over matching the pre-existing example's
  name. Flag to design/architecture in case there's a reason to prefer
  consistency with the existing test fixture's name instead.
- **`sink` value in the shipped template**: defaulted to `{"kind": "none"}`
  above (fire-and-forget, matching every other shipped process example in the
  design docs). No outbound reflection exists for this action today (see
  "out of scope"), so `none` is the only value that doesn't misrepresent
  capability that isn't there.
- **Breaking-change vs. shim** (FR-7): resolved above in favor of a
  `BREAKING CHANGE:` footer with no shim. Revisit only if architecture finds a
  concrete external-caller reason to prefer a soft landing.
- **Whether to reword the `github-conflicts` CLAUDE.md bullet's trailing
  clause** ("the check-based replacement for the bespoke `mergeability_watcher`
  detection") or drop it outright: leaning drop (a description of prior art
  that no longer exists reads as confusing, not historical, once the watcher
  is gone) — left for design/review to confirm during the doc sweep.
