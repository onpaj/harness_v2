# Review — state the single-shot contract explicitly; harden the re-prompt

## What I checked

Diffed the actual source/test changes against `design-01.md` (`git diff
1b3349c^..5e5b231 -- src/harness/behaviors/agent.py
src/harness/drivers/claude_cli.py tests/test_agent_behavior.py
tests/test_claude_cli.py`), read both changed functions in full context, and
re-read the plan/design for the requirements this diff must satisfy.

## FR-1 — `compose_prompt` (behaviors/agent.py)

- New paragraph inserted verbatim at the exact position the design specifies:
  after the artifact-write line, before `"Finish by choosing exactly one
  outcome:"` — confirmed by reading `agent.py:186-196` directly, not just the
  diff.
- Contains both anchor substrings (`"single non-interactive turn"`,
  `"Do not launch it in the background"`), names no tool/command specifics
  (invariant #14 — no branching on persona identity), and is unconditional
  (part of the same `lines.extend([...])` call, so every step/persona gets it
  regardless of `outcomes`/`hints`/`description`).
- `compose_prompt`'s signature and every other line group are untouched. The
  one full-string test (`test_compose_prompt_unchanged_when_body_absent`) was
  updated in place, not weakened. The new
  `test_compose_prompt_states_the_single_shot_contract` asserts both
  substrings and the before-the-outcome-list ordering, matching FR-1's
  acceptance criteria exactly.

## FR-2 — `_verdict_reprompt` (drivers/claude_cli.py)

- New sentence inserted between the existing opening sentence and the closing
  "Reply with ONLY the verdict now" instruction — matches the design's
  placement.
- Fenced-JSON template tail is byte-identical to before (confirmed by reading
  `claude_cli.py:194-208`); function signature, call site
  (`_reprompt_verdict`), retry count and timeout handling are untouched — no
  new subprocess call, no new retry loop, matching the plan's explicit
  non-goal.
- `test_verdict_reprompt_tells_the_agent_not_to_wait_on_background_work`
  covers the new sentence and the unchanged template tail.

## FR-3 — regression tests

- `test_run_rescues_single_outcome_deferral_narration` reproduces the exact
  reported production shape (single-outcome step, pure deferral narration as
  final text, no fenced block) and confirms `fallback_verdict` still rescues
  it — a genuine characterization test guarding against regressing the
  existing fix-A path.
- `test_run_raises_when_multi_outcome_deferral_persists_through_reprompt`
  confirms the resume re-prompt is attempted exactly once (`len(calls) == 2`)
  and that a step still deferring after both nudges correctly raises
  `VerdictError` — the correct terminal behavior per invariant #3, not a new
  retry loop.

## Correctness / invariants

- No data model, port, or control-flow changes — confirmed by reading both
  functions end to end, not just the diff hunks. `ClaudeCliRunner.run`'s
  `try_verdict` → resume re-prompt → `fallback_verdict` → strict raise
  sequence is byte-identical apart from the reprompt's returned string.
- No branch on outcome/agent identity added anywhere (invariants #2, #14).
- `dispatcher.py`/`consumer.py` untouched; nothing here touches routing.

## Verification

`verify-01.md` shows the full suite green: `1513 passed, 1 skipped` (the skip
is the pre-existing opt-in `HARNESS_SMOKE_CLAUDE=1` smoke test). Development's
own artifact reports the identical result plus an isolated run of the new/
changed tests (5 passed) and `test_architecture.py` (27 passed, layering
guards unaffected).

## Verdict

Implementation matches the design precisely — correct wording, correct
placement, no unintended side effects, and the regression tests cover both
the rescued (single-outcome) and still-fails (multi-outcome) shapes called
for in the plan. Nothing to send back.

```json
{"outcome": "done", "summary": "Implementation matches design-01.md exactly: compose_prompt gained the single-shot-contract paragraph at the specified position, _verdict_reprompt gained the deferral-specific sentence with its template tail unchanged, and both FR-3 regression tests (single-outcome rescue, multi-outcome still-fails) are present and correct. No control-flow, port, or data-model changes; full suite green (1513 passed, 1 skipped)."}
```
