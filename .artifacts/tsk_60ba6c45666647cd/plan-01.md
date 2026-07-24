# Plan: Rename 'default' workflow to 'development'

## Summary

The bundled workflow that `harness init` writes out and that `harness run`
falls back to when no `--workflow` is given is currently called `default`.
This task renames that name (and every reference to it as *the default
workflow*) to `development`, without changing the workflow's topology
(`plan → design → architecture → development → review → land → end`). The
only functional wrinkle is that a workflow is looked up purely by filename
(`workflows/<name>.json`), so an already-initialized deployment — including
this machine's own `~/.harness` — has a live `workflows/default.json` that
must keep working across the upgrade.

## Context

`default` as a name says nothing about what the workflow is for; `development`
matches how the codebase already talks about it elsewhere (e.g. the
`development` *step* inside the chain, `DEFAULT_STEP_LABELS["development"]`).
This is a pure clarity/naming change — no new capability, no topology change.

The one real risk is operational, not architectural: this repo's own harness
instance is a live deployment. `~/.harness/workflows/default.json` exists on
this machine right now (confirmed by reading it directly), created by a past
`harness init`. `FilesystemWorkflowRepository.get(name)` resolves a workflow
purely by `<root>/<name>.json` — the JSON's internal `"name"` field is
cosmetic and falls back to the filename if absent (`fs_workflows.py:60-61`).
So if the code ships a new default of `"development"` and nothing else
changes, the next `harness run` (or the launchd service restart) looks for
`workflows/development.json`, doesn't find it on this machine, and fails with
`WorkflowNotFound` — the harness would break itself. That is exactly the
"existing deployments still work" acceptance criterion, and it needs a real
migration, not just a renamed constant.

## Functional requirements

**FR-1 — `harness init`'s default workflow name becomes `development`**
- AC1: `cli.DEFAULT_WORKFLOW == "development"`.
- AC2: `cli.DEFAULT_DEFINITION["name"] == "development"`.
- AC3: `harness init --root <tmp>` with no `--workflow` flag creates
  `workflows/development.json` (not `default.json`), with the transition
  chain unchanged.
- AC4: `harness run` / `harness service install` — which invoke `harness run`
  with no explicit `--workflow` — resolve the `development` workflow by
  default.

**FR-2 — Backward-compatible migration for existing deployments**
- AC1: When `workflows/default.json` already exists and
  `workflows/development.json` does not, running `harness init` (with the
  default `--workflow`) migrates forward by copying the existing
  `default.json` content into `development.json` — preserving any operator
  edits — rather than overwriting it with a fresh `DEFAULT_DEFINITION`.
- AC2: The legacy `workflows/default.json` is never deleted or modified by
  the migration. A task created before the upgrade with
  `workflow_template == "default"` must still resolve and run to completion
  after the upgrade.
- AC3: The same migration is reachable from the `harness run` code path (not
  only `harness init`), since that is what the launchd service actually
  invokes on restart — the operator should not have to remember to run
  `harness init` by hand after an upgrade for the service to come back up.
- AC4: A brand-new root (no pre-existing `default.json`) gets only
  `development.json` — the migration must not manufacture a `default.json`
  where none existed.
- AC5: An operator who explicitly passes `--workflow default` is left alone —
  migration only fires for the *default* name, never for an explicit,
  deliberate choice of `"default"` as a custom workflow name.

**FR-3 — Update source/doc references to "the default workflow"**
- AC1: `cli.py` comments referencing the "default workflow" concept reworded
  for `development`.
- AC2: `GithubTaskSource.__init__`'s `workflow: str = "default"` parameter
  default (`drivers/github_source.py:33`) becomes `"development"`.
- AC3: The matching fake/test double in `drivers/memory.py:212`
  (`workflow: str = "default"`) becomes `"development"` for consistency with
  the real driver's default.
- AC4: `README.md`'s worked example (`"name": "default"`, README.md:192) and
  its surrounding prose read `development`.
- AC5: Historical documents under `docs/superpowers/{specs,plans}/*.md` are
  **not** edited — they are point-in-time records of already-shipped phases
  (git history is the authority on what was true when written); see Scope.

**FR-4 — Tests reflect the new default**
- AC1: `tests/test_cli.py` tests that call `main(["init", "--root", ...])`
  with no `--workflow` and then assert on `workflows/default.json` are
  updated to `workflows/development.json`; prefer asserting via the imported
  `DEFAULT_WORKFLOW` constant over a hardcoded literal so the test can't
  silently drift from the constant again.
- AC2: A new test covers the FR-2 migration: pre-seed
  `workflows/default.json` with custom content, run `harness init` with no
  `--workflow`, assert `development.json` now has that content and
  `default.json` is untouched.
- AC3: A new test covers `harness run` picking up the migrated workflow
  without a prior explicit `harness init` call (FR-2/AC3).
