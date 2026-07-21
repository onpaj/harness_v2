# Plan: raise and make configurable the per-step agent timeout

## Summary

Every step run by `ClaudeCliBehavior` is killed after a hardcoded 600s (10 min)
unless overridden by the single global `--agent-timeout` CLI flag. We raise the
default to a more realistic value and add a **per-step override** so a slow step
(e.g. `dev`, `review`) can get more time than a fast one (e.g. `plan`) without
raising the timeout for every step in the workflow.

## Context

A codebase investigation (no prior artifacts exist for this task — this is the
first step) found that timeout configurability already exists, but only at one
granularity:

- `harness run --agent-timeout` (`src/harness/cli.py:768`, default `600.0`) sets
  **one** value applied uniformly to every agent step in the workflow.
- `build(..., agent_timeout: float = 600.0)` (`src/harness/app.py:212`) threads
  that single value into every `ClaudeCliBehavior` it constructs
  (`src/harness/app.py:294`).
- `ClaudeCliBehavior.__init__(..., timeout: float = 600.0)`
  (`src/harness/behaviors/agent.py:34`) stores it and passes it to
  `AgentRunner.run(timeout=...)`, which raises `AgentError` on expiry
  (`src/harness/drivers/claude_cli.py:329-332`).

600s is too short for real `claude -p` steps doing nontrivial work (multi-file
`dev` steps, `review` steps that read a diff and reason about it), and a single
global knob forces the operator to over-provision every step to the needs of
the slowest one. Per invariant 14 ("the persona is data, not code" — the
difference between personas is the content of `AgentSpec`), the natural place
for a per-step override is the step's own `AgentSpec` / `agents/<step>.json`
entry, sitting alongside `model`, `allowed_tools`, etc.

## Functional requirements

**FR-1 — Raise the global default.**
The default agent timeout rises from `600.0` to `1800.0` seconds (30 min).
- AC: a fresh `harness run` invoked without `--agent-timeout` gives every
  agent step 1800s before `AgentError` fires.
- AC: `harness run --help` shows the new default.
- Applies to all three places the `600.0` default is currently duplicated:
  `cli.py` (`--agent-timeout`), `app.py` (`build(agent_timeout=...)`), and
  `behaviors/agent.py` (`ClaudeCliBehavior(timeout=...)`) — kept in sync so
  none of them silently falls back to the old value if called without the
  keyword.

**FR-2 — Per-step timeout override via `AgentSpec`.**
`AgentSpec` gains an optional field `timeout: float | None = None`. `None`
means "use the run's global default"; a numeric value overrides it for that
step only.
- AC: `AgentSpec(name=..., prompt=..., timeout=120.0)` round-trips through
  `AgentCatalog.get()` unchanged.
