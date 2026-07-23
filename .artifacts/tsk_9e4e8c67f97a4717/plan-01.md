# Plan: continuously reflect task state onto the source GitHub issue's labels

## Summary

Restore the outward half of the GitHub round-trip for tasks ingested through the
newer Process/`github-issues` action: as such a task moves through steps and
finishes, its source issue's labels should keep tracking it, the way they already
do for tasks ingested by the classic `GithubTaskSource`. The fix is a new,
reflection-only `TaskSource` driver registered per GitHub-origin repository
whenever classic ingestion is turned off, wired only in `cli.py` — no port, no
schema, no dispatcher/consumer change.

## Context

**This worktree is 57 commits behind `origin/main`** (`git fetch origin && git
log --oneline HEAD..origin/main | wc -l` → 57; verified with `git diff
origin/main..HEAD` → 0). Everything this task references — `docs/adr/0010`…
`0015`, the `Process` aggregate, `drivers/github_issues_check.py`,
`drivers/fs_processes.py`, invariants #18–#40, the `sink` field — exists only on
`origin/main`, not in this branch's history. This repo has hit the same situation
before (see commit `5741572` and its chain `1ac08e6`/`59b20c8`/`7b7ca58`): the
resolution there, and the one this plan assumes, is that **development's first
action is `git fetch origin && git merge origin/main`** on the task branch
(merge, not rebase — origin/main's own invariant on this, confirmed still true as
of `46e0c5d`), before touching any file this plan names. All findings below are
grounded by reading the actual files at `origin/main` (via `git show
origin/main:<path>`), not by memory or guesswork.

Grounding read, for the record:
- `docs/adr/0010-tasksource-single-external-port.md`, `0014-triggers-produce-tasks-not-placements.md`,
  `0015-process-authoring-aggregate.md`
