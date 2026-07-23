# Plan — Reviewer agent: validate implementation against spec, plan and all ADRs

## Summary

Extend `_REVIEW_PERSONA` in `src/harness/cli.py` so the `review` step's
checklist explicitly covers **plan conformance** and **ADR / `CLAUDE.md`
invariant conformance**, in addition to the spec/architecture checks it already
performs, and returns `request_changes` (with a specific artifact reference)
when either is violated. This is a persona/data-only change — no new outcome,
no new class, no branch on the agent's name.

## Context

The reviewer is the harness's last gate before landing. Its persona already
checks conformance to the spec and architecture and looks for correctness
bugs, but says nothing about the **plan** (`docs/superpowers/plans/…`) or the
**ADRs** (`docs/adr/…`), which encode this codebase's load-bearing invariants
(`CLAUDE.md` → "Invariants — do not break", each pointing at an ADR). An ADR
violation — `dispatcher.py`/`consumer.py` importing a driver, the router doing
I/O, a step's decision leaking into the consumer — is exactly the kind of
regression that should bounce back to `development`, not quietly reach
landing. `tests/test_architecture.py` mechanically guards a handful of these,
but not all of them, and the reviewer is the natural place to catch the rest.

**Branch-staleness finding (important, not obvious from the task text alone):**
this task's worktree (`harness/tsk_7f898f0a219f494d`) was branched **42 commits
behind `origin/main`**. Two things the task description assumes already exist
in `_REVIEW_PERSONA` are in fact *missing from this branch* and only present on
`origin/main`:

1. The **sync-with-base-branch block** (`git fetch origin` → resolve base →
   `git merge origin/<base>` → on conflict, capture conflicting paths, `git
   merge --abort`, return `request_changes`) — added on `main` in commit
   `23ac7e7`, verified in `tests/test_cli.py::test_review_persona_syncs_with_base_branch_before_checking_conformance`.
2. `docs/adr/` itself — 15 ADR files (`0000`…`0014`) plus `CLAUDE.md` growing to
   38 numbered invariants — added on `main` across several later commits (the
   ADR set, the healer workflow, generic triggers, etc.).

If `development` edits the *current, stale* `_REVIEW_PERSONA` in place, it
would (a) silently drop the sync/merge-conflict behavior the task explicitly
says to preserve — it isn't there to preserve — and (b) land a PR whose diff on
`src/harness/cli.py` conflicts with `main`'s own edit to the very same string,
risking that block being clobbered on merge; and the reviewer would have no
`docs/adr/` to read against in this branch until it does exist. **The branch
must sync with `origin/main` before the persona edit is made**, so the new
instructions are appended to the real, current persona text and `docs/adr/`
exists in the worktree. This is the reviewer's own sync-with-base-branch
behavior, just needed one step earlier than usual because it doesn't exist yet
on this branch to run itself. See Open Questions for the fallback if this
can't be done cleanly.

## Functional requirements

**FR-1 — Plan conformance check.**
The `Check:` list in `_REVIEW_PERSONA` gains a bullet requiring the reviewer to
verify the implementation follows the agreed plan (`docs/superpowers/plans/…`
for this task's own artifacts, or the task's own `plan-*.md` artifact) — it
doesn't silently skip or reinterpret planned steps.
*Acceptance:* the persona string contains an instruction to check the
implementation against the plan, positioned in the `Check:` list alongside
spec/architecture/completeness/correctness.

