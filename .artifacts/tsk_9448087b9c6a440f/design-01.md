# Design — `open-issue` finisher hard-fails on a missing fenced-json drafts block

No UI surface is involved: this is a pure-function contract fix inside
`src/harness/issue_drafts.py`, consumed by `src/harness/behaviors/open_issue.py`.
The UX/UI section is omitted.

## Ground truth this design targets

Per `plan-01.md`, the ticket was written against an older, regex-based
`issue_drafts.py` (`_FENCED_JSON = r"```json\s*(.*?)```"`) that no longer
exists. The current `origin/main` shape (commits `0e96a30`, `3a6e1db`,
`eb19efc`) scans for a literal `` ```json `` opener and decodes with
`json.JSONDecoder().raw_decode`, specifically so a fenced block *inside* a
draft's own `body` string can't be mistaken for the block's closing fence.
The defect is the same — a non-empty artifact with no opener raises
`DraftError` instead of yielding zero drafts — but the fix lands in this
newer code shape, not the one the ticket quotes. (The worktree sync needed
to get from this task's stale `HEAD` to that shape is implementation's
concern, already flagged in the plan; this design assumes it has happened.)

## Component design

### `issue_drafts.py` — the only component that changes

**Responsibility, unchanged:** turn an artifact's trailing fenced JSON block
into `list[IssueDraft]`, treating "nothing to report" (blank artifact,
explicit `[]`) as zero drafts and "garbled report" (block present but
undecodable, or not an array) as `DraftError`. The fix folds a third
"nothing to report" shape — *no block at all* — into the first bucket
instead of the second, and widens what counts as an opener.

**Boundary that must not move:** the distinction between "found zero
things to file" and "the agent's output is broken" is still exactly two
buckets. Today's code conflates "no opener" into the broken bucket; the fix
moves it into the clean bucket without touching the line between "opener
found, decodes to something wrong" and everything else.

**Internal shape — `_decode_last_block`:**

Today, `_decode_last_block` raises `DraftError` directly when `starts`
(the list of opener positions) is empty, and also when every candidate
opener fails to decode. Both currently look identical to the caller (a
raised `DraftError`), which is exactly why `parse_drafts` can't currently
tell "no block" from "block present but broken" apart to treat them
differently. The fix gives `_decode_last_block` three distinct outcomes
instead of two:

1. no opener anywhere → a sentinel value (not an exception)
2. opener(s) found, none decode → `DraftError` (unchanged)
3. opener(s) found, last one that decodes wins → the decoded value (unchanged)

A module-private sentinel object (`_NO_BLOCK = object()`) is the signal for
case 1 — chosen over `None` because `None` is itself expressible in JSON
(`raw_decode` could legitimately return it if a draft author ever wrote a
bare `null`), so it cannot double as "absent." `parse_drafts` checks
`raw is _NO_BLOCK` and returns `[]`; every other value flows through the
existing array/object/field validation unchanged.

**Case-insensitive opener matching:** the substring scan that builds
`starts` currently matches `` ```json `` byte-for-byte. It switches to
scanning a lower-cased copy of the artifact for the (already lower-case)
`_OPENER` literal, while the *decode* still happens against the original,
un-lowered artifact at the same character offsets. This is safe because
`str.lower()` on the ASCII letters in `` ```json `` never changes the
character count, so offsets computed against the lowered copy line up
exactly with the original — no separate index-mapping is needed.

**Whitespace tolerance (leading indentation before the opener, trailing
content after the closing fence):** already satisfied by the existing
substring scan (matches anywhere in the string, not anchored to line start)
and by `raw_decode` stopping exactly where the JSON value ends. This
requires no new production code — only regression tests confirming the
existing tolerance, per the plan's FR-4.

**What does not change:** `_OPENER`'s literal value, `_skip_space`,
`IssueDraft`, `DraftError`, `marker_for`, and every validation rule inside
`parse_drafts`'s per-draft loop (missing title, non-string body, non-array
labels, non-array top-level value). The module's declared constraint —
imports nothing from `harness` — is untouched; the sentinel is a bare
`object()`, no new dependency.

### `behaviors/open_issue.py` — no code change

`OpenIssueBehavior.run` already treats an empty `drafts` list as the clean
path: `if drafts:` guards the loop that resolves the repository slug and
opens issues, so a `[]` result flows straight to
`BehaviorResult(DONE, "no issues to file")` with `slug_for` never called and
`IssueTracker.open_issue` never invoked. The only thing that changes is
which inputs reach that branch — a prose-only artifact, once fixed
upstream, produces `[]` instead of a raised `DraftError`, so the existing
`try: drafts = parse_drafts(...) except DraftError: raise IssueError(...)`
wrapper simply stops firing for this one input shape. The `try/except`
itself stays, since case 2 above (a present-but-broken block) still needs
to surface as `IssueError` → task failure — that guard is the ticket's
explicit non-negotiable (FR-2).

## Interfaces (signatures unchanged, only behavior narrows)

```python
# issue_drafts.py
def parse_drafts(artifact: str) -> list[IssueDraft]: ...
    # blank artifact            -> []                 (unchanged)
    # explicit `[]` block       -> []                 (unchanged)
    # no ```json opener at all  -> []                 (CHANGED: was DraftError)
    # opener present, bad JSON  -> raises DraftError   (unchanged)
    # opener present, non-array -> raises DraftError   (unchanged)
    # opener present, bad draft -> raises DraftError   (unchanged, per-field)

