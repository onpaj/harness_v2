# Development: continuously reflect task state onto the source GitHub issue's labels

Implemented exactly as planned in `plan-01.md`, designed in `design-01.md`, and
corrected/endorsed in `architecture-01.md` — extract a standalone
`GithubLabelReflector`, have `GithubTaskSource` compose it, and register the
reflector in `cli.py` only when `--no-github-source` delegates ingestion to a
Process. No port, schema, dispatcher, or consumer change.

## 0. Prerequisite: synced with `origin/main`

This worktree was 57 commits behind `origin/main` (confirmed again at the
start of this step). Ran `git fetch origin && git merge origin/main --no-edit`
(merge commit `cd4f340`), then confirmed a clean baseline with
`.venv/bin/pytest -q` (1099 passed, 1 skipped) before touching any file named
in the plan. All work below is against the merged tree.

## 1. Files changed

- **`src/harness/drivers/github_source.py`** — added `GithubLabelReflector`
  (subclasses `TaskSource` directly, per architecture-01.md §2.3's correction
  — not `Trigger`, since `Trigger` names the inbound-only shape and this class
  is the exact mirror: `poll()` always `[]`, real `report_progress`/`finish`).
  It owns the entire state→label mapping (`_set_state`/`_mine`/`_issue`,
  `_managed` label set) for a single `(client, repo)` pair, matched purely by
  `task.data.source.kind == "github"` and `.repo == self._repo` — it doesn't
  care who created the task (`GithubTaskSource.poll()` or
  `GithubIssuesCheck.evaluate()`), only where it's headed.

  `GithubTaskSource` is refactored to *compose* a `GithubLabelReflector`
  internally in its constructor and delegate `report_progress`/`finish` to
  it — its own `_set_state`/`_mine`/`_issue`/`_managed`/`_pr_label`/
  `_failed_label` are gone, replaced by `self._reflector`. `poll()` and every
  other public behavior/constructor argument are unchanged. This is a pure
  refactor: `tests/test_github_source.py`'s 11 pre-existing tests pass
  unmodified, proving delegation is behavior-preserving (FR-1/AC2, FR-5).

