# Plan: skip confirmation in create-harness-issue skill when the request is already complete

## Summary

The `create-harness-issue` skill (`.claude/skills/create-harness-issue/SKILL.md`)
currently always pauses at step 4 ("Preview and confirm") and asks the user for
an explicit go-ahead before calling `gh issue create`. This task changes that:
when the user's request already supplies everything the skill needs (repo,
title, and enough context to fill the body) with no ambiguity to resolve, the
skill should draft the issue and create it directly, showing the result
afterward instead of asking for permission before acting. When something is
missing or ambiguous (repo can't be resolved, title is vague, user gave no
real content for the body), the skill still asks — this task doesn't remove
that safety net, it removes the *redundant* confirmation for the already-clear
case.

## Context

This is a one-file skill/prompt-engineering change, not application code —
there's no test suite or architecture invariant from the harness project
(`CLAUDE.md`'s invariants govern `harness_v2`'s Python core, not its bundled
skills) that applies here. The change is about editing the instructions the
skill gives to whichever agent runs it, so the "requirements" below are about
the resulting document's content and structure, not about code behavior.

Today the skill treats "outward-facing" (creating a GitHub issue) as reason
enough to always confirm. The user has decided that's excessive friction when
they've already given a fully-specified request (e.g. "create a harness issue
in onpaj/foo titled 'Add X' with body Y") — in that case the skill should just
do it, the same way `create-harness-issue`'s sibling skills act directly once
they have enough information.

## Functional requirements

**FR-1: Add a completeness check before the preview/confirm step.**
Before creating the issue, the skill must decide whether it has everything it
needs to act without further input:
- Repo is resolved (explicit `owner/repo`, or unambiguously inferred from `gh
  repo view`).
- Title is a concrete, self-contained, imperative instruction (not a vague
  headline needing the agent to invent the actual ask).
- Enough substance exists to fill in a meaningful body (context/goal/acceptance
  criteria aren't just placeholders the skill is inventing wholesale from
  nothing).
- Acceptance: the rewritten SKILL.md contains an explicit rule/step the agent
  follows to make this determination before deciding whether to confirm.

**FR-2: Skip confirmation when complete; create directly.**
When FR-1's check passes, the skill proceeds straight from drafting the
title/body to running `gh issue create` — no "preview and confirm" pause, no
waiting for explicit go-ahead.
- Acceptance: step "Preview and confirm" is no longer an unconditional gate;
  the workflow shows the direct-create path as the default when the request is
  complete.

**FR-3: Keep confirmation for incomplete/ambiguous requests.**
When the repo can't be resolved, the title is vague, or the user hasn't
supplied enough for a meaningful body, the skill still asks the user rather
than guessing or fabricating content.
- Acceptance: the rewritten SKILL.md explicitly retains a path where the
  agent asks the user for missing/ambiguous pieces (repo, title clarity,
  substantive content) before creating anything.

**FR-4: Report after creation instead of before.**
Since the confirmation-before-create step goes away for the direct path, the
skill must still show the user what was created — repo, title, body, label,
and the issue URL — as a post-hoc report, so the user isn't surprised and can
still ask to edit/close/redo if the result isn't what they wanted.
- Acceptance: the "Report the issue URL" step is extended (or a new step
  added) to also surface the title/body/repo actually used, immediately after
  creation, for the direct-create path.

**FR-5: Update "Common mistakes" section for consistency.**
The existing mistake entry "Creating without confirmation. Outward-facing;
always preview first." directly contradicts the new behavior and must be
revised to reflect the new rule (confirm only when something is missing or
ambiguous, not unconditionally).
- Acceptance: no remaining line in SKILL.md instructs the agent to "always"
  confirm before creating.

## Non-functional requirements

- **Consistency**: the change must not contradict the skill's own frontmatter
  `description` (still triggers on the same phrases) or the "repo-agnostic"
  guarantee — resolving the repo automatically is unaffected.
