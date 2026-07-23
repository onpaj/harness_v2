# Development — Reviewer agent: validate implementation against spec, plan and all ADRs

## Summary

Implemented `plan-01.md` / `design-01.md` / `architecture-01.md` verbatim: the
worktree was already synced with `origin/main` (a prior step's merge commit
`0f2ed82` had already brought in the sync-with-base-branch persona block and
`docs/adr/`, confirmed by `git rev-list --count HEAD..origin/main` → `0` and
`ls docs/adr/` → 15 files present), so no additional sync was needed before
editing. The edit itself is a pure additive change to `_REVIEW_PERSONA` in
`src/harness/cli.py`, plus two new tests in `tests/test_cli.py`.

## Files changed

- **`src/harness/cli.py`** — `_REVIEW_PERSONA` string, additive only:
  - Inserted two new `Check:` bullets, positioned right after "Adherence to
    the architecture" and before "Completeness" (upstream-artifact checks
    grouped together, as design-01.md specifies):
    - **Plan conformance** — does the implementation follow the agreed plan
      (`docs/superpowers/plans/…` or the task's own `plan-*.md` artifact)
      without silently skipping or reinterpreting planned steps?
    - **ADR / invariant conformance** — read the ADRs in `docs/adr/` relevant
      to the files being reviewed (and the matching entries in `CLAUDE.md`'s
      "Invariants — do not break" list) and verify none is violated.
  - Appended two new `request_changes` criteria after the existing
    correctness-bug bullet:
    - the implementation deviates from the plan without justification, or
    - the implementation violates an ADR or a documented invariant from
      `CLAUDE.md`.
  - Extended the summary-specificity closing sentence with one clause
    requiring the summary to name the concrete artifact out of alignment
    (spec requirement / plan step / ADR number or invariant), worded
    generically so it covers all six criteria, not just the two new ones
    (resolves plan's Open Question Q3 the way design-01.md settled it).
  - The one unavoidable one-character touch: the correctness-bug bullet's
    trailing `.` became `,` since it's no longer the last item in the list —
    no wording change, no test asserts on that punctuation.
  - **Untouched, verified byte-for-byte:** the role/framing paragraph, the
    entire sync-with-base-branch block (fetch → resolve base → merge → capture
    conflicting paths → abort → `request_changes`), the four pre-existing
    `Check:`/`request_changes` bullets themselves, and the nitpick carve-out +
    `done` case sentence.
  - `AGENT_PERSONAS["review"]`'s tool list (`["Read", "Grep", "Glob", "Bash"]`)
    and `review`'s `allowed_outcomes` are unchanged — no new outcome, no new
    tool, no workflow/router/dispatcher edit.

- **`tests/test_cli.py`** — added `AGENT_PERSONAS` to the existing `harness.cli`
  import, plus two new tests immediately after the two pre-existing
  `test_review_persona_syncs_with_base_branch_before_checking_conformance` /
  `test_review_allowed_outcomes_unaffected_by_sync_instructions` (both left
  unmodified and still pass, confirming the sync block and outcome list are
  unaffected):
  - `test_review_persona_checks_plan_and_adr_conformance` — asserts, via the
    same index-ordering technique the existing sync test uses, that:
    - `"docs/adr"` and `"docs/superpowers/plans"` both appear inside the
      `Check:` → `Return the verdict` span (i.e. in the Check list, not the
      request_changes list or the sync block).
    - `"deviates from the plan"` and `"violates an ADR"` both appear inside
      the `Return the verdict` → `In that case` span (i.e. in the
      request_changes list).
    - `"naming the concrete artifact"` appears after `In that case` (the
      closing sentence carries the new naming requirement).
  - `test_review_persona_tool_list_and_outcomes_unchanged_by_plan_adr_instructions`
    — asserts `_allowed_outcomes_for(workflow, "review")` is still exactly
    `["done", "request_changes"]` and `AGENT_PERSONAS["review"][1]` is still
    `["Read", "Grep", "Glob", "Bash"]` — pinning FR-6 (no outcome/tool
    surface change) with an explicit test, on top of the pre-existing
    `test_review_allowed_outcomes_unaffected_by_sync_instructions`.

## Scope check

`git diff --stat` touches exactly two files:

```
 src/harness/cli.py | 19 +++++++++++++++----
 tests/test_cli.py  | 29 +++++++++++++++++++++++++++++
 2 files changed, 44 insertions(+), 4 deletions(-)
```

No workflow file, router, dispatcher, port, or driver was touched — consistent
with invariant 14 / ADR-0007 (persona is data, not code) and FR-6.

## How to verify

```sh
.venv/bin/pip install -e ".[dev]"   # first run only, this venv had no pytest installed
.venv/bin/pytest -q
```

Result: **1024 passed, 1 skipped** (the skip is the opt-in
`tests/test_smoke_claude.py`, gated on `HARNESS_SMOKE_CLAUDE=1`, unrelated to
this change).

Targeted nodes for this task specifically:

```sh
.venv/bin/pytest -q \
  tests/test_cli.py::test_review_persona_syncs_with_base_branch_before_checking_conformance \
  tests/test_cli.py::test_review_allowed_outcomes_unaffected_by_sync_instructions \
  tests/test_cli.py::test_review_persona_checks_plan_and_adr_conformance \
  tests/test_cli.py::test_review_persona_tool_list_and_outcomes_unchanged_by_plan_adr_instructions
```

All four pass, confirming both pre-existing behavior (sync block position,
outcome list) and the new plan/ADR conformance wording (position, exact
substrings) are correct.

## Notes

- No further sync was required in this step: the merge into `origin/main`
  (visible as commit `0f2ed82` in this branch's history) had already happened
  before this development step started, so `docs/adr/` and the sync-with-
  base-branch persona block were already present and the target text in
  `design-01.md` matched the pre-edit file byte-for-byte (confirmed by reading
  the file before editing).
- No mechanical ADR-enforcement code was added (no new `tests/test_architecture.py`
  AST checks) — per plan/design/architecture, this task is a prompt-text
  change; ADR conformance is judged by the reviewer agent reading the files,
  not by a new static gate.
