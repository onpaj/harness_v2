# Plan — state the single-shot contract explicitly; harden the re-prompt

## Summary

`ClaudeCliBehavior`/`ClaudeCliRunner` already run a step's persona as one
non-interactive `claude -p` turn and already recover a *missing* verdict block
for a run that otherwise finished (fix A: single-outcome fallback, fix C:
resume-and-re-prompt for a multi-outcome step — both landed in `6333c6e`).
This task closed anyway, because the agent didn't merely forget the closing
block — it deliberately deferred, launched verification in the background,
and ended its turn expecting a second turn ("will resume once notified") that
single-shot `claude -p` never grants. Fix C's own re-prompt uses the *same*
resumed session and only says "reply with the verdict now" — it does not tell
the agent the background work will never be checked again, so an agent that
is still waiting has no reason to change its answer. The fix is two additive
changes: state the single-shot contract in the composed prompt so the
deferral doesn't happen in the first place, and strengthen the re-prompt's
own wording for the case where it happens anyway.

## Context

Reported by the harness's own self-heal pipeline
(`<!-- harness-issue:tsk_ca233edc62ea40e2:c245ee6a -->`) against a
`development`-workflow task in `Anela.Heblo`: plan, design, architecture and
development all completed real work (clean build, `dotnet format
--verify-no-changes`, passing tests) but the task still landed in `failed/`
with `verdict is not readable JSON: 'Waiting for the two background
verification commands ... to finish — will resume once notified.'`

