# Development: skip confirmation in create-harness-issue when the request is already complete

## Summary

Implemented the plan/design/architecture as specified, editing exactly one
file: `.claude/skills/create-harness-issue/SKILL.md`. No code changes — this
is a prompt/instruction-only skill file, so there's no test suite to run and
no `pytest` coverage applies (confirmed by the architecture doc: skills
aren't in the module map, none of `CLAUDE.md`'s 23 invariants govern them).

## What changed

Three regions of `.claude/skills/create-harness-issue/SKILL.md`, all other
lines (frontmatter, Overview, contract table, steps 1–3, step 5) untouched:

1. **Step 4** — retitled from "Preview and confirm" to "Check completeness,
   then create or ask." Replaced the unconditional confirm-before-create gate
   with a router that evaluates three named checks (`repo_resolved`,
   `title_concrete`, `body_substantive`), each stated with its true/false
   test and the plan's concrete examples ("Add rate limiting to the login
   endpoint" vs. "Rate limiting" / "Bug in parser") kept verbatim. All-true
   routes straight to step 5 with no pause; any false routes to a targeted
   ask about that specific gap, worded as "ask, then continue" — not a
   backward "go to step 3" instruction, per the architecture's correction.
   The existing "don't add harness state labels" sentence was carried over
   into the new step 4 text.

2. **Step 5** — unchanged (`gh issue create` with temp-file body).

3. **Step 6** — retitled from "Report the issue URL" to "Report what was
   created," extended to a fixed five-field report (repo, title, body
   summary, label, URL), applied uniformly to both the direct-create and
   after-ask paths — one step, no 6a/6b split, matching the design.

4. **Common mistakes** — the single bullet "Creating without confirmation.
   Outward-facing; always preview first." (which now contradicted the new
   behavior) was replaced with two bullets: one warning against skipping the
   ask on a genuinely ambiguous request, one warning against asking anyway
   when the request is already complete. Both are needed per the
   architecture's risk analysis — a single bullet would re-bias the file
   toward whichever failure mode it names.

## Verification

This is a markdown instruction file with no executable surface, so
verification is by inspection against the plan's acceptance criteria:

- FR-1 (explicit completeness check before creating): step 4 states the
  three named criteria — done.
- FR-2 (skip confirmation when complete): step 4's routing rule sends an
  all-true draft straight to step 5 — done.
- FR-3 (keep asking when incomplete): step 4's any-false branch still asks,
  targeted at the specific failing criterion — done.
- FR-4 (report after creation): step 6 now reports repo/title/body
  summary/label/url for both paths — done.
- FR-5 (fix the "always confirm" mistake entry): replaced with two bullets,
  no remaining line says "always" confirm — done.
- No other section changed: confirmed by diff — steps 1–3, step 5, the
  contract table, and frontmatter are byte-identical to before.

To verify by hand: read
`.claude/skills/create-harness-issue/SKILL.md` end to end and confirm step
numbering is still 1–6, no dangling cross-references, and the frontmatter
`description` still matches actual behavior (it does — repo-agnostic
triggers are unaffected).

```json
{"outcome": "done", "summary": "Rewrote SKILL.md step 4 into a completeness-check router (repo_resolved/title_concrete/body_substantive) that creates directly when all three pass and asks a targeted question otherwise, extended step 6 into a five-field post-creation report, and replaced the contradictory 'always confirm' mistake bullet with a matched pair covering both failure directions."}
```
