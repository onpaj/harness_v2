# Architecture review — `open-issue` finisher hard-fails on a missing fenced-json drafts block

## Verdict

**Approved, with two refinements the implementation step must fold in.** The
design is sound, correctly scoped, and grounded in the actual code — verified
directly below, not taken on the plan/design's word. No invariant in this
repo's CLAUDE.md is implicated: the change is confined to a pure,
package-free domain module and its tests.

## Fact-check against the real codebase

I pulled `src/harness/issue_drafts.py`, `src/harness/behaviors/open_issue.py`,
`tests/test_issue_drafts.py` and `tests/test_open_issue_behavior.py` straight
from `origin/main` (the current worktree HEAD predates the module's
introduction by 100 commits, so this had to be checked against the remote,
not the checkout) and compared them line-by-line against the design's claims:

- `_decode_last_block` raises `DraftError("the artifact has no fenced json
  block of issue drafts")` exactly when `starts` (the `` ```json `` opener
  scan) is empty — confirmed, matches the design's premise precisely.
- `parse_drafts` already treats a blank/whitespace-only artifact as `[]`, and
  an explicit `` ```json\n[]\n``` `` block also decodes to `[]` — confirmed,
  so folding "no opener at all" into the same bucket is a narrowing of an
  existing asymmetry, not a new concept.
- `OpenIssueBehavior.run`'s `if drafts:` guard already skips `slug_for` and
  every `open_issue` call for an empty list, settling `BehaviorResult(DONE,
  "no issues to file")` — confirmed. The design's claim that **no code change
  is needed in `open_issue.py`** holds; only its caught exception stops firing
  for this one input shape.
- `tests/test_issue_drafts.py::test_a_report_with_no_fenced_block_is_an_error`
  and `tests/test_open_issue_behavior.py::test_a_malformed_block_raises_issue_error`
  both currently exercise a *missing-block* artifact (not a malformed one) and
  assert the old behavior — confirmed, these are exactly the tests the design
  identifies as encoding the bug and needing rewrite.
- The offset-alignment argument for case-insensitive matching (lower-casing
  an ASCII literal never changes its length, so indices computed against a
  lowered copy still address the original string correctly) is correct and
  needs no separate index-mapping.
- I also checked the adjacent twin module, `merge_verdict.py`
  (`parse_verdict`), since it shares the same fenced-block convention. Its own
  docstring is explicit that treating a missing artifact as an error, not a
  benign zero, is *deliberate* there — a merge-review step has no legitimate
  "nothing to report" path the way `heal`'s `skip` path does for
  `issue_drafts`. It is correctly out of scope; the plan and design's silence
  on it is not an oversight.

Nothing here conflicts with a guarded invariant: the fix touches no port, no
driver, no dispatcher/consumer/router code, and no workflow/finisher wiring.
`issue_drafts.py` stays fully package-free (the `_NO_BLOCK` sentinel is a bare
`object()`, no new import), so `test_architecture.py`'s import-boundary
checks are unaffected — there isn't even a specific guard over this module
today, and this change gives it no reason to need one.

## Refinement 1 — the module's own docstrings will become wrong; fix them in the same change

`issue_drafts.py`'s module docstring states, under "Two deliberate
asymmetries": *"A non-empty artifact with no readable block is an error — a
persona that wrote a report but malformed its block is a real fault worth
surfacing."* `_decode_last_block`'s own docstring says: *"Raises `DraftError`
when there is no block, or when the last one that could be a block does not
decode."*

Both of these are direct statements of the exact contract this task reverses.
The design's "what does not change" list doesn't mention them, but leaving
them as-is after the fix lands would make the module actively self-contradict
its own behavior — worse than silence, since a future reader would trust the
docstring over the code. This is a small, in-scope addition to the
implementation step, not a new requirement: update both docstrings (module-
level "asymmetries" bullet, and `_decode_last_block`'s "Raises" line) to
describe the corrected three-way split — blank artifact, no opener at all,
and an explicit `[]` are all zero drafts; only a *present* block that fails to
decode or isn't an array is an error.

## Refinement 2 — the worktree sync is no longer a plain fast-forward; say so precisely

The plan (written before its own commit landed) correctly diagnosed that this
worktree's `HEAD` was a strict ancestor of `origin/main` and called the
needed sync "a clean fast-forward." That is now stale: the plan and design
steps each added a commit (`2f1bbb7`, `ed64a4d`) on top of that same
ancestor, so this branch and `origin/main` have **diverged** from their
common ancestor `6d15952` —

```
git log --oneline HEAD..origin/main | wc -l   # 100
git log --oneline origin/main..HEAD | wc -l   # 2
```

A fast-forward is no longer sufficient — it would either fail or require
discarding the two artifact commits. The implementation step must instead
**merge `origin/main` into this task branch** (`git merge origin/main`), not
rebase: this repo's git conventions (invariant #29, and the general
merge-not-rebase posture for any branch that might already be pushed) apply
here by the same logic even though this isn't the landing behavior's own
merge. Confirmed conflict-free ahead of time: both local commits touch only
`.artifacts/tsk_9448087b9c6a440f/*`, and none of the 100 upstream commits
touch that path — this merge needs no conflict resolution, just the commit.
Do this before creating or editing any `src/harness` file, or the
implementation step will be working from a tree that doesn't contain
`issue_drafts.py` at all.

## Everything else in plan/design stands as written

FR-1 through FR-5, the `_NO_BLOCK` sentinel design, the case-insensitive
opener scan, and the exact test-contract rewrites in
`test_issue_drafts.py`/`test_open_issue_behavior.py` are all correct as
specified and require no further architectural input. Proceed to
implementation with the two refinements above folded in.

```json
{"outcome": "done", "summary": "Design approved against the real origin/main code (verified directly, not taken on trust): OpenIssueBehavior needs no change, the _NO_BLOCK sentinel and case-insensitive scan are sound, no invariant is implicated. Two refinements for implementation: (1) update issue_drafts.py's module and _decode_last_block docstrings, which currently assert the old buggy contract as deliberate design; (2) the worktree sync is no longer a fast-forward (branch has diverged from origin/main by 2 artifact-only commits since the plan was written) — merge origin/main in, confirmed conflict-free, before touching any src file."}
```
