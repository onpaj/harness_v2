# Development — include the issue body in every agent's prompt

## Summary

Implemented exactly as specified in `plan-01.md` / `design-01.md` /
`architecture-01.md`: `compose_prompt` in `src/harness/behaviors/agent.py` now
reads `task.data["body"]` (via a new sibling helper `_body_of`) in addition to
the existing `_request_of` precedence chain, and appends it as a blank-line
separated paragraph under the `Task:` line — but only when non-empty and
different from the request text, so the no-body case stays byte-identical to
today's output.

## Files changed

- **`src/harness/behaviors/agent.py`**
  - `compose_prompt`: computes `body = _body_of(task)` alongside the existing
    `request = _request_of(task)`; the `lines` list is now built in three
    parts — the header/`Task:` line, an optional body paragraph inserted only
    when `body and body != request`, then the unchanged
    artifacts/verdict-block tail.
  - New helper `_body_of(task: Task) -> str`, placed directly below
    `_request_of`, mirroring its null-safety: reads `task.data.get("body")`,
    returns `""` unless the value is a non-blank string (after `.strip()`).
  - No signature change to `compose_prompt`, no change to `ClaudeCliBehavior`,
    no new import.

- **`tests/test_agent_behavior.py`**
  - Added `from dataclasses import replace` (needed because `Task` is frozen —
    tests build a body-bearing variant via `replace(make_task(), data={...})`
    rather than mutating `.data`).
  - Added helper `_prompt_for(task, *, spec=None)` to reduce repetition across
    the new cases (mirrors the existing `compose_prompt(...)` call shape).
  - New tests, one per FR:
    - `test_compose_prompt_includes_issue_body_when_present` (FR-1): body text
      shows up in the prompt alongside the title.
    - `test_compose_prompt_unchanged_when_body_absent` (FR-2): asserts the
      **exact** prompt string for the no-body case, not just a substring — the
      exact-equality regression guard called out as a risk mitigation in
      `architecture-01.md`.
    - `test_compose_prompt_treats_whitespace_only_body_as_absent` (FR-3):
      whitespace-only body produces output identical to the no-body case.
    - `test_compose_prompt_does_not_duplicate_body_equal_to_request` (FR-4):
      `body == request` produces output identical to the no-body case (no
      duplicate paragraph).
  - The pre-existing
    `test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes` test
    (built from body-less `make_task()`) was left untouched and still passes,
    confirming the fix is backward compatible for manually submitted tasks.

## Verification

```
$ .venv/bin/pytest -q tests/test_agent_behavior.py
............
12 passed in 0.65s

$ .venv/bin/pytest -q
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 45%]
........................................................................ [ 60%]
........................................................................ [ 75%]
...........................................................s............ [ 90%]
.............................................                            [100%]
476 passed, 1 skipped, 1 warning in 20.13s
```

The single skip is the opt-in `tests/test_smoke_claude.py` (requires
`HARNESS_SMOKE_CLAUDE=1`), unaffected by this change. No test in
`tests/test_architecture.py` was affected — no port, driver, or import
boundary was touched.

## How to verify

1. `.venv/bin/pytest -q tests/test_agent_behavior.py` — the 4 new tests plus
   the untouched pre-existing one all pass.
2. `.venv/bin/pytest -q` — full suite green, no regressions elsewhere.
3. Manually: construct a `Task` with `data={"title": "...", "body": "..."}` (the
   shape `GithubTaskSource.poll()` produces) and call `compose_prompt(...)` —
   the returned string contains both the title (on the `Task:` line) and the
   full body text as a separate paragraph directly below it.

## Scope notes

Confined to the two files named in the plan/design — no schema, port, or
signature changes; `GithubTaskSource` was not touched (it already produced the
correct data). This is a bug-fix-only diff, appropriate for a `fix:` commit
per the repo's semantic-release convention.
