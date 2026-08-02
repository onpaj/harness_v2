# Architecture review: raise the per-agent timeout default to 5400.0s

## Verdict

**Approved as designed.** This is a pure numeric-default change with no
structural surface. I verified every literal, line number, and mechanism the
plan/design cite against the current tree — all match exactly. No invariant
in `CLAUDE.md` governs a default timeout value, and none is put at risk by
this change.

## What I checked against the live tree

```
src/harness/cli.py:2139:    run.add_argument("--agent-timeout", type=float, default=1800.0, dest="agent_timeout")
src/harness/app.py:355:    agent_timeout: float = 1800.0,
src/harness/behaviors/agent.py:37:        timeout: float = 1800.0,
tests/test_cli.py:654,669: test_run_defaults_agent_timeout_to_1800 / assert ... == 1800.0
tests/test_agent_behavior.py:148: assert call["timeout"] == 1800.0
tests/test_scheduled_trigger.py:23: unrelated hour-bucket comment
tests/test_triggers_port.py:10: unrelated parse_interval("30m") duration fact
CHANGELOG.md:569,573: the 600->1800 precedent entry (PR #30)
```

All exactly as the design describes — the three production sites, the two
test sites in scope, and the two coincidental-reuse sites correctly excluded.

The override resolution the design relies on is confirmed live in
`app.py:617-618`:

```python
effective_timeout = (
    spec.timeout if spec.timeout is not None else agent_timeout
)
```

`spec.timeout` is `AgentSpec.timeout: float | None = None`
(`src/harness/ports/agent.py:30`) — the per-step override from PR #30 is
untouched by this change, exactly as FR-2 requires. `tests/test_architecture.py`
has no reference to `timeout` at all, so this change cannot trip any of the
architecture-guard tests (import-boundary checks, branch-on-outcome checks,
etc.) — there is nothing in that file to trip.

## Invariant alignment

Walking the invariants that could plausibly be implicated by a change inside
`app.py`/`behaviors/agent.py`/`cli.py`:

- **#13 (agent lives behind `AgentRunner`)** — untouched. This changes a
  timeout *value* passed into existing construction sites, not how
  `ClaudeCliBehavior` talks to `AgentRunner`. No new subprocess/CLI-flag
  knowledge enters the behavior.
- **#14 (persona is data, not code)** — untouched. No branch on step/agent
  name is introduced; `agents/<step>.json`'s `"timeout"` key remains the only
  per-persona lever, and `_write_default_agents` keeps writing `null` for it.
- **Module map / layering** — the three edit sites (`cli.py`, `app.py`,
  `behaviors/agent.py`) are exactly where these defaults already live; no new
  file, no new import, no new cross-layer dependency. `dispatcher.py`,
  `consumer.py`, `router.py` are untouched, as both plan and design commit to.
- **Invariant #1 (driver behind a port, wiring only in `app.py`)** — holds:
  `agent_timeout` stays a plain constructor float threaded through `app.py`'s
  existing wiring path; no port shape changes.

No invariant requires a specific timeout value or forbids raising this one.
This is a bounded, reversible configuration change (a single-line revert per
site restores 1800.0), consistent with the finding's explicit framing as a
tuning problem rather than a harness defect.

## Scope discipline

The plan/design correctly reject the step-decomposition lever for this pass
(documented as a future option, not this task's job) and correctly keep
`AgentSpec.timeout`/`agents/<step>.json` as the belt-and-suspenders per-repo
escape hatch if 5400.0s still proves tight for a specific repository. Nothing
in the design reaches into `dispatcher.py`, `consumer.py`, `router.py`, or
any port surface — the acceptance criterion "configuration/step-definition
only; no orchestration code modified" is structurally satisfiable by the
three-site edit as scoped.

## Guidance for the development step

1. Change the three literals (`cli.py:2139`, `app.py:355`,
   `behaviors/agent.py:37`) from `1800.0` to `5400.0` together, in one commit.
2. Update `tests/test_cli.py::test_run_defaults_agent_timeout_to_1800` and
   `tests/test_agent_behavior.py:148` to assert `5400.0`. Do not touch
   `tests/test_scheduled_trigger.py:23` or `tests/test_triggers_port.py:10` —
   both confirmed unrelated reuses of the number `1800`.
3. Add a `CHANGELOG.md` entry, `fix:`-prefixed (patch bump), mirroring the
   PR #30 entry's shape.
4. Run `.venv/bin/pytest -q` — expect `test_architecture.py` to pass
   unmodified, since nothing it inspects changes shape.

```json
{"outcome": "done", "summary": "Verified every literal, line number and mechanism (cli.py:2139, app.py:355, behaviors/agent.py:37, the AgentSpec.timeout override at app.py:617-618, and the two in-scope vs two out-of-scope test sites) cited by the plan/design against the live tree — all match exactly. This is a pure numeric-default change touching only the three existing constructor/argparse defaults; no CLAUDE.md invariant governs or is put at risk by a timeout value, no orchestration file (dispatcher/consumer/router) is touched, and test_architecture.py has no reference to timeout so no architecture guard is implicated. Approved as designed; wrote .artifacts/tsk_a3233efcf2c443ac/architecture-01.md with implementation guidance for the development step."}
```
