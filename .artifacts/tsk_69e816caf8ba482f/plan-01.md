# Plan — Record per-step token usage, surface per-task totals

## Summary

Every `claude -p` run already reports Anthropic token usage (input/output, plus
cache read/creation) in its stream-json output, and the harness throws it away.
This step wires that data through the existing agent → behavior → consumer →
task channel so it is persisted per step attempt and summed per task, then
exposed through the board/task API and shown in the UI. No pricing, no dollar
figures — token counts only, both directions.

## Context

`ClaudeCliRunner` parses the `claude -p --output-format stream-json` NDJSON
stream in `_drain` (`src/harness/drivers/claude_cli.py`) and keeps only the
terminal `result` envelope's `result`/`is_error`/`session_id` fields, mapping
them onto `AgentRun(outcome, summary, raw)` (`src/harness/ports/agent.py`).
The same terminal envelope also carries a `usage` block (input tokens, output
tokens, cache read/creation tokens) and `total_cost_usd`; the `system/init`
message earlier in the stream carries the resolved `model` (relevant when
`fallback_model` kicks in and the model that actually ran differs from
`spec.model`, per invariant #14 — persona is data, not a branch). None of this
reaches `BehaviorResult`, `task.data`, or `HistoryEntry` today, so there is no
visibility into what a task costs, per step or in total. This is the first,
deliberately narrow step toward cost estimation; a follow-up multiplies
tokens × a model price table. That table does not exist yet and is out of
scope here.

## Functional requirements

**FR-1 — `AgentRun` carries structured usage.**
Extend `AgentRun` (`src/harness/ports/agent.py`) with `input_tokens: int = 0`,
`output_tokens: int = 0`, `cache_read_tokens: int = 0`,
`cache_creation_tokens: int = 0`, `total_cost_usd: float | None = None`, and
`model: str | None = None` (the resolved model actually used).
*Acceptance:* the dataclass has these fields with safe defaults so every
existing call site (`parse_verdict`, `try_verdict`, `fallback_verdict`,
`verdict_from_final`, every test construction of `AgentRun`) keeps compiling
unchanged.

**FR-2 — `ClaudeCliRunner` populates usage from the stream.**
In `_drain`, also capture the `usage` block from the terminal `result`
message (it is the cumulative total for the turn, so no manual summation
across per-turn `assistant` messages is needed) and the `model` reported by
the `system`/`init` message. Thread both through `run()` into the `AgentRun`
returned on every path that currently builds one (`try_verdict` success,
`_reprompt_verdict` recovery, `fallback_verdict` rescue).
*Acceptance:* a fed synthetic stream-json transcript (system/init with a
model, one or more assistant turns, a terminal result with a `usage` block
and `total_cost_usd`) yields an `AgentRun` whose token/model fields match the
transcript. Missing/malformed `usage` degrades to the zero defaults, never an
exception — this is telemetry, not a correctness gate.

**FR-3 — `FakeAgentRunner` can script usage.**
No structural change needed beyond FR-1: `FakeAgentRunner.runs`/`default`
already accept a caller-constructed `AgentRun`, so a test scripts
`AgentRun(outcome="done", summary="...", input_tokens=120, output_tokens=45,
model="claude-sonnet-5")` directly. Confirm this in a unit test rather than
adding new plumbing.

**FR-4 — the behavior carries usage into `BehaviorResult.data`.**
In `ClaudeCliBehavior.run` (`src/harness/behaviors/agent.py`), after the
runner returns, build a per-attempt usage record keyed by `step`+`attempt`
(both already local variables) and the running per-task total (read the
existing total off `task.data`, add this run's counts), and return both via
`BehaviorResult(outcome, summary, data={...})`.
*Acceptance:* `BehaviorResult.data` contains a `tokens` entry for this
step/attempt and an updated `tokens_total`; the behavior performs no branch
on outcome or agent name (invariants #2, #14).

**FR-5 — per-attempt usage is persisted attempt-indexed.**
Add a `tokens` field to `HistoryEntry` (`src/harness/models.py`): an optional
dict of `{input, output, cache_read, cache_creation, total_cost_usd, model}`
(all-`None`/absent when not an agent step, e.g. `open-pr`/`open-issue`
finishers that never call `AgentRunner`), with matching `to_dict`/`from_dict`
(omit the key when `None`, mirroring the existing `outcome`/`summary`/
`reason` pattern). The consumer's `_deliver` (`src/harness/consumer.py`)
already merges `result.data` into `task.data`; give it the last piece — pull
the per-step usage back out of `result.data` and attach it to the
`HistoryEntry` it is about to append, so it rides the audit trail instead of
sitting only in `task.data`.
*Acceptance:* a `request_changes` re-run of a step produces two `HistoryEntry`
rows for that step, each with its own attempt-scoped `tokens`, neither
overwriting the other — mirrors invariant #10's attempt-indexing for
artifacts.

**FR-6 — per-task totals are stored on the task.**
`task.data["tokens_total"] = {"input": N, "output": M, ...}` accumulates as
each step delivers (computed in FR-4, merged into `task.data` by the existing
`_deliver` merge — no consumer change needed beyond what FR-5 requires).
*Acceptance:* after a multi-step task completes, `task.data["tokens_total"]`
equals the sum of every step/attempt's `input`/`output` tokens recorded in
history.

**FR-7 — routing never reads token data.**
Neither `router.py` nor `dispatcher.py` nor `consumer.py`'s outcome handling
reads `tokens`/`tokens_total` for any decision.
*Acceptance:* `tests/test_architecture.py` (or a new assertion alongside it)
confirms no such reference; behaviorally, a task with zero usage routes
identically to one with usage recorded.

**FR-8 — surfaced through the existing board/task API.**
`Task.to_dict()` already includes `data` and `history` verbatim, so
`/api/board` and `/api/tasks/{id}` expose `tokens_total` and per-entry
`tokens` with no route change. Add `input_tokens`/`output_tokens` (and
optionally `model`) to `AgentActivity` (`src/harness/ports/board.py`) and
populate them in `BoardProjection.agent_history`
(`src/harness/projection.py`), lifting from the same `HistoryEntry.tokens`
FR-5 introduced.
*Acceptance:* `AgentActivity.to_dict()` carries the new fields; existing
history entries with no `tokens` (older data, or non-agent steps) yield
`None`/absent, not an error.

**FR-9 — shown in the UI.**
Task detail (`src/harness/api/templates/_task.html`) shows per-task
input/output totals prominently (e.g. near `lastOutcome`) once populated, and
adds an input/output tokens column to the existing history table
(alongside `outcome`/`reason`), leaving other columns/rows unaffected when a
row has no `tokens`.
*Acceptance:* manual check against a task run through `FakeAgentRunner` with
scripted usage — verified via `/verify` after implementation, not asserted
here.

## Non-functional requirements

- **Never a routing input.** Token/model data is pure record-keeping; it must
  never leak into any `(status, lastOutcome)` decision (invariants #4, #8).
- **Backward compatible.** Existing tasks/history entries with no `tokens`
  key must load and render without error — `from_dict` defaults to `None`,
  templates guard on presence.
- **No new I/O or timing dependency.** All new test coverage stays in-memory
  and clock-free (`FakeAgentRunner`, `FakeClock`); the only place touching a
  real subprocess is the existing opt-in `tests/test_smoke_claude.py`.
- **Degrade, don't fail, on malformed usage.** A missing or unparseable
  `usage`/`model` field in the CLI's output must not raise — it's telemetry
  layered on top of an already-successful verdict parse, not a new failure
  mode for the run.

## Data model

- `AgentRun` (port, `ports/agent.py`): `+input_tokens: int`,
  `+output_tokens: int`, `+cache_read_tokens: int`,
  `+cache_creation_tokens: int`, `+total_cost_usd: float | None`,
  `+model: str | None`.
- `HistoryEntry` (`models.py`): `+tokens: dict[str, Any] | None` — shape
  `{"input": int, "output": int, "cache_read": int, "cache_creation": int,
  "total_cost_usd": float | None, "model": str | None}`, keyed implicitly by
  the entry's own `at`/`from_step` (attempt identity already lives in
  `task.data`/artifacts layout — the entry itself doesn't need a separate
  `attempt` field since `.artifacts/<id>/<step>-NN` already tracks that
  correspondence; the entry is simply one-row-per-attempt by construction).
- `task.data["tokens_total"]`: `{"input": int, "output": int}` — running sum
  across all steps/attempts, updated on every delivering step.
- `AgentActivity` (`ports/board.py`): `+input_tokens: int | None`,
  `+output_tokens: int | None`, `+model: str | None`.

## Interfaces

- No new endpoints. `/api/board` and `/api/tasks/{id}` (existing,
  `api/routes.py`) expose the new `task.data.tokens_total` and
  `history[].tokens` fields automatically via `Task.to_dict()`.
- Task detail template (`_task.html`) gains a totals readout and a history
  table column; no new route or JS wiring beyond template changes.

## Dependencies and scope

**Depends on:** current `ClaudeCliRunner`/`ClaudeCliBehavior`/`Consumer`
shape (all read above); no changes to `router.py`, `dispatcher.py`, or the
queue/workflow machinery.

**In scope:** capturing input/output (+cache) token counts and the resolved
model at the driver; carrying them through `BehaviorResult` → `HistoryEntry`
→ `task.data`; per-task total accumulation; board/task API + UI exposure;
an ADR documenting the new persisted data shape and that it's record-only.

**Out of scope (explicitly, per the issue):** a model → price table, any
dollar-cost computation or display beyond passing through the CLI's own
`total_cost_usd` for future cross-check, and any change to routing/dispatch
behavior.

## Rough plan

1. `ports/agent.py`: extend `AgentRun` with the new fields (FR-1).
2. `drivers/claude_cli.py`: parse `usage`/`model`/`total_cost_usd` out of the
   terminal `result` message and the `system/init` message in `_drain`;
   thread through `run()`'s three return paths (`try_verdict`,
   `_reprompt_verdict`, `fallback_verdict`) (FR-2). Add unit tests against
   synthetic stream-json transcripts.
3. `drivers/memory.py`: no structural change; add/confirm a unit test that
   scripts `FakeAgentRunner` usage (FR-3).
4. `behaviors/agent.py`: build the per-attempt usage record and updated
   running total, return via `BehaviorResult.data` (FR-4).
5. `models.py`: add `HistoryEntry.tokens` + `to_dict`/`from_dict` (FR-5).
6. `consumer.py`: pull per-step usage out of `result.data` onto the new
   `HistoryEntry` field it appends in `_deliver` (FR-5/FR-6 — confirm the
   existing `merged_data` shallow-merge already handles `tokens_total`
   correctly, likely no change needed there beyond the entry attachment).
7. `ports/board.py` + `projection.py`: extend `AgentActivity` and
   `agent_history` (FR-8).
8. `api/templates/_task.html`: show per-task totals and a history tokens
   column (FR-9).
9. `docs/adr/0021-record-agent-token-usage.md`: new ADR — context (usage
   discarded today), decision (capture at the driver, carry via
   `BehaviorResult.data`, persist attempt-indexed on `HistoryEntry` +
   summed in `task.data.tokens_total`, record-only — never routes, resolved
   model captured for future pricing), consequences (unblocks a pricing
   follow-up; no behavior change to routing).
10. Run `.venv/bin/pytest -q`, including `tests/test_architecture.py`, and
    fix any fallout.

## Open questions

- **Does `HistoryEntry` need an explicit `attempt` number alongside
  `tokens`?** The artifact layout already tracks attempts via
  `.artifacts/<id>/<step>-NN`; the plan assumes one history row per attempt
  is sufficient correlation and doesn't duplicate the attempt number onto
  the entry. If a later consumer needs to join history rows to specific
  artifact attempts programmatically (not just "the Nth entry for this
  actor"), add an explicit `attempt: int | None` field at the same time —
  flagging this now so the implementer decides before or during FR-5, not
  after.
- **Cache token fields: keep both `cache_read` and `cache_creation`, or
  collapse?** Default: keep both, since Anthropic's usage block reports them
  separately and collapsing loses information a pricing follow-up would want
  back. If `claude -p`'s emitted `usage` shape turns out not to carry them
  reliably, degrade gracefully (zero default) rather than blocking capture
  of input/output.
- **UI placement of the per-task total** — near `lastOutcome` in the detail
  panel's summary list is the plan's default; the implementer may find a
  better spot (e.g. a dedicated "cost" tab reserved for the pricing
  follow-up) without changing the underlying data contract.
