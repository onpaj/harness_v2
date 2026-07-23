# Development — reviewer syncs with base branch, `request_changes` on conflict

## Summary

Implemented exactly the scope pinned by plan-01.md / design-01.md /
architecture-01.md: a persona-text change to `_REVIEW_PERSONA` in
`src/harness/cli.py`, plus tests at both layers the design specified (a fast
unit test pinning the wording/ordering, and a real-git smoke test that
exercises an actual conflicting merge). No port, driver, model field, or
workflow/routing change — consistent with invariants 2 and 14.

## Files changed

### `src/harness/cli.py`

Inserted a new first paragraph into `_REVIEW_PERSONA`, before the existing
`"Check:"` bullet list, implementing FR-1–FR-3 from plan-01.md verbatim per
design-01.md's wording:

1. `git fetch origin`.
2. Resolve the base branch via `git symbolic-ref refs/remotes/origin/HEAD`,
   falling back to `main`.
3. `git merge origin/<base>` — explicit "DO NOT create or switch branches...
   DO NOT force-push or force-resolve" guardrail, mirroring
   `_DEVELOPMENT_PERSONA`'s existing phrasing style.
4. On conflict: capture `git diff --name-only --diff-filter=U`, run
   `git merge --abort`, skip the rest of the review, and finish with
   `request_changes` whose summary/artifact name `origin/<base>` and the
   conflicting paths.
5. On a clean merge (fast-forward / merge commit / already up to date): fall
   through to the existing checklist unchanged — the sync step alone must
   never change the verdict.

Nothing else in the file changed: `AGENT_PERSONAS["review"]`'s tool list
(`Bash` already present), `_allowed_outcomes_for`, `DEFAULT_DEFINITION`'s
`review --request_changes--> development` edge are all untouched.

### `tests/test_cli.py`

Two new unit tests (fast, no git, no I/O):

- `test_review_persona_syncs_with_base_branch_before_checking_conformance` —
  pins FR-1's ordering (`"git fetch origin"` appears before `"Check:"`),
  that `"git merge origin"` / `"git merge --abort"` are present, that the
  conflict paragraph mentions `request_changes`, and that the persona states
  the no-branch-switch guardrail.
- `test_review_allowed_outcomes_unaffected_by_sync_instructions` — rebuilds
  the default workflow from `DEFAULT_DEFINITION` and asserts
  `_allowed_outcomes_for(workflow, "review") == ["done", "request_changes"]`,
  a regression guard confirming the persona-text change didn't touch routing.

### `tests/test_smoke_git.py`

Per design-01.md's "conflict-capable local runner" section:

- `_make_repo` now pins the initial branch name explicitly
  (`git init -q -b main`) instead of relying on the host's
  `init.defaultBranch`, since the new scenario resolves the base branch the
  same way the reviewer persona does (fallback `main`) and needs that to be
  deterministic across machines/CI. Harmless to the existing happy-path test,
  which never asserted a branch name.
- `EchoRunner` gained two optional constructor parameters:
  - `conflict_step`: names a step (here, `"review"`) whose `run()` performs
    the persona's real git contract via `subprocess` in `cwd` — fetch,
    resolve base, merge, and on conflict capture/abort/report — instead of
    the canned-verdict shortcut every other step (and every other `EchoRunner`
    instance) still uses.
  - `touch_file`: when set, the `development` step additionally edits that
    file in the worktree (here, `README.md`), so the task branch carries a
    real local change that can collide with a divergent `origin`.
  Both default to `None`/off, so the existing happy-path test
  (`test_task_lands_as_pull_request_on_real_git`) is unmodified in behavior.
- New test `test_review_syncs_with_base_and_requests_changes_on_real_conflict`:
  1. Builds the repo/remote fixture as usual, but first pushes the initial
     commit to `origin` (needed so a later divergent commit shares a common
     ancestor — otherwise git refuses the merge as "unrelated histories"
     rather than reporting a real conflict; discovered by hand-reproducing
     the scenario with plain `git` before wiring it into the test).
  2. Clones `origin`, edits `README.md`, commits and pushes — diverging
     `origin/main` from `repo`'s own `main` *before* the harness starts. Since
     the worktree branches from `repo` (not `origin`), no timing coordination
     with the running harness is needed: the divergence only becomes visible
     once `review` itself fetches.
  3. Runs the harness with `EchoRunner(conflict_step="review",
     touch_file="README.md")`; polls until `review-01.md` appears, then stops
     the loop (deliberately not waiting for the task to reach `done` —
     `EchoRunner`'s canned `development` step doesn't resolve the conflict, so
     a second `review` pass would hit the identical conflict again; the test
     only needs to observe one conflicting pass, matching FR-3's acceptance
     criteria, not drive the loop to completion).
  4. Asserts: the artifact mentions the conflict, `origin/main`, and
     `README.md`; no `MERGE_HEAD` is left behind and `git status --porcelain`
     is empty (the abort ran and the working tree is clean); and the task's
     history records the `review` step's outcome as `request_changes` and a
     dispatcher entry routing `review -> development` (invariant 3: the
     consumer's entry carries the outcome, the dispatcher's separate entry
     carries where it routed to — verified directly against the real history
     shape rather than assumed).

## How to verify

```sh
.venv/bin/pytest -q                                    # full suite: 475 passed, 1 skipped
.venv/bin/pytest -q tests/test_cli.py -k review          # the two new unit tests
.venv/bin/pytest -q tests/test_smoke_git.py -v           # both real-git smoke tests, including the new conflict one
.venv/bin/pytest -q tests/test_architecture.py           # unaffected, still 14 passed — confirms no port/layer touched
```

Manual sanity check of the persona text:

```sh
.venv/bin/python -c "from harness.cli import _REVIEW_PERSONA; print(_REVIEW_PERSONA)"
```

## Scope check against architecture-01.md's guardrails

- No new port, driver, model field, or branch in `ClaudeCliBehavior`/
  `compose_prompt` — verified by grep: only `src/harness/cli.py` (the
  persona string) and the two test files changed.
- `_DEVELOPMENT_PERSONA`, `DEFAULT_DEFINITION`, `router.py`, `dispatcher.py`
  are all untouched.
- `tests/test_architecture.py` passes unmodified (14/14), confirming
  invariants 1, 2, 5, 11, 17, 20, 23 (the ones it guards) all still hold.
