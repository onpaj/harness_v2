# Review — include the issue body in every agent's prompt

## Verdict

`done`. The implementation conforms to `plan-01.md` / `design-01.md` /
`architecture-01.md` exactly, with no scope drift.

## What I checked

- **Diff scope**: only `src/harness/behaviors/agent.py` (new `_body_of`
  helper + three-part `lines` build in `compose_prompt`) and
  `tests/test_agent_behavior.py` (4 new tests). Matches the plan's declared
  in-scope files; nothing else touched.
- **FR-1 (body included when present)**: `compose_prompt` now computes
  `body = _body_of(task)` and appends it as a blank-line-separated paragraph
  under the `Task:` line when non-empty and different from `request`.
  Covered by `test_compose_prompt_includes_issue_body_when_present`.
- **FR-2 (backward compatible, no body)**: `test_compose_prompt_unchanged_when_body_absent`
  asserts the full output string byte-for-byte, not a substring — this is
  the exact-equality regression guard `architecture-01.md` flagged as a risk
  mitigation, and it's present. The pre-existing
  `test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes` was left
  untouched and still passes.
- **FR-3 (whitespace-only body treated as absent)**: `_body_of` strips and
  returns `""` for blank/whitespace-only values;
  `test_compose_prompt_treats_whitespace_only_body_as_absent` asserts
  equality with the no-body prompt.
- **FR-4 (no duplication when body == request)**: guarded by `body != request`
  in `compose_prompt`; `test_compose_prompt_does_not_duplicate_body_equal_to_request`
  confirms output equals the no-body case.
- **Invariant #14 (persona is data)**: `compose_prompt` still branches only
  on `task.data` shape, never on `spec.name` or agent identity. No new
  branch introduced on outcome/agent.
- **Layering / architecture**: no new port, driver, schema, or signature
  change; `_body_of` sits next to `_request_of` at the same visibility.
  Nothing here touches the boundaries `test_architecture.py` guards.
- **Traced the rendering logic by hand** against both the "body present and
  differing" and "no body / body absent" cases — the blank-line placement in
  `lines` produces exactly the shapes documented in `design-01.md`.

## Verification run

```
$ .venv/bin/pytest -q tests/test_agent_behavior.py
............
12 passed in 0.07s

$ .venv/bin/pytest -q
476 passed, 1 skipped, 1 warning in 16.39s
```

No regressions. The one skip is the opt-in `tests/test_smoke_claude.py`,
unrelated to this change.

## Non-blocking notes

- The step commit's subject line (`Added _body_of helper and wired it into
  compose_prompt...`) doesn't carry a conventional-commit prefix, matching
  the pattern already used by the other step commits on this task branch
  (`plan-01`, `design-01`, `architecture-01`). Since this is consistent with
  how every prior step on this task commits and the repo's `fix:` convention
  is called out for the *landing* commit rather than intermediate step
  commits, this isn't a blocker — flagging only in case landing expects a
  differently-formatted final message.

No changes requested.
