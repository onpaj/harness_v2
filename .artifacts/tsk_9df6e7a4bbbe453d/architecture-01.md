# Architecture: raise and make configurable the per-step agent timeout

## Verification of prior steps

I re-read the plan and design against the actual source (`ports/agent.py`,
`drivers/fs_agents.py`, `app.py:build`/`behavior_for`, `cli.py`,
`behaviors/agent.py`, `drivers/claude_cli.py`, `drivers/memory.py`,
`tests/test_fs_agents.py`) rather than taking their claims on faith. Every
line reference and code snippet in `plan-01.md` and `design-01.md` matches
what's actually on disk, including:

- The three `600.0` literals (`cli.py:768`, `app.py:212`,
  `behaviors/agent.py:34`) and the single flow-through path
  `agent_timeout` → `behavior_for` → `ClaudeCliBehavior(timeout=...)` →
  `AgentRunner.run(timeout=...)` → `asyncio.wait_for` in `claude_cli.py:327`.
- `AgentSpec` is a frozen dataclass, every field keyword-only in practice
  (no positional call sites), so appending `timeout: float | None = None`
  last is source-compatible.
- `FilesystemAgentCatalog.get()`'s existing validation shape (try/except →
  `AgentNotFound`, demonstrated by the `allowed_outcomes` block and
  `tests/test_fs_agents.py`'s `test_invalid_name_raises`-style tests) is the
  right template to extend for `"timeout"`.
- The actual workflow order is `plan → design → architecture → development →
  review → land → end` (`cli.py:61-68`), confirming this architecture step
  correctly follows plan and design in this task's run.
- `MemoryAgentCatalog` (`drivers/memory.py`) does no per-field handling — it's
  a dict-backed fake returning whatever `AgentSpec` a test constructed — so it
  needs no code change, only exercising in new/existing tests.

I have no corrections to the plan or design. This document confirms the
approach architecturally, resolves the plan's open questions with a firm
decision, and gives the development step exact guidance.

## Alignment with existing patterns and integration points

This change stays entirely within one existing seam and touches no
invariant-guarded boundary:

