# Architecture assessment: skip confirmation in create-harness-issue when complete

## Verdict

Approve the plan and design as scoped, with one structural correction to how
step 4 is written (below) and one addition the design left implicit (the
ask-path's re-entry point). This is a single-file, single-section rewrite —
no component boundaries, no ports, no tests to design. The "architecture" here
*is* the control flow inside one markdown workflow, so this assessment reads
that control flow the way I'd read a state machine, and fixes the one place
it's underspecified enough to cause drift.

## Alignment with existing patterns

Confirmed by reading the current file
(`.claude/skills/create-harness-issue/SKILL.md`, 85 lines) directly rather than
trusting the plan/design's quoted excerpts:

- The file has exactly one workflow, six numbered steps, no sub-steps, no
  conditionals anywhere today. The plan/design's step 4 → 4a/4b split
  introduces the *first* branch this file has ever had. That's fine — it's the
  minimum structure needed — but it means there's no existing in-repo
  convention for "how a harness skill writes a branch" to copy. I checked; no
  sibling skill file exists under `.claude/skills/` to pattern-match against
  (`create-harness-issue` is the only skill directory in this repo). The
  design's own judgment (numbered sub-steps 4a/4b, a fenced pseudocode block
  for the decision table) is a reasonable, self-consistent choice given there's
  nothing to conform to — keep it.
- `CLAUDE.md`'s 23 invariants govern `harness_v2`'s Python core
  (dispatcher/router/behaviors/ports). None apply to a bundled skill prompt.
  Confirmed by module map — skills aren't listed there. The plan is correct to
  scope this as documentation-only with no test surface.
- The frontmatter `description` says "Triggers on ... Repo-agnostic; works in
  any repo" — unaffected by this change, correctly left alone by both the plan
  and design.

## Proposed architecture

Keep the plan/design's shape (completeness check → route → direct-create *or*
ask → uniform report), with one fix to the branch's *mechanics*:

**Options considered for where the completeness check lives:**

1. *As new step 4, replacing "Preview and confirm" wholesale* (plan/design's
   choice). Step numbering 1–6 stays stable; step 4's prose changes meaning
   from "always pause" to "decide, then route."
2. *As a sub-check folded into step 3 ("Draft")*, with step 4 renamed
   "Create or ask." Rejected: conflates drafting with judging the draft: an
   agent re-reading the file mid-task would have to hold two responsibilities
   in one step, and FR-1's acceptance criterion ("an explicit rule/step") is
   cleaner as its own numbered step.
3. *As a new step inserted before step 4*, pushing everything down
   (5→6, 6→7). Rejected: churns every downstream cross-reference for no
   benefit — step 4 already sits exactly where the check belongs (right after
   drafting, right before creating), so replacing its content in place is
   strictly simpler than renumbering.

**Decision: option 1.** Step 4 keeps its number and position, its title
changes ("Preview and confirm" → "Check completeness, then create or ask"),
its body becomes the router. This matches the design doc.

**The one correction to the design:** the design's flowchart shows 4b looping
"back to step 3/4 with the answer folded in" but the written SKILL.md steps
must not literally say "go to step 3" — that reads as an internal
implementation instruction to a human, not as a workflow an *agent* re-executes
each time it's invoked fresh. Concretely: the ask in 4b is not a loop
construct, it's the *same mechanism the file already uses at step 1* ("If
neither resolves, ask which repo") — the skill pauses, gets an answer, and
naturally continues from wherever it left off, because the whole file is read
and re-applied as a linear script each time. Write 4b as "ask about the
specific missing/ambiguous piece(s), then proceed once answered" — not as an
explicit numbered goto. This keeps step 4 consistent in *style* with step 1's
existing ask-pattern (which the file already has and doesn't call a "loop"),
rather than introducing a new narrative device (backward jumps) this file has
never used.

**Report step (6):** design's fixed five-field report (`repo`, `title`,
`body_summary`, `label`, `url`) is correct and is the load-bearing safety
mechanism that makes removing the pre-create pause acceptable — it's what the
plan's NFR ("must not drift toward skipping confirmation on genuinely
ambiguous requests") is actually counting on procedurally: the user always
sees a full accounting, just after instead of before. Apply it uniformly to
both the 4a and 4b→5 paths, exactly as designed, so step 6 needs no branch of
its own.

## Implementation guidance

