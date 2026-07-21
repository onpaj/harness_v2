# Development: raise and make configurable the per-step agent timeout

Implemented exactly as specified in `architecture-01.md` (which itself
verified `design-01.md` and `plan-01.md` against the real source). No
deviations.

## Changes

**`src/harness/ports/agent.py`**
- `AgentSpec` gets a new trailing field `timeout: float | None = None`.
  `None` means "inherit the harness-wide default"; a step file can opt into
  a longer/shorter timeout without touching any other step.

**`src/harness/drivers/fs_agents.py`**
- `FilesystemAgentCatalog.get()` now parses an optional `"timeout"` key.
  Validation (fail fast via `AgentNotFound`, same message shape as the
  existing `allowed_outcomes` check):
  - absent or `null` → `None` (inherit)
  - `bool` → rejected explicitly (Python's `bool` is an `int` subclass;
    without this guard `"timeout": true` would silently become `1.0`s)
  - non-numeric → rejected
  - `<= 0` → rejected
  - otherwise coerced to `float`
- Module docstring's example schema updated to show `"timeout": null`.

**`src/harness/app.py`**
- `build()`'s `agent_timeout` parameter default raised `600.0` → `1800.0`.
- `behavior_for(step)`: binds `spec = catalog.get(step)` once (was inlined),
  computes `effective_timeout = spec.timeout if spec.timeout is not None
  else agent_timeout`, and passes `timeout=effective_timeout` into
  `ClaudeCliBehavior`. This is the resolution rule: a step's own `timeout`
  wins, otherwise the harness-wide `--agent-timeout` applies.

**`src/harness/behaviors/agent.py`**
- `ClaudeCliBehavior.__init__`'s `timeout` parameter default raised
  `600.0` → `1800.0` (defensive fallback only — `build()` always passes
  `timeout=` explicitly; this matters for direct construction, e.g. in
  tests).

**`src/harness/cli.py`**
- `--agent-timeout` CLI flag default raised `600.0` → `1800.0`.
- `_write_default_agents()` now writes `"timeout": null` into every fresh
  `agents/<step>.json` so the on-disk schema is self-documenting from
  `harness init` onward.

## Tests

- `tests/test_agent_ports.py` — `AgentSpec` default (`timeout is None`) and
  explicit construction (`timeout=120.0` round-trips).
- `tests/test_fs_agents.py` — valid numeric `"timeout"` round-trips, missing
  key → `None`, explicit `null` → `None`, `"timeout": true` →
  `AgentNotFound`, `0` / negative → `AgentNotFound`, non-numeric string →
  `AgentNotFound`.
- `tests/test_app.py` — new
  `test_behavior_for_uses_spec_timeout_override_else_agent_timeout`: builds
  a harness with a `MemoryAgentCatalog` where one step's spec sets
  `timeout=45.0` and another doesn't, passes `agent_timeout=900.0` to
  `build()`, and asserts (via `harness.consumers`, matched by
  `consumer.actor`) that the step with an override got `45.0` and the step
  without got the fallback `900.0`.
- `tests/test_cli.py` — new tests:
  - `test_init_writes_default_agents_with_null_timeout`: `harness init`
    writes `"timeout": null` into `agents/development.json`.
  - `test_run_defaults_agent_timeout_to_1800`: `harness run` with no
    `--agent-timeout` flag calls `build(agent_timeout=1800.0)` (`build`/
    `serve` monkeypatched to capture the call instead of actually running).
  - `test_run_accepts_explicit_agent_timeout`: `--agent-timeout 60` flows
    through to `build(agent_timeout=60.0)`.
- `tests/test_agent_behavior.py` — fixed a pre-existing test
  (`test_runs_agent_in_worktree_cwd_with_spec`) that asserted the old
  `600.0` default when constructing `ClaudeCliBehavior` without an explicit
  `timeout=`; updated to `1800.0` to match the new default.

## Verification

Ran the full suite (a `.venv` didn't yet exist in this worktree; created one
with `python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"` per the
project's documented dev setup — it's gitignored, not part of the diff):

```
.venv/bin/pytest -q
```

Result: **481 passed, 1 skipped** (the skip is the opt-in
`tests/test_smoke_claude.py`, gated behind `HARNESS_SMOKE_CLAUDE=1`,
unaffected by this change). Also ran `tests/test_architecture.py` on its own
as the architecture doc's suggested trip-wire — 14 passed, confirming no
invariant-guarded boundary was touched.

## How to verify

```
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Manually: `harness init --root /tmp/x` then inspect
`/tmp/x/agents/development.json` — it now contains `"timeout": null`.
Setting it to e.g. `120` and running `harness run --root /tmp/x` (with
`--agent claude`) makes that one step time out after 120s while every other
step still uses the (now 1800s, or whatever `--agent-timeout` was passed)
default.
