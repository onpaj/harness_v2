# Design — Record per-step token usage, surface per-task totals

No UI wireframe section: this issue extends an existing read-only detail view
(`_task.html`) with two small additions (a totals readout, a table column) —
there is no new screen, flow, or interaction to storyboard. The concrete
markup is specified below under "UI changes" instead.

## 1. Component design

No new components. Five existing modules each grow by one small, additive
piece; none changes its role or its position in the module map.

```
ClaudeCliRunner._drain / .run          (drivers/claude_cli.py)
        │  parses usage + model out of the stream-json `result`/`init` messages
        ▼
AgentRun                                (ports/agent.py)
        │  now carries token/cache/cost/model fields
        ▼
ClaudeCliBehavior.run                   (behaviors/agent.py)
        │  builds a per-attempt usage record + running per-task total,
        │  returns both via BehaviorResult.data
        ▼
Consumer._deliver                       (consumer.py)
        │  merges result.data into task.data (existing);
        │  additionally lifts the per-step record onto the HistoryEntry
        │  it appends (new)
        ▼
Task.history[i].tokens  +  task.data["tokens_total"]
        │  read verbatim by the existing board/task API (no route change)
        ▼
BoardProjection.agent_history            (projection.py)
        │  lifts tokens onto AgentActivity, same pattern as outcome/summary
        ▼
AgentActivity                            (ports/board.py)
        │  exposed to the agent-history API surface
        ▼
_task.html                               (api/templates)
        │  totals readout + history table column
```

Responsibility boundaries, stated explicitly because several modules touch
the data and it would be easy to blur who owns what:

- **`ClaudeCliRunner`** (driver) is the only place that knows the CLI's
  stream-json shape. It is the sole translator from "Anthropic API usage
  block" to "plain ints on `AgentRun`". Nothing downstream parses JSON usage
  again.