Single file: `.claude/skills/create-harness-issue/SKILL.md`. Edit in place,
three regions:

1. **Step 4** (currently lines 59–63). Replace with:
   - One sentence stating the router's job (decide complete vs. not).
   - The three named criteria, each with a one-line true/false test — reuse
     the design's exact wording (`repo_resolved`, `title_concrete`,
     `body_substantive`); these names double as the vocabulary step 6's report
     and the ask-path's targeted question both draw on, so keep them literal
     and re-used rather than re-worded per occurrence.
   - The routing rule: all three true → proceed straight to step 5; any false
     → ask about *that specific* field, then proceed once answered (no
     "go to step 3" phrasing — see correction above).
   - Carry over the existing constraint sentence about not adding harness
     state labels — it's currently attached to step 4 and has nothing to do
     with confirmation; keep it, just relocate it to sit naturally in the new
     step 4 text (it's a "don't do X" note independent of the branch).

2. **Step 6** (currently line 73, "Report the issue URL"). Replace with the
   five-field report. Keep it as *one* step 6 — don't split into "6a direct /
   6b after-ask" since the report content and shape don't differ by path.

3. **Common mistakes** (currently lines 75–84). Replace the last bullet
   ("Creating without confirmation. Outward-facing; always preview first.")
   with two bullets, per the design:
   - A new mistake: creating directly on a request that fails the step-4
     check (i.e., skipping the *ask*, not skipping a preview) — this is the
     bullet that keeps the safety net legible as a "mistake to avoid" rather
     than just living implicitly in step 4's prose.
   - A companion bullet stating that a fully-specified request should *not*
     be held up by asking anyway — otherwise a future reader could "fix" the
     first bullet by reintroducing a blanket ask, silently undoing this whole
     task. Both bullets are needed; either alone re-creates a failure mode.

No other line changes. Steps 1, 2, 3, 5, the contract table, and the
frontmatter are untouched — confirmed against the current file's actual
content above, not just the plan's claim that they're unaffected.

## Data flow (for this step, informally)

```
user request
   → step 1: repo (resolved | unresolved)
   → step 2: label ensured (side effect)
   → step 3: title, body (drafted)
   → step 4: {repo_resolved, title_concrete, body_substantive}
        all true  → step 5 directly
        any false → ask (targeted) → step 5, once answered
   → step 5: gh issue create → url
   → step 6: report {repo, title, body_summary, label, url}
```

This is the same shape as the design doc's diagram; restated here only to
confirm it survives the one correction above (no backward edges — the ask in
4b flows forward into step 5, it doesn't re-enter step 3/4 as a loop).

## Risks and mitigations

- **Risk: "title_concrete" and "body_substantive" are judgment calls, and a
  future agent could rationalize a vague request as complete to avoid the
  friction of asking.** Mitigation: the plan's own examples ("Bug in parser"
  vs. "Add rate limiting to the login endpoint") must survive verbatim into
  the rewritten step 4 as the concrete anchor for the boundary — don't
  abstract them away into pure prose. This is already FR-1's intent; call it
  out here because it's the single biggest way this task could regress into
  "always creates directly" over time.
- **Risk: the ask-path, if worded as a "go back to step 3" instruction, reads
  as pseudocode rather than skill prose and could confuse an agent about
  whether it's re-running the whole file from step 1.** Mitigation: covered
  above — word it as "ask, then continue" matching step 1's existing
  ask-pattern, not as an explicit jump.
- **Risk: the two-bullet replacement in "Common mistakes" could be written as
  one bullet that only warns about the new failure mode (over-eager direct
  create) and drops the reminder not to over-ask.** Mitigation: the plan
  requires both; keep both in the implementation. A single bullet re-biases
  the file toward whichever mistake it mentions.
- **No risk to test coverage, CI, or the release process** — this file isn't
  imported by Python code, isn't covered by `pytest -q`, and a change to it
  doesn't touch `dispatcher`/`router`/`behaviors`, so none of `CLAUDE.md`'s
  guarded invariants are in play. Confirmed by the module map, which doesn't
  list `.claude/skills/` at all.

## Prerequisites before implementation begins

None. The target file exists, was read in full above, and the plan/design's
scope (three regions of one file) is fully bounded. Implementation can proceed
directly to editing `.claude/skills/create-harness-issue/SKILL.md`.
