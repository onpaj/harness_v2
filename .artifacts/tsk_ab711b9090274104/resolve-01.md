# Merge conflict resolution — PR #121

## Scope

`git status` at the start of this step showed one file left `UU` (both modified,
unresolved): `tests/test_cli.py`. Every other changed file (`src/harness/api/app.py`,
`src/harness/api/routes.py`, `src/harness/cli.py`, `src/harness/app.py`,
`src/harness/drivers/fs_processes.py`, `src/harness/ports/process_admin.py`, the
`process_form` template/static changes, and the other test files) was already
staged as a clean, non-conflicting merge result — no markers to resolve there.

## The conflict

`tests/test_cli.py` had two conflict hunks (lines ~275 and ~304), both in the same
shape:

```
<<<<<<< HEAD
async def fake_serve(harness, port, poll_interval, source_interval=30.0, pr_poll_interval=0.0, reconcile_interval=300.0):
=======
async def fake_serve(harness, port, poll_interval, source_interval=30.0, pr_poll_interval=0.0, reconcile_interval=300.0, registry=None):
>>>>>>> origin/main
```

`HEAD`'s `fake_serve` stub (in `test_run_heal_repo_warns_when_the_slug_is_not_registered`
and `test_run_heal_repo_registered_emits_no_repo_warning`) was missing the
`registry=None` parameter that `origin/main` had added to the real
`harness.cli.serve()` signature (confirmed at `src/harness/cli.py:1780-1788`).
Every other `fake_serve` stub already in this file (e.g. at line 250 and line 329)
already carries `registry=None` — so `HEAD`'s two stubs were simply out of date
relative to the rest of the file, not an intentional divergence.

## Resolution

Took `origin/main`'s signature for both hunks — `registry=None` added to both
`fake_serve` stubs — matching every other fake-serve stub in the file and the real
`serve()` signature. No test-body logic changed, just the stub's parameter list so
it accepts the `registry` keyword `harness.cli._run` now passes through.

## Verification

- `grep -n "^<<<<<<<\|^=======\|^>>>>>>>" tests/test_cli.py` → no matches, all
  markers removed.
- Full test suite: `.venv/bin/pytest -q` → **1385 passed, 1 skipped** (in a clean
  shell, i.e. with the ambient `HARNESS_HEAL_REPO` / `GITHUB_TOKEN` env vars from
  this machine's shell unset — those leaked into the sandbox environment and are
  unrelated to the merge; several `test_cli.py` tests assert *no* heal-repo warning
  and *no* `label-issue` finisher when neither flag/env is passed, and fail if the
  ambient values are present. With them unset, the suite is fully green).
- `git add tests/test_cli.py` staged the resolution; `git status` now reports "All
  conflicts fixed but you are still merging" with nothing left unmerged.