- **`ClaudeCliBehavior`** (behavior) is the only place that knows about
  attempts and running totals. It reads the *previous* total off
  `task.data` (input) and computes the *new* total (output) — arithmetic,
  not parsing. It performs no branch on `run.outcome` or on the agent's name
  to do this (invariants #2, #14): the usage record is built unconditionally
  from whatever `run` came back with, success or not — the driver has
  already decided whether the run counts as `AgentRun` at all;
  `ClaudeCliBehavior` never gates *its own* branch on the outcome value.
- **`Consumer._deliver`** (orchestration) is the only place that writes
  `HistoryEntry`. It already merges `result.data` into `task.data`
  unconditionally, with no knowledge of what the keys mean (invariant #2 —
  consumer decides nothing). Lifting the per-step record onto the new
  `HistoryEntry.tokens` field is the same shape as every other
  `HistoryEntry` field it already sets from `result` (`outcome`, `summary`)
  — reading a known key out of `result.data`, not interpreting it.
- **`BoardProjection`** (read model) and **`AgentActivity`** (board port) do
  what they already do for `outcome`/`summary`/`reason`: lift a
  `HistoryEntry` field onto the read-model row, no computation.
- **Templates** render what the API already returns; no new endpoint, no new
  JS.

No component reads token data to make a decision. `router.py`,
`dispatcher.py`, and `consumer.py`'s outcome/status handling are untouched —
the only consumer.py change is additive (attaching a field to the entry it
was already building), not a new conditional.

### Interfaces

**`AgentRun`** (`ports/agent.py`) — extended, backward compatible (every new
field defaults, so every existing construction call — `try_verdict`,
`fallback_verdict`, `verdict_from_final`, every test literal — keeps
compiling):

```python
@dataclass(frozen=True)
class AgentRun:
    outcome: str
    summary: str
    raw: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_cost_usd: float | None = None
    model: str | None = None
```

`AgentRunner.run`'s contract (docstring) gains one sentence: usage/model are
best-effort telemetry — a runner that can't produce them (e.g. a future
non-Anthropic runner, or `FakeAgentRunner` when the test doesn't care)
leaves the defaults, never raises for their absence.

**`ClaudeCliRunner` internals** (`drivers/claude_cli.py`) — `_drain` already
distinguishes the terminal `result` message (`message.get("type") ==
"result"`) from everything else; it gains a second thing to remember: the
`model` off the `system`/`init` message. Both come back from `_drain` as a
third tuple element so `run()` can build the final `AgentRun` from them:

```python
async def _drain(proc, on_output) -> tuple[dict | None, str, str, str | None]:
    # returns (final_result_message, raw_stdout, stderr, resolved_model)
```

A private helper turns the terminal message into the four usage ints plus
`total_cost_usd`:

```python
def _usage_from_result(message: dict) -> dict[str, int | float | None]:
    """Read `usage`/`total_cost_usd` off a terminal `result` message.

    Missing or malformed fields degrade to zero/`None`, never raise — this is
    telemetry layered on an already-successful verdict parse, not a new
    failure mode for the run (matches `_extract_verdict`'s tolerant shape).
    """
```

`run()`'s three `AgentRun`-producing paths — the immediate `try_verdict`
success, the `_reprompt_verdict` recovery, and the `fallback_verdict` rescue
— all currently build their `AgentRun` from the *terminal* `result` message
(the reprompt path returns its own envelope from a **second**, smaller
`claude -p --resume` call whose usage is call-two's, not call-one's: no
existing field tracks two-call cost today, so this design keeps that
asymmetry — the reprompt path's usage/model is read off the reprompt's own
envelope, exactly mirroring how it already reads that envelope's `outcome`/
`summary` rather than the first call's). The `fallback_verdict` rescue keeps
the *first* call's `final` message's usage — it never issues a second call,
so there is only ever one usage block for that path. Concretely: `run()`
computes `usage = _usage_from_result(final)` and `model = <resolved model
from init>` once, right after `final is not None` is established, and passes
both into whichever `AgentRun` it ultimately returns via `dataclasses.
replace`-style construction (`try_verdict`/`fallback_verdict` build the base
`AgentRun`, `run()` layers usage/model on top with `replace()` rather than
threading five extra parameters through every function signature in
`claude_cli.py`). The reprompt path is the one exception: `_reprompt_verdict`
builds its own `AgentRun` internally (from its own envelope, which also
carries `usage`/no `system/init` — the reprompt call is `--resume`, so it
reuses the original session's model; `_reprompt_verdict` reads the reprompt
envelope's own `usage` block the same way, and falls back to the outer
`model` already resolved from the original call's `init` message, since a
resumed session doesn't re-emit `system/init`).

**`ClaudeCliBehavior.run`** (`behaviors/agent.py`) — after `run =
await self._runner.run(...)` and the existing `handle.commit(run.summary)`,
build the two data payloads and return them:

```python
tokens = {
    "input": run.input_tokens,
    "output": run.output_tokens,
    "cache_read": run.cache_read_tokens,
    "cache_creation": run.cache_creation_tokens,
    "total_cost_usd": run.total_cost_usd,
    "model": run.model,
}
previous_total = task.data.get("tokens_total") or {}
tokens_total = {
    "input": previous_total.get("input", 0) + run.input_tokens,
    "output": previous_total.get("output", 0) + run.output_tokens,
}
return BehaviorResult(
    run.outcome,
    run.summary,
    data={"tokens": tokens, "tokens_total": tokens_total},
)
```

`data["tokens"]` is this step/attempt's record (consumed by `Consumer.
_deliver` and attached to the `HistoryEntry`, see below — it does **not**
need to also live under a stable `task.data` key, since the history entry
*is* its permanent, attempt-indexed home). `data["tokens_total"]` is the
already-summed new running total, merged into `task.data` by the existing
shallow-merge in `_deliver` — no consumer change needed for this half, it is
exactly the same mechanism landing's PR identity already uses.

**`HistoryEntry`** (`models.py`) — one new optional field, same pattern as
`outcome`/`summary`/`reason` (omitted from `to_dict()` when `None`, so old
persisted history — no `tokens` key at all — round-trips unchanged):

```python
@dataclass(frozen=True)
class HistoryEntry:
    at: str
    actor: str
    from_step: str | None
    to_step: str | None
    outcome: str | None = None
    summary: str | None = None
    reason: str | None = None
    tokens: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        raw = {...}  # unchanged
        if self.tokens is not None:
            raw["tokens"] = self.tokens
        return raw

    @classmethod
    def from_dict(cls, raw) -> HistoryEntry:
        return cls(..., tokens=raw.get("tokens"))
```

**`Consumer._deliver`** (`consumer.py`) — currently builds `entry` before
computing `merged_data`. Reorder trivially so the entry can read
`result.data`:

```python
def _deliver(self, task: Task, result: BehaviorResult) -> None:
    entry = HistoryEntry(
        at=self._clock.now(),
        actor=self.actor,
        from_step=self._step,
        to_step=None,
        outcome=result.outcome,
        summary=result.summary or None,
        tokens=(result.data or {}).get("tokens"),
    )
    merged_data = {**task.data, **(result.data or {})}
    ...
```

This is the same "read a known key off `result.data`" the consumer already
does implicitly for every other field it copies from `result` — it adds no
branch on outcome or on the *value* of `tokens`, only an unconditional
`.get()` that is `None` for every non-agent behavior (`LandingBehavior`,
`OpenIssueBehavior`, `ResolveConflictBehavior`, `DummyBehavior`) exactly as
`reason` already is `None` for every non-`_fail` entry. `merged_data` still
carries `tokens_total` through unchanged — no second code path needed for
that half.

**`AgentActivity`** (`ports/board.py`) — three new optional fields, same
`to_dict()` shape as the rest:

```python
@dataclass(frozen=True)
class AgentActivity:
    task_id: str
    title: str
    at: str
    outcome: str | None
    summary: str | None
    reason: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {..., "inputTokens": self.input_tokens,
                "outputTokens": self.output_tokens, "model": self.model}
```

**`BoardProjection.agent_history`** (`projection.py`) — lifts the new fields
the same way it lifts `outcome`/`summary`/`reason` today:

```python
tokens = entry.tokens or {}
AgentActivity(
    ...,
    input_tokens=tokens.get("input"),
    output_tokens=tokens.get("output"),
    model=tokens.get("model"),
)
```

### `FakeAgentRunner` / test seam

No structural change (confirms plan FR-3). A test scripts usage directly:

```python
FakeAgentRunner(default=AgentRun(
    outcome="done", summary="...",
    input_tokens=120, output_tokens=45, model="claude-sonnet-5",
))
```

Add a helper builder is unnecessary — the dataclass literal is already
minimal; introducing a factory function would be an abstraction the task
doesn't need.

## 2. Data schemas

### `AgentRun` (in-process port value, not persisted directly)

| field | type | notes |
|---|---|---|
| `outcome` | `str` | unchanged |
| `summary` | `str` | unchanged |
| `raw` | `str` | unchanged |
| `input_tokens` | `int` | default `0` |
| `output_tokens` | `int` | default `0` |
| `cache_read_tokens` | `int` | default `0` |
| `cache_creation_tokens` | `int` | default `0` |
| `total_cost_usd` | `float \| None` | default `None`; passthrough from CLI, not computed |
| `model` | `str \| None` | resolved model (post-fallback), default `None` |

### `HistoryEntry.tokens` (persisted, per attempt, JSON on disk via `fs_queue`)

Shape stored under `history[i].tokens` when the handling was an agent run;
key omitted entirely (not `null`) for a non-agent finisher/behavior or for
history predating this change:

```json
{
  "input": 1234,
  "output": 567,
  "cache_read": 0,
  "cache_creation": 0,
  "total_cost_usd": 0.0231,
  "model": "claude-sonnet-5"
}
```

### `task.data["tokens_total"]` (persisted, per task, accumulated)

```json
{"input": 5820, "output": 1904}
```

Deliberately narrower than the per-entry shape — the total is the two
numbers a pricing follow-up needs fast, not a place to also sum
cache/cost (a cost total is exactly what the follow-up computes properly
from the model-priced per-step figures, so it isn't pre-summed here as a
naive USD sum across possibly-different models).

### API response shapes (no new endpoints — existing responses gain fields)

`GET /api/board`, `GET /api/tasks/{id}` — `Task.to_dict()`'s existing
`data` and `history` keys now may carry the two shapes above:

```json
{
  "id": "tsk_...",
  "data": {"tokens_total": {"input": 5820, "output": 1904}, "...": "..."},
  "history": [
    {
      "at": "...", "actor": "consumer:design", "from": "design", "to": null,
      "outcome": "done", "summary": "...",
      "tokens": {"input": 1234, "output": 567, "cache_read": 0,
                 "cache_creation": 0, "total_cost_usd": 0.0231,
                 "model": "claude-sonnet-5"}
    }
  ]
}
```

Agent-history surface (`AgentActivity.to_dict()`, wherever it's serialized
today — same route(s) as before, no new one):

```json
{
  "taskId": "tsk_...", "title": "...", "at": "...",
  "outcome": "done", "summary": "...", "reason": null,
  "inputTokens": 1234, "outputTokens": 567, "model": "claude-sonnet-5"
}
```

### stream-json input shapes consumed (not stored verbatim — parsed by `_usage_from_result`)

Terminal `result` message (existing `claude -p --output-format stream-json`
output, only the fields newly read are called out):

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "result": "...final text with fenced verdict...",
  "session_id": "...",
  "total_cost_usd": 0.0231,
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 567,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0
  }
}
```

`system`/`init` message (only `model` is newly read; everything else is
already ignored by `render_stream_line`):

```json
{"type": "system", "subtype": "init", "model": "claude-sonnet-5", "...": "..."}
```

Both reads are defensive (`dict.get`, `isinstance` checks) — a missing or
oddly-shaped field degrades to the zero/`None` default rather than raising,
per the non-functional requirement "degrade, don't fail, on malformed
usage".

## UI changes

`api/templates/_task.html`, additive only:

1. Info panel `<ul class="kv">` — one more `<li>` after `lastOutcome`,
   rendered only when the task has any usage recorded (guards the common
   case of an old or in-flight task with no `tokens_total` yet):

   ```html
   {% if task.data.tokens_total %}
   <li><span class="k">tokens</span>
       <span class="v">{{ task.data.tokens_total.input or 0 }} in /
       {{ task.data.tokens_total.output or 0 }} out</span></li>
   {% endif %}
   ```

2. History table — one more `<th>`/`<td>` pair, rendered per row from
   `entry.tokens`, blank for a row with none (a finisher, or history
   predating this change):

   ```html
   <tr><th>time</th><th>actor</th><th>from</th><th>to</th>
       <th>outcome</th><th>tokens</th><th class="wrap">reason</th></tr>
   ...
   <td>{{ (entry.tokens.input ~ " / " ~ entry.tokens.output) if entry.tokens else "—" }}</td>
   ```

No new tab, no new route, no JS. `task.data`'s existing raw `<pre>` dump
already shows `tokens_total` today even before any template change — the
two additions above are purely presentational sugar on top of data that is
already technically visible.

## Non-goals (unchanged from the plan)

No pricing table, no dollar-figure computation beyond passing through the
CLI's own `total_cost_usd` per step for future cross-check, no new route, no
routing-relevant use of any of this data (invariants #4, #8 — confirmed by
inspection above: `router.py`/`dispatcher.py` are untouched files in this
design, and the one `consumer.py` change is an unconditional field
attachment, not a branch).
