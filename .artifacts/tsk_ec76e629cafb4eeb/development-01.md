# Development — triage Process: `claimed_label` + the `label-issue` finisher

## Summary

Implemented plan-01/design-01/architecture-01 in full, including all four
architecture corrections, plus one correctness bug found during
implementation that none of the three prior steps caught (see "Deviation from
the design" below). All 1243 tests pass (`pytest -q`); a manual smoke run of
`harness init`/`harness run` (both with and without `GITHUB_TOKEN`) confirms
the CLI still starts cleanly.

## FR-0 — merged onto `origin/main`

The worktree was 75 commits behind. `git merge origin/main --no-edit`
completed with no conflicts (only this task's own `.artifacts/*.md` files
existed locally); full suite was green before any further edit.

## FR-1 + FR-2 — `claimed_label` param + cross-process collision guard

- `src/harness/cli.py::_process_sources`'s `github_issues_factory` now reads
  `params.get("claimed_label", "harness:queued")` alongside `label`, with a
  shared type guard rejecting a non-string value for either
  (`ProcessValidationError(field="params")`).
- `src/harness/drivers/fs_processes.py::FilesystemProcessRepository.build()`
  gained a `default_github_issues_label` keyword (threaded from
  `args.github_label` in `cli.py`, per architecture Correction 3 — the
  omitted-`label` default is the CLI's configurable flag, not a literal
  `fs_processes.py` can hardcode) and a same-pass collision check: every
  `github-issues` file's `label`/`claimed_label` is collected into one `seen`
  map, and a value reused by two different files fails the whole build,
  naming both. Implemented as architecture's simpler alternative (re-parsing
  each file's raw JSON a second time) rather than design's `_build_one`
  tuple-return change — smaller surface area, `_build_one`'s signature is
  untouched.
- Module docstring documents the residual footgun (static, exact-string only)
  and that `FilesystemProcessAdmin.write` can't run this check.
- Tests: `tests/test_cli.py` (claimed_label threading, default-fallback,
  non-string rejection) and `tests/test_fs_processes.py` (four new tests:
  label collision, claimed_label collision, clean triage pairing, and the
  omitted-`label`-uses-the-CLI-default case Correction 3 called out).

## FR-3 — structured `finishers` binding

- `src/harness/models.py`: new frozen `FinisherBinding(kind: str, config:
  dict = {})`; `Workflow.finishers: dict[str, FinisherBinding]`;
  `finisher_for` returns `FinisherBinding | None`. No `str`-shim (confirmed,
  per architecture Correction 4, zero production callers of `finisher_for`).
- `src/harness/drivers/fs_workflows.py::_parse_workflow` accepts either a
  plain string (`"open-pr"` → `FinisherBinding(kind="open-pr")`) or an object
  (`{"kind": ..., ...}` → everything but `"kind"` becomes `config`), exactly
  as designed.
- Updated the two existing tests architecture flagged as asserting the old
  bare-string shape (`test_models.py::test_workflow_finisher_for_reads_configured_kind`,
  `test_fs_workflows.py::test_finishers_are_parsed_and_exposed`) in the same
  commit as the `models.py` change. Added: structured-form parse test,
  missing-kind-in-object-form test, and an admin round-trip test
  (`test_fs_workflow_admin.py`) proving the object form survives
  `write_raw`/`read_raw` unmodified.

## FR-4 — factory-shaped finisher registry + `LabelIssueBehavior`

- `src/harness/drivers/label_issue.py` (new — **`drivers/`, not
  `behaviors/`**, per architecture Correction 1: it depends on `GithubClient`,
  a driver with no port, and `test_behaviors_import_only_ports_not_drivers`
  forbids that import from `behaviors/`; `drivers/` → `drivers/` is already
  precedented by `github_issues_check.py`). `LabelIssueBehavior.run()` runs
  the wrapped `inner` behavior, then — if `task.data.source` is present and
  the returned `Outcome.value` has a mapped label — calls
  `GithubClient.add_label`. A missing `data.source` or an unmapped outcome is
  a no-op with a note appended to `summary`; `outcome`/`data` always pass
  through untouched (routing is never affected).
- `src/harness/app.py::build()`: the finisher registry is now `dict[str,
  Callable[[str, dict, Callable[[], ConsumerBehavior]], ConsumerBehavior]]`
  (kind → factory). `step_bindings` (was `step_finishers`) compares whole
  `FinisherBinding`s across served workflows, not just kind strings, per
  design's FR-4 acceptance criterion. `open-pr`'s factory is `lambda step,
  config, inner: landing` — unchanged observable behavior.
- `src/harness/cli.py::_run`: one `GithubClient` is now built (mirroring the
  five other independent constructions already in this function — Correction
  2) and threaded into both `_process_sources` (removing a redundant sixth
  construction there) and a new `finishers` dict registering `"label-issue"`
  only when a token exists; passed to `build(..., finishers=finishers or
  None)`.

### Deviation from the design: the "inner" argument had to become a lazy thunk

Design and architecture both settled on the finisher factory signature
`(step, config, inner: ConsumerBehavior)` — `inner` eagerly built by always
calling `_inner_behavior_for(step)` before checking whether any finisher is
even bound. Architecture ratified this as free because `ClaudeCliBehavior`/
`ResolveConflictBehavior.__init__` only assign constructor arguments.

That ratification missed one thing: **`catalog.get(step)` itself can raise**
(`AgentNotFound`), and it does, unconditionally, for the landing step — the
existing `_write_default_agents` deliberately never writes an agent file for
`LANDING_STEP` because `land` is normally *fully replaced* by `open-pr` and
was never expected to need one. Implementing the design literally broke the
pre-existing test `test_resolver_task_flows_through_resolve_and_land_to_done`
immediately (`AgentNotFound: agent 'land' does not exist`) — not a new test I
wrote, an existing one that was passing before this change.

Fix: `behavior_for` now passes a **zero-arg thunk** (`lambda:
_inner_behavior_for(step)`) instead of an already-built behavior. `open-pr`'s
factory ignores it, so it's never invoked and `catalog.get("land")` is never
called. `label-issue`'s factory calls `inner()` — which does need a real
catalog agent, and does have one, since `label-issue` is only ever bound to a
step (like `triage`) that has a persona configured. This is documented in
`app.py`'s `_inner_behavior_for`/`behavior_for` comments, ADR-0018, and
CLAUDE.md invariant #41 (rewritten to describe the lazy-thunk shape, not the
eager one the design docs assumed). Added two regression tests in
`test_app.py` making this explicit:
`test_finisher_factory_receives_step_config_and_a_lazy_inner_thunk` and
`test_finisher_that_never_calls_inner_never_triggers_catalog_lookup` (the
latter directly reproduces the shape that broke — a `land`-only-open-pr
workflow with a catalog that has no `land` entry).

