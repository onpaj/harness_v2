# Review — `open-issue` finisher hard-fails on a missing fenced-json drafts block

## What I checked

- Read `plan-01.md`, `design-01.md`, `architecture-01.md`, `development-01.md`, `verify-01.md`.
- Diffed this branch against `origin/main` for `src/harness/issue_drafts.py`,
  `tests/test_issue_drafts.py`, `tests/test_open_issue_behavior.py`.
- Read the full current `issue_drafts.py` and the relevant part of
  `behaviors/open_issue.py`.
- Ran `.venv/bin/pytest -q` myself (not just trusting the artifact):
  **1763 passed, 1 skipped** — matches the reported verify run.

## Conformance to spec / acceptance criteria

1. `parse_drafts('A clean review. No issues.')` → `[]`, no raise. ✅
   `test_a_report_with_no_fenced_block_is_zero_drafts_not_an_error` covers
   this exact shape (renamed from the old error-expecting test).
2. Present-but-invalid-JSON / non-array block still raises `DraftError`. ✅
   `_decode_last_block`'s "opener(s) found, none decode" path is untouched;
   `test_a_block_that_is_not_an_array_is_an_error` /
   `test_broken_json_is_an_error` are unchanged and pass.
3. Uppercase/mixed-case opener tag and whitespace-tolerant fence both parse. ✅
   Case-insensitivity implemented by scanning a lower-cased copy for offsets
   (length-preserving for ASCII, so no remapping needed) while decoding
   against the original text — new tests
   `test_an_uppercase_opener_tag_is_matched`,
   `test_a_mixed_case_opener_tag_is_matched`,
   `test_a_fence_indented_or_followed_by_blank_lines_still_parses` all pass.
4. `OpenIssueBehavior`-level test for a prose-only artifact completing
   cleanly. ✅ `test_a_report_with_no_drafts_block_files_nothing_and_still_succeeds`
   asserts `DONE`, `"no issues to file"` in summary, and
   `tracker.opened == []`. Traced through `OpenIssueBehavior.run`: the
   `if drafts:` guard (unchanged, not shown as a diff since it needed no
   change) means `slug_for`/`IssueTracker.open_issue` are never reached for
   an empty list.
5. Existing empty-artifact / explicit-`[]` / malformed-block behavior
   unchanged; full suite passes. ✅ Verified independently above.

## Architecture / invariants

- The fix stays entirely inside `issue_drafts.py`, a package-free base
  module per the module map — no new import, no dependency on `harness`.
  `_NO_BLOCK` is a bare `object()` sentinel, correctly distinguished from a
  legitimate decoded `null`.
- `OpenIssueBehavior` (invariant 26) required no code change — confirmed by
  reading it: the empty-drafts branch already existed and simply now
  receives more inputs that reach it.
- `DraftError`'s "opener present but broken" guard (the genuine fault case)
  is untouched — the two-bucket boundary described in `design-01.md` holds:
  no-opener moved into the clean bucket, broken-opener stayed in the error
  bucket.
- No workflow/finisher JSON, port, or driver touched — correctly scoped to a
  pure-function contract fix as the finding requested.

## Correctness

- Case-insensitive offset scan: `lowered.startswith(_OPENER, index)` gives
  offsets into `lowered`, reused directly against `artifact` for
  `raw_decode`. Correct only because lower-casing can't change string length
  for the ASCII opener literal — true here, and the docstring says so
  explicitly, so a future reader isn't left to rediscover the assumption.
- Last-opener-wins and the reversed-scan/failure-message logic are untouched
  by the diff, so the existing nested-fence and multi-block guarantees are
  preserved.
- Test re-fixturing is honest: the old `test_a_malformed_block_raises_issue_error`
  used a *missing*-block artifact under a misleading name; it's now correctly
  split into a clean-success case and a genuinely malformed one (`[{,}]`
  inside a present opener), and the `match="not valid JSON"` assertion lines
  up with the actual `DraftError` message text in `_decode_last_block`.

## Verdict

No functional requirement is unmet, no architectural invariant is violated,
required tests are present, and I found no correctness bug. Nothing to send
back.

```json
{"outcome": "done", "summary": "Implementation matches the design exactly: _NO_BLOCK sentinel in _decode_last_block/parse_drafts turns a missing fence into [] while preserving the malformed/non-array DraftError guard; opener matching is case-insensitive via a length-preserving lower-cased scan. All 5 acceptance criteria are covered by tests, OpenIssueBehavior needed no change as predicted, and I independently reran the full suite (1763 passed, 1 skipped). No issues found."}
```
