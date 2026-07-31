# Development — `open-issue` finisher hard-fails on a missing fenced-json drafts block

## Worktree sync

Per architecture-01.md's refinement 2, this branch had diverged from
`origin/main` (2 artifact-only commits ahead, 100 commits behind). Merged
`origin/main` in (`git merge origin/main`, not a rebase) before touching any
`src/harness` file — the merge was conflict-free as predicted (both local
commits only touched `.artifacts/tsk_9448087b9c6a440f/*`, none of the
upstream commits touch that path). That merge brought in `issue_drafts.py`
and its test suite, which didn't exist on this branch's stale `HEAD`.

## Changes

### `src/harness/issue_drafts.py`

- Added a module-private sentinel `_NO_BLOCK = object()`, distinct from
  `None` (which is itself a legitimate decoded JSON value).
- `_decode_last_block`: when the opener scan finds no `` ```json `` (or
  case-insensitive variant) anywhere in the artifact, it now **returns
  `_NO_BLOCK`** instead of raising `DraftError`. The "opener(s) found, none
  decode" and "opener found, decodes" paths are unchanged.
- The opener scan is now **case-insensitive**: it scans a lower-cased copy of
  the artifact for the (already lower-case) `_OPENER` literal, while decoding
  still happens against the original text at the same offsets — safe because
  lower-casing ASCII letters never changes the string's length, so no
  index-remapping is needed.
- `parse_drafts`: after calling `_decode_last_block`, checks `raw is
  _NO_BLOCK` and returns `[]` in that case, before the array/type checks. A
  block that *is* present but decodes to a non-array, or fails to decode at
  all, still raises `DraftError` exactly as before — that guard is untouched.
- Updated both docstrings (module-level "Two deliberate asymmetries" bullet
  and `_decode_last_block`'s own docstring) per architecture-01.md's
  refinement 1, since they previously stated the old contract as deliberate
  design and would otherwise now contradict the code.

Whitespace tolerance (leading indentation before the opener, trailing
content/blank lines after the closing fence) required no production code
change — the substring scan already matches anywhere in the string and
`raw_decode` stops exactly where the JSON value ends — only regression tests
confirming it.

### `tests/test_issue_drafts.py`

- `test_a_report_with_no_fenced_block_is_an_error` → renamed
  `test_a_report_with_no_fenced_block_is_zero_drafts_not_an_error` and now
  asserts `parse_drafts(...) == []` instead of `pytest.raises(DraftError)`.
- Added `test_an_uppercase_opener_tag_is_matched` and
  `test_a_mixed_case_opener_tag_is_matched` (` ```JSON `, ` ```Json `).
- Added `test_a_fence_indented_or_followed_by_blank_lines_still_parses`.
- Every other existing case (non-array block, broken JSON, missing title,
  wrong-typed body/labels, last-block-wins, a draft body containing its own
  fenced block, trailing prose after the block, empty artifact, explicit
  `[]`) is unchanged.

### `tests/test_open_issue_behavior.py`

- `test_a_malformed_block_raises_issue_error` previously fed a *missing*-block
  artifact under a "malformed" name. Split into two:
  - `test_a_report_with_no_drafts_block_files_nothing_and_still_succeeds`
    (new): a prose-only, opener-free artifact — asserts no exception,
    `result.outcome == DONE`, `tracker.opened == []`, and `"no issues to
    file"` in `result.summary`. This is the acceptance-criterion-4 test at
    the `OpenIssueBehavior` level.
  - `test_a_malformed_block_raises_issue_error` (kept, re-fixtured): now
    feeds a *genuinely* malformed block (invalid JSON inside a present
    `` ```json `` opener) and asserts `IssueError` with `"not valid JSON"`,
    so it still exercises the "opener present, none decode" guard.

## Verification

```
.venv/bin/pytest -q
```

Full suite: **1763 passed, 1 skipped** (the skipped test is the opt-in
`HARNESS_SMOKE_CLAUDE` smoke, unaffected by this change and not run without
the env var). No venv existed in this worktree beforehand; created one with
`/Users/rem/.local/bin/python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
before running.

Targeted re-run for the touched files plus the architecture guard (all pass,
66 tests):

```
.venv/bin/pytest -q tests/test_issue_drafts.py tests/test_open_issue_behavior.py tests/test_architecture.py
```

## Acceptance criteria check

1. `parse_drafts('A clean review. No issues.')` → `[]`, no raise —
   covered by `test_a_report_with_no_fenced_block_is_zero_drafts_not_an_error`.
2. Present-but-invalid-JSON / non-array block still raises `DraftError` —
   `test_a_block_that_is_not_an_array_is_an_error`,
   `test_broken_json_is_an_error` unchanged and passing.
3. Uppercase/mixed-case opener tag and whitespace-tolerant fence both parse —
   three new tests added, all pass.
4. `OpenIssueBehavior`-level test for a prose-only artifact completing
   cleanly (`DONE`, zero `IssueTracker.open_issue` calls) — added and
   passing.
5. Existing empty-artifact / explicit-`[]` behavior unchanged; full suite
   passes.

## Files changed

- `src/harness/issue_drafts.py`
- `tests/test_issue_drafts.py`
- `tests/test_open_issue_behavior.py`
