# Design — Reviewer agent: validate implementation against spec, plan and all ADRs

## Summary

This task edits one Python string constant (`_REVIEW_PERSONA` in
`src/harness/cli.py`) and its accompanying unit tests. There is no UI, no new
port, no new outcome, no schema in the database/API sense — the only "product"
is the literal text handed to `claude -p` for the `review` step, and the only
"schema" worth pinning down precisely is that text's structure and exact
wording, since tests assert on substrings and byte positions within it. This
document specifies that text verbatim, where each new clause goes, and the
contract the accompanying tests check.

No UX/UI section — this feature has no user-facing surface; it changes an
agent prompt consumed by `claude -p`, not anything rendered to a person.

## Preconditions (confirmed, not assumed)

Verified directly in this worktree before writing this design:

- `docs/adr/` does not exist here (`ls docs/adr` → no such file or directory).
- `origin/main` is 44 commits ahead of this branch's merge-base
  (`d76a1708` vs. merge-base `0c8027b5`).
- `origin/main`'s `_REVIEW_PERSONA` already contains the sync-with-base-branch
  block (fetch → resolve base → merge → capture conflicting paths on failure →
  `git merge --abort` → `request_changes`) and the four existing `Check:` /
  `request_changes` bullets, unchanged from what's in this branch today except
  for that added block.
- `origin/main`'s `docs/adr/` has 15 files, `0000-adr-process.md` through
  `0014-triggers-produce-tasks-not-placements.md`.
- `origin/main`'s `tests/test_cli.py` already has
  `test_review_persona_syncs_with_base_branch_before_checking_conformance` and
  `test_review_allowed_outcomes_unaffected_by_sync_instructions`, asserting on
  the sync block and on `allowed_outcomes` respectively — neither test
  mentions plan/ADR conformance, confirming that part is genuinely new.

Per the plan (`plan-01.md`, Rough plan step 0), the worktree must sync with
`origin/main` before the persona edit, otherwise the sync block and
`docs/adr/` this design builds on don't exist yet on this branch. That sync is
implementation work and is out of scope for this document (which specifies
*what* the text should say, not the git mechanics of getting there) — it is
called out here only so the target text below is understood to apply to the
post-sync file, not the current one.

## Component design

`_REVIEW_PERSONA` is a single opaque string; there is no internal component
boundary to design. What matters is its **section structure**, since each
section is independently pinned by a test today and must stay that way:

```
1. Role + rigor framing               ("You are a senior code reviewer...")
2. Sync-with-base-branch block        (fetch / resolve base / merge / abort-on-conflict)
   — unchanged, untouched by this task
3. "Check:" bullet list                — 4 existing + 2 NEW bullets appended in place
4. "Return the verdict `request_changes` only when:" list
                                       — 4 existing + 2 NEW criteria appended in place
5. Summary-specificity sentence       — extended by one clause (naming requirement)
6. Nitpick carve-out + `done` case    — unchanged, untouched by this task
```

Sections 2 and 6 are preserved byte-for-byte (FR-5). Sections 3, 4 and 5 are
where this task's edits land, always as *appended* bullets/clauses — never a
rewrite of an existing sentence, so a future `git diff` on `_REVIEW_PERSONA`
shows pure additions.

**Placement decision (resolves plan's implicit ordering question):** the two
new `Check:` bullets go immediately after "Adherence to the architecture" and
before "Completeness" — spec/architecture/plan/ADR are all checks against an
*upstream artifact*, while completeness/correctness are checks against the
implementation itself. Grouping them keeps the list's existing logic (upstream
conformance first, implementation quality after) intact rather than just
tacking the new items onto the end.

Symmetrically, the two new `request_changes` criteria are appended after the
existing four (order there is a flat enumeration, not grouped, so end-of-list
is the natural, minimal-diff position).

## Content schema — exact text

### New `Check:` bullets (inserted after the architecture bullet, before Completeness)

```
"- Plan conformance — does the implementation follow the agreed plan "
"(`docs/superpowers/plans/…` or the task's own `plan-*.md` artifact) without "
"silently skipping or reinterpreting planned steps?\n"
"- ADR / invariant conformance — read the ADRs in `docs/adr/` relevant to the "
"files you're reviewing (and the matching entries in CLAUDE.md's "
"\"Invariants — do not break\" list) and verify none is violated.\n"
```

### New `request_changes` criteria (appended after the correctness-bug bullet)

```
"- the implementation deviates from the plan without justification, or\n"
"- the implementation violates an ADR or a documented invariant from "
"CLAUDE.md.\n"
```

### Summary-specificity sentence — extended, not replaced

Current (unchanged prefix/suffix, only the middle clause is new):

```
"In that case, write in the summary — specifically and actionably — what's "
"wrong and what to fix, naming the concrete artifact that's out of alignment "
"(the spec requirement, the plan step, or the ADR number / invariant) rather "
"than describing the symptom alone; the development step will go into "
"another round based on it.\n\n"
```

