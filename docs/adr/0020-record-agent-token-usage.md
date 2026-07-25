# ADR-0020: Record agent token usage, per attempt and per task

Status: Accepted

## Context

Every agent step runs `claude -p --output-format stream-json` behind
`ClaudeCliRunner`. The CLI already reports Anthropic token usage — input
tokens, output tokens, cache read/creation tokens, and `total_cost_usd` — on
the terminal `type == "result"` stream-json message, and the resolved model
actually used (relevant once `fallback_model` kicks in and differs from
`spec.model`) on the `system`/`init` message. None of it reached the harness:
`_drain` kept only the terminal message itself, `AgentRun` had no fields for
usage, and neither `BehaviorResult` nor `HistoryEntry` nor `task.data` had
anywhere to put it. There was no visibility into what a task cost, per step
or in total — the prerequisite for a future pricing follow-up (a model →
price table, explicitly out of scope here) is simply having the token counts
and the model they were spent against.

## Decision

- **Capture at the driver, nowhere else.** `ClaudeCliRunner` is the sole
  translator from the Anthropic `usage` block to plain ints. `_drain` gains a
  fourth return value (the resolved model, read off `system`/`init`); a new
  pure `_usage_from_result(message) -> dict` reads `usage`/`total_cost_usd`
  off the terminal `result` message, degrading to zero/`None` on anything
  missing or malformed rather than raising — this is telemetry layered on an
  already-successful verdict parse, not a new failure mode for the run.
  `AgentRun` grows six new fields (`input_tokens`, `output_tokens`,
  `cache_read_tokens`, `cache_creation_tokens`, `total_cost_usd`, `model`),
  all defaulted so every existing construction site keeps compiling.
  `run()` computes usage/model once and layers them onto whichever
  `AgentRun` it ultimately returns via `dataclasses.replace`, rather than
  threading five extra parameters through `try_verdict`/`fallback_verdict`/
  `verdict_from_final`. The verdict re-prompt path (`_reprompt_verdict`) is
  a second, smaller call — its usage is that call's own envelope, but a
  resumed session doesn't re-emit `system/init`, so the caller passes in the
  already-resolved `model` from the original call rather than losing it.

- **`BehaviorResult` carries a per-delivery fact (`tokens`) separately from
  the task-level merge (`data`).** `Consumer._deliver` shallow-merges
  `result.data` into `task.data` *unconditionally* — every key in `data`
  becomes a top-level task key. Per-attempt token counts are a fact about
  *this delivery*, the same kind of fact `outcome`/`summary` already are
  (dedicated `BehaviorResult` fields the consumer reads directly to build
  the `HistoryEntry`, never routed through the generic `data` merge). Giving
  per-attempt usage its own `BehaviorResult.tokens` field — attached straight
  to the `HistoryEntry` the consumer is already building — avoids a `data`
  key silently becoming a task-level fact under a name that looks
  task-scoped but only ever carries the last step's numbers. The per-task
  *running total* genuinely is a task-level fact, so it goes through `data`
  as `tokens_total`, correctly accumulated by the existing merge.

- **Persisted attempt-indexed on `HistoryEntry`, summed on `task.data`.**
  `ClaudeCliBehavior.run` builds `tokens = {"attempt", "input", "output",
  "cache_read", "cache_creation", "total_cost_usd", "model"}` — `attempt` is
  already a local variable there, and stamping it removes any ambiguity
  about which artifact attempt a token record corresponds to (attempt
  numbering and `HistoryEntry` rows aren't in lockstep in general: a failed
  run consumes a history row but not an artifact slot, since `next_attempt`
  counts files on disk, not history entries). It also computes the new
  running total by reading `task.data.get("tokens_total")` and adding this
  run's counts, deliberately narrower than the per-entry shape (just
  input/output — a naive cross-model USD sum is exactly what a pricing
  follow-up needs to compute properly, not pre-sum here). Both are built
  unconditionally from whatever `AgentRun` came back, with no branch on
  outcome or agent name (invariants #2, #14).

- **Record-only — never a routing input.** `router.py`'s `route(task,
  workflow)` and `dispatcher.py` read only `task.status`/`task.last_outcome`/
  `task.step`; neither has ever touched `task.data` at all, so invariants #4
  and #8 hold structurally, not just by discipline. The one `consumer.py`
  change (`tokens=result.tokens` on the `HistoryEntry` it already builds) is
  an unconditional attribute read, not a new conditional — it doesn't
  register as a branch to `test_consumer_has_no_branch_on_outcome_value`,
  and shouldn't: this is delivery, not decision-making.

- **Surfaced through the existing board/task API, no new route.**
  `Task.to_dict()` already serializes `data` and `history` verbatim, so
  `tokens_total` and per-entry `tokens` are visible on `/api/board` and
  `/api/tasks/{id}` with zero route changes. `AgentActivity` (the
  agent-history read model) gains `input_tokens`/`output_tokens`/`model`,
  lifted in `BoardProjection.agent_history` exactly the way `outcome`/
  `summary`/`reason` already are. The task detail template shows the
  per-task total near `lastOutcome` and a tokens column in the history
  table — additive only, guarded so an old or in-flight task with no
  `tokens_total`/`tokens` renders unchanged.

## Consequences

- A step's cost in tokens, and the model that actually ran, is now on the
  audit trail — the direct prerequisite for a follow-up pricing feature
  (model → price table, dollar-figure display), which is explicitly not
  part of this change.
- `AgentRun`, `BehaviorResult`, and `HistoryEntry` each grow optional fields
  with safe defaults; every existing call site, and every on-disk task/
  history predating this change, keeps working unchanged (`from_dict`
  defaults `tokens` to `None`, `to_dict` omits it when absent).
- No routing or dispatch behavior changes — a task with usage recorded
  routes identically to one with all usage zeroed, since neither `router.py`
  nor `dispatcher.py` reads `task.data` at all.
- The reprompt path's model-threading is the one easy-to-regress corner: if
  a future change to `_reprompt_verdict` stops accepting/forwarding the
  caller's `model`, that path silently ships `model=None` for a codepath
  already exercised whenever a multi-outcome step forgets its verdict block.