**FR-2 — ADR / invariant conformance check.**
The `Check:` list gains a bullet requiring the reviewer to read the ADRs in
`docs/adr/` relevant to the files touched by the change (and the "Invariants —
do not break" list in `CLAUDE.md`, which points at them) and verify none is
violated.
*Acceptance:* the persona string instructs reading `docs/adr/` (relevant ones,
not necessarily all 15) and `CLAUDE.md` invariants, and checking the diff
against them.

**FR-3 — `request_changes` criteria extended.**
The `request_changes` bullet list gains two entries:
- the implementation deviates from the plan without justification, or
- the implementation violates an ADR / documented invariant.
*Acceptance:* both conditions appear in the `request_changes` list, worded
consistently with the existing four (spec/architecture/tests/correctness).

**FR-4 — Actionable, specific summaries.**
When any `request_changes` criterion fires, the summary must name the specific
artifact that's out of alignment: the spec requirement (e.g. "FR-2"), the plan
step, or the ADR number (e.g. "ADR-0004 / invariant #4: router must not do
I/O"). This requirement already exists implicitly ("specifically and
actionably") — extend it to explicitly require naming the artifact for the two
new criteria, not just describing the problem in prose.
*Acceptance:* the persona text says, for the plan/ADR criteria specifically, to
name the plan step or ADR/invariant number, not just "what's wrong."

**FR-5 — Preserve existing behavior verbatim.**
The sync-with-base-branch block (fetch/resolve-base/merge/abort-on-conflict →
`request_changes`), the four existing `Check:` items, the four existing
`request_changes` criteria, and the "no stylistic nitpicks / subjective
preferences / out-of-scope improvements / missing documentation" carve-out are
unchanged in wording and position — only additions, no rewrites of unrelated
sentences.
*Acceptance:* diff on `_REVIEW_PERSONA` is additive to the `Check:` list, the
`request_changes` list, and (if FR-4 needs it) one clause on summary
specificity; the sync block and the carve-out sentence are untouched.

**FR-6 — No routing/outcome change.**
`review`'s `allowed_outcomes` stay `["done", "request_changes"]`;
`AGENT_PERSONAS["review"]` keeps its existing tool list
(`["Read", "Grep", "Glob", "Bash"]` — `Read`/`Glob` already suffice to read
`docs/adr/*.md`, `docs/superpowers/plans/*.md` and `CLAUDE.md`). No workflow,
router, or dispatcher file changes.
*Acceptance:* `tests/test_cli.py::test_review_allowed_outcomes_unaffected_by_sync_instructions`
(or its equivalent) still passes unmodified; `git diff` touches only
`src/harness/cli.py` (persona string) and test files.

## Non-functional requirements

- **No new I/O or cost surface.** The reviewer already has `Bash`/`Read`
  access; reading a handful of `.md` files is well within existing budget. Per
  FR-2, scope ADR reading to "relevant to the files touched" (as the task
  states) rather than mandating all 15 files every run, to keep the added
  token cost proportional to the change size.
- **Determinism of wording for tests.** Keep the new bullets as literal,
  greppable substrings (e.g. contains `"docs/adr"`, `"plan"`) so unit tests can
  pin them the same way existing tests pin `"git fetch origin"` / `"Check:"`.

## Data model

No new entities. Existing ones referenced by the persona:
- **Spec** — `docs/superpowers/specs/…` and/or the task's `plan-*.md` artifact.
- **Plan** — `docs/superpowers/plans/…` and/or the task's `plan-*.md` artifact
  (functional requirements + rough plan).
- **ADRs** — `docs/adr/000N-*.md`, one per invariant in `CLAUDE.md`'s
  "Invariants — do not break" list.
- **`_REVIEW_PERSONA`** (`src/harness/cli.py`) — the only artifact this task
  writes to.

## Interfaces

None (no endpoint, no event, no UI). The only "interface" is the prompt text
handed to `claude -p` for the `review` step via `AgentSpec.prompt`.

## Dependencies and scope

**In scope:**
- `src/harness/cli.py` — `_REVIEW_PERSONA` string only.
- Test updates to keep coverage of the new wording green (see Rough plan).

**Out of scope:**
- Any new outcome value, workflow transition, port, or driver.
- Mechanically enforcing ADR conformance (e.g. new AST checks in
  `tests/test_architecture.py`) — this task is about the reviewer's prompt,
  not new automated guards.
- Fixing the branch's staleness as a general problem (e.g. a harness feature
  that keeps worktrees synced with `main` automatically) — only sync *this*
  worktree as a prerequisite step for *this* change to land cleanly.

**Depends on:**
- `origin/main`'s existing sync-with-base-branch persona block (commit
  `23ac7e7`) and `docs/adr/` (multiple commits) being present in the worktree
  before the edit — see Context and Rough plan step 0.

