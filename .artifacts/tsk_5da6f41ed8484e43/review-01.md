# Review — reviewer syncs with base branch, `request_changes` on conflict

## Verdict: done

## Conformance to spec (plan-01.md)

- **FR-1** (sync-first): `_REVIEW_PERSONA` (`src/harness/cli.py:213-239`) now
  opens with the numbered fetch → resolve-base → merge procedure, inserted
  before the existing "Check:" bullet list. Confirmed by
  `test_review_persona_syncs_with_base_branch_before_checking_conformance`
  (`tests/test_cli.py`), which pins both the substrings and the ordering via
  `_REVIEW_PERSONA.index(...)`.
- **FR-2** (clean sync is a no-op for the verdict): step 5 of the new block
  explicitly says a successful merge (fast-forward / merge commit / already
  up to date) falls through to the unchanged checklist. The pre-existing
  happy-path smoke test (`test_task_lands_as_pull_request_on_real_git`) is
  untouched and still passes, exercising this path implicitly.
- **FR-3** (conflict → `request_changes`, reported specifically): the persona
  instructs capture-conflicts → `merge --abort` → skip the rest of the review
  → `request_changes` naming `origin/<base>` and the conflicting paths. The
  new real-git smoke test
  (`test_review_syncs_with_base_and_requests_changes_on_real_conflict`) drives
  an actual conflicting merge via an extended `EchoRunner` and asserts: the
  artifact mentions "conflict", `origin/main`, and `README.md`; no
  `MERGE_HEAD` remains; `git status --porcelain` is empty.
- **FR-4** (round-trips through `development`): no workflow/routing change was
  made or needed. The same smoke test asserts a history entry with
  `outcome == "request_changes"` from `review` and a separate dispatcher entry
  routing `review -> development`, confirming invariant 3 (consumer records
  outcome, dispatcher records the route) holds for this path too.

## Adherence to architecture (architecture-01.md)

- Change is confined to the `_REVIEW_PERSONA` string literal plus the two
  test files. `git diff main...HEAD --stat` confirms no port, driver, model
  field, or `ClaudeCliBehavior`/`compose_prompt`/router/dispatcher change.
- `tests/test_architecture.py` passes unmodified — invariants 1, 2, 5, 11, 17,
  20, 23 all still hold.
- `_allowed_outcomes_for(workflow, "review")` is asserted unchanged
  (`["done", "request_changes"]`) by
  `test_review_allowed_outcomes_unaffected_by_sync_instructions`.
- No new outcome value, no new edge, no base-branch-resolution helper added
  to production code — base-branch resolution happens only inside the
  agent's own shell (persona text) and, separately, inside the test double
  that stands in for it; `GithubForge`'s own API-based default-branch lookup
  is untouched, matching the architecture's explicit decision to keep these
  two mechanisms separate.

## Correctness

- Full suite: `.venv/bin/pytest -q` → **475 passed, 1 skipped**, matching the
  development artifact's reported numbers.
- Verified independently (not just trusting the development artifact) that
  the diff since `main` touches only `src/harness/cli.py` (persona text),
  `tests/test_cli.py`, `tests/test_smoke_git.py`, and this task's own
  `.artifacts/` files.
- The conflict-path summary/artifact contract (mentions `origin/<base>`,
  lists conflicting paths) is satisfied by the persona wording and exercised
  end-to-end by the real-git smoke test — not just asserted against a canned
  string.
- No empty-commit risk on the conflict path: `merge --abort` leaves the tree
  clean, and `GitWorkspaceHandle.commit`'s existing no-op-on-clean-status
  behavior (unchanged) means the behavior's own commit step naturally does
  nothing — no new logic needed and none was added.
- No safety regressions: the persona explicitly forbids force-push,
  force-resolve, branch creation/switching on the sync step, consistent with
  the existing no-force-push discipline elsewhere in the harness.

No functional requirement is unmet, no architectural conflict, no missing
test, no correctness bug found.

```json
{"outcome": "done", "summary": "Verified the _REVIEW_PERSONA sync-with-base-branch instruction block against plan/design/architecture: FR-1–FR-4 all satisfied, scope confined to the persona string plus two test files (no port/driver/routing change), full suite green (475 passed, 1 skipped), and the new real-git smoke test genuinely exercises a conflicting merge end-to-end rather than just asserting on persona text."}
```
