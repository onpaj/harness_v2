# Architecture assessment — record per-step token usage, surface per-task totals

## Verdict

The plan and design (`plan-01.md`, `design-01.md`) are architecturally sound and,
on inspection of the real code, almost entirely accurate — every module, line
range, and dataclass shape they cite checks out against the current tree. I
verified `ports/agent.py`, `drivers/claude_cli.py`, `behaviors/agent.py`,
`consumer.py`, `models.py`, `ports/board.py`, `projection.py`, `router.py`,
`dispatcher.py`, `api/templates/_task.html`, `artifacts_layout.py`, and
`test_architecture.py` directly rather than trusting the design's citations.

This assessment **endorses the design with one structural correction**
(§2.1 — `BehaviorResult` needs a dedicated `tokens` field, not a key inside
`data`), **one concrete resolution of the plan's open question** (§2.2 —
give `HistoryEntry.tokens` an explicit `attempt` field), and **one factual
fix** (§3 — the next ADR number is `0020`, not `0021`). Everything else below
is confirmation, sharpened implementation guidance, and risk-mitigation, not
a change of direction.

## 1. Alignment with existing patterns and integration points

Confirmed against the real code, not just the design's description:

- **`AgentRun` → `ClaudeCliBehavior` → `BehaviorResult.data` → `Consumer._deliver`
  → `task.data`/`HistoryEntry`** is exactly the channel the codebase already
  uses for structured facts a behavior produces (e.g. landing's PR identity).
  No new channel is needed.
- **`route()` (`router.py`) structurally cannot see `task.data` today** — its
  signature is `route(task, workflow)` and its body only ever reads
  `task.status`/`task.last_outcome`/`task.step`; it has no reference to
  `.data` at all. `dispatcher.py` likewise has zero `.data`/`.data[` references
  (confirmed by grep). This means **invariants #4/#8 are structurally
  guaranteed already** for the router/dispatcher half of FR-7 — adding token
  data cannot leak into a routing decision through these two files no matter
  what key name is chosen, because neither file has the machinery to read
  `task.data` in the first place. This *reduces* the testing burden the plan
  proposed (see §4).
- **`HistoryEntry`/`Task`/`BehaviorResult`** are all frozen dataclasses
  constructed exclusively with keyword arguments at every call site I found
  (`consumer.py`, `dispatcher.py`, `issue_reconciler.py`, `merge_reconciler.py`,
  `pr_watcher.py`, `task_control.py`, `failed_tasks_check.py`,
  `behaviors/{agent,landing,resolve_conflict,open_issue}.py`,
  `drivers/{dummy_behavior,memory}.py`). Appending new fields with defaults at
  the end of each dataclass is fully backward compatible — no call site needs
  to change to keep compiling.
- **`FakeAgentRunner`** (`drivers/memory.py`) already accepts a
  caller-constructed `AgentRun` via `runs`/`default`; scripting usage needs no
  new plumbing, confirming FR-3 as written.
- **`_task.html`** confirms the template already renders `task` as an object
  with Jinja attribute access (`entry.from_step`, `task.data.title`) and
  `task.data` as a raw dict dumped via `<pre>{{ task.data }}</pre>` — so
  `task.data.tokens_total` is visible today, before any template change, exactly
  as the design notes. The proposed additions are template-only, additive, and
  low-risk.
- **ADR numbering**: the plan/design assume the next ADR is `0021` ("the
  highest existing ADR is 0020"). `docs/adr/` actually stops at `0019`
  (`0019-heal-triage-and-dedup.md`) — several ADRs share the number `0018`
  (four files), which is presumably why the count was miscalculated. **The
  next ADR is `0020-record-agent-token-usage.md`.**

## 2. Proposed architecture

The design's data-flow diagram is correct and I adopt it as-is:

```
ClaudeCliRunner._drain / .run   (drivers/claude_cli.py)
        │ parses usage + resolved model out of stream-json
        ▼
AgentRun                         (ports/agent.py)
        │ carries token/cache/cost/model fields
        ▼
ClaudeCliBehavior.run            (behaviors/agent.py)
        │ builds a per-attempt usage record + running per-task total
        ▼
Consumer._deliver                (consumer.py)
        │ attaches the per-attempt record to the HistoryEntry it appends;
        │ merges the running total into task.data (existing shallow-merge)
        ▼
Task.history[i].tokens + task.data["tokens_total"]
        │ read verbatim by the existing board/task API
        ▼
BoardProjection.agent_history → AgentActivity → _task.html
```

Two decisions below refine the design's own interface sketch; everything else
in `design-01.md` §1/§2 (the `AgentRun` fields, the `_drain` tuple shape, the
`_usage_from_result` helper, the `AgentActivity`/`agent_history` additions, the
template snippets) is approved unchanged.

### 2.1 Decision: `BehaviorResult` needs a dedicated `tokens` field — not a `data["tokens"]` key

**Problem, found by tracing the actual merge, not assumed:** `Consumer._deliver`
does `merged_data = {**task.data, **(result.data or {})}` **unconditionally** —
every key in `result.data` lands in `task.data`, not just the ones the
behavior intends for that. The design's own `ClaudeCliBehavior.run` sketch
returns `BehaviorResult(outcome, summary, data={"tokens": tokens,
"tokens_total": tokens_total})`. If shipped as written, `task.data["tokens"]`
would silently become a top-level task key too — overwritten by whichever
step delivered last, carrying only that one step's numbers under a name that
looks task-scoped. This directly contradicts the design's own stated intent
("`data["tokens"]`... does **not** need to also live under a stable `task.data`
key") — the shallow-merge does it anyway, because `data` doesn't distinguish
"put this on the entry" from "put this on the task."

**Resolution — mirror how `outcome`/`summary` already work.** Those two are
*not* threaded through the generic `data` blob today; they are dedicated
top-level `BehaviorResult` fields that `_deliver` reads directly to build the
`HistoryEntry` (`entry = HistoryEntry(..., outcome=result.outcome,
summary=result.summary or None, ...)`), entirely separately from the
`merged_data` shallow-merge. Per-attempt token counts are a **per-delivery
fact of the same kind as `outcome`/`summary`** — not a task-level fact like
`tokens_total` — so it belongs on the same footing:

```python
# models.py
@dataclass(frozen=True)
class BehaviorResult:
    outcome: str
    summary: str = ""
    data: dict[str, Any] | None = None
    tokens: dict[str, Any] | None = None
    """Per-attempt usage for the HistoryEntry this delivery creates. Read
    directly by Consumer._deliver, never merged into task.data — the
    per-task total goes through `data` instead, exactly like every other
    task-level fact a behavior computes."""
```

```python
# behaviors/agent.py — ClaudeCliBehavior.run, replacing the design's sketch
tokens = {
    "attempt": attempt,          # see 2.2 — already a local variable here
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
    data={"tokens_total": tokens_total},   # task-level fact → shallow-merged
    tokens=tokens,                          # per-delivery fact → HistoryEntry only
)
```

```python
# consumer.py — _deliver; NO reordering needed (see note below)
entry = HistoryEntry(
    at=self._clock.now(),
    actor=self.actor,
    from_step=self._step,
    to_step=None,
    outcome=result.outcome,
    summary=result.summary or None,
    tokens=result.tokens,
)
merged_data = {**task.data, **(result.data or {})}
...
```

**Correction to the design's implementation note:** it says `_deliver`
"currently builds `entry` before computing `merged_data` [and needs to]
reorder trivially so the entry can read `result.data`." Looking at the real
`consumer.py`, `entry` is already built *before* `merged_data` — no reordering
is required either way, since `result` (and hence `result.tokens`/`result.data`)
is already in scope as the method parameter at the point `entry` is
constructed. Flag this so the implementer doesn't spend time on a no-op
"reorder" step.

Every other non-agent behavior (`LandingBehavior`, `OpenIssueBehavior`,
`ResolveConflictBehavior`, `DummyBehavior`, `FakeAgentRunner`'s consumer via
`memory.py`) constructs `BehaviorResult` without `tokens=`, which defaults to
`None` — `entry.tokens` is `None` for every non-agent handling, exactly as
`reason` is `None` for every non-`_fail` entry today. No existing call site
changes.

### 2.2 Decision: give the per-attempt record an explicit `attempt` field

The plan flagged this as an open question ("does `HistoryEntry` need an
explicit `attempt` number... or is one history row per attempt sufficient
correlation?") and defaulted to "no." Having read `artifacts_layout.py`, I'm
resolving it as **yes** — the "one row per attempt" assumption is not reliable
in general, and the fix is nearly free.

**Why it's not reliable:** `next_attempt()` (`artifacts_layout.py`) allocates
the next number by **scanning the worktree filesystem** for existing
`<step>-NN.md` files — it has no knowledge of `HistoryEntry` at all. If a run
fails after the attempt number is allocated but *before* the agent writes its
artifact file (a timeout, a `VerdictError`, a crash mid-run), `Consumer._fail`
still appends a `HistoryEntry` for that step (`to_step=FAILED`, no `tokens` —
the exception bypassed `BehaviorResult` entirely, so there's nothing to
attach), but no file was written, so the *next* attempt at that step reuses
the same number (`next_attempt` counts files, not history rows). A client
that infers "the Nth `HistoryEntry` for this step is attempt N" by filtering
`history` and counting would then be off by one relative to the artifact
folder — the fail row consumed a history slot but not an artifact slot.

This doesn't corrupt anything today (a fail entry never carries `tokens`
either way), but it means positional inference is fragile, and it's the kind
of assumption that a later consumer (a cost dashboard joining tokens to a
specific artifact attempt, exactly the follow-up this issue is explicitly
building toward) would get wrong. `attempt` is already a local variable in
`ClaudeCliBehavior.run` — stamping it into the `tokens` dict (as shown in
§2.1's snippet) costs one dict key and removes the ambiguity outright, so
there's no reason to leave it implicit.

Kept as a key *inside* `HistoryEntry.tokens` (not a new sibling top-level
field on `HistoryEntry`) — it's meaningless outside the context of a tokens
record, and this avoids growing `HistoryEntry`'s own field list for a value
that only matters when `tokens` is present.

## 3. Implementation guidance

Order of work (adjusts the plan's rough-plan step numbering only where the
above decisions bite; otherwise unchanged):

1. **`ports/agent.py`** — extend `AgentRun` exactly as designed: `input_tokens:
   int = 0`, `output_tokens: int = 0`, `cache_read_tokens: int = 0`,
   `cache_creation_tokens: int = 0`, `total_cost_usd: float | None = None`,
   `model: str | None = None`. Add one sentence to `AgentRunner.run`'s
   docstring: usage/model are best-effort telemetry, never required.

2. **`drivers/claude_cli.py`**:
   - `_drain` gains a fourth return value, the resolved model captured off the
     `system`/`init` message (`type == "system" and subtype == "init"` — the
     same predicate `render_stream_line` already uses at line ~258). New
     signature: `tuple[dict | None, str, str, str | None]`.
   - Add a pure `_usage_from_result(message: dict) -> dict[str, int | float |
     None]` that reads `usage.input_tokens`, `usage.output_tokens`,
     `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens`, and
     `total_cost_usd` off a terminal `result` message — `dict.get` at every
     level, `isinstance` checks on the numeric fields, degrading to `0`/`None`
     on anything missing or malformed. Same tolerant shape as `_extract_verdict`.
   - In `run()`: after `final is not None` is established, compute `usage =
     _usage_from_result(final)` and `model = <resolved model from _drain>`
     once. Every `AgentRun` `run()` ultimately returns gets these values
     layered on via `dataclasses.replace(base_run, input_tokens=...,
     output_tokens=..., ..., model=model)` — **do not** thread five new
     parameters through `try_verdict`/`fallback_verdict`/`verdict_from_final`/
     `_verdict_from_envelope`; those stay untouched, producing a bare
     `AgentRun(outcome, summary, raw=raw)` exactly as today, and `run()` wraps
     the result once at the point it's about to return it.
   - **The reprompt path needs one explicit wiring change the design's prose
     leaves implicit**: `_reprompt_verdict` builds its own `AgentRun` (via its
     own call to `try_verdict` on the *reprompt's* envelope, which has its own
     `usage` but no `system/init` — a resumed session doesn't re-emit it). For
     that `AgentRun` to carry the *outer* call's resolved model, `run()` must
     pass its already-computed `model` into `_reprompt_verdict(...)` as a new
     keyword parameter, and `_reprompt_verdict` must `replace()` its own
     `try_verdict` result with `usage_from_result(<reprompt envelope>)` +
     that passed-in `model` before returning it. Without this explicit
     threading, the reprompt path silently ships `model=None` for a codepath
     that already exists and is exercised whenever a multi-outcome step
     forgets its verdict block.
   - Unit-test both paths against synthetic stream-json transcripts: (a) a
     normal run — `system/init` with a model, one or more `assistant`
     messages, a terminal `result` with `usage`+`total_cost_usd`; (b) a
     reprompt recovery — assert the *reprompt's own* usage numbers appear but
     the *original* call's model does.

3. **`models.py`** — add `BehaviorResult.tokens` (§2.1) and `HistoryEntry.tokens`
   (§2.1/§2.2 combined shape below), both `dict[str, Any] | None = None`, with
   `to_dict`/`from_dict` following the existing `outcome`/`summary`/`reason`
   omit-when-`None` pattern.

4. **`behaviors/agent.py`** — build the `tokens` record (with `attempt`) and
   `tokens_total`, return via the corrected `BehaviorResult(..., data={...},
   tokens={...})` split (§2.1). No branch on `run.outcome` or agent name —
   the record is built unconditionally from whatever `run` is, matching
   invariants #2/#14.

5. **`consumer.py`** — attach `result.tokens` directly to the `HistoryEntry`
   in `_deliver` (no reordering needed, per §2.1's correction). This is a
   plain attribute read, not a conditional — it doesn't introduce a branch
   comparable to the one `test_consumer_has_no_branch_on_outcome_value`
   guards against (that test scans for `ast.Compare` nodes derived from
   `outcome`; an unconditional `.tokens` attribute read triggers nothing in
   it, and shouldn't — this is delivery, not decision-making).

6. **`ports/board.py` + `projection.py`** — extend `AgentActivity` with
   `input_tokens`/`output_tokens`/`model` (and, if useful, `attempt`), lift
   them from `entry.tokens` in `agent_history` exactly as the design shows —
   this is a direct copy of the existing `outcome`/`summary`/`reason` lifting
   pattern already in that method.

7. **`api/templates/_task.html`** — additive totals readout + history column,
   as designed. No route change; verify manually per FR-9's acceptance note.

8. **`docs/adr/0020-record-agent-token-usage.md`** (corrected number, §3) —
   Context: usage discarded today, no cost visibility. Decision: capture at
   the driver (`ClaudeCliRunner`), carry per-attempt facts via a dedicated
   `BehaviorResult.tokens` field (not `data`, precisely because `data` is an
   unconditional shallow-merge into `task.data` and per-attempt facts don't
   belong there — this is the one substantive addition to the design's
   original ADR sketch), carry the per-task running total via `data` (task
   level, correctly merged), record-only — never routes (invariants #4/#8,
   structurally guaranteed by `router.py`/`dispatcher.py` never touching
   `task.data`). Consequences: unblocks a pricing follow-up; no routing
   behavior change; `HistoryEntry`/`BehaviorResult`/`AgentRun` all grow
   optional fields, fully backward compatible.

9. `.venv/bin/pytest -q`, including `tests/test_architecture.py`.

### Data shapes (final, incorporating §2.1/§2.2)

```
AgentRun          + input_tokens: int = 0
                  + output_tokens: int = 0
                  + cache_read_tokens: int = 0
                  + cache_creation_tokens: int = 0
                  + total_cost_usd: float | None = None
                  + model: str | None = None

BehaviorResult    + tokens: dict[str, Any] | None = None   # NEW — not `data`

HistoryEntry      + tokens: dict[str, Any] | None = None
  tokens shape: {"attempt": int, "input": int, "output": int,
                 "cache_read": int, "cache_creation": int,
                 "total_cost_usd": float | None, "model": str | None}

task.data["tokens_total"]: {"input": int, "output": int}   # via BehaviorResult.data, unchanged from design

AgentActivity     + input_tokens: int | None = None
                  + output_tokens: int | None = None
                  + model: str | None = None
```

Everything else in `design-01.md` (the stream-json JSON examples, the API
response shapes, the UI template snippets) stands as written.

## 4. Risks and mitigations

- **Risk: assumed stream-json field names may not match the real CLI.** The
  design's `usage` shape (`input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`, `total_cost_usd`)
  is asserted by the issue brief but not yet observed against a real `claude
  -p --output-format stream-json --verbose` run in this repo.
  **Mitigation:** before finalizing `_usage_from_result`'s key names, run one
  real invocation by hand (or lean on `tests/test_smoke_claude.py`, the
  opt-in real-CLI smoke, gated on `HARNESS_SMOKE_CLAUDE=1`) and inspect the
  actual terminal `result` message. Keep `_usage_from_result` tolerant
  regardless (degrade to zero/`None`, never raise) so a naming mismatch is a
  silent zero, not a broken task — consistent with the non-functional
  requirement already in the plan.
- **Risk: the reprompt path's usage/model wiring is easy to get wrong** (see
  §3, step 2) since it requires passing `model` into `_reprompt_verdict` — a
  function that doesn't take it today. **Mitigation:** call this out
  explicitly in the PR/tests (done above); write a dedicated unit test that
  distinguishes "reprompt's own usage" from "original call's model" so a
  regression that silently drops one or the other is caught.
- **Risk: `BehaviorResult.data`/`tokens` split could be missed by an
  implementer skimming only the design doc**, re-introducing the
  `task.data["tokens"]` pollution bug described in §2.1. **Mitigation:** this
  assessment is the authoritative resolution — the ADR (step 8 above) should
  state the `data` vs `tokens` split explicitly as the decision, not just the
  data shapes, so it isn't silently reverted in a later refactor.
- **Risk: over-testing invariants #4/#8.** Since `router.py`/`dispatcher.py`
  structurally cannot read `task.data` (confirmed in §1), a new dedicated
  architecture test asserting "no `tokens` reference in router/dispatcher" is
  redundant with the existing import/attribute-shape guarantees and would
  test something that can't happen by construction. **Mitigation:** skip a
  new static test for that half of FR-7; instead add one small behavioral
  test — a multi-step `FakeAgentRunner` task with scripted usage routes
  identically (same `MoveTo`/`Finished` decisions) to the same task with all
  usage zeroed — which is what actually demonstrates the acceptance
  criterion end to end.
- **Risk: backward compatibility of on-disk history/data with no `tokens`
  key.** Low risk — `from_dict`'s `raw.get("tokens")` pattern already matches
  every other optional `HistoryEntry` field, and the template guards render
  with `{% if %}`/fallback `"—"`, matching existing rows with no `reason`.

## Prerequisites before implementation begins

1. Confirm the real `claude -p --output-format stream-json --verbose` usage
   block's exact field names (§4, first risk) — a five-minute manual check,
   ideally captured as a fixture transcript reused by the new unit tests.
2. No other blocking prerequisite: `ports/behavior.py`'s `ConsumerBehavior`
   ABC is untouched (`run(task) -> BehaviorResult` — the new field is
   optional), and every `BehaviorResult`/`HistoryEntry` construction site in
   the tree already uses keyword arguments, so the new fields are additive
   everywhere.

## Non-goals (unchanged)

No pricing table, no dollar-figure computation beyond passing through the
CLI's own `total_cost_usd` for future cross-check, no new API route, no
routing-relevant use of any of this data.