This resolves plan's Open Question Q3: the naming requirement is worded to
cover all criteria generically ("the concrete artifact... spec requirement,
plan step, or ADR number") rather than being scoped only to the two new ones —
but, per FR-5, this is the *only* sentence touched; the four existing
`Check:`/`request_changes` bullets themselves are not reworded.

### Full resulting `_REVIEW_PERSONA` (target state, post-sync + post-edit)

```python
_REVIEW_PERSONA = (
    "You are a senior code reviewer. You check the implementation against the "
    "specification and architecture from the previous steps. Be fair but "
    "rigorous — this is about correctness and conformance to the request, not "
    "stylistic preferences.\n\n"
    "Before anything else, sync the task branch with the repository's base "
    "branch:\n"
    "1. Run `git fetch origin`.\n"
    "2. Determine the base branch: run `git symbolic-ref "
    "refs/remotes/origin/HEAD` and strip the `refs/remotes/origin/` prefix; "
    "if that fails, use `main`.\n"
    "3. Run `git merge origin/<base>`. You are already checked out on the "
    "task branch — DO NOT create or switch branches, and DO NOT force-push "
    "or force-resolve anything.\n"
    "4. If the merge reports conflicts:\n"
    "   - Run `git diff --name-only --diff-filter=U` to capture the "
    "conflicting file paths.\n"
    "   - Run `git merge --abort` to leave the working tree clean.\n"
    "   - Do not attempt to resolve the conflict yourself, and do not judge "
    "code correctness — skip the rest of this review below.\n"
    "   - Write your output artifact and finish with outcome "
    "`request_changes`. The summary and the artifact must both state that "
    "merging `origin/<base>` produced conflicts and must list every "
    "conflicting file path from the previous step.\n"
    "5. If the merge succeeds — fast-forward, a merge commit, or \"Already "
    "up to date\" — continue with the review exactly as below. This sync "
    "step alone must never change your verdict.\n\n"
    "Check:\n"
    "- Conformance to the spec — does the implementation meet the functional "
    "requirements?\n"
    "- Adherence to the architecture — does it follow the proposed patterns "
    "and structure?\n"
    "- Plan conformance — does the implementation follow the agreed plan "
    "(`docs/superpowers/plans/…` or the task's own `plan-*.md` artifact) "
    "without silently skipping or reinterpreting planned steps?\n"
    "- ADR / invariant conformance — read the ADRs in `docs/adr/` relevant to "
    "the files you're reviewing (and the matching entries in CLAUDE.md's "
    "\"Invariants — do not break\" list) and verify none is violated.\n"
    "- Completeness — are the acceptance criteria met and the required tests "
    "written?\n"
    "- Correctness — obvious logic errors, missing error handling, security or "
    "concurrency problems.\n\n"
    "Return the verdict `request_changes` only when:\n"
    "- a functional requirement from the spec is not met,\n"
    "- the implementation conflicts with the architecture,\n"
    "- tests that were explicitly required are missing,\n"
    "- there is a clear correctness bug,\n"
    "- the implementation deviates from the plan without justification, or\n"
    "- the implementation violates an ADR or a documented invariant from "
    "CLAUDE.md.\n"
    "In that case, write in the summary — specifically and actionably — "
    "what's wrong and what to fix, naming the concrete artifact that's out "
    "of alignment (the spec requirement, the plan step, or the ADR number / "
    "invariant) rather than describing the symptom alone; the development "
    "step will go into another round based on it.\n\n"
    "Don't return `request_changes` over stylistic nitpicks, subjective "
    "preferences, out-of-scope improvements, or missing documentation. When "
    "the implementation is sound, return `done` (optionally with non-binding "
    "cleanup suggestions)."
)
```

Note the trailing comma changed from `.` to `,` on the "clear correctness
bug" bullet (`- there is a clear correctness bug,\n` vs. today's `.\n\n` on
`origin/main`) since it's no longer the last item in the list — this is the
one unavoidable one-character touch to an "existing" line, required to keep
the list grammatically an enumeration; it changes no wording and no test
asserts on that specific punctuation.

## Verification contract (what tests pin)

Two existing tests on `origin/main` must keep passing unmodified, since
nothing they check is touched:
- `test_review_persona_syncs_with_base_branch_before_checking_conformance` —
  checks the sync block precedes `Check:`, and pins `git merge --abort` →
  `request_changes` before `Check:`. Unaffected: the sync block's text and its
  position relative to `Check:` are unchanged.
- `test_review_allowed_outcomes_unaffected_by_sync_instructions` — checks
  `_allowed_outcomes_for(workflow, "review") == ["done", "request_changes"]`.
  Unaffected: no workflow/outcome change (FR-6).

New coverage needed for the added text (exact substrings to assert on, so
they can be pinned the same way as `"git fetch origin"` is today):
- Both `"docs/adr"` and `"docs/superpowers/plans"` appear in
  `_REVIEW_PERSONA`, positioned between the `"Check:"` index and the
  `"Return the verdict"` index (i.e., inside the Check list, not the
  request_changes list or the sync block).
- `"deviates from the plan"` and `"violates an ADR"` both appear between the
  `"Return the verdict"` index and the `"In that case"` index (i.e., inside
  the request_changes list).
- A phrase requiring the summary to name a specific artifact (e.g.
  `"naming the concrete artifact"`) appears after the `"Return the verdict"`
  index — i.e. the closing sentence carries the new naming clause.
- `_allowed_outcomes_for(workflow, "review")` is still exactly
  `["done", "request_changes"]` and `AGENT_PERSONAS["review"][1]` is still
  `["Read", "Grep", "Glob", "Bash"]` (no tool list change — `Read`/`Glob`
  already reach `docs/adr/*.md` and `docs/superpowers/plans/*.md`).

## Out of scope (confirmed from plan, unchanged)

- No new outcome value, no workflow/router/dispatcher edit.
- No mechanical ADR enforcement (no new `tests/test_architecture.py` AST
  checks) — this is a prompt-text change, the enforcement is the reviewer
  agent reading and judging, not a static check.
- No general fix for worktree staleness as a harness feature — only this
  worktree's sync, as a one-time prerequisite for this edit to land cleanly.
