# Review — JSON templates / predefined defaults for steps that require JSON input

## Verdict

**done.** Implementation matches `design-01.md`/`architecture-01.md` exactly,
including the architecture step's one correctness fix. Verified directly
against the diff (`909aa59..737c09a`), not just against `development-01.md`'s
narrative.

## What was checked

- **`git diff 909aa59..737c09a -- src/harness/cli.py`** read in full.
  `_agent_definition_template` (FR-1) is exactly the design's listing.
  `_write_default_agents` (FR-2) changes only the dict-construction line —
  loop, `LANDING_STEP` skip, exists-skip, write are byte-identical to before.
  `_agent_init` (FR-3) checks root → workflow (`WorkflowNotFound`) → `land`
  rejection → step-in-workflow, **and only then** calls
  `layout.agents.mkdir(...)` — confirmed the architecture's correctness fix
  (validate-before-mkdir) is actually implemented, not just claimed.
- **Argparse wiring**: `agent` subparser group with `init` action, mirroring
  the `service install|uninstall|status` pattern, placed after `run` and
  before `service` as specified.
- **`tests/test_cli.py` diff**: all 14 tests described in `development-01.md`
  are present and match their descriptions — known/unknown-step template
  content (FR-1), missing-file scaffold, existing-file-not-overwritten,
  `--force` overwrite, uninitialized-root failure, unknown-workflow failure,
  landing-step rejection (with an assertion that `agents/land.json` is never
  written), step-not-in-workflow rejection, the mkdir-ordering regression
  guard (`test_agent_init_rejected_call_does_not_create_an_empty_agents_dir`),
  and the FR-4 round-trip test through the real `FilesystemAgentCatalog`.
- **Full test suite**: ran `.venv/bin/pytest -q` — **483 passed, 1 skipped**,
  matching the claimed result exactly (the skip is the pre-existing opt-in
  `HARNESS_SMOKE_CLAUDE` test, unrelated). Also ran
  `tests/test_architecture.py tests/test_cli.py` directly — **64 passed** —
  confirming the architecture-invariant guards (no new imports from
  `dispatcher.py`/`consumer.py` into drivers, etc.) are untouched and green.
- **`CLAUDE.md` diff**: the `harness init` bullet is extended to document the
  new on-demand `harness agent init <step>` path, as planned (plan step 5).
- **Acceptance criteria from the task**, checked one by one:
  - "A step that requires JSON data can declare a template / default JSON
    value" — `_agent_definition_template` provides this for any step name,
    known or custom, deriving `allowed_outcomes` live from the workflow.
  - "Operator is presented with the template instead of an empty input" —
    `harness agent init <step>` writes the file and prints path + content to
    stdout, satisfying this without a GUI (correctly out of scope per
    invariant #5/#11, `api/` stays read-only).
  - "Predefined values are valid for the step... parse and satisfy
    expectations out of the box" — proven by the FR-4 round-trip test
    (`test_agent_init_round_trips_through_filesystem_agent_catalog`), which
    loads the written file through the real `FilesystemAgentCatalog.get`.
- **Scope discipline**: no changes to `dispatcher.py`, `consumer.py`,
  `router.py`, or any `drivers/` module beyond the already-imported
  `FilesystemWorkflowRepository`. `_submit`/`_write_default_repos` untouched,
  matching the plan's explicit exclusion of `submit --data` and `repos.json`.

## Non-blocking observations

None worth raising — the one thing the architecture step flagged as needing
a fix (mkdir ordering) was fixed and is covered by a dedicated regression
test.

```json
{"outcome": "done", "summary": "Implementation verified directly against the diff: FR-1 template extraction, FR-2 unchanged init behavior, FR-3 on-demand command with the architecture's validate-before-mkdir fix correctly applied, and FR-4 round-trip through FilesystemAgentCatalog are all present and correct. Full suite 483 passed/1 skipped confirmed by direct run; test_architecture.py guards untouched. All acceptance criteria met, no scope creep."}
```
