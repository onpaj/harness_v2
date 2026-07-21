# Design: raise and make configurable the per-step agent timeout

No UI surface — this is a data-model, driver, and CLI-default change. `api/`
and `BoardView`/`ArtifactView` are untouched, so the UX/UI section is omitted.

## Component design

No new component and no new port. The change extends one existing dataclass
(`AgentSpec`) and touches four existing components along the path the value
already flows through. Boundaries and responsibilities, unchanged:

```
cli.py (--agent-timeout, default 1800.0)
   │  args.agent_timeout
   ▼
app.py: build(agent_timeout=1800.0)
   │
   ├── FilesystemAgentCatalog.get(step) ──► AgentSpec(..., timeout: float|None)
   │        (drivers/fs_agents.py: parses "timeout" from agents/<step>.json)
   │
   ▼
behavior_for(step):
   effective = spec.timeout if spec.timeout is not None else agent_timeout
   ClaudeCliBehavior(..., timeout=effective)
   │
   ▼
AgentRunner.run(..., timeout=effective)   (drivers/claude_cli.py — unchanged)
```

Each component keeps its existing single responsibility; only its data widens:

- **`ports/agent.py` — `AgentSpec`.** Gains `timeout: float | None = None`.
  Still pure data, still no behavior. `None` is the sentinel for "inherit
  the run's global default" — chosen over a numeric default (e.g. re-stating
  `1800.0` here) so the *only* place the global default is spelled out is
  `build()`'s `agent_timeout` parameter; a step file that omits `"timeout"`
  tracks whatever `--agent-timeout` the operator sets that run, forever,
  without the catalog itself ever going stale.

- **`drivers/fs_agents.py` — `FilesystemAgentCatalog.get()`.** Gains one more
  parse/validate block, same shape as the existing `allowed_outcomes` block:
  read `raw.get("timeout")`, validate, raise `AgentNotFound` on a bad value,
  else pass through to `AgentSpec`. No change to its contract (`get(name) ->
  AgentSpec`, raises `AgentNotFound`) — the responsibility "JSON → validated
  spec, fail fast on garbage" is unchanged, just wider.

- **`drivers/memory.py` — `MemoryAgentCatalog`.** No code change. It stores
  and returns whatever `AgentSpec` a test constructs, so `timeout` already
  round-trips through it via `AgentSpec`'s new default. Confirmed by reading
  it: `__init__(self, specs: dict[str, AgentSpec])` / `get` does a dict
  lookup — no per-field handling to update.

- **`app.py` — `build()` / `behavior_for(step)`.** Gains the two-line
  resolution rule above, placed where `behavior_for` already builds
  `ClaudeCliBehavior` (`timeout=agent_timeout` becomes
  `timeout=effective_timeout`). `behavior_for`'s responsibility — "given a
  step, produce the `ConsumerBehavior` that runs it" — is unchanged; timeout
  resolution is just one more thing it decides from data it already has
  (`catalog.get(step)`'s result), not a new responsibility.

- **`behaviors/agent.py` — `ClaudeCliBehavior`.** No change beyond the
  default value of its own `timeout` parameter (`600.0` → `1800.0`, kept
  only as a defensive default for direct construction — e.g. in tests that
  build it without going through `build()`). It still receives one resolved
  `timeout` float and does not know whether that number came from a step
  override or the global default; the resolution happens one layer up
  (`app.py`), not here, keeping invariant 14 (no branching on persona
  identity inside the behavior).

- **`cli.py` — `--agent-timeout` default, `_write_default_agents`.** The
  flag's default rises to `1800.0`. `_write_default_agents`'s template gains
  `"timeout": None` alongside the existing `"model": None`, so a freshly
  `harness init`'d `agents/<step>.json` documents the knob without setting a
  per-step value — no step gets a pre-populated non-default timeout, matching
  FR-5 and the plan's resolved open question (flat global default, override
  available but not pre-seeded).

No component gains a new incoming or outgoing dependency; the module map's
layering (`drivers` → `app` wiring → `cli`) is unchanged, and `ports/agent.py`
remains untouched by anything outside `drivers/fs_agents.py`, `drivers/
memory.py`, `behaviors/agent.py`, and `app.py` — the existing set of callers.

## Data schemas

### `AgentSpec` (`src/harness/ports/agent.py`)

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt: str
    model: str | None = None
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[Outcome, ...] = (Outcome.DONE,)
    timeout: float | None = None
    # NEW. None = inherit build()'s agent_timeout (the run-wide default).
    # A positive float overrides it for this step only.
```

Field placement: appended last, after `allowed_outcomes` — this is a
frozen dataclass constructed exclusively via keyword arguments at every call
site in the codebase (`fs_agents.py`, `memory.py` test fixtures, direct test
construction), so appending is source-compatible everywhere; no call site
passes `AgentSpec` positionally.

### `agents/<step>.json` (on disk, read by `FilesystemAgentCatalog`)

```jsonc
{
  "prompt": "...",
  "model": null,
  "fallback_model": null,
  "allowed_tools": [],
  "allowed_outcomes": ["done"],
  "timeout": null            // NEW, optional. Number of seconds, or null/absent
                              // to inherit --agent-timeout. Must be a positive
                              // number if present and non-null.
}
```

Validation rule in `FilesystemAgentCatalog.get()` (mirrors the existing
`allowed_outcomes` try/except → `AgentNotFound` pattern):

```python
raw_timeout = raw.get("timeout")
if raw_timeout is None:
    timeout = None
elif isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
    raise AgentNotFound(
        f"agent {name!r} has invalid timeout: expected a positive number, "
        f"got {raw_timeout!r}"
    )
elif raw_timeout <= 0:
    raise AgentNotFound(
        f"agent {name!r} has invalid timeout: must be positive, got {raw_timeout!r}"
    )
else:
    timeout = float(raw_timeout)
```

(`isinstance(raw_timeout, bool)` is excluded explicitly because `bool` is a
`int` subclass in Python — `"timeout": true` must not silently parse as
`1.0`.)

### Resolution rule (`app.py`, inside `behavior_for`)

```python
effective_timeout = spec.timeout if spec.timeout is not None else agent_timeout
```

No request/response shapes, event payloads, or persisted `Task` fields
change — `timeout` is resolved once at behavior-construction time inside
`build()` and never touches `task.data`, the event stream, or `BoardView`.

### Defaults changing from `600.0` to `1800.0`

Three call sites, kept textually in sync (no shared constant introduced —
matches the existing style where each layer restates its own default; a
shared module-level constant was considered and rejected as unnecessary
indirection for three literals that a grep instantly finds):

| Site | Symbol |
|---|---|
| `src/harness/cli.py` | `run.add_argument("--agent-timeout", type=float, default=1800.0, ...)` |
| `src/harness/app.py` | `build(..., agent_timeout: float = 1800.0, ...)` |
| `src/harness/behaviors/agent.py` | `ClaudeCliBehavior.__init__(..., timeout: float = 1800.0)` |

## Test surface (for the development step)

No new test files needed — extend the four already identified in the plan:
`tests/test_agent_ports.py` (`AgentSpec.timeout` default/override),
`tests/test_fs_agents.py` (valid/missing/invalid `"timeout"` parsing),
`tests/test_app.py` (`behavior_for` resolution: step override wins, absent
falls back to `agent_timeout`), `tests/test_cli.py` (new `--agent-timeout`
default value, `_write_default_agents` template includes `"timeout": null`).