def _decode_last_block(artifact: str) -> object:
    # returns _NO_BLOCK (new private sentinel) when no opener is found,
    # instead of raising — parse_drafts is the only reader of this sentinel.
```

No change to `OpenIssueBehavior.__init__`, `BehaviorResult`, `IssueTracker`,
`IssueRef`, `DraftError`'s type (still a `ValueError`), or any workflow/
finisher JSON. `marker_for` is untouched.

## Data schemas

None apply — no persisted, wire, or event schema is touched. The only
"schema" in play is the artifact's informal contract (markdown ending in a
`` ```json `` array), and the change is that the contract's *absence* is
now read as "nothing to report" rather than "malformed."

## Test-contract changes (what the suite must assert going forward)

These are the observable contracts the implementation step's tests must
encode — not a task breakdown, just the behavioral spec each test name
below is standing in for:

- `tests/test_issue_drafts.py`: the existing
  `test_a_report_with_no_fenced_block_is_an_error` encodes the old, now-wrong
  contract (`pytest.raises(DraftError, match="no fenced json block")` for a
  prose-only artifact) and must instead assert `parse_drafts(...) == []` for
  that same input. New cases cover an uppercase/mixed-case opener
  (`` ```JSON ``) decoding the same as `` ```json ``, and a fence that is
  indented or followed by trailing blank lines still parsing. Every other
  existing case (non-array block, broken JSON, missing title, wrong-typed
  body/labels, last-block-wins, a draft body containing its own fenced
  block, trailing prose after the block) keeps its current assertion
  unchanged.
- `tests/test_open_issue_behavior.py`: `test_a_malformed_block_raises_issue_error`
  currently feeds a *missing*-block artifact under a "malformed" name and
  must split into two: a new case for a missing-block artifact asserting no
  exception, `BehaviorResult.outcome == DONE`, `tracker.opened == []`, and a
  summary containing "no issues to file" (mirrors the existing
  `test_an_empty_array_files_nothing_and_still_succeeds` shape but with a
  prose-only, opener-free `text`); and, if a same-named regression guard for
  "malformed block still raises" is kept, it must be re-fixtured with a
  genuinely malformed block (e.g. invalid JSON inside a present `` ```json ``
  opener) so it still exercises case 2 of `_decode_last_block`.
- Full suite (`.venv/bin/pytest -q`) must pass with no other test's
  assertions touched — the change is scoped to the "no opener" branch and
  the opener match's case-sensitivity, nothing else in either module.