- It extends `AgentSpec`, a **pure data** carrier (invariant 14: "the persona
  is data, not code"). Adding `timeout` is the same kind of change as the
  existing `model`/`fallback_model`/`allowed_tools` fields — no new
  behavior, no branch on identity.
- It extends `FilesystemAgentCatalog.get()`'s existing "parse JSON, validate,
  fail fast via `AgentNotFound`" responsibility — the same responsibility it
  already has for `allowed_outcomes`, just one more field.
- It extends `app.py`'s `build()`/`behavior_for`, which already **is** the
  one place permitted to wire concrete values into a behavior (invariant 17:
  `AgentRunner`/`AgentCatalog`/`RepositoryRegistry` are unknown to
  dispatcher/consumer — only wiring touches them). Timeout resolution is
  exactly that kind of wiring decision.
- It touches no port signature (`ports/agent.py`'s ABCs are unchanged), no
  dispatcher/consumer/router code, no event/board/API surface. None of the
  architecture tests in `tests/test_architecture.py` are implicated — this is
  the important negative check, and I confirmed by reading `AgentSpec`'s
  only outside callers (`fs_agents.py`, `memory.py`, `app.py`, tests) that
  none of them sit in a layer this change would push through a forbidden
  edge.

Because the whole change is additive-and-optional (`timeout: float | None =
None` at the data layer, `"timeout"` absent-tolerant at the file layer), it
carries zero migration risk for anything already deployed.

## Proposed architecture

No new component. One field widens (`AgentSpec.timeout`), one parse/validate
block is added (`FilesystemAgentCatalog.get`), one resolution rule is added
(`app.py`'s `behavior_for`), and three defaults move in lockstep
(`600.0` → `1800.0`). The design doc's data-flow diagram is correct and I
adopt it as the target diagram unchanged:

```
cli.py (--agent-timeout, default 1800.0)
   │  args.agent_timeout
   ▼
app.py: build(agent_timeout=1800.0)
   │
   ├── FilesystemAgentCatalog.get(step) ──► AgentSpec(..., timeout: float|None)
   │
   ▼
behavior_for(step):
   effective = spec.timeout if spec.timeout is not None else agent_timeout
   ClaudeCliBehavior(..., timeout=effective)
   │
   ▼
AgentRunner.run(..., timeout=effective)   (drivers/claude_cli.py — unchanged)
```

### Key decisions

**Decision 1 — where does the override live?**
Options: (a) a new CLI flag per step (`--agent-timeout review=3600`), (b) a
separate timeouts config file, (c) a field on the existing
`AgentSpec`/`agents/<step>.json`.
Chosen: **(c)**. `agents/<step>.json` is already the per-step configuration
surface for everything else that varies by persona (`model`,
`fallback_model`, `allowed_tools`, `allowed_outcomes`). A second
configuration channel for one more per-step knob would be pure duplication
of a solved problem, and it's the option the plan already picked — I concur.

**Decision 2 — sentinel for "use the global default."**
Options: (a) `timeout: float | None = None` where `None` means "inherit",
(b) restate the numeric default (`1800.0`) directly in `AgentSpec`.
Chosen: **(a)**. With (b), every existing `agents/<step>.json` written before
this change (no `"timeout"` key → field defaults to some number) would
freeze at whatever default existed the day the file was parsed, silently
diverging from a future `--agent-timeout` change. `None`-as-inherit keeps
`build()`'s `agent_timeout` the single source of truth for "the default,"
and a step file only overrides when it explicitly says so. This also makes
backward compatibility trivial: an old file with no `"timeout"` key parses
to `None` and behaves exactly as before.

**Decision 3 — validation strictness.**
Options: (a) coerce anything numeric-ish, silently ignore garbage, (b) fail
fast with `AgentNotFound` on a non-numeic or non-positive value, matching the
existing `allowed_outcomes` pattern.
Chosen: **(b)**. Per the non-functional requirement in the plan and the
project's general posture (`Broken JSON has no one to attribute history
to` — gotchas section), a bad config value must surface at catalog-read time
(inside `build()`, before any task is dispatched), not three hours into a
run when the agent silently gets `0` seconds or a string coerced who-knows-how.
`isinstance(x, bool)` must be excluded explicitly before the `int` check —
`bool` is an `int` subclass in Python, so `"timeout": true` would otherwise
silently become `1.0` seconds and every step would appear to time out
instantly. This is a real footgun worth a one-line guard and a regression
test.

**Decision 4 — new default value (1800.0s).**
The plan flagged this as an open question. I confirm **1800.0 (30 min)** as
the right default: it's a 3x increase over the current 600s, generous enough
for a `dev`/`review` step doing real multi-file work with `claude -p`,
without being unbounded (an agent that hangs for 30 minutes is worth failing
and investigating, not waiting out indefinitely). I reject pre-seeding
per-step defaults in the `harness init` template (e.g. a longer default
baked into `development.json` than `plan.json`) — that adds asymmetric,
silently-diverging state into every fresh install for a problem the flat
1800s bump already solves adequately; an operator who profiles their actual
run times can tune specific steps later via the now-available override.

**Decision 5 — no shared constant for the three `1800.0` literals.**
The design doc considered and rejected introducing a module-level constant
shared by `cli.py`, `app.py`, and `behaviors/agent.py`. I concur: three
literals, one grep away from each other, kept in sync by this one change and
by convention thereafter (this mirrors how the existing `600.0` was already
triplicated with no shared constant) — a shared constant would be structural
overhead for a value that changes maybe once every few phases, and it would
be the *only* cross-module constant of its kind in the codebase, an
unjustified precedent.

## Implementation guidance

Concrete, in dependency order (matches the plan's rough-plan section — I
confirm this ordering and add the exact edit points found in the real
files):

1. **`src/harness/ports/agent.py`** — add
   `timeout: float | None = None` as the last field of `AgentSpec` (after
   `allowed_outcomes`). No other change to this file; `AgentRunner`/
   `AgentCatalog` ABC signatures are untouched.

2. **`src/harness/drivers/fs_agents.py`**, inside `get()`, immediately after
   the `allowed_outcomes` parse block and before the `return AgentSpec(...)`:
   parse `raw.get("timeout")`, validate (reject `bool`, reject non-numeric,
   reject `<= 0`), raise `AgentNotFound(f"agent {name!r} has invalid
   timeout: ...")` on failure — same message shape as the existing
   `allowed_outcomes` error (`f"agent {name!r} has invalid ...: {error}"`).
   Pass the validated `float | None` into the `AgentSpec(...)` call's new
   `timeout=` kwarg.

3. **`src/harness/app.py`**, inside `behavior_for(step)`'s `catalog is not
   None` branch (`app.py:283-295`): bind `spec = catalog.get(step)` once
   (currently `catalog.get(step)` is inlined directly into the `spec=`
   kwarg — pull it into a local so it can be read twice), compute
   `effective_timeout = spec.timeout if spec.timeout is not None else
   agent_timeout`, pass `timeout=effective_timeout` instead of
   `timeout=agent_timeout`.

4. **Defaults**, changed together in the same commit so they never disagree:
   `cli.py:768` (`--agent-timeout` default), `app.py:212` (`build`'s
   `agent_timeout` parameter default), `behaviors/agent.py:34`
   (`ClaudeCliBehavior.__init__`'s `timeout` parameter default — this one is
   a defensive fallback only, since `build()` always passes `timeout=`
   explicitly; it matters for direct construction in tests).

5. **`src/harness/cli.py`**'s `_write_default_agents` (`cli.py:284-301`):
   add `"timeout": None` to the `definition` dict, positioned after
   `"allowed_outcomes"` to mirror the field order in `AgentSpec` and in the
   design doc's documented on-disk schema.

6. **Tests** — extend the four files the plan named, I confirm each is the
   right location by inspection:
   - `tests/test_agent_ports.py`: `AgentSpec` default (`timeout is None`) and
     explicit construction (`timeout=120.0` round-trips).
   - `tests/test_fs_agents.py`: add cases alongside the existing
     `test_defaults_when_fields_missing` / `test_invalid_name_raises`
     siblings — valid numeric `"timeout"`, missing key → `None`, `"timeout":
     true` → `AgentNotFound`, `"timeout": 0` / negative → `AgentNotFound`,
     `"timeout": "soon"` → `AgentNotFound`.
   - `tests/test_app.py`: a workflow/catalog fixture where one step's spec
     sets `timeout` and another doesn't, asserting `behavior_for` (or
     whatever `build()` exposes for inspection) constructs
     `ClaudeCliBehavior` with the step's override vs. the passed
     `agent_timeout` fallback.
   - `tests/test_cli.py`: assert the parsed `--agent-timeout` default is
     `1800.0`, and that `_write_default_agents`'s written JSON contains
     `"timeout": null`.

No new test file, no change to `tests/test_architecture.py` is needed or
expected — confirm this stays true during development (a quick
`pytest tests/test_architecture.py` after the change is a fast trip-wire).

## Data flow

Unchanged shape from the design doc, confirmed against the real call graph:
`FilesystemAgentCatalog.get(step)` runs once per step at `build()` time (not
per task, not per attempt) → produces one `AgentSpec` per step → `build()`
resolves one `effective_timeout` float per step → that float is baked into
the one `ClaudeCliBehavior` instance the consumer for that step queue holds
for the harness's whole lifetime. There is no per-task or per-attempt
timeout resolution; `Task`, `BehaviorResult`, the event stream, and
`BoardView`/`ArtifactView` are untouched, matching invariants 5 and 11 (API/
projection never import drivers, and only the behavior touches
agent/workspace ports).

## Risks and mitigations

- **Risk: a bad `"timeout"` value in an existing deployed `agents/<step>.json`
  is discovered only when someone hand-edits the file later.**
  Mitigation: validation happens at `catalog.get()`, called from `build()`
  before the harness starts consuming (per the existing "missing spec →
  fail fast" comment already in `app.py`), so a bad edit fails
  `harness run` immediately at startup, not mid-task. No new risk beyond
  what already exists for `allowed_outcomes`.
- **Risk: `bool`-as-`int` silently produces a 1-second or 0-second timeout.**
  Mitigation: explicit `isinstance(x, bool)` rejection before the numeric
  check (Decision 3), plus a regression test asserting `"timeout": true`
  raises `AgentNotFound` rather than silently parsing.
- **Risk: raising the default to 1800s masks a genuinely hung agent for much
  longer before the task fails.**
  Accepted, not mitigated further — this is an explicit trade-off the brief
  asked for (the current 600s kills real work too early) and 30 min is a
  bounded, operator-visible number, not "wait forever." Out of scope per the
  plan: no retry-with-longer-timeout, no dynamic backoff.
- **Risk: three unsynced default literals drift apart in a future edit.**
  Mitigation: accepted as-is (Decision 5) since it mirrors the existing
  pattern; the four-file test suite in step 6 above pins all three values,
  so a future edit that changes one without the others fails
  `test_cli.py`/`test_app.py` at minimum.

## Prerequisites before implementation begins

None outside this repository. No new dependency, no schema migration for
data already on disk (every existing `agents/<step>.json` parses unchanged
since `"timeout"` is optional), no port or invariant change, no coordination
with other in-flight work. Development can start directly from this
assessment and the design doc's schemas.
