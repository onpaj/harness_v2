# Development — JSON templates / predefined defaults for steps that require JSON input

## Summary

Implemented exactly as specified in `design-01.md` / `architecture-01.md`,
applying the architecture step's one correctness fix. All changes are inside
`src/harness/cli.py` plus its test file and `CLAUDE.md`; no new module, no new
runtime dependency, no change to `dispatcher.py`/`consumer.py`/`router.py`/any
`drivers/` module.

## Files changed

- **`src/harness/cli.py`**
  - Added `_agent_definition_template(step, allowed_outcomes) -> dict` (FR-1):
    the single function that computes a full, valid `AgentSpec` JSON dict for
    a step — known steps (`plan`/`design`/`architecture`/`development`/`review`)
    get their `AGENT_PERSONAS` entry, any other step name gets the existing
    generic fallback (`_agent_persona`/`_agent_tools`, unchanged).
  - Rewired `_write_default_agents` (FR-2) to call `_agent_definition_template`
    instead of building the dict inline. Loop, `LANDING_STEP` skip,
    exists-skip and the write are byte-for-byte unchanged — confirmed by the
    pre-existing `test_init_creates_layout_and_default_workflow` and
    `test_init_is_idempotent_and_keeps_edits` passing unmodified.
  - Added `_agent_init(args) -> int` (FR-3): the handler for the new
    `harness agent init <step>` command. Checks run in this order, per the
    architecture step's correctness fix — **all validation before any
    filesystem write**:
    1. root initialized (`layout.tasks.is_dir()`) — same message as `_submit`.
    2. workflow resolves (`FilesystemWorkflowRepository.get`, catches
       `WorkflowNotFound`) — same handling as `_init`.
    3. `step != LANDING_STEP` — explicit rejection with a clear message
       (scaffolding `agents/land.json` would produce a file the dispatcher
       never reads, per invariant #12).
    4. `step in workflow.steps()` — rejects a step that isn't part of the
       named workflow.
    Only after all four pass does it `mkdir` the `agents/` directory (fixing
    the ordering the architecture review flagged: a rejected call for `land`
    or an unknown step no longer leaves a stray empty `agents/` behind).
    Existing file + no `--force` → prints "already exists" notice plus the
    current content, exit 0, file untouched. Missing file, or `--force` →
    writes the fresh template, prints path + content, exit 0.
  - Added the `agent` subparser group (`agent init <step> [--root] [--workflow]
    [--force]`) in `main()`, wired the same way `service install/uninstall/status`
    already are, placed after `run` and before `service`.
  - New import: `FilesystemWorkflowRepository` from `harness.drivers.fs_workflows`
    (already used elsewhere in the codebase; no new dependency).

- **`tests/test_cli.py`** — 14 new tests, following the existing `main([...])`
  + `tmp_path` + `capsys` pattern (no new fakes):
  - `test_agent_definition_template_known_step_uses_its_persona` — FR-1,
    verifies `_agent_definition_template("review", …)` returns
    `_REVIEW_PERSONA` verbatim, its tool list, and the passed-in outcomes.
  - `test_agent_definition_template_unknown_step_gets_generic_fallback` —
    FR-1, generic branch.
  - `test_agent_init_creates_missing_file` — FR-3, happy path on a custom
    workflow with an unknown step name (`triage`), asserts file content and
    stdout.
  - `test_agent_init_leaves_existing_file_untouched_without_force` — FR-3
    idempotency.
  - `test_agent_init_force_overwrites_an_existing_file` — FR-3 `--force`.
  - `test_agent_init_without_init_fails_cleanly` — uninitialized root.
  - `test_agent_init_unknown_workflow_fails_cleanly` — `WorkflowNotFound`.
  - `test_agent_init_rejects_the_landing_step` — `land` hard-errors, and
    confirms `agents/land.json` is never written.
  - `test_agent_init_rejects_a_step_not_in_the_workflow` — step-not-in-workflow.
  - `test_agent_init_rejected_call_does_not_create_an_empty_agents_dir` —
    regression guard for the architecture step's correctness fix: a rejected
    call must not create `agents/` as a side effect.
  - `test_agent_init_round_trips_through_filesystem_agent_catalog` — FR-4:
    the written file is read back through the real
    `FilesystemAgentCatalog.get`, no exception, correct `prompt`/
    `allowed_outcomes`.
  - A small `_write_custom_workflow_with_triage_step` helper backs the
    workflow-scoped tests (custom workflow whose only step, `triage`, is not
    in `AGENT_PERSONAS`; transitions use real `Outcome` values `done`/
    `request_changes` so the round-trip test parses cleanly through
    `FilesystemAgentCatalog`, which only accepts `Outcome` enum members).
  - New imports: `_REVIEW_PERSONA`, `_agent_definition_template` from
    `harness.cli`; `FilesystemAgentCatalog` from `harness.drivers.fs_agents`;
    `Outcome` from `harness.models`; `shutil` (stdlib, for the regression
    guard).

- **`CLAUDE.md`** — extended the existing `harness init` bullet under "What
  is responsible for what" to mention the new on-demand
  `harness agent init <step>` path, keeping the doc in sync with behavior
  (plan step 5).

## How to verify

```sh
.venv/bin/pytest -q
```

483 passed, 1 skipped (the skip is the pre-existing opt-in
`HARNESS_SMOKE_CLAUDE` test, unrelated to this change).

Manual smoke test (also run during development):

```sh
cd /tmp && mkdir demo && cd demo
python -m harness.cli init --root .
python -m harness.cli agent init development --root .   # existing file → prints, unchanged
python -m harness.cli agent init triage --root .         # not in default workflow → error, exit 2
python -m harness.cli agent init land --root .           # landing step → error, exit 2
```

All three failure paths print a clear `error: ...` to stderr and leave the
filesystem exactly as before the call (no stray `agents/land.json`, no stray
`agents/triage.json`).

## Notes / deviations from the design docs

None beyond the one deliberate fix the architecture step called for
(validation-before-`mkdir` ordering in `_agent_init`), which is implemented
exactly as instructed and covered by
`test_agent_init_rejected_call_does_not_create_an_empty_agents_dir`.
