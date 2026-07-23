# Review — triage Process: `claimed_label` + `label-issue` finisher

## Verdict: done

## What was checked

Read plan-01.md, design-01.md, architecture-01.md, development-01.md, then
verified against the actual diff (commit `9d6633c`) rather than trusting the
development summary:

- `src/harness/drivers/github_issues_check.py` — confirmed `claimed_label`
  was pre-existing (added in `#79`, unrelated commit), not newly introduced
  here; the task correctly scoped itself to exposing it through the process
  JSON surface only.
- `src/harness/cli.py` (`_process_sources`, `_run`) — `claimed_label` read
  from `params`, type-guarded alongside `label`; one `GithubClient` built in
  `_run` and threaded into both `_process_sources` and the new `finishers`
  dict, matching architecture's Correction 2.
- `src/harness/drivers/fs_processes.py` — the cross-file collision guard
  correctly re-parses each file's raw `action` after `_build_one` succeeds,
  collects `label`/`claimed_label` into a shared `seen` map, and raises
  naming both files on a collision. `default_github_issues_label` is threaded
  from `args.github_label` (Correction 3), not hardcoded.
- `src/harness/models.py` — `FinisherBinding(kind, config)` frozen dataclass;
  `Workflow.finishers` retyped; no unnecessary back-compat shim (verified via
  the ADR's own grep claim — `finisher_for` has no production caller besides
  `app.py`, which never calls it).
- `src/harness/drivers/fs_workflows.py` — `_parse_workflow` accepts both the
  bare-string and object forms exactly as designed; unknown/empty kind and
  malformed shapes raise clearly.
- `src/harness/app.py` — finisher registry is now kind → factory
  `(step, config, inner_thunk) -> ConsumerBehavior`; `open-pr`'s factory
  ignores all three args; `step_bindings` conflict check compares whole
  `FinisherBinding`s. Confirmed the lazy-thunk fix is real and necessary: the
  landing step has no catalog agent (`_write_default_agents` skips it), so an
  eager `inner` would break the ordinary case — this is regression-tested
  directly (`test_finisher_that_never_calls_inner_never_triggers_catalog_lookup`
  reproduces the exact break).
- `src/harness/drivers/label_issue.py` — `LabelIssueBehavior` wraps `inner`,
  reads `result.outcome.value`, applies `GithubClient.add_label` against
  `task.data.source`, no-ops with a summary note on missing source or
  unmapped outcome, never touches `outcome`/`data` (routing untouched,
  invariant #8). Correctly placed under `drivers/` — confirmed
  `test_behaviors_import_only_ports_not_drivers` only globs `behaviors/*.py`,
  so this file is exempt by construction, and `drivers/`→`drivers/` imports
  are unconstrained (precedented by `github_issues_check.py`).
- Architecture invariants: no new port was added (deliberately, per task
  scope); `dispatcher.py`/`consumer.py` untouched; `app.py`/`cli.py` remain
  the only wiring sites. Ran `test_architecture.py` — green.

## Acceptance criteria — verified against tests, not just claims

- ✅ `claimed_label` param + validation + collision guard —
  `tests/test_cli.py`, `tests/test_fs_processes.py::test_two_processes_sharing_a_claimed_label_fail_the_build`,
  `test_triage_process_with_no_collision_builds_cleanly`,
  `test_collision_check_honours_the_default_github_issues_label`.
- ✅ `label-issue` finisher: data-configured, no branch on step/persona name,
  fails at build on unknown kind (pre-existing `finisher_registry` check,
  unchanged), no-op-with-note for missing `data.source` —
  `tests/test_label_issue_behavior.py` covers approve/reject/no-source/
  unmapped-outcome/routing-untouched.
- ✅ Structured finisher syntax documented, plain-string form still works —
  `tests/test_fs_workflows.py`, `tests/test_fs_workflow_admin.py` round-trip.
- ✅ PM persona as data — `agents/triage.json` template in the new spec doc
  (named by step, not persona, with a documented reason: `AgentCatalog.get`
  is keyed by step name, and the product-manager identity lives in the
  prompt content — consistent with invariant #14's existing shape). Not a
  literal `product-manager.json` per the acceptance text's example filename,
  but the acceptance criterion itself says "PM persona is data... vision/fit
  context is prompt content, not code," which is satisfied; the filename
  detail is explained, not glossed over.
- ✅ Tests: claim-swap with custom labels, approve→`harness:todo`,
  reject→`harness:needs-info`, re-scan doesn't re-claim — all present and
  passing in `tests/test_triage_process_e2e.py`.
- ✅ `CLAUDE.md` (invariants #39/#41, module map, driver bullets) and the
  processes spec updated; new ADR-0018 and a dedicated feature spec added.

## Correctness

Ran the full suite and the feature-relevant subset directly:

```
.venv/bin/pytest -q
# 1243 passed, 1 skipped

.venv/bin/pytest -q tests/test_architecture.py tests/test_app.py tests/test_cli.py \
  tests/test_fs_processes.py tests/test_fs_workflows.py tests/test_fs_workflow_admin.py \
  tests/test_label_issue_behavior.py tests/test_triage_process_e2e.py tests/test_models.py
# 318 passed
```

No logic errors found. The one real design gap (eager `inner` resolution
breaking the landing step) was caught by the implementer themselves during
development, fixed correctly (lazy thunk), and backed by two targeted
regression tests plus an ADR update — exactly the kind of self-correction
this review would otherwise have had to flag.

## Non-binding notes (not blocking)

- The PM agent template is written to a new markdown spec rather than wired
  into `harness agent init`'s code paths. This matches the task's own
  FR-5/FR-6 framing ("documented, not seeded") and is consistent with how
  `processes/`, `workflows/`, and `agents/` are already runtime config outside
  the repo — not a gap, just worth knowing if a future task wants
  `harness agent init triage --workflow triage` to scaffold it.
- The cross-file collision guard is explicitly static/exact-string only, and
  both the code comment and the spec say so plainly — good, no follow-up
  needed here.
