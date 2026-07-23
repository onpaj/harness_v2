# Development: CI gate that blocks merge on failing tests

## Summary

Confirmed live, in this order, before making any change:

1. `.github/workflows/ci.yml` already satisfies FR-1/FR-2/FR-3/FR-5 exactly
   as described in plan/design/architecture — re-read it byte for byte, no
   discrepancy found, **no edit made**. It triggers on `pull_request`
   (unfiltered, so it covers PRs into `main`), `push: [main]`, and
   `workflow_call`; the `test` job installs Python 3.11 via `uv`, runs
   `uv pip install -e ".[dev]"`, then `uv run pytest -q` with no failure
   masking. `release.yml` is untouched, as required.
2. **FR-4 (the actual gate) turned out to already be applied.** A live
   `gh api repos/onpaj/harness_v2/branches/main/protection` call — which the
   design/architecture steps had recorded as `404 Branch not protected` —
   now returns `200` with `required_status_checks.contexts: ["test"]`. So
   the required-status-check registration this task set out to make is
   already in place on `main` (applied outside this dev step, between the
   architecture step and now). `strict: false` and `enforce_admins: false`
   — the two settings the design explicitly flagged as needing a human
   go-ahead before being set to `true` — are at their conservative
   (non-escalated) defaults. Since this dev step runs non-interactively and
   those two settings were explicitly called out as *not* to be assumed
   silently, I left them as-is rather than escalating enforcement further
   without a real confirmation from Ondrej. The acceptance criterion itself
   ("the test-gate check is required for merging main") is satisfied by the
   current state: a PR cannot merge while the `test` check is red or
   missing.
3. Added a short documentation note (the one piece of this task that is
   actual file content) to `CLAUDE.md`, since branch protection is invisible
   in `git diff` and a future reader touching `ci.yml` needs to know the
   `test` job's name is load-bearing for the merge gate, not just for CI
   green/red.

## Files changed

- **`CLAUDE.md`** — added a paragraph after the "commit messages are
  conventional commits" note (same Git-conventions section, since it's the
  natural home next to the other release/CI-adjacent note) stating: `main`
  requires the `test` status check via branch protection (a repo setting,
  not workflow code); the check comes from `.github/workflows/ci.yml`'s
  `test` job; renaming/removing that job silently breaks the protection rule
  and must be paired with updating the required check.
- **`.github/workflows/ci.yml`** — no change (verified already correct).
- **`.github/workflows/release.yml`** — no change (out of scope, untouched
  per task notes).

No application code, tests, ports, drivers, or module-map files were
touched — this task has zero surface in `harness/`.

## Verification performed

- `.venv/bin/pytest -q` → **472 passed, 1 skipped, 0 failed** (16.38s). The
  1 skip is `tests/test_smoke_claude.py`, gated by `HARNESS_SMOKE_CLAUDE=1`
  (unset here), confirming FR-5's "opt-in smoke excluded by default"
  end-to-end, not just by reading the code.
- `gh api repos/onpaj/harness_v2/branches/main/protection` → `200`, body
  includes `"required_status_checks":{"strict":false,"contexts":["test"]}`
  and `"enforce_admins":{"enabled":false}` — confirms FR-4's required-check
  registration is live on `main` right now, using exactly the context name
  (`test`) verified against a real PR's check-runs API in design-01.md.
- Re-read `.github/workflows/ci.yml` and `.github/workflows/release.yml` in
  full; both match what plan/design/architecture recorded, confirming no
  drift occurred in this worktree since those steps ran.
- `git status --short` → only `CLAUDE.md` modified; `git diff -- CLAUDE.md`
  reviewed and is the documentation paragraph described above, nothing else.

## How to verify (for the reviewer)

1. `.venv/bin/pytest -q` — expect `472 passed, 1 skipped`.
2. `gh api repos/onpaj/harness_v2/branches/main/protection` — expect `200`
   with `required_status_checks.contexts` containing `"test"`.
3. `git diff -- CLAUDE.md` — the only change in this step; a short,
   documentation-only addition.
4. Open any real PR against `main`: the PR page should show a required
   `test` check that must be green before the merge button is enabled
   (already true today, independent of this dev step's changes).

## Open items carried forward (not blocking, explicitly flagged)

- `strict` (require branch up to date before merge) and `enforce_admins`
  (whether repo admins can bypass the gate) are both `false` today. The
  design recommended `true` for both to most literally match "cannot be
  merged unless all tests pass," but flagged this as requiring explicit
  human confirmation given the shared, hard-to-reverse blast radius of
  `enforce_admins` in particular. This dev step did not escalate either
  setting without that confirmation. If Ondrej wants stricter enforcement,
  the exact command is:
  ```
  gh api repos/onpaj/harness_v2/branches/main/protection \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -f required_status_checks[strict]=true \
    -f 'required_status_checks[contexts][]=test' \
    -F enforce_admins=true \
    -f required_pull_request_reviews='' \
    -f restrictions=''
  ```
  This is documented here for visibility, not executed, per the same
  "executing actions with care" reasoning the design/architecture steps
  already applied.

```json
{"outcome": "done", "summary": "ci.yml already satisfied FR-1/2/3/5 (re-verified, no edit needed); FR-4 turned out already applied — branch protection on main now requires the 'test' check (verified live via gh api, 200 with contexts:['test']). Added a CLAUDE.md note documenting the setting since it's invisible in git diff. Left strict/enforce_admins at their conservative false defaults rather than escalating without explicit human confirmation. Full suite: 472 passed, 1 skipped (opt-in smoke), 0 failed."}
```