- `src/harness/ports/source.py` (`TaskSource`, `Trigger`, `Progress`, `FinishResult`)
- `src/harness/drivers/source_reflector.py` (`SourceReflectorSink`)
- `src/harness/drivers/github_source.py` (`GithubTaskSource`)
- `src/harness/drivers/github_issues_check.py` (`GithubIssuesCheck`, the `github-issues` action)
- `src/harness/drivers/scheduled_trigger.py` (`ScheduledTrigger`)
- `src/harness/drivers/fs_processes.py` (`compile_process`, the `sink` seam)
- `src/harness/drivers/github_client.py` (`add_label`/`remove_label` idempotency, both `FakeGithubClient` and `HttpGithubClient`)
- `src/harness/cli.py` (`_github_sources`, `_process_sources`, `_run`'s `sources` composition, `--no-github-source`)
- `src/harness/app.py` (`build()`: the same `sources` list feeds both `SourcePoller` *and* `SourceReflectorSink`)
- `tests/test_github_source.py`, `tests/test_processes_e2e.py`, `tests/test_cli.py` (existing conventions to extend)

**Root cause, precisely.** `SourceReflectorSink.emit()` fans `report_progress`/
`finish` out to *every* `TaskSource` in the `sources` list; each source's own
`_mine()` guard (matching `task.data.source.kind`/`repo`) decides whether it
acts — routing is per-task-data, not per-originating-producer. So a task created
by `GithubIssuesCheck` (the Process action) already carries `data.source =
{"kind": "github", "repo": ..., ...}`, identical in shape to one created by
`GithubTaskSource.poll()`. **If a `GithubTaskSource` for that repo happens to be
registered, it already reflects state for that task today**, regardless of who
created it. The gap only opens when `GithubTaskSource` for that repo is *not*
registered — which is exactly the configuration `cli.py`'s own `--no-github-source`
help text recommends "when a process handles it instead" (avoiding double
ingestion: both `GithubTaskSource.poll()` and `GithubIssuesCheck.evaluate()` list
`harness:todo` issues by the same label and would otherwise race to claim the
same issue twice). `GithubTaskSource` is the *only* class that knows how to turn
`Progress`/`FinishResult` into a label change — disabling it for ingestion
disables it for reflection too, since the two responsibilities were never split.

The fix is therefore to **split reflection out of `GithubTaskSource` into its own
lean, always-available driver**, and register one per GitHub-origin repo whenever
classic ingestion is off. This needs no `Process`/`sink` schema change:
ADR-0015 itself anticipates this — "a destination identity … that defaults to
`source.kind`, so same-origin processes need declare nothing." GitHub→GitHub is
exactly the same-origin case; only a GitHub→non-GitHub sink (explicitly out of
scope here) would need the `sink` seam widened.

## Functional requirements

**FR-1 — Reflection works for Process-sourced GitHub tasks.**
A GitHub issue's labels are continuously updated as its task progresses, whether
the task was ingested by `GithubTaskSource` (classic `--github-workflow`/
`--github-step`) or by a Process's `github-issues` action (`--no-github-source`).
- AC1: With `--no-github-source` and a `processes/*.json` file whose action is
  `github-issues` targeting a workflow, driving a task end-to-end against a
  `FakeGithubClient` shows the issue's labels going
  `harness:todo` (initial) → `harness:queued` (claimed by the check) →
  the label for each step that has one in `DEFAULT_STEP_LABELS` as the task
  enters it → `harness:pr-open` on success or `harness:failed` on failure.
- AC2: Without `--no-github-source` (classic ingestion active), behavior is
  byte-for-byte unchanged from today — same label sequence, same number of
  `add_label`/`remove_label` calls per event (no new duplicate reflector is
  registered alongside an active `GithubTaskSource` for the same repo).

**FR-2 — Idempotent, non-blocking.**
Reporting the same `Progress`/`FinishResult` twice for the same task produces no
net label change (calling `report_progress`/`finish` twice yields the same label
set as calling it once) — this already falls out of `add_label`/`remove_label`
being individually idempotent (verified in `github_client.py`) plus the existing
`_set_state` shape (remove all managed-but-not-target, add target), so the new
driver reuses that shape rather than reinventing it. The reflector never raises
in a way that stops the orchestration loop (`CompositeEventSink` already isolates
`SourceReflectorSink` as a whole; unchanged by this task).

**FR-3 — Foreign tasks are silently ignored.**
A task with no `data.source`, a `kind` other than `"github"`, or a `repo` that
doesn't match this reflector's own repo produces zero `add_label`/`remove_label`
calls — mirrors `GithubTaskSource._mine()` exactly (reuse, not reimplement).

**FR-4 — No new coupling into the orchestration core.**
The new driver implements the existing `ports.source.Trigger` (a `TaskSource`
whose `poll()` is always `[]`) and is wired exclusively in `cli.py` (registered
into the same `sources` list `_run` already builds). `dispatcher.py`/`consumer.py`
are untouched; `test_architecture.py`'s existing source-boundary guards
(`test_source_poller_imports_only_ports_and_models`,
`test_orchestration_does_not_import_source_port`, or whatever their current
names are on `origin/main` — confirm exact names post-merge) keep passing
unmodified.

**FR-5 — Single source of truth for the label state machine.**
The label-mapping logic (`_managed` set, `_set_state`, `_mine`, `progress.step →
label`) is not duplicated between `GithubTaskSource` and the new reflector.
`GithubTaskSource` is refactored to *compose* the new reflector internally for
its own `report_progress`/`finish` (delegation), so there is exactly one
implementation of "how a state maps to a label" to keep correct over time.
`GithubTaskSource`'s existing public behavior and existing tests
(`tests/test_github_source.py`) are unaffected — this is an internal refactor.

## Non-functional requirements

- **Idempotency (invariant #21).** Covered by FR-2; no new persisted state is
  introduced — the reflector, like `GithubTaskSource`, is stateless about label
  history and recomputes the target label set from the incoming `Progress`/
  `FinishResult` every time.
- **No blocking of decision-making (invariant #21).** The reflector's `poll()`
  always returns `[]`, so it adds one extra cheap tick per `SourcePoller` cycle
  and nothing else; `report_progress`/`finish` run only from the event-fan-out
  path, which is already exception-isolated at the `CompositeEventSink` layer.
- **Rate-limit / API-call hygiene.** Exactly one reflecting source per
  GitHub-origin repo must ever be registered (never both `GithubTaskSource` *and*
  the new reflector for the same repo) — gate reflector construction on
  `args.no_github_source` (see Rough plan) rather than registering
  unconditionally, to avoid doubling `add_label`/`remove_label` calls per event
  in the common case.
- **No architecture drift.** `test_architecture.py`'s guards (drivers-only import
  of ports, no `sink`/source-port leakage into `dispatcher.py`/`consumer.py`,
  `api/`/`projection.py` never importing `drivers/`) must stay green with zero
  changes to their assertions.

## Data model

No new fields, no schema change. `task.data.source` keeps its existing shape
(`{kind, repo, issue, url}`, stamped by whichever `Check`/`TaskSource` first
creates the task — `GithubTaskSource.poll()` or `GithubIssuesCheck.evaluate()`,
both already populate it identically). The Process `sink` field stays exactly as
documented on `origin/main` (`{"kind": "none"}` or absent) — this task does not
widen `_ACCEPTED_SINK_KINDS` in `fs_processes.py`; that seam is reserved for a
genuinely different destination (e.g. Slack), explicitly out of scope here.

## Interfaces

- **New driver class** (exact name/location for design to pin down; working
  name `GithubLabelReflector`), implementing `ports.source.Trigger`:
  - `poll() -> list[Task]` — always `[]`.
  - `report_progress(task, progress) -> None` / `finish(task, result) -> None` —
    same signature and same label-mapping semantics as
    `GithubTaskSource.report_progress`/`finish` today, factored out so both
    classes share it (FR-5).
  - Constructor needs the same knobs `GithubTaskSource` uses for its managed-label
    bookkeeping: `client`, `repo`, `claimed_label` (default `"harness:queued"`,
    matching `GithubIssuesCheck`'s claim label so the reflector's `_managed` set
    correctly clears it on first progress), `pr_label`, `failed_label`,
    `step_labels`. No `clock`, no ingestion-only knobs (`workflow`/`step`/
    `repository`/`worktree_root`/`select_label`) — those stay on
    `GithubTaskSource` alone.
- **`cli.py`**: a new `_github_reflectors(args, root, registry, *,
  slug_of=github_slug, client=None) -> list[TaskSource]`, mirroring
  `_github_sources`'s per-repo enumeration (same "no token → `[]`", "repo with no
  GitHub origin → skip with a warning" shape), building the new reflector per
  repo with `DEFAULT_STEP_LABELS`. In `_run`:
  ```python
  github = [] if args.no_github_source else _github_sources(args, root, registry)
  reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
  sources = github + reflectors + mergeability
  ```
  No new CLI flag — reuses the existing `--no-github-source` signal, since it
  already marks "ingestion is delegated elsewhere" and every GitHub-origin repo
  in `repos.json` is enumerated identically by both `_github_sources` and the new
  `_github_reflectors` (all-or-nothing coverage, no partial-repo case to reason
  about).
- **No new port.** `ports/source.py`'s `TaskSource`/`Trigger` are exactly enough.
- **No API/UI surface change.**

## Dependencies and scope

**In scope**
- Syncing this worktree with `origin/main` before any edit (see Context).
- The new reflector driver + `GithubTaskSource` refactor to compose it.
- `cli.py` wiring (`_github_reflectors`, `_run`'s `sources` composition).
- Unit tests for the new driver (label transitions, idempotency, `_mine`
  filtering, unknown-step no-op) plus a wiring test in `tests/test_cli.py`.
- An end-to-end test extending `tests/test_processes_e2e.py`'s pattern: a
  `github-issues` process, `FakeGithubClient`, drive-to-quiet, assert the issue's
  label sequence.
- Doc upkeep: `CLAUDE.md`'s module map / driver list gains the new file (repo
  convention — several prior commits update `CLAUDE.md` alongside the code that
  changes the invariant it documents).

**Out of scope**
- A non-GitHub sink (Slack, etc.) or widening `_ACCEPTED_SINK_KINDS` /
  `fs_processes.py`'s sink validation — explicitly excluded by the task notes.
- Expanding `DEFAULT_STEP_LABELS` to cover `plan`/`design`/`architecture` (today
  only `development`/`review`/`land` have a label; an issue sits at
  `harness:queued` through the earlier steps). This is pre-existing behavior for
  classic `GithubTaskSource` too — restoring parity with it is the goal, not
  improving on it. Flagged as an open question below in case the acceptance
  criteria's step examples imply otherwise.
- Any change to `dispatcher.py`, `consumer.py`, `router.py`, or any port other
  than reusing the existing `TaskSource`/`Trigger` ABC.
- Making `SourceReflectorSink.emit()` isolate exceptions *per source* (today one
  misbehaving source in the list can stop the rest of that tick's fan-out from
  running, though `CompositeEventSink` still protects the orchestration loop
  overall). Worth a one-line hardening but not required by the stated acceptance
  criteria — flagged as an open question, not committed to this plan's scope.

## Rough plan

1. **Sync**: `git fetch origin && git merge origin/main` on this task's branch.
   Confirm `docs/adr/0010`/`0014`/`0015`, `drivers/github_issues_check.py`,
   `drivers/fs_processes.py`, and `tests/test_processes_e2e.py` land as read
   above; re-run `pytest -q` once merged to confirm a clean baseline before any
   edit.
2. **Extract the reflector.** In `drivers/github_source.py` (or a new sibling
   module if design prefers), pull `GithubTaskSource`'s `_set_state`/`_mine`/
   `_managed`-construction/`report_progress`/`finish` into the new
   `GithubLabelReflector(Trigger)`. Refactor `GithubTaskSource` to hold one
   internally and delegate its own `report_progress`/`finish` to it. Run
   `tests/test_github_source.py` unmodified to confirm the refactor is
   behavior-preserving.
3. **Wire it in `cli.py`.** Add `_github_reflectors`, thread it into `_run`'s
   `sources` composition as shown above, gated on `args.no_github_source`.
4. **Unit tests** for `GithubLabelReflector`: step-label transitions, `finish`
   ok/not-ok, idempotent double-call, foreign-kind/foreign-repo/no-source no-op,
   unknown-step no-op — same fixture shape as `tests/test_github_source.py`
   (`FakeGithubClient`, `build_source`-style helper).
5. **Wiring test** in `tests/test_cli.py`: `_github_reflectors` returns one
   reflector per GitHub-origin repo (mirroring the existing `_github_sources`/
   `_process_sources` test style); confirm `_run`'s composed `sources` list has
   reflectors present iff `--no-github-source`.
6. **End-to-end test** in `tests/test_processes_e2e.py`: a `github-issues`
   process targeting `default`, a `FakeGithubClient` seeded with one
   `harness:todo` issue, `drive_until_quiet`, assert the final label is
   `harness:pr-open` (or the appropriate terminal label) and that an
   intermediate label (e.g. `harness:coding` for `development`) was observed
   along the way if the test harness allows inspecting mid-run state (otherwise
   assert the final state plus a unit-level assertion that `report_progress` was
   invoked with each expected step).
7. **Docs**: update `CLAUDE.md`'s driver list/module map entry for the new file;
   confirm no invariant text needs a new numbered entry (this is a driver
   addition behind an existing port, not a new architectural rule).
8. **Full suite** (`.venv/bin/pytest -q`) plus `tests/test_architecture.py`
   explicitly, then commit with a `fix:` conventional-commit subject (this
   restores previously-intended behavior — a patch-level release).

## Open questions

- **Should `DEFAULT_STEP_LABELS` be widened to cover `plan`/`design`/
  `architecture`** so the acceptance criteria's illustrative "queued → current
  step" reads literally for every step, not just development/review/land?
  Default taken: no — out of scope, matches existing `GithubTaskSource` behavior,
  and the acceptance criteria describe the sequence as an example ("e.g."), not
  an exhaustive requirement.
- **Exact module home for `GithubLabelReflector`**: same file as
  `GithubTaskSource` (cohesive, both are "GitHub + labels") vs. a new
  `drivers/github_label_reflector.py` (keeps `github_source.py` focused on
  ingestion). Default taken: same file, since `GithubTaskSource` will compose it
  directly and the two are already tightly related; design can override.
- **Should reflectors register unconditionally** (always, for every GitHub-origin
  repo, accepting the minor duplicate-API-call cost when `GithubTaskSource` is
  also active) instead of being gated on `--no-github-source`? Default taken:
  gate on the flag — avoids doubling label API calls in the common (classic
  ingestion) case, and the flag already signals "ingestion is delegated
  elsewhere," which is precisely when the gap exists.
- **Per-source exception isolation in `SourceReflectorSink.emit()`**: worth
  hardening now that a second, less-battle-tested reflection path exists? Default
  taken: leave as-is (out of scope, not required by acceptance criteria); flag
  for a follow-up if development finds it's cheap and low-risk to add.
