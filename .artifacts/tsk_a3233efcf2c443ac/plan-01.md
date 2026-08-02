# Plan: raise the per-agent timeout ceiling that starved the `development` step

## Summary

Task `tsk_2302af2e59ae4971` (workflow `development`, repo `Anela.Heblo`) completed
`plan` (11m), `design` (12m) and `architecture` (32m) cleanly, then failed on
`development` with `behavior raised an exception: claude timed out after
1800.0s` while implementing a legitimately large refactor (splitting a
33-method interface into six, repointing 21 consumers and ~13 test files). The
harness behaved correctly — this is an operational/tuning problem, not a
harness defect (invariant #3: the consumer settled the task to `failed/`
exactly as it should). We raise the per-agent timeout ceiling that governs the
`development` step so a turn of this scale fits inside its budget.

## Context

This is not a new problem shape. `agents/agent.py`'s per-agent timeout has
been raised once before by this same project, in exactly this same way:
commit `71bc447` / PR #30 (`56a50b8`, "configurable per-step agent timeout,
default raised to 1800s") took the ceiling from 600s to the current 1800s, and
*also* added a per-step override (`AgentSpec.timeout`, read from an optional
`"timeout"` key in `agents/<step>.json`) precisely so a slow step didn't force
every step onto the same budget. That machinery already exists and is
unaffected by this change — this plan is the same lever pulled a second time,
because 1800s has again proven tight for the `development` step specifically.

The failure is not an isolated data point. The harness's own history
(`archived/*.json` and `done/*.json` on the live deployment) shows:

- The three steps preceding the failed `development` attempt (`plan`,
  `design`, `architecture`) — on the *same* task, comparable complexity — took
  11–32 minutes each. A `development` turn asked to mechanically touch ~35
  files (6 new interfaces, 6 physically-split repository classes, 21
  consumer repoints, ~13 test files) is squarely in that same "tens of
  minutes" bracket, and 1800s (30 min) left no margin.