- AC: `AgentSpec` without `timeout` defaults to `None` (no behavior change for
  specs that don't set it).

**FR-3 — `agents/<step>.json` accepts an optional `"timeout"` key.**
`FilesystemAgentCatalog.get()` (`src/harness/drivers/fs_agents.py`) reads an
optional `"timeout"` number from the JSON file into `AgentSpec.timeout`.
- AC: a step file with `"timeout": 3600` produces `AgentSpec.timeout == 3600.0`.
- AC: a step file without the key produces `AgentSpec.timeout is None`
  (unchanged behavior, backward compatible with every existing `agents/*.json`
  on disk).
- AC: a non-numeric or non-positive `"timeout"` value (e.g. `"soon"`, `0`,
  `-5`) raises `AgentNotFound` with a message naming the step and the problem,
  matching the existing validation style for `allowed_outcomes`.

**FR-4 — `build()` resolves the effective timeout per step.**
`app.py`'s `behavior_for(step)` picks `spec.timeout` when the catalog entry
sets one, otherwise falls back to the `agent_timeout` passed into `build()`.
- AC: a workflow with `review.json` carrying `"timeout": 3600` and every other
  step's JSON omitting `timeout`, run with `--agent-timeout 900`, produces:
  `review` step behavior with `timeout=3600.0`, all other step behaviors with
  `timeout=900.0`.

**FR-5 — `harness init` template stays valid and discoverable.**
`_write_default_agents` (`src/harness/cli.py:284`) keeps writing agent JSON
files that validate under the new schema. Add `"timeout": None` to the
generated template (mirroring how `"model": None` is already there) so an
operator opening a fresh `agents/<step>.json` sees the knob exists.
- AC: `harness init` on an empty root still produces JSON that
  `FilesystemAgentCatalog.get()` parses without error.

## Non-functional requirements

- **Backward compatibility**: every existing `agents/<step>.json` on a
  deployed harness (no `"timeout"` key) continues to work unchanged, picking
  up whatever `--agent-timeout` the run uses (now defaulting higher).
- **No new port surface**: this stays inside the existing `AgentSpec` /
  `AgentCatalog` / `AgentRunner` ports (`ports/agent.py` is otherwise
  untouched) — no new port, no dispatcher/consumer change, no architecture
  test to update beyond what already covers `ports/agent.py`.
- **Fail fast, not silent**: an invalid `timeout` in a step's JSON must be
  caught at catalog-read time (`AgentNotFound`, surfaced at `build()` per the
  existing "missing spec → fail fast" comment in `app.py`), not at the point
  the agent is invoked mid-run.

## Data model

`AgentSpec` (`src/harness/ports/agent.py`), extended:

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt: str
    model: str | None = None
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[Outcome, ...] = (Outcome.DONE,)
    timeout: float | None = None   # NEW — None = inherit the run's global default
```

`agents/<step>.json` on disk, extended (new key optional, default `null`):

```json
{
  "prompt": "...",
  "model": null,
  "fallback_model": null,
  "allowed_tools": [],
  "allowed_outcomes": ["done"],
  "timeout": null
}
```

No change to `Task`, `BehaviorResult`, queue/event models, or persisted task
state — timeout resolution happens entirely at `build()`/behavior-construction
time, not per-task.

## Interfaces

- CLI: `harness run --agent-timeout SECONDS` — unchanged flag, new default
  (`1800.0` instead of `600.0`). Still the run-wide fallback.
- File format: `agents/<step>.json` — new optional `"timeout"` number field.
- No API (`api/`), event, or board projection changes — timeout is a
  construction-time input to a behavior, invisible to `BoardView`/`ArtifactView`.
  (`AgentError` on expiry already surfaces through the existing failed-task
  path; that path is untouched.)

## Dependencies and scope

**Rests on**: existing `AgentSpec`/`AgentCatalog`/`ClaudeCliBehavior`/`build()`
wiring from phase 3 (invariants 13, 14, 17). No new invariant is introduced;
FR-2/FR-3 are a direct application of invariant 14 ("the persona is data").

**In scope**: `ports/agent.py` (`AgentSpec`), `drivers/fs_agents.py`
(`FilesystemAgentCatalog.get`), `drivers/memory.py` (`MemoryAgentCatalog`, if
it independently constructs `AgentSpec` — check during design/dev), `app.py`
(`build`'s timeout resolution in `behavior_for`), `cli.py` (new
`--agent-timeout` default, `_write_default_agents` template), tests for all of
the above.

**Out of scope**:
- Per-attempt or dynamic timeouts (e.g. longer timeout on retry after
  `request_changes`) — only a static per-step value.
- Timeout for anything other than the agent subprocess wall-clock
  (`claude_cli.py`'s `asyncio.wait_for`) — queue polling intervals, source
  polling, landing/push timeouts are untouched.
- Changing what happens on expiry — still `AgentError` → task fails via the
  existing consumer error path. No retry-with-longer-timeout behavior.
- A per-repository or per-workflow (as opposed to per-step) timeout axis.

## Rough plan

1. **Design** (next step): confirm the `AgentSpec.timeout: float | None`
   field placement and the validation rule for `FilesystemAgentCatalog`
   (reuse the existing `allowed_outcomes`-style try/except → `AgentNotFound`
   pattern); confirm whether `drivers/memory.py`'s `MemoryAgentCatalog` needs
   a matching change for test symmetry.
2. Add `timeout: float | None = None` to `AgentSpec`
   (`src/harness/ports/agent.py`).
3. Teach `FilesystemAgentCatalog.get()` to parse and validate the optional
   `"timeout"` key (`src/harness/drivers/fs_agents.py`).
4. Resolve the effective timeout in `app.py`'s `behavior_for(step)`:
   `spec.timeout if spec.timeout is not None else agent_timeout`.
5. Raise the default from `600.0` to `1800.0` in `cli.py`
   (`--agent-timeout`), `app.py` (`build`), and `behaviors/agent.py`
   (`ClaudeCliBehavior`) so all three agree.
6. Update `_write_default_agents` in `cli.py` to include `"timeout": null` in
   the generated template.
7. Tests: `AgentSpec` default/override (`test_agent_ports.py`),
   `FilesystemAgentCatalog` valid/invalid `"timeout"` parsing
   (new or existing `fs_agents` test file — confirm location during design),
   `build()` per-step timeout resolution (`test_app.py`), CLI default value
   (`test_cli.py`).
8. Update `CHANGELOG.md`/docs only if the project's release process expects
   it (commit as `feat:` — this is a behavior change and new capability, will
   bump the minor version per the repo's conventional-commits release
   pipeline).

## Open questions

- **Exact new default value.** 1800s (30 min) is a reasonable, generous bump
  over 10 min without being unbounded; the request didn't specify a number.
  Picked as a sensible default — flag if the operator wants a different
  figure (e.g. per-step defaults baked into the `harness init` template
  instead of a single flat number, e.g. `dev`/`review` steps templated with a
  longer default than `plan`/`design`). Default chosen: **flat 1800s global
  default, per-step override available but not pre-populated with different
  values per step** — keeps the change minimal and lets the operator tune
  specific steps as they observe real run times.
- **`MemoryAgentCatalog`** (`src/harness/drivers/memory.py:340`, used by
  in-memory/unit tests) constructs `AgentSpec` directly rather than parsing
  JSON — needs checking whether it already forwards arbitrary `AgentSpec`
  fields (likely yes, since it's a thin fake) or needs an explicit update to
  accept/pass through `timeout` in its test-facing API.
- **Whether to also expose per-step timeout as a CLI convenience** (e.g.
  `--agent-timeout review=3600`) was considered and rejected: the
  `agents/<step>.json` file already exists as the per-step configuration
  surface (model, tools, outcomes), so a step's timeout belongs there too
  rather than inventing a second configuration channel.
