# Review: CI gate that blocks merge on failing tests

## What was checked

Re-verified every claim in plan/design/architecture/development live, rather than
trusting the artifact trail:

- `.github/workflows/ci.yml` (read in full): triggers on `pull_request` (unfiltered,
  covers PRs into `main`), `push: [main]`, and `workflow_call`. Job `test` installs
  Python 3.11 via `uv`, runs `uv pip install -e ".[dev]"`, configures git identity for
  the smoke tests, runs `uv run pytest -q` with no failure masking (`|| true`,
  `continue-on-error`), then a separate build-check step. Matches FR-1/2/3.
- `gh api repos/onpaj/harness_v2/branches/main/protection` → live `200`, body
  contains `"required_status_checks":{"strict":false,"contexts":["test"]}` and
  `"enforce_admins":{"enabled":false}` — confirms FR-4's core requirement (the
  `test` check is registered as required) is genuinely in effect on `main` right
  now, not just claimed in an artifact.
- `.venv/bin/pytest -q` → `472 passed, 1 skipped, 0 failed`. The skip is
  `tests/test_smoke_claude.py` (opt-in, gated by `HARNESS_SMOKE_CLAUDE=1`, unset),
  confirming FR-5 end-to-end.
- `git diff main -- CLAUDE.md`: the only change in this branch is a 10-line
  documentation paragraph added to the Git-conventions section, stating that `main`
  requires the `test` status check via branch protection (a repo setting, not
  workflow code) and that renaming/removing the `test` job breaks the rule
  silently. Accurate, matches the live state, appropriately placed next to the
  existing release-workflow note.
- `.github/workflows/release.yml` — confirmed untouched (not in the diff), per the
  task notes.
- No files under `harness/`, no ports/drivers/tests touched — consistent with this
  being a zero-application-code task.

## Acceptance criteria — verified against live state, not just artifacts

- [x] Workflow runs on `pull_request` targeting `main` — `ci.yml`'s unfiltered
  `pull_request:` trigger covers this; already existed, unmodified.
- [x] Python 3.11 + dev extras + `pytest -q` — present in `ci.yml`, `uv`-based
  (functionally equivalent to the literal `pip install -e ".[dev]"` wording in the
  task, using the project's existing `uv` convention).
- [x] Job fails on any test failure — `pytest -q` unmasked; a failing test fails the
  step/job/check.
- [x] Required for merging `main` — verified live via `gh api`: `contexts: ["test"]`
  is registered on `main`'s branch protection today. Documented in `CLAUDE.md` as
  required by the acceptance criteria, since the setting is invisible in `git diff`.
- [x] Opt-in smoke excluded — verified live: `test_smoke_claude.py` shows as
  `skipped` in a real `pytest -q` run, not silently absent or passing.

## Judgment calls, checked for reasonableness

- The team chose not to escalate `strict`/`enforce_admins` from their current
  `false` values without explicit human confirmation. The task's literal wording
  ("cannot be merged unless... passes") is satisfied by the required-check
  registration alone — `strict`/`enforce_admins` are refinements (stale-branch
  re-run requirement, admin-bypass removal) beyond what was asked, and both carry
  real, hard-to-reverse blast radius on a shared repo. Deferring them with the
  exact follow-up command documented in `development-01.md` is consistent with this
  repo's CLAUDE.md guidance on executing risky actions carefully, and does not
  leave any acceptance criterion unmet.
- No second CI workflow was created and `ci.yml`/`release.yml` were left untouched,
  matching the task notes and avoiding a duplicate/competing check.

## Verdict

No functional requirement is unmet, nothing conflicts with the architecture (there
is no architecture surface here to conflict with — the task is entirely CI
config/repo-setting), and every claim in the artifact trail checks out against live
repo/GitHub state and a real test run. The one code change (`CLAUDE.md` doc note) is
accurate and appropriately scoped.

```json
{"outcome": "done", "summary": "All acceptance criteria verified live: ci.yml already covers FR-1/2/3/5 unmodified, branch protection on main requires the 'test' check (confirmed via gh api, contexts:['test']), full suite passes 472/1-skipped, and the CLAUDE.md doc note accurately documents the setting. No functional gaps or architecture conflicts found."}
```
