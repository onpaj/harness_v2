# Review — record per-step token usage, surface per-task totals

## Verdict: done

## What I checked

Read `plan-01.md`, `design-01.md`, `architecture-01.md`, `development-01.md`,
`development-02.md`, `verify-01.md`, `verify-02.md`, then read the actual diff
(`git diff 37aeaba cf9ee0d`) rather than trusting the artifacts' narration —
every file the development artifacts claim to have touched, I read directly.

### Conformance to spec / acceptance criteria

- **`AgentRun` carries token counts** (`ports/agent.py`): `input_tokens`,
  `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`,
  `total_cost_usd`, `model`, all defaulted — every existing construction site
  still compiles. ✅
- **Resolved model captured**: `_drain` (`claude_cli.py`) now returns a 4-tuple
  including the model read off `system`/`init`; `_reprompt_verdict` correctly
  threads in the *original* call's model since a resumed session doesn't
  re-emit `system/init`. ✅
- **Per-attempt persistence, attempt-indexed**: `ClaudeCliBehavior.run` stamps
  `attempt` (the same local variable driving `next_attempt`/artifact
  placement) onto the `tokens` dict, carried via the dedicated
  `BehaviorResult.tokens` → `HistoryEntry.tokens` field — correctly *not*
  routed through the generic `data` shallow-merge, which would otherwise leak
  a stray task-level `tokens` key (the exact bug the architecture step caught
  and the implementation avoids). Verified `next_attempt` semantics and the
  `request_changes` re-run test (`test_request_changes_rerun_gets_its_own_attempt_and_tokens`)
  — two attempts, two distinct token records, neither overwritten. ✅
- **Per-task total**: `task.data["tokens_total"]` accumulated by reading the
  previous total off `task.data` and adding the current run's input/output —
  goes through the existing `data` merge, correctly a task-level fact.
  Multi-step accumulation verified in both the diff
  (`test_multi_step_task_accumulates_tokens_across_steps`) and by tracing the
  consumer's `merged_data = {**task.data, **(result.data or {})}` path. ✅
- **Surfaced via board/task API**: `Task.to_dict()` serializes `data`/`history`
  verbatim (no route changes needed, matches design). `AgentActivity` gained
  `input_tokens`/`output_tokens`/`model`, lifted in
  `BoardProjection.agent_history` the same way `outcome`/`summary` already
  are. `_task.html` shows the per-task total and a per-row tokens column,
  guarded against absence (`{% if task.data.tokens_total %}`, `if entry.tokens
  else "—"`). ✅
- **Record-only, no routing input**: confirmed `router.py`/`dispatcher.py`
  never read `task.data` at all (structural, not just discipline — matches the
  ADR's own claim). `consumer.py`'s one change (`tokens=result.tokens`) is an
  unconditional attribute read on the `HistoryEntry` constructor call, not a
  new conditional — `test_consumer_has_no_branch_on_outcome_value` (now
  AST-based in `test_architecture.py`) still passes. ✅
- **`FakeAgentRunner` scriptable, in-memory tests**: `AgentRun`'s new fields
  are plain constructor kwargs, no new plumbing needed in the fake runner.
  Tests cover per-attempt counts, multi-step totals, and the
  `request_changes` re-run scenario exactly as required. ✅
- **`.venv/bin/pytest -q` green**: re-ran it myself, both under the ambient
  shell environment (`HARNESS_HEAL_REPO`/`GITHUB_TOKEN` set) and with
  `env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN` — **1374 passed, 1 skipped** in
  both cases, including `test_architecture.py`. The attempt-02 fix for the 8
  `test_cli.py` failures (ambient-env leakage into tests that assumed a clean
  environment, unrelated to this feature) is exactly the right fix: isolates
  the affected tests with `monkeypatch.delenv`, touches no production code. ✅

### Architecture adherence

- No new port needed; correctly reuses `BehaviorResult.data` for the task-level
  fact and adds a dedicated `BehaviorResult.tokens`/`HistoryEntry.tokens` field
  for the per-delivery fact — this distinction is the one structural
  correction the architecture step made to the original plan, and it's
  implemented faithfully.
- ADR-0020 (correct number — 0020, not 0021) is thorough: documents the
  capture point, the `tokens` vs `data` split and why, attempt-indexing
  rationale, the record-only guarantee, and the reprompt path's model-only
  threading as a known regression risk.
- Invariants #2 (consumer decides nothing), #4 (router pure), #8
  (route/dispatch never read `data`/`repository`/`step`), #10 (attempt-indexed
  records), #14 (persona is data, no branch on agent name) all verified intact
  by direct inspection, not just by re-reading the artifact's claims.

### Correctness

- `_usage_from_result` degrades safely on missing/malformed `usage`/
  `total_cost_usd` (bool excluded from the int/float check, non-dict `usage`
  handled) — no new failure mode layered onto an already-successful verdict
  parse, matching the design's tolerant-parsing precedent (`_extract_verdict`).
- `with_usage`/`dataclasses.replace` correctly layers usage onto whichever
  `AgentRun` the runner ultimately returns (try_verdict / reprompt /
  fallback_verdict / strict raise-path), so no exit path silently drops usage.
- No security, concurrency, or backward-compatibility issues: all new fields
  are optional/defaulted, `from_dict`/`to_dict` round-trip correctly omitting
  absent `tokens`, old on-disk tasks/history load unchanged.

No functional requirement is missing, no invariant is violated, no required
test is missing, and the full suite is green in the actual verify environment.
