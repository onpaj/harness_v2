# Merge conflict resolution — PR #121

## Scope

Merging the base branch into this PR's branch produced exactly one conflicted
file: `src/harness/app.py`. Every other file listed as modified in `git status`
had already been auto-merged cleanly by git (no markers).

## The conflict

Both sides touched the `"failed-tasks"` entry of the `checks` registry built in
`app.build()`:

- **HEAD** (this PR's branch, invariant #25/#39) extended `FailedTasksCheck`'s
  factory to pass `repository=params.get("repository")` through — the
  `--heal-repo`/`HARNESS_HEAL_REPO` value the autoheal process wiring stamps
  into `action.params.repository` in `cli.py`, so a heal task gets a worktree
  the same way any ordinary agent step does.
- **origin/main** wrapped the factory in a `CheckDefinition(spec=FAILED_TASKS_SPEC,
  factory=...)` so the action carries a declarative `CheckSpec` — the process
  admin form renders it as a fully-defined action ("no settings needed")
  instead of falling back to a raw-JSON blob for an "unknown" check.

Both changes are independent and additive — one adds a parameter to the
factory closure, the other wraps that same factory in a declarative
descriptor. `drivers/failed_tasks_check.py` (not itself conflicted; git merged
it cleanly) already carries both the `repository` constructor parameter and
the exported `SPEC` constant, confirming the two features were designed to
coexist.

## Resolution

Combined both: kept the `CheckDefinition(spec=FAILED_TASKS_SPEC, factory=...)`
wrapper from `origin/main`, with the wrapped factory lambda extended to pass
`repository=params.get("repository")` as HEAD's branch did. `FAILED_TASKS_SPEC`
itself is left with no declared `params` — `repository` is not an
operator-facing form field; it's wired programmatically by the autoheal setup
in `cli.py` (`processes/autoheal.json`'s `action.params.repository`), never
entered through the admin UI, so no `ParamSpec` entry was needed.

## Verification

- No conflict markers remain anywhere in the tree (`grep -rn '^<<<<<<<\|^=======\|^>>>>>>>'`
  found only an unrelated `====` comment separator in `api/static/sse.js`).
- Built a fresh venv (`python3.11 -m venv .venv && pip install -e ".[dev]"`)
  and ran the full suite: `1357 passed, 1 skipped` (the skip is the
  opt-in `HARNESS_SMOKE_CLAUDE` test, expected).
- Two locally-set environment variables (`HARNESS_HEAL_REPO`, `GITHUB_TOKEN`)
  caused 8 unrelated pre-existing test failures by changing `cli.py`'s
  auto-wiring behavior (serving the `heal` workflow / registering the
  `label-issue` finisher) — confirmed unrelated to this conflict by rerunning
  with `env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN`, which passed clean.

```json
{"outcome": "done", "summary": "Resolved the single app.py conflict by combining both sides: CheckDefinition(spec=FAILED_TASKS_SPEC, ...) wrapping a factory that still passes repository=params.get(\"repository\") through to FailedTasksCheck. Full test suite passes (1357 passed, 1 skipped) in a fresh venv with no stray env vars."}
```
