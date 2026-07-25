# Development — record per-step token usage, surface per-task totals

Implements the plan/design/architecture artifacts verbatim, including the
architecture assessment's corrections (`BehaviorResult.tokens` as a dedicated
field rather than a `data["tokens"]` key, an explicit `attempt` inside the
tokens record, ADR number 0020).

## What changed

**`src/harness/ports/agent.py`** — `AgentRun` gains six defaulted fields:
`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`,
`total_cost_usd`, `model`. `AgentRunner.run`'s docstring notes these are
best-effort telemetry.

**`src/harness/drivers/claude_cli.py`**
- New pure `_usage_from_result(message) -> dict` reads `usage`/
  `total_cost_usd` off a terminal `result` message, degrading to zero/`None`
  on anything missing or malformed (never raises).
- `_drain` now returns a fourth value, the resolved model read off the
  `system`/`init` message.
- `ClaudeCliRunner.run` computes usage/model once after `final` is
  established and layers them onto whichever `AgentRun` it returns
  (`try_verdict` success, `fallback_verdict` rescue, or the strict
  `verdict_from_final` raise path) via `dataclasses.replace`.
- `_reprompt_verdict` (the verdict re-prompt / `--resume` recovery path) now
  takes the outer call's resolved `model` as a parameter and layers its
  *own* envelope's usage plus that passed-in model onto its result — a
  resumed session doesn't re-emit `system/init`, so the model can only come
  from the original call.

**`src/harness/models.py`**
- `BehaviorResult` gains `tokens: dict | None = None` — a per-delivery fact
  read directly by the consumer onto the `HistoryEntry`, deliberately
  *not* routed through the `data` shallow-merge (which is unconditional and
  would otherwise leak a stray `task.data["tokens"]` key, as the
  architecture review caught).
- `HistoryEntry` gains `tokens: dict | None = None`, round-tripped through
  `to_dict`/`from_dict` following the existing omit-when-`None` pattern.

**`src/harness/consumer.py`** — `_deliver` attaches `result.tokens` directly
to the `HistoryEntry` it builds (no reordering needed — `result` was already
in scope). Purely additive; not a branch (`test_consumer_has_no_branch_on_outcome_value`
still passes).

**`src/harness/behaviors/agent.py`** — `ClaudeCliBehavior.run` builds, after
the commit, a `tokens` record (`attempt`, `input`, `output`, `cache_read`,
`cache_creation`, `total_cost_usd`, `model`) and a running `tokens_total`
(read the previous total off `task.data`, add this run's counts), returned as
`BehaviorResult(outcome, summary, data={"tokens_total": ...}, tokens={...})`.
Built unconditionally from whatever `run` came back with — no branch on
outcome or agent name (invariants #2, #14).

**`src/harness/ports/board.py`** — `AgentActivity` gains `input_tokens`,
`output_tokens`, `model` (all optional), serialized as `inputTokens`/
`outputTokens`/`model` in `to_dict()`.

**`src/harness/projection.py`** — `BoardProjection.agent_history` lifts the
new fields off `entry.tokens` the same way it already lifts `outcome`/
`summary`/`reason`.

**`src/harness/api/templates/_task.html`** — additive only: a `tokens` line
in the info panel (rendered only when `task.data.tokens_total` is present)
and a `tokens` column in the history table (`input / output`, `—` for a row
with none). Verified by direct Jinja render (see Verification below) and
`tests/test_api_html_mobile.py`.

**`docs/adr/0020-record-agent-token-usage.md`** — new ADR (0020, not 0021 —
the highest existing ADR is 0019) documenting the decision, in particular
the `BehaviorResult.tokens` vs `data` split and the record-only guarantee.

**`CLAUDE.md`** — two bullets (`ports/agent.py`, `behaviors/agent.py`) note
the new fields and point at ADR-0020.

**`tests/test_smoke_claude.py`** (opt-in, `HARNESS_SMOKE_CLAUDE=1`) — added
assertions that the `plan` step's history entry carries real, non-zero token
counts and a model, and that `task.data["tokens_total"]` reflects them —
this is the one place real `usage` parsing against the actual CLI gets
exercised, per the issue's own note.

## Tests added

- `tests/test_claude_cli.py` — `_usage_from_result` (full fields, missing
  usage, malformed fields, non-dict usage block); `ClaudeCliRunner.run`
  against a fake subprocess double (usage/model threading on a normal run,
  degrade-to-zero when `usage` is absent, and the reprompt path carrying its
  *own* usage but the *original* call's model); `_drain`'s existing test
  updated for the new 4-tuple return and asserts the resolved model.
- `tests/test_agent_ports.py` — `AgentRun` token field defaults;
  `FakeAgentRunner` scripting usage needs no new plumbing (FR-3).
- `tests/test_agent_behavior.py` — `BehaviorResult.tokens`/`data` shape on a
  normal run; multi-step token accumulation across two steps sharing a
  worktree (FR-6); a `request_changes` re-run of the same step producing a
  second attempt with its own, non-overwritten token counts (FR-5).
- `tests/test_models.py` — `BehaviorResult.tokens` default/carry;
  `HistoryEntry.tokens` round-trip, omission when absent, and
  backward-compatible default when loading history with no `tokens` key.
- `tests/test_consumer.py` — `result.tokens` lands on the `HistoryEntry`,
  never leaks into `task.data`; absent when the behavior doesn't set it.
- `tests/test_projection.py` — `agent_history` lifts tokens onto
  `AgentActivity`; defaults to `None` when the entry has none.
- `tests/test_board_port.py` — `AgentActivity.to_dict()` carries/defaults the
  new fields.

## Verification

```sh
.venv/bin/pytest -q
```
1374 passed, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE` smoke), including
`tests/test_architecture.py` (25/25) — router/dispatcher never read
`task.data`, so routing-neutrality holds structurally, not just by
discipline.

Manually confirmed the template renders correctly for both a token-carrying
history row and a token-less one (a Jinja `Environment` render against
`_task.html` with a scripted `Task`/`HistoryEntry`, asserting the totals line
and the `100 / 40` / `—` cells appear as expected).

To see it against the real CLI:
```sh
HARNESS_SMOKE_CLAUDE=1 .venv/bin/pytest tests/test_smoke_claude.py -q
```

Note: this repo's `.venv` did not exist at the start of this session (only
`pyproject.toml`); it was created fresh with `python3.11 -m venv .venv &&
pip install -e ".[dev]"` per `CLAUDE.md`'s "Working here" section, no
change to project config.

One environment note unrelated to this change: running the full suite with
`HARNESS_HEAL_REPO`/`GITHUB_TOKEN` set in the shell makes 8 pre-existing
`test_cli.py` tests fail (they assert `stderr == ""`/an exact served-workflow
list, and a real `GITHUB_TOKEN` changes CLI wiring). Verified this reproduces
identically on the pre-change tree, so it isn't caused by this work — run
with `env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN .venv/bin/pytest -q` for a
clean signal.