## Rough plan

0. **Sync the worktree with `origin/main` first.** `git fetch origin && git
   merge origin/main` (or rebase, if the harness's own git conventions for
   task branches prefer that — check for a documented convention before
   choosing) to bring in the sync-with-base-branch persona block and
   `docs/adr/`. Resolve any conflicts on `_REVIEW_PERSONA` by keeping main's
   sync block and the current (pre-this-task) checklist/criteria verbatim —
   this task's own edits come next, in step 2. If a clean merge/rebase isn't
   possible non-interactively, fall back to hand-porting just the sync block
   and `docs/adr/` from `origin/main` into this branch (see Open Questions).
1. Re-read the now-current `_REVIEW_PERSONA` (post-sync) to confirm the exact
   text to build on.
2. Insert the plan-conformance and ADR/invariant-conformance bullets into the
   `Check:` list (FR-1, FR-2).
3. Append the two new `request_changes` criteria (FR-3), and tighten the
   summary-specificity wording for these two (FR-4).
4. Confirm the sync block, the existing four `Check:`/`request_changes` items,
   and the nitpick carve-out are untouched (FR-5); confirm
   `AGENT_PERSONAS["review"]` and workflow transitions are untouched (FR-6).
5. Update/extend `tests/test_cli.py`: extend
   `test_review_persona_syncs_with_base_branch_before_checking_conformance`
   (or add a sibling test) to assert the plan/ADR bullets exist inside the
   `Check:`→next-section span, and a new assertion (e.g.
   `test_review_persona_checks_plan_and_adr_conformance`) that both new
   `request_changes` criteria are present and mention naming a specific
   artifact. Keep `test_review_allowed_outcomes_unaffected_by_sync_instructions`
   passing unmodified.
6. Run the full suite (`.venv/bin/pytest -q`) — must stay green, including
   `tests/test_smoke_git.py`'s conflict-path smoke test (unaffected, since
   `EchoRunner` simulates the sync/merge itself rather than parsing persona
   text) and `tests/test_architecture.py`.
7. Artifact: development's output summarizes exactly what was inserted, at
   what position in the string, and how to verify (pytest node IDs).

## Open questions

- **Q1 — How should worktree staleness be resolved?** Default: merge
  `origin/main` into the task branch (`git merge origin/main`) rather than
  rebase, since `CLAUDE.md`'s "Git conventions" section (as read on this stale
  branch) says commits land straight on `main` with no rebasing culture
  documented for task branches, and a merge preserves both histories without
  rewriting commits the harness's own worker already recorded. If `development`
  finds this repo has since adopted a different convention (e.g. squash/rebase
  on landing), defer to that instead.
- **Q2 — Exact wording for "relevant ADRs."** Left to `development`'s
  discretion: something like "read the ADRs in `docs/adr/` relevant to the
  files you're reviewing (use `Grep`/`Glob` to find them if the mapping isn't
  obvious) and the matching invariants in `CLAUDE.md`." Default chosen over
  "read all ADRs every time" to keep the check proportional to change size, as
  the task text implies ("Read the ADRs relevant to the files touched").
- **Q3 — Should FR-4's "name the artifact" requirement apply to *all* four
  `request_changes` criteria or just the two new ones?** Default: word it so
  it naturally covers all criteria (spec requirement / plan step / ADR number
  / test name), since a reviewer citing "FR-2" or "ADR-0004" for the *existing*
  spec/architecture criteria is equally more actionable — but don't rewrite the
  existing four bullets themselves, only the closing sentence about what the
  summary must contain (keeps FR-5's "additive only" constraint).
