# Architecture assessment — Reviewer agent: validate against spec, plan and all ADRs

## Verdict

**Proceed as planned and designed.** `plan-01.md` and `design-01.md` are
architecturally sound, correctly scoped, and — I re-verified every load-bearing
claim in both directly against the repo rather than trusting the prior
artifacts — factually accurate. This document exists to (a) confirm that
alignment explicitly, (b) settle the one open question the plan left
unresolved, and (c) tighten the sequencing/risk picture for `development`.

## Alignment with existing patterns

- **Invariant 14 / ADR-0007 (persona is data, not code).** The change is a pure
  edit to the `_REVIEW_PERSONA` string literal in `src/harness/cli.py`. No new
  branch on the agent's name, no new class, no change to `behaviors/agent.py`.
  Fully conforms.
- **No port/outcome surface change.** `review`'s `allowed_outcomes` stay
  `["done", "request_changes"]`; `AGENT_PERSONAS["review"]`'s tool list
  (`["Read", "Grep", "Glob", "Bash"]`) already reaches `docs/adr/*.md`,
  `docs/superpowers/plans/*.md` and `CLAUDE.md` — nothing to add there. Router,
  dispatcher and workflow files are untouched, so invariants #1–#4 and the
  `test_architecture.py` guards are unaffected by construction.
- **This is exactly the shape of the codebase's most recent precedent.**
  `origin/main` commit `e255cc1` ("Have the reviewer agent merge main into the
  working branch and return `request_changes` when the merge conflicts")
  already extended this same persona the same way — additive prose plus one
  new `request_changes` condition, no code path. This task is that pattern
  applied a second time.

## Confirmed: the worktree is stale, and that fact drives sequencing

I independently verified this, not just re-stated the plan's finding:

```
$ git rev-list --count HEAD..origin/main
45
$ git merge-base HEAD origin/main
0c8027b58155dd01d68e502e4e838d424e8036ea
$ git diff 0c8027b HEAD -- src/harness/cli.py tests/test_cli.py CLAUDE.md
(empty)
```

- This branch is **45 commits behind `origin/main`** (plan said 42; the true
  count as of now is 45 — origin has moved further since the plan was
  written. Not architecturally significant, but `development` should re-check
  the count itself rather than trust either number).
- `docs/adr/` does not exist in this worktree; on `origin/main` it has all 15
  files (`0000`…`0014`) the design document lists.
- **Critically, the diff above is empty**: this task branch has made *zero*
  local edits to `cli.py`, `tests/test_cli.py`, or `CLAUDE.md` since it
  diverged. That means the sync-first step the plan requires is architecturally
  low-risk, not just necessary — a `git merge origin/<base>` will apply
  origin's changes to all three files with **no conflicts to resolve by hand**,
  because only one side (origin) has touched them. I byte-compared
  `origin/main`'s live `_REVIEW_PERSONA` against `design-01.md`'s
  "reconstructed" version and they are identical, confirming the design's
  target text is not a guess.

This changes the plan's Open Question Q1 from "which strategy is safer" to
"which strategy is *architecturally mandated*" — see next section.

## Settling plan's Open Question Q1: merge, not rebase — no longer a judgment call

`plan-01.md` left the merge-vs-rebase choice as a default-with-fallback,
guarded by "if `development` finds this repo has since adopted a different
convention, defer to that." It has. `origin/main`'s `CLAUDE.md` now states this
as **invariant #29**, added since this branch was cut:

> **Conflict resolution is always a merge, never a rebase.**
> `WorkspaceHandle.merge()` produces a two-parent merge commit, deliberately —
> a rebase would rewrite history on a branch that may already be pushed,
> breaking the no-force-push invariant `GitWorkspaceHandle.push()` relies on
> (a plain `push -u`, no `--force`).

This is exactly the situation `development` is in: a pushed (or pushable) task
branch that needs `origin/main`'s history folded in. **Directive:
`development` must sync via `git fetch origin && git merge origin/main`, never
`git rebase`.** Since the three-file diff above is empty, this merge is
expected to be conflict-free and fast; if it isn't (e.g. some other file was
touched by both sides), resolve conservatively and re-verify the target
`_REVIEW_PERSONA` text against `design-01.md`'s "Full resulting persona"
section afterward — don't assume the merge alone gets the wording exactly
right if a real three-way conflict marker ever shows up on that string.

## Implementation guidance