- Many `development` steps on this same harness_v2 repository — a codebase of
  comparable or greater size/complexity — show wall-clock times well past
  1800s while still finishing `done` (several in the 1–3+ hour range,
  e.g. `tsk_847b9cdf580e4fe1`, `tsk_ec76e629cafb4eeb`, `tsk_60ba6c45666647cd`).
  This number is not a clean measurement of pure agent wall-clock — it is
  measured from "dispatcher hands the task to `development`" to "consumer
  finishes it," which also includes any time the task spent queued behind
  another task in the same step (this workflow leaves `maxParallel` for
  `development` at its default of 1 — see `CLAUDE.md`'s "A step's concurrency
  is workflow config" section). It is nonetheless a consistent signal that a
  30-minute ceiling is tight for this repository's `development` step, on top
  of the one clean, unconfounded signal we do have: the exact failure message
  itself.
- The harness's own history also already contains an earlier live occurrence
  of the previous ceiling (600s) being hit and then succeeding once the
  600→1800 change rolled out (`tsk_2631964987af47b8`: two `"timed out after
  600.0s"` attempts, then `done` on the third) — i.e., this exact
  raise-the-ceiling remedy has already worked once on this deployment for the
  same symptom.

A prior heal/dedup pass on this same failure (task `tsk_5f8d3b9c8e824581`,
commits `bc1ddbc`/`78a19c5`/`8707c79`/`44e1282`) already confirmed there is no
duplicate open issue and scoped this strictly as a configuration change, with
two levers: raise the timeout, or decompose the step. That scoping is carried
into this plan unchanged.

## Functional requirements

**FR-1 — Raise the global per-agent timeout default.**
The default effective timeout for every agent-driven step (in particular
`development`, which has no per-step override set) rises from the current
`1800.0` seconds. The exact new value is intentionally **not prescribed
here** (per the filed finding) — the design step picks it, informed by the
data above and by the observed durations of *this repository's* own
`development` runs.
- AC: a fresh `harness run` invoked without `--agent-timeout` gives every
  agent step the new default before `AgentError` fires.
- AC: `harness run --help` shows the new default.
- AC: the three sites that currently all read `1800.0` are changed together,
  so none of them silently keeps the old value if constructed without an
  explicit keyword:
  - `src/harness/cli.py:2139` — `--agent-timeout` argparse default
  - `src/harness/app.py:355` — `build(..., agent_timeout: float = 1800.0)`
  - `src/harness/behaviors/agent.py:37` — `ClaudeCliBehavior.__init__(...,
    timeout: float = 1800.0)` (defensive fallback only; `build()` always
    passes an explicit value, but callers that construct the behavior
    directly, e.g. tests, would otherwise silently disagree)

**FR-2 — No change to the per-step override mechanism.**
`AgentSpec.timeout` / `agents/<step>.json`'s optional `"timeout"` key
(shipped by PR #30) is untouched and remains the escape hatch for an operator
who wants `development` (or any single step) on a different budget than the
rest of the workflow, without another code change. `_write_default_agents`
keeps writing `"timeout": null` into freshly generated agent files — `null`
still means "inherit the (now higher) global default," unchanged.
- AC: an existing `agents/development.json` with `"timeout": null` on a
  deployed harness inherits the new, higher global default automatically once
  the harness package is upgraded — no manual file edit required.
- AC: a step whose JSON already sets an explicit numeric `"timeout"`
  continues to use that value, unaffected by the global default change.

**FR-3 — Tests and docs stay in sync with the new number.**
Every test and doc reference to the literal `1800.0` as *the* default (as
opposed to references to the old 600→1800 migration, which stay historical)
is updated to the new value.
- AC: `tests/test_cli.py::test_run_defaults_agent_timeout_to_1800` (or its
  renamed/updated equivalent) asserts the new default.
- AC: `tests/test_agent_behavior.py` and any other test asserting
  `timeout == 1800.0` as the current default are updated.
- AC: `CHANGELOG.md` gets an entry for the bump (the release pipeline is
  driven by conventional-commit prefixes — see Dependencies below).

## Non-functional requirements

- **Risk: a higher ceiling delays detecting a genuinely hung agent.** Raising
  the timeout trades faster failure detection for headroom on legitimately
  large turns. This was already an accepted trade-off in the 600→1800 change;
  widen it consciously, not open-endedly — the design step should pick a
  value generous enough for this repository's largest realistic
  `development` turn without being unbounded. Since `AgentRun` now records
  token usage and cost per attempt (ADR-0020), a stuck run at the new ceiling
  is at least visible after the fact via cost, even though the timeout itself
  doesn't shrink to catch it sooner.
- **No new port surface, no orchestration change.** This stays entirely
  inside existing config-default constants (`cli.py`/`app.py`/
  `behaviors/agent.py`) and the already-shipped `AgentSpec.timeout` override.
  No change to `dispatcher.py`, `consumer.py`, `router.py`, or any behavior's
  logic — only the numeric defaults they're constructed with.
- **Zero-touch rollout for the live deployment.** Because
  `/Users/rem/harness-root/agents/development.json`'s `"timeout"` is
  currently `null`, the fix takes effect there automatically the next time
  `harness update` runs (the `com.harness.autoupdate` launchd job, every 30
  minutes) — no manual edit of that machine-local, uncommitted file is
  required for the acceptance criteria to hold in practice.

## Data model

No task/queue/event model changes. The only "data" this touches is
configuration:

```python
# src/harness/ports/agent.py (unchanged by this task)
@dataclass(frozen=True)
class AgentSpec:
    ...
    timeout: float | None = None   # None = inherit the run's global default
```

```json
// agents/<step>.json on disk (unchanged shape; only the inherited default changes)
{
  "...": "...",
  "timeout": null
}
```

The three duplicated numeric literals (`cli.py`, `app.py`, `behaviors/agent.py`)
are the only "data" whose value moves in this task.

## Interfaces

- CLI: `harness run --agent-timeout SECONDS` — unchanged flag, new default.
- File format: `agents/<step>.json`'s optional `"timeout"` key — unchanged,
  still the per-step override surface.
- No API (`api/`), event, or board projection changes — timeout is a
  construction-time input to a behavior, invisible to `BoardView`/`ArtifactView`.

## Dependencies and scope

**Rests on**: the per-step timeout override shipped by PR #30 (commit
`71bc447`) — `AgentSpec.timeout`, `FilesystemAgentCatalog`'s validation of the
optional `"timeout"` key, and `app.py`'s `behavior_for` resolution
(`spec.timeout if spec.timeout is not None else agent_timeout`). None of that
is modified here.

**In scope**: the three duplicated `1800.0` literals
(`cli.py`, `app.py`, `behaviors/agent.py`), the tests that assert them, and
`CHANGELOG.md`.

**Out of scope**:
- Decomposing the `development` step into smaller units (the finding's second
  lever). Rejected for this pass: it would mean splitting one workflow step
  into several, each needing its own persona and workflow edges — a
  materially larger change than a config-default bump, for a problem this
  plan's data suggests the simpler lever already resolves (it did once
  before, for the same symptom). Left as a documented option if a future
  occurrence shows the new ceiling is still insufficient.