That is `_verdict_from_envelope`'s strict raise (`claude_cli.py:143`), reached
only after both recovery paths in `ClaudeCliRunner.run` already gave up: the
first pass (`try_verdict`) found no parseable verdict, the resume re-prompt
(when the step allows more than one outcome and a `session_id` is present)
came back empty too, and `fallback_verdict` (the single-outcome rescue) does
not apply to a step with more than one allowed outcome. Whichever of those
two recovery paths was live for this step, the underlying cause is the same:
the model believes it is mid-conversation and a later turn will pick up where
this one left off. Nothing in the composed prompt (`compose_prompt`,
`behaviors/agent.py`) or the re-prompt text (`_verdict_reprompt`,
`drivers/claude_cli.py`) currently says otherwise. The routing/consumer split
(invariant #3) worked exactly as designed — the task correctly failed instead
of silently reporting a false `done` — but a completed unit of real work
(build, format, tests all green) was discarded and must be redone from
scratch on retry.

## Functional requirements

**FR-1 — State the single-shot contract in the composed prompt.**
`compose_prompt` (`behaviors/agent.py`) gains a fixed paragraph, present in
every step's prompt regardless of persona or outcome set, stating: this is
exactly one non-interactive turn; there is no follow-up turn and nothing will
"resume once notified"; any command whose result the verdict depends on must
be run to completion synchronously within this turn — no launching a
long-running or background job and ending the turn early; the fenced verdict
JSON block must be the last content of the message.
- Acceptance: `compose_prompt(...)` output contains this paragraph verbatim
  (or a stable, greppable substring of it) for every call, independent of
  `outcomes`/`hints`/`description`. A unit test asserts the paragraph appears
  before the existing "Finish by choosing exactly one outcome" section (so it
  reads as scene-setting, not an afterthought) and that none of the existing
  `compose_prompt` tests (`test_agent_behavior.py`) need their other
  assertions changed — this is additive.
- Acceptance: the paragraph names no tool or command specifics (it must hold
  for every persona/repo, per invariant #14 — no branching on agent identity)
  and gives the model deterministic, unambiguous language ("run to
  completion," "no background job," "last content of the message") rather
  than a soft suggestion.

**FR-2 — Strengthen the resume re-prompt for a step that already deferred.**
`_verdict_reprompt` (`drivers/claude_cli.py`) currently only complains the
verdict block was missing. Add a sentence covering the deferral case
specifically: whatever background command was started will not be waited for
or checked by this process; if it hasn't already finished, either check its
result now (poll/wait on it inline) or decide based on what already ran, but
the turn ends now with a verdict — there will not be a further turn.
- Acceptance: `_verdict_reprompt(allowed)`'s returned text still ends with the
  same fenced-JSON template (existing tests keep passing) and additionally
  contains the new sentence. A new unit test in `test_claude_cli.py` asserts
  its presence.
- Acceptance: no change to `_reprompt_verdict`'s control flow, retry count, or
  timeout handling — this is a wording-only change to the one existing
  re-prompt call, not a new call or a second retry (a step that still can't
  produce a verdict after this must still fail, per invariant #3).

**FR-3 — Cover the reported failure shape with a regression test.**
Add a test exercising the exact scenario: a single `claude -p` stream-json
run whose terminal `result` is pure deferral narration (no fenced block) for
(a) a single-outcome step, confirming `fallback_verdict` already rescues it
today (a characterization test, since FR-1 should make this rarer but the
existing rescue must not regress), and (b) a multi-outcome step, confirming
the resume re-prompt is attempted and — when the resumed session gives an
equally unhelpful "still waiting" reply — the run still raises `VerdictError`
(the correct terminal behavior; this task doesn't add a second retry).
- Acceptance: both tests live in `tests/test_claude_cli.py` alongside the
  existing fix-A/fix-C tests (`test_fallback_single_outcome_synthesizes_the_
  only_outcome`, `test_run_reprompt_path_carries_its_own_usage_but_original_
  model`) and use the same `FakeAgentRunner`-adjacent scripted-subprocess
  style already in that file — no new test infrastructure.

## Non-functional requirements

- **No new agent turn.** FR-2 is a wording change to an existing call; it
  must not add latency, cost, or a new subprocess invocation for the common
  case (a persona that already returns a clean verdict pays nothing extra).
- **No behavior/routing change.** Neither FR-1 nor FR-2 touches `outcome`
  values, the dispatcher, or the consumer — invariants #2/#3/#13 are
  unaffected. This is prompt text and error-message text only.
- **Backward compatible.** Every existing `compose_prompt` and
  `_verdict_reprompt` test must keep passing unmodified except where they
  assert on the exact full string (if any do, they gain the new substring,
  they don't lose old assertions).

## Data model

No data model changes. No new fields on `Task`, `AgentSpec`, `AgentRun`, or
`BehaviorResult`. This is a text-composition change in two existing pure
functions (`compose_prompt`, `_verdict_reprompt`).

## Interfaces

No new ports, endpoints, or events. `compose_prompt`'s and `_verdict_
reprompt`'s signatures are unchanged — only their returned string content
grows.

## Dependencies and scope

Rests on:
- `behaviors/agent.py::compose_prompt` (FR-1)
- `drivers/claude_cli.py::_verdict_reprompt` (FR-2)
- The existing fix-A/fix-C recovery machinery (`try_verdict`,
  `fallback_verdict`, `_reprompt_verdict`) from `6333c6e`, which this task
  extends rather than replaces.

Out of scope:
- Any second re-prompt attempt, retry loop, or extending the resume mechanism
  to single-outcome steps (single-outcome already has an unconditional
  rescue via `fallback_verdict` — it needs no resume at all).
- Detecting "deferral narration" heuristically (e.g. regex-sniffing phrases
  like "will resume" or "waiting for") to trigger a special code path. The
  chosen fix is prevention (tell the agent up front) plus a clearer generic
  re-prompt, not classification of failure text.
- Changing `AgentSpec`/persona files themselves (e.g. `agents/development.
  json`) — the single-shot contract belongs in the harness-composed prompt
  (`compose_prompt`), which every persona shares, not in per-persona prompts
  invariant #14 says must not encode this kind of thing individually.
- Any change to `verify`/build/test command execution itself — the fix is
  about what the agent is told about turn structure, not about how commands
  are run.

## Rough plan

1. Add the single-shot-contract paragraph to `compose_prompt` in
   `behaviors/agent.py`, placed after the task/artifact framing and before
   the "Finish by choosing exactly one outcome" section.
2. Update/add unit tests in `tests/test_agent_behavior.py` asserting the new
   paragraph is present and ordered correctly, without altering existing
   assertions.
3. Add the deferral-specific sentence to `_verdict_reprompt` in
   `drivers/claude_cli.py`.
4. Add/update unit tests in `tests/test_claude_cli.py`: the wording assertion
   for `_verdict_reprompt`, plus the two regression tests from FR-3 (single-
   outcome rescue characterization, multi-outcome still-fails-cleanly case).
5. Run the full suite (`.venv/bin/pytest -q`) and confirm no existing test
   needed a behavior change, only additive assertions.
6. Design/architecture steps (next in this task's own workflow) confirm
   there's no structural change needed beyond the two text edits — this is
   deliberately a small, surgical fix, not a redesign of the verdict-recovery
   mechanism.

## Open questions

- **Exact wording of the two paragraphs** is a copy-editing decision best
  left to the development step — I've specified the required *content*
  (FR-1, FR-2) precisely enough to test against, but not a literal final
  string, so the implementer has room to phrase it naturally within the
  existing prompt's voice.
- **Whether FR-3(b)'s "still fails cleanly" is the right final behavior for a
  step that defers twice in a row** (once initially, once on re-prompt): this
  plan's default is yes — per invariant #3 and this project's explicit
  non-goal of adding a second retry, a persona that ignores both the
  strengthened prompt and the strengthened re-prompt has a genuine defect
  worth surfacing as `failed/`, not papering over with more retries. If a
  future incident shows FR-1+FR-2 aren't sufficient in practice, that's a
  separate follow-up, not scope creep on this one.
