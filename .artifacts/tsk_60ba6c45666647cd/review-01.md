# Review: Rename 'default' workflow to 'development'

## Verified against plan/design/architecture

- `DEFAULT_WORKFLOW`/`DEFAULT_DEFINITION["name"]` in `cli.py` renamed
  `"default"` → `"development"`; topology (`start`/`transitions`) unchanged —
  confirmed byte-for-byte identical except the `name` key.
- `_migrate_legacy_workflow(layout, workflow_name)` matches the design
  exactly: guarded on `workflow_name == DEFAULT_WORKFLOW`, no-ops if
  `development.json` already exists or `default.json` doesn't, copies
  verbatim, never deletes/modifies the legacy file. Docstring carries the
  topology-invariant note the architecture step asked for.
- Wired into both `_init` (before the create-if-missing check, so custom
  legacy content survives) and `_run` (before `build()`, so a service
  restart with no prior `init` still resolves) — both call sites verified by
  reading `cli.py` directly.
- `github_source.py` and `memory.py` defaults updated in lockstep, as
  specified.
- README worked example updated.
- No stray literal `"default"` workflow references remain in `src/harness/`
  (grepped); all argparse `--workflow`/`--github-workflow` defaults resolve
  through the `DEFAULT_WORKFLOW` constant.
- `--github-workflow` diverging from `--workflow` is explicitly scoped as a
  non-goal in the design (an operator's explicit custom choice) — correctly
  left unhandled, not a gap.

## Acceptance criteria

- [x] `workflows/default.json` → `workflows/development.json`: no such file
  is tracked in the repo (it's a runtime-generated artifact); the rename is
  fully captured by the `DEFAULT_WORKFLOW` constant + migration helper, which
  is the correct scope.
- [x] All code/docs references to the 'default' workflow updated.
- [x] `harness init` creates `workflows/development.json`.
- [x] Backward compatibility: legacy `default.json` is preserved and copied
  forward automatically on both `init` and `run`, never deleted/overwritten.
- [x] Tests pass: ran `.venv/bin/pytest -q` myself — 474 passed, 1 skipped,
  matching the development step's report.

## Test quality

Both new tests in `test_cli.py` are meaningful, not just happy-path checks:
`test_init_migrates_legacy_default_workflow_preserving_edits` asserts the
legacy file survives untouched, and
`test_run_migrates_legacy_default_workflow_without_prior_init` drives an
actual `dispatcher.tick()` and asserts the task left the inbox and landed in
`queues/plan/` — real dispatch, not just a successful `build()`. This matches
the architecture review's explicit guidance.

The four other test files touched (`test_source_memory.py`,
`test_smoke_github.py`, `test_phase4_e2e.py`, `test_smoke.py`) were each
genuinely coupled to the changed default value (not arbitrary fixture
strings), and the fixes are minimal and correct.

## Verdict

No functional requirement is unmet, no architecture conflict, no missing
required test, no correctness bug found.

```json
{"outcome": "done", "summary": "Verified the rename end-to-end against plan/design/architecture: DEFAULT_WORKFLOW/DEFAULT_DEFINITION and both driver defaults renamed to 'development' with unchanged topology, _migrate_legacy_workflow correctly wired into both _init and _run and preserves the legacy file, README updated, no stray 'default' literals remain in src/harness. Ran the full suite myself: 474 passed, 1 skipped. New migration tests exercise real dispatch, not just build(). No issues found."}
```
