# Merge conflict resolution — PR #56

## Conflicted files

- `src/harness/behaviors/agent.py`
- `tests/test_agent_behavior.py`

## Nature of the conflict

Both sides touched `compose_prompt` in `behaviors/agent.py`:

- **This branch (HEAD)** added a `_body_of` helper and restructured
  `compose_prompt` to insert the GitHub issue body (when present and distinct
  from the request) as a paragraph right under the `Task:` line.
- **`origin/main`** independently strengthened the closing verdict wording —
  from "When you're done, finish with exactly this machine-readable verdict
  (and nothing after it)" to the more emphatic "The harness reads your result
  by machine, not by eye. Your final message MUST end with exactly this
  fenced verdict block and nothing after it — not a prose summary... A
  missing block fails the task."

Both changes are real and independent — one reshapes *what* goes into the
prompt (the body), the other reshapes *how the ending is phrased* (the
verdict). Resolution keeps both: the body-insertion structure from this
branch, with `origin/main`'s stronger verdict phrasing at the end.

`tests/test_agent_behavior.py` conflicted symmetrically: this branch added
four body-related tests (`test_compose_prompt_includes_issue_body_when_present`,
`test_compose_prompt_unchanged_when_body_absent`,
`test_compose_prompt_treats_whitespace_only_body_as_absent`,
`test_compose_prompt_does_not_duplicate_body_equal_to_request`), while
`origin/main` added `test_compose_prompt_demands_the_verdict_block_as_the_last_thing`.
Kept all five. The exact-string regression test
(`test_compose_prompt_unchanged_when_body_absent`) was updated to assert the
new (`origin/main`) verdict wording instead of the old one, since that's now
the actual `compose_prompt` output.

## Verification

- No conflict markers remain (`grep -rn "<<<<<<<\|=======\|>>>>>>>"` over both
  files returns nothing).
- `tests/test_agent_behavior.py`: 13/13 pass.
- Full suite: `857 passed, 1 skipped, 43 errors`. The 43 errors are all in
  `tests/test_api_agents.py` / `tests/test_api_workflows.py`
  (`Board.__init__() got an unexpected keyword argument 'columns'`) — a
  pre-existing mismatch between those test files (written against an older
  flat `Board(columns=...)` shape) and this branch's already-evolved
  `Board(workflows=<BoardTab...>)` shape. Unrelated to the conflicted files
  and already confirmed pre-existing/out-of-scope in this repo's history
  (commit `84fb9c2`, an earlier merge-conflict resolution on this same
  branch lineage, explicitly notes the same 43 failures as pre-existing at
  the merge base).
