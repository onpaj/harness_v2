# Plan — `open-issue` finisher hard-fails on a missing fenced-json drafts block

## Summary

`parse_drafts` (`src/harness/issue_drafts.py`) raises `DraftError` whenever a
non-empty artifact has no fenced ` ```json ` block at all, and
`OpenIssueBehavior.run` (`src/harness/behaviors/open_issue.py`) lets that
propagate uncaught, so `Consumer.tick()`'s blanket handler routes the whole
task to `failed/`. This fires deterministically for any clean review whose
agent concludes "nothing to file" in prose instead of an explicit empty
`[]` array — observed on task tsk_64e2f752f3304aa2, step `arch-review`. The
fix makes "no block at all" a clean zero-drafts result, symmetric with how
an empty artifact and an explicit `[]` are already treated, while keeping
every genuine-malformed-output guard (present-but-invalid-JSON, present-but-
not-an-array) exactly as strict as it is today.

## Context

Two parsers read the same convention — the agent's final text ends in a
fenced ` ```json ` block — and disagree on what "no block" means:

- `drivers/claude_cli.py::_extract_verdict` (the step's verdict): no fenced
  block → falls back to reading the whole text as JSON; unreadable → `None`,
  an explicitly *recoverable* miss the caller decides on.
- `issue_drafts.py::parse_drafts` (the step's issue drafts): no fenced block
  → immediately raises `DraftError`, a hard task failure.

`parse_drafts` already treats a blank artifact and an explicit `[]` as "zero
drafts, not an error" (per its own module docstring: *"An empty artifact is
zero drafts, not an error... A non-empty artifact with no readable block is
an error"*) — but that stated design is itself the bug. A persona that wrote
a full prose report and simply didn't append a machine-readable block (the
most likely shape for "I reviewed this, found nothing") gets punished exactly
like one that emitted garbage. Any workflow bound to the `open-issue`
finisher — the shipped `heal`/`file-issue` step, and any future review-style
step like `arch-review` — hits this every time its agent has nothing to
report cleanly in prose.

**Codebase note (important for the next step):** the bug report was written
against an older shape of this code (a shared `_FENCED_JSON` regex, also
present in `claude_cli.py`). That shape no longer exists on `origin/main`.
`issue_drafts.py` was rewritten (commits `0e96a30`, `3a6e1db`, `eb19efc`) to
scan for a literal `` ```json `` opener and decode with
`json.JSONDecoder().raw_decode`, specifically so a fenced code block *inside*
a draft's own `body` string can't be mistaken for the block's closing fence.
The underlying defect described in the ticket is still present and still
raises the exact quoted error message — only the code shape changed. This
plan targets the **current** `issue_drafts.py`/`open_issue.py`, not the
regex-based version described in the ticket's "Proposed change" section.

**Worktree staleness — resolve before implementing.** This task's worktree
(branch `harness/tsk_9448087b9c6a440f`) is checked out at commit `6d15952`,
which is **100 commits behind `origin/main`** and does not contain
`src/harness/issue_drafts.py` at all (it predates that module's introduction).
`git log HEAD..origin/main` is 100 commits and `git log origin/main..HEAD` is
0 — i.e. `HEAD` is a strict ancestor of `origin/main`, so bringing the branch
up to date is a **clean fast-forward, not a merge or rebase**. The
implementation step must do this first, or it will be editing/creating files
that don't match either the ticket or this plan. See Open Questions.

## Functional requirements

**FR-1 — a missing fenced block is zero drafts, not an error.**
`parse_drafts(artifact)` returns `[]` when `artifact` is non-empty but
contains no `` ```json `` opener anywhere, instead of raising `DraftError`.
- AC: `parse_drafts("A clean review. No issues.")` returns `[]` and raises
  nothing.
- AC: `parse_drafts("# Report\n\nAll good, nothing to flag.\n")` returns `[]`.

**FR-2 — genuine malformed-output guards are unchanged.**
When a `` ```json `` block *is* found but decodes to invalid JSON, or decodes
to a non-array value, `parse_drafts` still raises `DraftError` with its
existing message shape.
- AC: `parse_drafts('```json\n{"title": "not an array"}\n```')` still raises
  `DraftError` matching `"must be a JSON array"`.
- AC: `parse_drafts("```json\n[{,}]\n```")` still raises `DraftError` matching
  `"is not valid JSON"`.
- AC: every other existing case in `tests/test_issue_drafts.py` (broken JSON,
  missing title, wrong-typed body/labels, the-last-block-wins, a draft body
  that itself contains a fenced block, trailing prose after the block) keeps
  passing unmodified.

**FR-3 — the fence's `json` tag is matched case-insensitively.**
A block opened with `` ```JSON ``, `` ```Json ``, etc. is found and parsed
the same as `` ```json ``.
- AC: `parse_drafts('```JSON\n[{"title": "x"}]\n```')` returns one draft
  titled `"x"`.
- AC: mixed case (`` ```Json ``) behaves the same.

**FR-4 — whitespace around the fence doesn't defeat matching.**
A block preceded by leading whitespace/indentation, or followed by trailing
whitespace before/after the closing fence, is still found.
- AC: a block whose opener is indented, or which has trailing blank lines
  after the closing ` ``` `, still parses. (Largely already true today via
  the substring scan + `_skip_space`/`raw_decode`'s natural tolerance of
  trailing content — this AC is a regression guard, not expected to need much
  new code, see Rough plan.)

**FR-5 — `OpenIssueBehavior` completes cleanly with zero drafts to file.**
A step bound to the `open-issue` finisher whose agent's artifact is
prose-only (no fenced block) settles `DONE` with nothing filed, instead of
raising `IssueError` and failing the task.
- AC: a new/updated test at the `OpenIssueBehavior` level — using the
  existing `StubInner`/`MemoryIssueTracker`/`MemoryArtifactStore` fixtures in
  `tests/test_open_issue_behavior.py` — asserts: no exception raised, the
  returned `BehaviorResult.outcome == DONE`, `tracker.opened == []` (i.e.
  `IssueTracker.open_issue` called zero times), and the summary reads
  something like "no issues to file".

## Non-functional requirements

- **No behavior change for well-formed input.** Every existing passing test
  in `tests/test_issue_drafts.py` and `tests/test_open_issue_behavior.py`
  that doesn't directly encode the old "missing block is an error" behavior
  must keep passing unmodified.
- **No new dependency, no I/O.** This is a pure-function change in a module
  that already imports nothing from the `harness` package (per its own
  docstring) — that constraint must not be broken.
- **Security/robustness:** don't relax the genuine malformed-JSON or
  non-array guards; the fix narrows exactly one failure mode (no block found)
  and touches nothing else in the decode path.

## Data model

No persistent data model changes. Touches only:
- `IssueDraft` (unchanged shape: `title`, `body`, `labels`).
- `DraftError` (still raised for genuinely malformed output; no longer raised
  for "no block found").
- No change to `IssueTracker`, `IssueRef`, `BehaviorResult`, or any workflow/
  finisher config shape.

## Interfaces

- `issue_drafts.parse_drafts(artifact: str) -> list[IssueDraft]` — signature
  and return type unchanged; only the "no block found" branch's behavior
  changes (raise → return `[]`).
- `issue_drafts._decode_last_block` (private helper) needs a way to signal
  "no opener found" distinctly from "opener(s) found but none decode" so
  `parse_drafts` can tell the two apart. Suggested shape (implementation's
  call, not prescriptive): a module-private sentinel object returned instead
  of raising when `starts` is empty; `parse_drafts` checks for the sentinel
  and returns `[]`, otherwise proceeds exactly as today. Keep the "opener(s)
  found but all fail to decode" path raising `DraftError` unchanged.
- `OpenIssueBehavior.run` — no interface change; it will simply stop
  receiving a `DraftError` for this one input shape, so its existing
  `try/except DraftError as error: raise IssueError(...)` wrapping becomes
  unreachable for this case (still needed for FR-2's genuine-malformed path).

## Dependencies and scope

**In scope:**
- `src/harness/issue_drafts.py` — the `_decode_last_block`/`parse_drafts`
  fix (FR-1, FR-2) and case-insensitive opener matching (FR-3).
- `tests/test_issue_drafts.py` — `test_a_report_with_no_fenced_block_is_an_error`
  currently asserts the *old* (buggy) behavior and must be rewritten to
  assert `parse_drafts(...) == []` for that same input (renaming it, e.g. to
  `test_a_report_with_no_fenced_block_is_zero_drafts`); add cases for FR-3/FR-4.
- `tests/test_open_issue_behavior.py` — `test_a_malformed_block_raises_issue_error`
  currently feeds `text="# A report with no block\n"` (a *missing* block, not
  malformed JSON) and asserts `IssueError` — this test's premise no longer
  holds and must be split/rewritten: (a) a new test for FR-5 (missing block →
  `DONE`, nothing filed, per the AC above), and (b) if a "malformed block
  raises" regression guard is still wanted, it must use a genuinely malformed
  fixture (e.g. invalid JSON inside a present block) rather than a missing one.

**Out of scope / explicitly not touched:**
- `drivers/claude_cli.py::_extract_verdict` and its `_FENCED_JSON` regex —
  unrelated code path, already has its own tolerant fallback, not shared with
  `issue_drafts.py` in the current codebase.
- `OpenIssueBehavior`'s wiring, the finisher registry, workflow JSON, or any
  persona/agent prompt content.
- Any change to how drafts with a present-but-malformed block are handled
  (FR-2 explicitly preserves today's strictness there).
- `IssueTracker`/`GithubIssueTracker`/label handling — untouched.

**Dependencies:**
- The worktree must be brought to (or past) `origin/main`'s current tip
  before implementation — see the Context note and Open Questions. Since
  `HEAD` is a strict ancestor of `origin/main`, this is a plain fast-forward.

## Rough plan

1. **Sync the worktree.** Fast-forward `harness/tsk_9448087b9c6a440f` to
   current `origin/main` (or otherwise ensure the working tree has
   `src/harness/issue_drafts.py` in the shape described above) before editing
   anything.
2. **Fix `_decode_last_block`/`parse_drafts` (FR-1, FR-2).** Introduce a
   distinct "no opener found" signal (e.g. a private sentinel) instead of
   raising `DraftError` when `starts` is empty; have `parse_drafts` return
   `[]` on that signal. Leave the "opener(s) found, none decode" path raising
   `DraftError` exactly as today.
3. **Case-insensitive opener matching (FR-3).** Change the substring scan
   that builds `starts` to match `` ```json `` case-insensitively (compare
   against a lower-cased artifact, or an equivalent case-insensitive check),
   without changing how the JSON body itself is decoded.
4. **Verify FR-4 needs no further code** by adding regression tests for an
   indented/whitespace-padded fence; only add code if a test actually fails.
5. **Update `tests/test_issue_drafts.py`:** rewrite the "no fenced block"
   test to assert `[] `instead of raising; add FR-3/FR-4 cases.
6. **Update `tests/test_open_issue_behavior.py`:** replace/split
   `test_a_malformed_block_raises_issue_error` per the Dependencies section;
   add the FR-5 test (`OpenIssueBehavior.run` on a prose-only artifact →
   `DONE`, `tracker.opened == []`).
7. **Run the full suite** (`.venv/bin/pytest -q`) and confirm no regressions,
   per the acceptance criteria's requirement #5.

## Open questions

- **Worktree staleness (flagged above, defaulting to "sync first"):** this
  plan assumes the implementation step fast-forwards the branch to
  `origin/main` before touching any files. If for some reason that isn't
  possible or desired, the plan and the ticket's described code shape both
  become inapplicable and the task needs to be re-scoped against whatever
  commit is actually being worked from.
- **Regression-guard for "malformed block still raises":** the ticket's AC2
  wants existing malformed-JSON/non-array coverage kept; the one existing
  test that exercised a *missing*-block case under the "malformed" name
  (`test_a_malformed_block_raises_issue_error`) needs a real malformed
  fixture if the author wants to keep a same-named regression test at the
  `OpenIssueBehavior` level — defaulting to yes (add it) since FR-2 explicitly
  requires the guard to survive, and losing behavior-level coverage of it
  would be a regression.
- **Case-insensitivity scope:** the ticket only asks for the `json` tag to be
  case-insensitive, not the triple backticks (which have no case) or any
  other fence variant (e.g. `~~~json`). Defaulting to exactly what's asked —
  no support for non-backtick fences.
