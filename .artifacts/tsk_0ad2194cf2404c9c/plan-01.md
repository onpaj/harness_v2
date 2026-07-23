# Plan: CI gate that blocks merge on failing tests

## Summary
The repository already has `.github/workflows/ci.yml`, a reusable workflow with a
`test` job that sets up Python via `uv`, installs the package with dev extras, and
runs `pytest -q`. It's wired with `on: pull_request` (no branch filter, so it covers
PRs targeting `main`), `on: push` to `main`, and `workflow_call` so `release.yml`
gates the release job on it (`needs: test`). Functionally, the "run the suite on
every PR" half of this task is already done in code. What's missing is the
branch-protection setting that makes GitHub actually **block merge** when that job
is red or hasn't run — that's a repo-setting, not a code change, and it is not yet
configured (`GET /repos/onpaj/harness_v2/branches/main/protection` → 404, "Branch
not protected").

## Context
`release.yml` already depends on `ci.yml`'s `test` job so a red `main` can't cut a
release (`needs: test` at `.github/workflows/release.yml:16`). But nothing stops a
PR with failing tests from being merged in the first place — GitHub only blocks
merge on a status check if that check is marked **required** in branch protection
for `main`. Today `main` has no branch protection at all, so a red PR can be merged
manually regardless of what CI reports.

## Functional requirements

**FR-1 — PR workflow trigger.**
Already satisfied: `ci.yml` line 6 has `pull_request:` with no `branches:` filter,
which fires on every PR regardless of target branch — including ones targeting
`main`. No code change needed.
*Acceptance:* opening/pushing to a PR against `main` shows a `CI / test` check run.

**FR-2 — Python 3.11 + dev extras + pytest -q.**
Already satisfied: `ci.yml` installs Python 3.11 via `uv python install 3.11`,
creates a venv, runs `uv pip install -e ".[dev]"`, then `uv run pytest -q`. This is
functionally equivalent to the `pip install -e ".[dev]"` / `pytest -q` called for
in the task description — using `uv` is the project's existing convention
(`astral-sh/setup-uv@v5`), not a deviation worth "fixing."
*Acceptance:* CI logs show Python 3.11.x, a successful editable install with dev
extras, and a `pytest -q` invocation.

**FR-3 — Job fails on any test failure.**
Already satisfied: `pytest -q` exits non-zero on any failure, which fails the
GitHub Actions step and the job. No masking (`|| true`, `continue-on-error`)
present.
*Acceptance:* a deliberately broken test in a scratch branch produces a failed
`CI / test` check.

**FR-4 — Required status check on `main` (the actual gate).**
**Not yet done — this is the real remaining work.** Configure branch protection on
`main` requiring the `test` job (as surfaced by `ci.yml`, i.e. the check named
`CI / test`) to pass before merging, via:
```
gh api repos/onpaj/harness_v2/branches/main/protection \
  -X PUT \
  -H "Accept: application/vnd.github+json" \
  -f required_status_checks[strict]=true \
  -f 'required_status_checks[contexts][]=test' \
  -f enforce_admins=true \
  -f required_pull_request_reviews='' \
  -f restrictions=''
```
(exact payload/context name to be confirmed against what GitHub actually reports
for the reusable-workflow job name — reusable `workflow_call` jobs show up as
`<workflow file>/<job>` or just the job name depending on caller; verify by
inspecting an actual PR's check-run name before applying).
This is a repo-setting mutation with a shared, hard-to-reverse blast radius
(affects every future PR merge on this repo) — the design/dev step should apply it
deliberately and call it out for confirmation rather than folding it silently into
a code commit. The acting GitHub identity currently has `ADMIN` permission on
`onpaj/harness_v2`, so it is technically possible to script.
*Acceptance:* `gh api repos/onpaj/harness_v2/branches/main/protection` returns 200
with the `test` context listed in `required_status_checks.contexts`; the GitHub PR
UI shows "Merging is blocked" while the check is pending/failing, and the merge
button is unavailable until it's green.

**FR-5 — Opt-in smoke excluded by default.**
Already satisfied: `tests/test_smoke_claude.py` only runs when
`HARNESS_SMOKE_CLAUDE=1` is set (per `pytest.ini`/test-level skip logic already in
the suite), and `ci.yml` sets no such env var, so `pytest -q` in CI skips it
automatically. No code change needed; confirm during verification that the CI log
shows it as `skipped`, not `passed`/absent.

## Non-functional requirements
- **Reliability:** the git/FS smoke tests (`test_smoke.py`, `test_smoke_git.py`)
  need a configured git identity; `ci.yml` already sets `user.name`/`user.email`/
  `init.defaultBranch` globally before running tests (lines 28–32) — keep this.
- **Speed:** `uv` + `enable-cache: true` keeps the PR feedback loop fast; no change
  needed, but don't regress it by switching back to plain `pip`.
- **Security:** branch protection changes use the existing `gh` authentication of
  a repo admin; no new secrets or tokens required. `enforce_admins=true` is a
  judgment call — it means even the repo admin can't bypass the gate by pushing
  directly; flag this choice explicitly rather than assuming it.

## Data model
Not applicable — this task has no application data model. The only "entities" are
GitHub-native: the Actions workflow (`ci.yml`), the check run it produces
(`test`/`CI / test`), and the branch-protection rule on `main` that references
that check-run name by string.

## Interfaces
- **GitHub Actions workflow** (`.github/workflows/ci.yml`): triggers on
  `pull_request` and `push: [main]`, job `test` — already implemented, no new
  workflow file needed.
- **GitHub REST API / `gh` CLI**: `PUT
  /repos/{owner}/{repo}/branches/{branch}/protection` to register the required
  status check. This is the one interface that still needs to be exercised (either
  by this task's implementation step via `gh api`, or manually by Ondrej in repo
  Settings → Branches if the harness should not be granted admin-mutation
  authority over branch protection).
- **PR UI**: the merge button becomes conditionally blocked once the required
  check is registered — no direct interface work, it's a side effect of FR-4.

## Dependencies and scope
- Depends on: existing `.github/workflows/ci.yml` and `release.yml` (untouched by
  this task, per the task notes), repo admin access for the branch-protection API
  call.
- In scope: verifying `ci.yml` already meets FR-1/2/3/5 (it does — likely no diff
  needed there), applying or clearly documenting the FR-4 branch-protection
  change, and confirming end-to-end behavior (a failing-test PR shows blocked
  merge).
- Out of scope (per task notes): linting/type-checking gates; modifying
  `release.yml`; changing the smoke-test opt-in mechanism.
- Explicitly out of scope for this plan: deciding `enforce_admins` on behalf of
  the user — surfaced as an open question below instead of silently defaulting.

## Rough plan
1. **Confirm no code diff is needed** for FR-1/2/3/5: re-read `ci.yml` against the
   acceptance criteria one more time at design/dev time in case this plan is
   stale by then, but expect zero changes to the workflow file itself.
2. **Determine the exact check-run context name** GitHub assigns to the reusable
   `test` job as consumed via `pull_request` (open a throwaway PR or inspect an
   existing one) — needed to target `required_status_checks.contexts` correctly.
3. **Apply branch protection on `main`** via `gh api` (or hand the exact command
   to Ondrej to run, if the harness should not self-grant this authority) —
   require the `test` check, decide `enforce_admins` explicitly rather than
   defaulting silently.
4. **Verify end-to-end**: open a scratch PR with a deliberately failing test,
   confirm the merge button is blocked; fix the test, confirm it unblocks.
5. **Document the setting** in the repo (e.g. a short note in `CLAUDE.md` or a
   `docs/` note) so future readers know branch protection — not just the
   workflow file — is what enforces the gate, since that setting isn't visible
   in a `git diff`.

## Open questions
- **Should the harness itself mutate branch protection via `gh api`, or should
  this task only document the exact command for Ondrej to run manually?** The
  acting identity has admin rights, so it's technically possible, but this is a
  shared, hard-to-reverse repo-setting change per the "executing actions with
  care" guidance. Default taken for this plan: implement it as a documented,
  explicit step that a human confirms before/while it runs — not a silent part
  of a `git commit`.
- **Should `enforce_admins` be `true` or `false`?** `true` means even repo admins
  can't merge past a red check; `false` leaves an escape hatch. No stated
  preference in the task. Default taken: recommend `true` (matches the stated
  goal "cannot be merged unless... passes"), flagged for explicit confirmation.
- **Exact required-check context string** for a `workflow_call`-based job
  consumed via a direct `pull_request` trigger — GitHub's naming here can be
  `test` or `CI / test` depending on how the check is surfaced; must be verified
  against a live check run before the branch-protection API call, not assumed.