- **`src/harness/cli.py`**:
  - New `_github_reflectors(args, root, registry, *, slug_of=github_slug,
    client=None) -> list[TaskSource]`, sibling to `_github_sources`/
    `_mergeability_sources`, same enumeration shape (no token → `[]`, repo
    with no GitHub origin → silently skipped, already warned about by
    `_github_sources`). Builds one `GithubLabelReflector` per GitHub-origin
    repo in `repos.json`, using `DEFAULT_STEP_LABELS`.
  - `_run`'s `sources` composition:
    ```python
    github = [] if args.no_github_source else _github_sources(args, root, registry)
    reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
    sources = github + reflectors + mergeability
    ```
    Both branches key off the same `args.no_github_source` flag, so a repo is
    structurally covered by exactly one of `GithubTaskSource` (classic
    ingestion, which now reflects via its own composed reflector) or the
    standalone `GithubLabelReflector` (process-delegated ingestion) — never
    both, so no doubled `add_label`/`remove_label` calls (FR-4, the plan's/
    architecture's "gate, don't register unconditionally" decision).

- **`CLAUDE.md`** — extended the existing `drivers/github_source.py` module-map
  entry to describe `GithubLabelReflector` and its `--no-github-source`
  wiring; `tests/test_claude_md_module_map.py` still passes (no new file, so
  the driver-file list on that line is unchanged).

## 2. Tests added

- **`tests/test_github_source.py`** (+11 tests, all against
  `GithubLabelReflector` directly): `poll()` always `[]`; known-step sets the
  step label; unknown step is a no-op; `finish` ok/not-ok; double
  `report_progress`/double `finish` are idempotent (no net label change, FR-2);
  no-`data.source` task is ignored; foreign-repo task is ignored; foreign-`kind`
  task is ignored (FR-3); non-managed labels (e.g. `bug`) are preserved.
- **`tests/test_cli.py`** (+4 tests): `_github_reflectors` builds one reflector
  per GitHub-origin repo; skips a repo without a GitHub origin; returns `[]`
  without a token; and a `main(["run", ...])`-level test
  (`test_run_gates_github_sources_and_reflectors_mutually_exclusively`) that
  monkeypatches both `cli._github_sources` and `cli._github_reflectors` to spy
  functions and asserts `_run` calls exactly one of them per invocation, keyed
  by `--no-github-source` — proving the mutual-exclusion property structurally,
  not just by reading the two-line diff.
- **`tests/test_processes_e2e.py`** (+1 test,
  `test_github_issues_process_reflects_task_state_onto_issue_labels`): builds a
  harness the way `--no-github-source` configures it — a `github-issues`
  Process (`_process_sources`) for ingestion plus a standalone
  `GithubLabelReflector` for the same repo appended to `sources` — drives a
  seeded `harness:todo` issue through `plan → development → review → land →
  end` against a `FakeGithubClient` and a `ScriptedBehavior`, and asserts the
  issue's final label is `harness:pr-open` (via `LandingBehavior` +
  `MemoryForge`) and that `development`/`review` were actually run (`land` runs
  through the built-in `LandingBehavior`, not the scripted one, as confirmed by
  reading `app.py`'s `behavior_for`).

## 3. Verification

```
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```
Result: **1115 passed, 1 skipped** (was 1099 passed, 1 skipped on the merged
baseline before this step's edits — the delta is exactly the 16 new tests
above; zero regressions).

Targeted runs during development, all green:
- `tests/test_github_source.py` (22, was 11)
- `tests/test_cli.py` (118, was 114)
- `tests/test_processes_e2e.py` (6, was 5)
- `tests/test_architecture.py` (unmodified, still green — confirms invariants
  #18–#20 hold: the new class is wired only in `cli.py`, `dispatcher.py`/
  `consumer.py` untouched, no `ports.source` leakage)
- `tests/test_claude_md_module_map.py`, `tests/test_adr_docs.py`

## 4. Acceptance criteria check

- [x] As a task moves between states/steps, its source GitHub issue's labels
  are updated to reflect the current state — restored for Process-sourced
  tasks via `GithubLabelReflector`, registered whenever `--no-github-source`
  is set; unchanged (still working) for classic `GithubTaskSource`-sourced
  tasks via the composed reflector.
- [x] Idempotent, non-blocking — `_set_state`'s "remove all managed but
  target, add target" shape is unchanged and stateless; two consecutive
  `report_progress`/`finish` calls for the same state produce the same label
  set (tests: `test_reflector_double_report_progress_is_idempotent`,
  `test_reflector_double_finish_is_idempotent`). `poll()` is always `[]` so
  the reflector adds one cheap no-op tick per `SourcePoller` cycle; the
  reflection call itself runs only from `SourceReflectorSink.emit()`, whose
  exception posture is unchanged by this task.
- [x] A task with no `data.source` or a foreign `kind`/`repo` produces zero
  `add_label`/`remove_label` calls — `_mine()` is the same guard
  `GithubTaskSource` already used, now shared by both classes (tests:
  `test_reflector_ignores_task_without_source`,
  `test_reflector_ignores_task_from_another_repo`,
  `test_reflector_ignores_task_from_foreign_kind`).
- [x] No new coupling into `dispatcher.py`/`consumer.py` — the new class
  implements the existing `ports.source.TaskSource` and is wired exclusively
  in `cli.py`'s `_run`; `test_architecture.py` passes unmodified.

## 5. Scope notes (matching the plan's explicit exclusions)

- `DEFAULT_STEP_LABELS` was **not** widened to cover `plan`/`design`/
  `architecture` — matches pre-existing `GithubTaskSource` behavior; an issue
  sits at `harness:queued` through those earlier steps, same as before this
  change.
- The Process `sink` field / `_ACCEPTED_SINK_KINDS` in `fs_processes.py` was
  **not** touched — GitHub→GitHub reflection needed no schema widening, per
  ADR-0015's same-origin-defaults-to-nothing-declared design intent.
- `SourceReflectorSink.emit()`'s per-source exception isolation was **not**
  hardened — pre-existing gap, not worsened by this change (source count per
  repo is unchanged in both configurations by construction), left as a
  possible fast-follow per architecture-01.md's fourth decision.

## 6. How to verify manually

```sh
.venv/bin/pytest -q tests/test_github_source.py tests/test_cli.py tests/test_processes_e2e.py tests/test_architecture.py
.venv/bin/pytest -q
```
Both commands were run during this step and are green.