- AC4: `.venv/bin/pytest -q` passes in full.

## Non-functional requirements

- No behavior change to workflow topology — steps and transitions are
  byte-for-byte identical, only the name and file location move.
- Migration is a small, targeted addition (a handful of lines gated on "new
  name missing, legacy name present"), not a general migration framework —
  consistent with this project's preference for the smallest change that
  solves the actual problem.
- No data loss: an operator-customized `default.json` is copied forward, not
  overwritten or deleted.

## Data model

No new entities. `Workflow` is unchanged (`models.py`). A workflow is
identified solely by its filename under `workflows/<name>.json`
(`fs_workflows.py`); the JSON's own `"name"` field is display-only. `Task
.workflow_template` is an opaque string that must match some
`workflows/<name>.json` — its default value is what's changing, not its
type or role.

## Interfaces

- CLI: `--workflow` / `--github-workflow` default value changes from
  `"default"` to `"development"` (`harness init`, `harness run`). No new
  flags.
- No HTTP/board/API surface change — `BoardView`/`ArtifactView` only ever
  display whatever string is stored in `workflowTemplate`; they don't care
  what the default is.

## Dependencies and scope

**In scope:**
- `src/harness/cli.py` — `DEFAULT_WORKFLOW`, `DEFAULT_DEFINITION`, the `_init`
  (and `_run`) migration logic, comments.
- `src/harness/drivers/github_source.py` — `workflow` parameter default.
- `src/harness/drivers/memory.py` — matching fake's `workflow` parameter
  default.
- `README.md` — worked example.
- `tests/test_cli.py` — assertions coupled to `DEFAULT_WORKFLOW`, plus new
  migration tests.

**Explicitly out of scope:**
- Historical phase spec/plan documents under `docs/superpowers/`. They
  describe phases already implemented and merged; editing them would
  misrepresent history rather than document it.
- The ~50 other test files (`test_models.py`, `test_dispatcher.py`,
  `test_router.py`, the `test_*_e2e.py`/`test_smoke*.py` family, etc.) that
  use the literal string `"default"` purely as an arbitrary, self-contained
  `workflow_template`/`name=` fixture value — they construct their own
  `default.json` (or in-memory equivalent) and pass `"default"` explicitly,
  so they are unaffected by what the CLI's default happens to be. Renaming
  them is cosmetic churn, not required by any acceptance criterion.
- Renaming the workflow's `development` *step* (a different namespace from
  the workflow's name) — not requested and would be a separate, larger
  change to the transition chain.
- Deleting `~/.harness/workflows/default.json` on this or any machine — left
  to the operator once they've confirmed no in-flight task still references
  it.

**Depends on:** nothing upstream; this is a self-contained, leaf-level
change.

## Rough plan

1. **Design**: pin down exactly where the migration check lives — a single
   small helper (e.g. `_migrate_legacy_workflow(layout)`), called from both
   `_init` and `_run` before the definition file is read/written, firing only
   when `args.workflow == DEFAULT_WORKFLOW`, `development.json` is absent,
   and `default.json` is present.
2. Rename the constant/definition in `cli.py`
   (`DEFAULT_WORKFLOW = "development"`, `DEFAULT_DEFINITION["name"] =
   "development"`) and adjust nearby comments.
3. Implement the migration helper and wire it into `_init`/`_run`.
4. Update `GithubTaskSource`'s and its fake's `workflow` parameter default.
5. Update `README.md`'s example and prose.
6. Update `tests/test_cli.py` fixtures/assertions tied to the constant; add
   the two migration tests (FR-4/AC2, AC3).
7. Run `.venv/bin/pytest -q`; fix any fallout.
8. Sanity-check against this machine's real `~/.harness` (confirmed today to
   contain `workflows/default.json` with the standard 7-step chain) before
   any service relying on this code is restarted, since it is a live
   deployment, not just a test fixture.

## Open questions

- Should the ~50 unrelated test files that use `"default"` as an arbitrary
  fixture name be renamed to `"development"` for repo-wide consistency?
  Default taken here: no — leave them, since they test generic behavior
  rather than the default-workflow feature itself (see Scope).
- The new workflow name `development` coincides with the name of one of its
  own steps (`architecture → development → review`). Confirmed not a
  technical collision (different namespace: workflow filename vs. step id
  within a workflow), but it may read confusingly in prose ("the
  `development` workflow's `development` step"). Default taken here: no step
  rename, just flag it if it causes confusion in docs.
- Should `harness update` (the `uv tool upgrade` wrapper) itself trigger the
  migration, or is it sufficient that the next `harness run` invocation
  (i.e., the next service restart) performs it? Default taken here: `_run`
  carries the check too (FR-2/AC3), so migration is self-healing on next
  restart with no separate operator step required.
