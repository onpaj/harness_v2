# Merge conflict resolution — PR #126

## Scope

`git status` at the start of this step showed four files left `UU` (both
modified, unresolved): `CLAUDE.md`, `src/harness/app.py`, `src/harness/cli.py`,
`tests/test_cli.py`. Every other changed file was already a clean,
non-conflicting merge result.

All four conflicts had the same underlying shape: `HEAD` (this branch) added
Jira issue ingestion (`drivers/jira_client.py`, `drivers/jira_issues_check.py`,
the `jira-issues` process action), while `origin/main` independently added a
declarative `CheckSpec`/`ParamSpec`/`CheckDefinition` layer so the process
admin form can render an action's parameters from data (wrapping
`github-issues`, `github-conflicts` and `failed-tasks` in `CheckDefinition`),
plus an unrelated `registry` parameter added to `cli.serve()`. Both sides are
additive and compose — nothing to choose between, just combine.

## Resolution per file

- **`CLAUDE.md`**: kept both `drivers/jira_client.py`/`drivers/jira_issues_check.py`
  doc lines from `HEAD` (new feature, no equivalent on the other side) and took
  `origin/main`'s updated `ports/triggers.py` doc line (mentions the new
  `CheckSpec`/`ParamSpec`/`CheckDefinition`/`check_spec_of` exports) — confirmed
  those exports already exist un-conflicted in `ports/triggers.py`, so the
  `origin/main` doc line is simply more accurate, not a stale alternative.

- **`src/harness/app.py`**: `HEAD` registered `"failed-tasks"` as a bare
  lambda; `origin/main` wraps the same factory in
  `CheckDefinition(spec=FAILED_TASKS_SPEC, factory=...)` so the process form
  treats it as a fully-defined action. Took `origin/main`'s version — the
  `CheckDefinition`/`FAILED_TASKS_SPEC` imports were already present
  un-conflicted at the top of the file, confirming this is the intended shape.

- **`src/harness/cli.py`** (`_process_check_factories`, two hunks):
  - Import hunk: kept both `HEAD`'s `from harness.drivers.jira_issues_check
    import JiraIssuesCheck` and `origin/main`'s `from harness.ports.triggers
    import CheckDefinition` — both are used later in the function.
  - Return-dict hunk: kept `HEAD`'s `jira_issues_factory` (defined just above,
    unconflicted) and `origin/main`'s `CheckDefinition`-wrapped
    `github-issues`/`github-conflicts` entries. Added `"jira-issues":
    jira_issues_factory` as a bare factory (no `CheckSpec` exists yet for it
    on either side of the merge — confirmed by grepping
    `drivers/jira_issues_check.py`), which is a valid `CheckFactory` on its
    own and falls back to `check_spec_of`'s generic name-only spec in the
    admin form, exactly as any other unspecced factory would.

- **`tests/test_cli.py`** (two `fake_serve` stubs): `HEAD`'s stub signature
  was missing the `registry=None` parameter `origin/main` added to the real
  `harness.cli.serve()` (confirmed at `src/harness/cli.py:1861-1869`) and that
  every other `fake_serve` stub in this file already carries. Took
  `origin/main`'s signature for both hunks — no test-body logic changed.

## Verification

- `grep -rn '^<<<<<<<\|^=======\|^>>>>>>>' CLAUDE.md src/harness/app.py
  src/harness/cli.py tests/test_cli.py` → no matches.
- Built a fresh `.venv` (none existed in this worktree) and installed
  `pip install -e ".[dev]"`.
- Full test suite, `GITHUB_TOKEN`/`HARNESS_HEAL_REPO` unset:
  **1414 passed, 1 skipped** (the skip is the opt-in `HARNESS_SMOKE_CLAUDE=1`
  smoke test, expected).
