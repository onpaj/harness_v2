# Design: raise the per-agent timeout default from 1800s to 5400s

No UI section — this task has no user-facing surface. It changes three numeric
constructor/argparse defaults inside the harness's own process; nothing in
`api/`, `BoardView`, or `ArtifactView` is touched (per the plan's "Interfaces"
section — timeout is a construction-time input to a behavior, invisible to
every read-side UI port).

## Decision: the new default value is `5400.0` seconds (90 minutes)

The plan deliberately left the exact number to this step. Picking it:

- **Precedent shape.** The one prior tuning pass for this exact symptom (PR
  #30, `71bc447`) moved the ceiling 600s → 1800s — a 3x jump — and that fully
  resolved the same failure mode on this deployment (`tsk_2631964987af47b8`:
  two `timed out after 600.0s` attempts, then `done` once the 1800s default
  shipped). Applying the same proportional jump gives `1800 * 3 = 5400`.
- **Fit against the one clean data point.** The failing task's own scope
  (splitting a 33-method interface into six, repointing 21 consumers, editing
  ~13 test files) ran into the 1800s wall with no visibility into how far past
  it the turn would have needed to go. The three steps immediately before it
  on the *same* task (`plan` 11m, `design` 12m, `architecture` 32m) show this
  workflow's non-`development` steps already spend up to ~32 minutes on
  comparable analytical depth without touching any files; a mechanical edit
  turn touching ~35 files is a heavier-weight task than any of those three,
  so a budget with real margin above the 30-minute mark it just failed at is
  the target, not a token bump to e.g. 2400s.
- **Bounded, not unbounded.** The plan's own non-functional requirement warns
  against widening the ceiling open-endedly, since a higher timeout delays
  detecting a genuinely hung agent. 5400s keeps a hang boundable (90 minutes,
  not hours) while giving 3x the room the failing turn ran out of — consistent
  with the confounded-but-directionally-consistent signal that many
  `development` steps on this same harness's own repository (a comparable or
  larger codebase) run long relative to 1800s already (`tsk_847b9cdf580e4fe1`,
  `tsk_ec76e629cafb4eeb`, `tsk_60ba6c45666647cd`), without reaching for a value
  large enough to swallow that fully queue-confounded upper range as if it were
  pure agent wall-clock.
- **Escape hatch stays available.** `AgentSpec.timeout` (PR #30) is untouched.
  If 5400s still proves tight for a specific repository's `development` step,
  the operator sets an explicit `"timeout"` in that repo's
  `agents/development.json` without another code change — this design doesn't
  need to get the number perfect for every repository, only raise the shared
  default to a value that fits the observed failure with real margin.

## Component design

No new components, ports, or behaviors. Three existing default-value sites
move together, so no caller that omits the keyword silently keeps the old
number:

| Site | Current | New |
|---|---|---|
| `src/harness/cli.py:2139` — `--agent-timeout` argparse `default=` | `1800.0` | `5400.0` |
| `src/harness/app.py:355` — `build(..., agent_timeout: float = ...)` | `1800.0` | `5400.0` |
| `src/harness/behaviors/agent.py:37` — `ClaudeCliBehavior.__init__(..., timeout: float = ...)` | `1800.0` | `5400.0` |

Resolution order is unchanged: `behavior_for` in `app.py` still computes
`spec.timeout if spec.timeout is not None else agent_timeout` — only the
right-hand fallback value changes. `_write_default_agents` keeps emitting
`"timeout": null` for freshly generated `agents/<step>.json` files, so a
`null` step continues to mean "inherit the global default," which is now
5400.0 instead of 1800.0.

No change to `ClaudeCliBehavior`'s timeout-handling logic, `AgentError`
construction, or the consumer's failure path (invariant #3 governs this
unchanged: an expired agent still raises, the consumer still settles the task
to `failed/`).

## Data / config schema

Nothing new is introduced; the shape of every existing schema element is
unchanged — only the numeric default each one falls back to moves.

```python
# src/harness/ports/agent.py — unchanged
@dataclass(frozen=True)
class AgentSpec:
    ...
    timeout: float | None = None   # None = inherit the run's global default (now 5400.0)
```

```json
// agents/<step>.json on disk — unchanged shape
{
  "...": "...",
  "timeout": null
}
```

```
# CLI surface — unchanged flag, new default surfaced in --help
harness run --agent-timeout SECONDS   # default now 5400.0
```

## Test and doc surfaces that encode the old default

These are the literal sites the plan's FR-3 flags as needing to track the new
number (identification only — sequencing the edits is the development step's
job):

- `tests/test_cli.py::test_run_defaults_agent_timeout_to_1800` — asserts
  `captured["agent_timeout"] == 1800.0`; must assert `5400.0` against the new
  default (rename optional, the assertion value is not).
- `tests/test_agent_behavior.py:148` — asserts `call["timeout"] == 1800.0`;
  must assert `5400.0`.
- `CHANGELOG.md` — a new entry recording the bump, `fix:`-prefixed per this
  repo's conventional-commit release convention (patch bump, matching how the
  600→1800 change was itself released).
- Values that are *not* in scope and must not change: `tests/test_scheduled_trigger.py:23`'s
  `1800s` (an hour-bucket comment for cron/interval bucketing, unrelated to
  agent timeout) and `tests/test_triggers_port.py:10`'s `parse_interval("30m") == 1800.0`
  (a duration-string parsing fact, also unrelated). Both were checked and
  confirmed to be coincidental reuses of the same number, not the default this
  task changes.

## Non-functional / rollout

Unchanged from the plan: no orchestration code (`dispatcher.py`, `consumer.py`,
`router.py`, or any behavior's decision logic) is touched — only the three
numeric defaults. The live deployment's `agents/development.json` has
`"timeout": null`, so the higher default takes effect there automatically on
the next `harness update` with no manual file edit.

```json
{"outcome": "done", "summary": "Picked 5400.0s (90 min, a 3x jump matching the 600->1800 precedent) as the new shared per-agent timeout default, identified the exact three literal sites to change together and the exact test/changelog sites to update in step, and confirmed the two other 1800-valued test sites are unrelated (cron bucketing, duration parsing) and out of scope. Wrote .artifacts/tsk_a3233efcf2c443ac/design-01.md."}
```