- Per-attempt or dynamic timeouts (e.g., a longer timeout on a
  `request_changes` retry). Only the static default changes.
- A per-repository timeout axis. The existing per-step override
  (`agents/development.json`'s `"timeout"` key) already lets an operator give
  one specific deployment's `development` step its own value without a code
  change, if the new global default still isn't enough for a particular repo.
- Any change to what happens on expiry (still `AgentError` → task fails via
  the existing consumer error path, per invariant #3).
- Instrumenting a clean, queue-wait-free measurement of per-agent wall-clock
  time (noted as a data gap in Context). Worth a future, separate
  improvement for making the *next* tuning pass data-driven instead of
  reasoning from confounded step-to-step timestamps, but not needed to ship
  this fix.

## Rough plan

1. **Design** (next step): pick the new default value, informed by this
   plan's data (the failed task's own step timings, and this repository's
   historical `development` durations) — not prescribed here.
2. Raise the literal in `src/harness/cli.py`'s `--agent-timeout` argparse
   default (currently line 2139).
3. Raise the literal in `src/harness/app.py`'s `build(agent_timeout=...)`
   default (currently line 355).
4. Raise the literal in `src/harness/behaviors/agent.py`'s
   `ClaudeCliBehavior.__init__(timeout=...)` default (currently line 37).
5. Update every test asserting the old default as *the current* default
   (`tests/test_cli.py`, `tests/test_agent_behavior.py`, and any other site
   `grep -n "1800"` surfaces at development time that isn't historical/
   migration commentary).
6. Add a `CHANGELOG.md` entry; commit as `fix:` (this corrects an
   under-provisioned operational default, matching the repo's
   conventional-commits release convention — a patch bump, not a feature).
7. Run the full suite (`.venv/bin/pytest -q`) including
   `tests/test_architecture.py`'s invariant checks (unaffected, but must
   stay green) before finishing.

## Open questions

- **Exact new default value.** Deliberately left to the design step, per the
  finding's explicit instruction not to prescribe one. The data in Context
  supports something meaningfully larger than 1800s; the previous bump was a
  3x jump (600→1800) and resolved the same symptom once — a similar-order
  jump is a reasonable starting point for design to evaluate, not a
  prescription.
- **Whether the live deployment's `agents/development.json` should also get
  an explicit `"timeout"` override** as a belt-and-suspenders measure ahead
  of the next scheduled auto-update. This is a machine-local, uncommitted
  operational action outside this repository and this PR's scope (see
  `repos.json`'s "machine-specific, uncommitted" convention) — flagged for
  the operator to consider independently, not a deliverable of this task.