1. **Sync first, edit second — sequencing is a hard prerequisite, not an
   optional cleanup step.** Do not attempt the persona edit against the
   current, stale `_REVIEW_PERSONA`; the string design specifies edits
   *relative to `origin/main`'s version*, which includes the sync-with-base-
   branch block this branch doesn't have yet.
2. **Where the edit lands:** `src/harness/cli.py`, the `_REVIEW_PERSONA`
   constant, post-sync. Use `design-01.md`'s "Full resulting `_REVIEW_PERSONA`"
   section verbatim — I've confirmed it matches `origin/main`'s real content
   plus the two additions, so it can be copied rather than re-derived.
3. **Structure to preserve:** the six-section shape design-01.md lays out
   (role/framing → sync block → Check: list → request_changes list → summary-
   specificity sentence → nitpick carve-out/done case). Sections 2 and 6 are
   byte-for-byte untouched; sections 3–5 get additive insertions only. This
   keeps the diff on `_REVIEW_PERSONA` reviewable as pure additions, which
   matters because the *next* reviewer pass (on this task's own PR) will be
   checking this same file for exactly the kind of unjustified rewrite this
   task is asking the persona to start catching in others — eating your own
   dog food here is not optional, it's the acceptance bar.
4. **Test placement:** extend `tests/test_cli.py` alongside the two existing
   `test_review_persona_syncs_with_base_branch_before_checking_conformance`
   and `test_review_allowed_outcomes_unaffected_by_sync_instructions` (both
   confirmed present on `origin/main`, lines 199–219). Add index-ordered
   substring assertions the same way the existing sync test does
   (`.index(...)` comparisons), per design-01.md's "Verification contract"
   section — that section is precise enough to implement directly, no further
   architectural input needed.
5. **No other files should change.** `git diff` on the final commit, restricted
   to this task's own edits (i.e. excluding the sync-merge commit itself),
   should touch only `src/harness/cli.py` and `tests/test_cli.py`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Development skips the sync step and edits the stale persona in place, producing a `_REVIEW_PERSONA` that both lacks the sync block *and* conflicts with `origin/main`'s own edit to the same region when this PR is later merged. | Sequencing is called out as step 0, before step 1, in both this document and the plan. The empty three-file diff above means the fix (running the merge) is cheap — there's no excuse to skip it. |
| A future reader assumes "merge vs. rebase" is still open, since `plan-01.md` phrased it as a default-with-fallback. | This document resolves it definitively via the newly-landed invariant #29 — treat that as closing the plan's Q1, not as new information to re-debate. |
| Scope creep: since `docs/adr/` will newly exist in the worktree after sync, there may be a temptation to also add mechanical ADR-conformance checks (AST-based, in `tests/test_architecture.py`). | Explicitly out of scope per both plan and design — this task is a prompt-text change; the reviewer *agent* judges ADR conformance by reading, it is not a new static gate. Don't add one. |
| The two new `Check:`/`request_changes` bullets get worded loosely enough that they're not reliably greppable, weakening future test coverage. | Design already pins exact substrings (`"docs/adr"`, `"docs/superpowers/plans"`, `"deviates from the plan"`, `"violates an ADR"`, `"naming the concrete artifact"`) with index-ordering constraints — follow that literally rather than paraphrasing. |

## Prerequisites before implementation begins

1. `git fetch origin && git merge origin/main` on this task branch (expected
   clean, per the empty diff above).
2. Re-read the post-merge `_REVIEW_PERSONA` and `tests/test_cli.py` to confirm
   they match what this document and `design-01.md` describe (a sanity check,
   not a re-derivation).
3. Then proceed with the additive edits exactly as `design-01.md` specifies.

## Open questions

None remaining — plan's Q1 is resolved above (merge, never rebase, now a
documented invariant); Q2 (wording for "relevant ADRs") and Q3 (scope of the
naming requirement) were already resolved with concrete text in
`design-01.md` and don't need further architectural input.

```json
{"outcome": "done", "summary": "Architecture assessment written to architecture-01.md: confirmed plan-01.md and design-01.md are sound and factually accurate (independently re-verified the 45-commit staleness, the empty 3-file diff proving a conflict-free sync, and origin/main's exact _REVIEW_PERSONA/docs/adr/tests/test_cli.py content). Settled the plan's open merge-vs-rebase question definitively using origin/main's newly-added invariant #29 ('conflict resolution is always a merge, never a rebase') — development must run `git merge origin/main`, not rebase. No architectural concerns block implementation; sequencing (sync before edit) is the one hard prerequisite."}
```