- **Safety**: the bar for "already has everything it needs" must be described
  concretely enough (FR-1) that a future agent reading the skill doesn't drift
  toward skipping confirmation on genuinely ambiguous requests just because
  skipping is now the "normal" path. Prefer being explicit about what counts as
  ambiguous (vague title, unresolved repo, no real body content) over leaving
  it to judgment alone.
- No behavior change to unrelated steps: label existence check (step 2),
  `harness:todo`-only labeling policy, and repo-resolution order (explicit >
  inferred > ask) all stay as they are.

## Data model

Not applicable — this is a markdown instruction file, not application code.
The only "entities" are the sections of SKILL.md itself:
- Frontmatter (`name`, `description`) — unchanged.
- `Overview` / contract table — unchanged.
- `Workflow` (numbered steps 1–6) — step 4 restructured into a branch
  (complete → create directly; incomplete/ambiguous → ask), step 6 extended
  per FR-4.
- `Common mistakes` — the confirmation-related bullet revised per FR-5.

## Interfaces

None (no code, no API). The "interface" is the skill's own workflow as read
and executed by an agent invoking `gh` commands — unchanged commands
(`gh label list`, `gh label create`, `gh issue create`), only the surrounding
decision of when to pause changes.

## Dependencies and scope

**Depends on:** nothing beyond the current file
`.claude/skills/create-harness-issue/SKILL.md`.

**In scope:**
- Rewriting step 4 of the Workflow section into a completeness check +
  conditional confirm/create branch.
- Adjusting step 6 (reporting) to cover the direct-create path.
- Updating the "Common mistakes" section.

**Out of scope:**
- Changing the label-existence check, repo-resolution logic, or body
  template.
- Any change to harness_v2's Python core (dispatcher/router/behaviors) — this
  task touches only the bundled skill file.
- Adding new labels or changing which labels the skill is allowed to set.
- Building any automated test for the skill (skills are prompts, not code;
  there's no test harness for SKILL.md content in this repo).

## Rough plan

1. Read the current `.claude/skills/create-harness-issue/SKILL.md` in full
   (done above) to use as the exact base for edits.
2. Rewrite Workflow step 4 ("Preview and confirm") into two parts:
   a. A completeness check (per FR-1) run after drafting title/body (step 3).
   b. A branch: if complete, proceed directly to step 5 (create); if
      incomplete/ambiguous, ask the user for the missing piece(s) before
      proceeding (per FR-3).
3. Extend step 6 ("Report the issue URL") to include a short summary of what
   was created (repo, title, label) alongside the URL, so the direct-create
   path still gives the user full visibility (FR-4).
4. Revise the "Common mistakes" bullet about confirmation to match the new
   default-direct / ask-when-ambiguous behavior (FR-5).
5. Re-read the full file afterward to check step numbering, internal
   cross-references, and that the frontmatter/description still match the
   actual behavior described.

## Open questions

- **Where exactly is the line between "complete" and "ambiguous"?** The user's
  request says "when it already has everything it needs" — I've interpreted
  this as: repo resolved unambiguously, title is a concrete imperative
  instruction (not a bare headline), and there's real substance for the body
  beyond what the skill would have to invent from nothing. Default taken:
  encode these three concrete checks in the skill (FR-1) rather than leaving
  "enough information" undefined, so behavior stays predictable.
- **Should the direct-create path show a preview at all, even without waiting
  for a reply (e.g. print the draft then immediately create)?** Default taken:
  no — the point of the change is to remove the pause, so the report happens
  after creation (FR-4), not as a pre-creation preview the user isn't asked to
  react to. This keeps the distinction between "asking" and "reporting" clean.
- **Does "no confirmation" ever apply to the label-creation step (step 2)?**
  Default taken: no — that step already has no confirmation gate today (it's
  a mechanical existence check), so it's unaffected and out of scope.
