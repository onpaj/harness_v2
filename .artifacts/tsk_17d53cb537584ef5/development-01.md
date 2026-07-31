# Development — state the single-shot contract explicitly; harden the re-prompt

Implemented exactly the design in `design-01.md` (FR-1/FR-2/FR-3). Both changed
functions stay pure text builders; no new functions, call sites, control flow,
ports, or data-model changes.

## Files changed

- `src/harness/behaviors/agent.py` — `compose_prompt`: inserted the new
  single-shot-contract paragraph into the existing `lines.extend([...])` call,
  right after the artifact-write line and before "Finish by choosing exactly
  one outcome:". Verbatim text from the design, including the two stable
  anchor substrings (`"single non-interactive turn"`,
  `"Do not launch it in the background"`).
- `src/harness/drivers/claude_cli.py` — `_verdict_reprompt`: inserted the new
  deferral-specific sentence between the existing opening sentence and the
  closing "Reply with ONLY the verdict now..." instruction. Fenced JSON
  template tail is byte-identical to before.
- `tests/test_agent_behavior.py`:
  - Updated `test_compose_prompt_unchanged_when_body_absent` (full-string
    equality) to include the new paragraph at its exact position.
  - Added `test_compose_prompt_states_the_single_shot_contract` — checks both
    anchor substrings are present and that the new paragraph precedes the
    outcome list.
- `tests/test_claude_cli.py`:
  - Added `_verdict_reprompt` to the existing import block.
  - Added `test_verdict_reprompt_tells_the_agent_not_to_wait_on_background_work`
    — checks the new sentence and that the fenced template tail is unchanged.
  - Added `test_run_rescues_single_outcome_deferral_narration` — characterizes
    the exact reported-production shape (single-outcome step, deferral
    narration as final text) being rescued by the existing `fallback_verdict`
    path (fix A), confirming it isn't regressed by this change.
  - Added `test_run_raises_when_multi_outcome_deferral_persists_through_reprompt`
    — a multi-outcome step whose deferral narration persists through the one
    resume re-prompt still raises `VerdictError` (→ `failed/`), exactly 2
    calls made, no extra retry loop introduced.

## Verification

```
.venv/bin/pytest -q
```

Result: `1513 passed, 1 skipped` (the skip is the pre-existing opt-in
`HARNESS_SMOKE_CLAUDE=1` smoke test, unaffected by this change).

Ran the new/changed tests in isolation to confirm they exercise the new code:

```
.venv/bin/pytest -q tests/test_agent_behavior.py tests/test_claude_cli.py \
  -k "single_shot or states_the_single_shot or verdict_reprompt_tells or \
      rescues_single_outcome_deferral or raises_when_multi_outcome_deferral or \
      unchanged_when_body_absent"
```

Result: `5 passed`.

Also re-ran `tests/test_architecture.py` (27 passed) — the layering guards
(behaviors import only ports, no dispatcher/consumer import of drivers) are
untouched, as expected for a string-only change confined to
`behaviors/agent.py::compose_prompt` and `drivers/claude_cli.py::_verdict_reprompt`.

```json
{"outcome": "done", "summary": "Added the single-shot-contract paragraph to compose_prompt and the deferral-specific sentence to _verdict_reprompt, plus FR-1/FR-2/FR-3 tests (updated full-string test, new contract-wording test, new reprompt-wording test, and two FR-3 regression tests for single- and multi-outcome deferral). Full suite: 1513 passed, 1 skipped (pre-existing opt-in smoke)."}
```