- Updated `tests/test_app.py`'s two pre-existing `finishers=` call sites
  (`RecordingFinisher()` → `lambda step, config, inner: RecordingFinisher()`).
  Added: factory receives `(step, config, inner)` correctly; the two lazy-thunk
  regression tests above; a config-conflict test (same kind, different
  `config`, across two served workflows, fails at build).
- `tests/test_label_issue_behavior.py` (new): approve→label, reject→label,
  inner runs exactly as it would unbound, missing-`data.source`→no-op-with-note,
  unmapped-outcome→no-op-with-note, routing data (`result.data`) untouched.
- `tests/test_cli.py`: `label-issue` registered only with a token; the
  registered factory builds a real `LabelIssueBehavior` when called.
- `tests/test_triage_process_e2e.py` (new): the full acceptance-criteria e2e
  — a `triage` workflow/process pair on in-memory drivers, two issues scanned
  under `harness:triage`/claimed into `harness:validating`, one scripted
  `done` (→ `harness:todo`) and one `request_changes` (→
  `harness:needs-info`), both tasks reach `done/`, and a re-scan after
  claiming yields nothing new (no re-claim, since neither issue carries the
  scan label anymore).

## FR-5 + FR-6 — templates (documented, not seeded)

No `agents/`, `workflows/`, or `processes/` directories exist in this git
repo — they are runtime config under a deployment's `--root` (confirmed:
`harness init` writes them fresh at a machine-specific location, e.g.
`~/harness-root/`, never checked into source control). So "documented, not
seeded" here means markdown, not repo-committed JSON files:
`docs/superpowers/specs/2026-07-23-triage-process-design.md` (new) gives the
full `agents/triage.json`, `workflows/triage.json`, `processes/triage.json`
templates verbatim, matching the ratified corrections (no `"name"` key in the
agent template, a named workflow not a workflow-less step target). No
`harness init` code path was touched — confirmed by the full `test_cli.py`
suite passing unmodified.

## Docs

- `CLAUDE.md`: module map row gains `label_issue`; a new bullet under the
  detailed drivers list explains the `drivers/`-not-`behaviors/` placement;
  invariant #41 rewritten to describe the factory + lazy-thunk shape (not the
  eager shape originally designed); invariant #39 gains a clause on
  `claimed_label` and the collision guard; the `fs_processes.py` and
  `github_issues_check.py` bullets gain one clause each.
- `docs/adr/0018-finisher-registry-becomes-a-factory.md` (new): the
  ADR-0016-style record covering the factory shape, the `drivers/` placement
  exception, and — importantly — the lazy-thunk correction and why it was
  necessary (the design/architecture docs said it wasn't; this ADR is the
  place that now says it is, and why).
- `docs/superpowers/specs/2026-07-23-triage-process-design.md` (new): the
  full feature spec + templates, per FR-5/FR-6.
- `docs/superpowers/specs/2026-07-22-github-issues-action-design.md`: one-line
  forward pointer to the new spec from its "Completion check" section.

## Verification

```sh
.venv/bin/pytest -q
# 1243 passed, 1 skipped
```

Also manually smoke-tested (not part of the automated suite):
`harness init` + `harness run --agent dummy --forge fake` starts and shuts
down cleanly both with and without `GITHUB_TOKEN` set — confirming the new
`_run` wiring (the extra `GithubClient` construction, the `finishers` dict)
doesn't break the existing offline/online startup paths.

## Files changed

- `CLAUDE.md`, `docs/superpowers/specs/2026-07-22-github-issues-action-design.md`
- `src/harness/app.py`, `src/harness/cli.py`
- `src/harness/drivers/fs_processes.py`, `src/harness/drivers/fs_workflows.py`
- `src/harness/models.py`
- `src/harness/drivers/label_issue.py` (new)
- `docs/adr/0018-finisher-registry-becomes-a-factory.md` (new)
- `docs/superpowers/specs/2026-07-23-triage-process-design.md` (new)
- `tests/test_app.py`, `tests/test_cli.py`, `tests/test_fs_processes.py`,
  `tests/test_fs_workflow_admin.py`, `tests/test_fs_workflows.py`,
  `tests/test_models.py`
- `tests/test_label_issue_behavior.py` (new),
  `tests/test_triage_process_e2e.py` (new)
