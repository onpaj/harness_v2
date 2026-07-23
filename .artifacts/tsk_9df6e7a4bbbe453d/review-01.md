# Review: raise and make configurable the per-step agent timeout

## Verification method

Read `plan-01.md`, `design-01.md`, `architecture-01.md`, `development-01.md`,
then verified the actual diff (`git show 71bc447`) against each rather than
trusting the development report's prose. Ran the full suite and the
architecture trip-wire directly.

## Conformance to spec (plan-01.md)

- **FR-1** (default 600.0 → 1800.0): confirmed in all three sites
  (`cli.py:769`, `app.py:212`, `behaviors/agent.py:34`), kept in lockstep. No
  stray `600.0` left anywhere relevant (`grep` finds only an unrelated
  `BUDGET_SECONDS = 600.0` constant in `test_smoke_claude.py`, a different
  knob). The `--help` AC ("shows the new default") isn't literally met since
  the argument has no `help=` string — but this was already true before this
  change (verified against the pre-change `cli.py`), so it's a pre-existing
  gap, not a regression introduced here, and not worth blocking on.
- **FR-2** (`AgentSpec.timeout: float | None = None`): present, appended
  last, matches the design's schema exactly. Round-trip and default tests
  added.
- **FR-3** (`agents/<step>.json` optional `"timeout"` key,
  `FilesystemAgentCatalog.get()`): implemented with the exact validation
  rule specified (reject `bool`, reject non-numeric, reject `<= 0`), same
  `AgentNotFound` message shape as `allowed_outcomes`. Tests cover valid,
  missing, explicit `null`, `true`, `0`, `-5`, and a non-numeric string.
- **FR-4** (`behavior_for` resolution): `spec = catalog.get(step)` bound
  once, `effective_timeout = spec.timeout if spec.timeout is not None else
  agent_timeout`, passed into `ClaudeCliBehavior`. Verified with a new
  `test_app.py` test using two steps (one overridden, one not) and asserting
  both resolved values via `harness.consumers`.
- **FR-5** (`_write_default_agents` template): `"timeout": None` added,
  covered by `test_init_writes_default_agents_with_null_timeout`.

## Adherence to architecture

Matches `architecture-01.md`'s implementation guidance point-for-point: no
new port, no touched invariant-guarded boundary, `AgentSpec` stays pure data
(invariant 14), resolution happens only in `app.py`'s wiring layer
(invariant 17). `tests/test_architecture.py` (14 tests) passes unchanged.

## Correctness

- `bool`-before-`int` guard is present and tested — the one real footgun
  identified in the design is handled correctly.
- `None`/absent-key backward compatibility preserved for every existing
  `agents/<step>.json` on disk.
- No dispatcher/consumer/router changes; no event/board/API surface change,
  consistent with the plan's declared scope.

## Test results

```
.venv/bin/pytest -q
```
481 passed, 1 skipped (unaffected opt-in `test_smoke_claude.py`) — matches
the development report exactly.

```
.venv/bin/pytest tests/test_architecture.py -q
```
14 passed.

## Verdict

Implementation is complete, correct, and conforms to the spec/architecture.
No functional requirement is missed, no invariant is violated, no required
test is missing. The `--help` default-display AC is technically unmet but
pre-existing and out of scope for this change — not blocking.
