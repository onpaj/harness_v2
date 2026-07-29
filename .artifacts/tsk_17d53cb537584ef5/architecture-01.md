# Architecture assessment — state the single-shot contract explicitly; harden the re-prompt

## Verdict

**Approved as designed.** This is a two-paragraph text change confined to two
already-pure functions (`compose_prompt`, `_verdict_reprompt`). I cross-checked
every claim in `design-01.md` against the current source
(`src/harness/behaviors/agent.py`, `src/harness/drivers/claude_cli.py`) and the
existing test suite (`tests/test_agent_behavior.py`,
`tests/test_claude_cli.py`) — line numbers, function bodies, control flow and
test names all match what the design describes. Nothing here requires a
structural decision; this note exists to record that the check was done and
why no invariant is at risk.

## Alignment with existing patterns and invariants

- **No layering violation.** `compose_prompt` lives in `behaviors/agent.py`
  (behavior layer, touches only ports); `_verdict_reprompt` lives in
  `drivers/claude_cli.py` (driver layer, implements `AgentRunner`). The two
  functions gain text independently — neither call site changes, and neither
  file gains a new import of the other. `test_architecture.py`'s
  behaviors-import-only-ports guard is untouched.
- **Invariant #13 (agent behind `AgentRunner`)** — untouched. `ClaudeCliBehavior`
  still knows nothing of subprocesses; the new paragraph is composed the same
  way the existing verdict-block paragraph already is, inside the same
  `lines.extend([...])`/`"\n".join(lines)` shape.
- **Invariant #14 (persona is data, not code)** — the new `compose_prompt`
  paragraph is unconditional and persona-agnostic: same text for every step
  regardless of `AgentSpec` contents, `outcomes`, `hints`, or `description`.
  It names no tool/command specifics, matching the plan's explicit acceptance
  criterion. No branch on agent name is introduced anywhere.
- **Invariant #3 (dispatcher owns routing; consumer writes `failed` only on
  its own inability to deliver)** — verified against the live control flow in
  `ClaudeCliRunner.run` (`claude_cli.py:400-481`): `try_verdict` →
  (multi-outcome + session_id) `_reprompt_verdict` → `fallback_verdict` →
  strict raise via `verdict_from_final`. FR-1/FR-2 touch only the *text* fed
  into this flow (the composed prompt, and the one existing re-prompt
  string); no new branch, no new call, no change to when `VerdictError`
  propagates. A step that still can't produce a verdict after both
  strengthened texts still raises and still lands in `failed/` — the
  designed non-goal (no second retry) is what invariant #3 requires anyway.
- **Invariant #42 (a step's outcome vocabulary comes from the workflow)** —
  unaffected; `outcomes`/`hints`/`description` are still computed exactly as
  before in `ClaudeCliBehavior.run` and passed through unchanged. The new
  paragraph doesn't reference outcomes at all, so it can't drift from the
  live vocabulary the way a hardcoded outcome list would.
- **Module map / ports** — no new port, no new field on `Task`, `AgentSpec`,
  `AgentRun`, or `BehaviorResult`. Confirmed by reading both functions'
  current signatures: neither gains a parameter.

## Source-level verification performed

1. `compose_prompt` (`behaviors/agent.py:147-210`): the insertion point the
   design specifies — after the artifact-write line
   (`"Write your output for this step to the file {artifact_relpath}."`) and
   before `"Finish by choosing exactly one outcome:"` — is exactly the blank
   line at the current lines 187/188/189. The design's placement claim is
   correct against the file as it stands today, not a stale reference.
2. `_verdict_reprompt` (`drivers/claude_cli.py:194-204`): current body is a
   two-sentence intro + fenced template, exactly as the design's "before"
   description states. The insertion point (a new middle sentence between the
   opening complaint and the closing "Reply with ONLY the verdict now")
   is unambiguous and doesn't disturb the fenced-template tail the design
   promises stays byte-identical.
3. `ClaudeCliRunner.run`'s recovery ladder (`claude_cli.py:453-481`) and
   `_reprompt_verdict` (`claude_cli.py:483-538`) confirm the design's FR-3(b)
   regression test is physically accurate: the resume path calls
   `asyncio.create_subprocess_exec` exactly once more (via `try_verdict` on
   its own envelope), any failure there degrades to `None`, and
   `fallback_verdict` correctly declines for `len(allowed) > 1`, falling
   through to the strict raise. The "2 calls total, then `VerdictError`"
   assertion in the design's test sketch is achievable with the current code,
   not aspirational.
4. All eleven test names the design references
   (`test_compose_prompt_unchanged_when_body_absent`,
   `test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes`,
   `test_compose_prompt_renders_hint_and_description`,
   `test_compose_prompt_includes_issue_body_when_present`,
   `test_compose_prompt_treats_whitespace_only_body_as_absent`,
   `test_compose_prompt_does_not_duplicate_body_equal_to_request`,
   `test_compose_prompt_demands_the_verdict_block_as_the_last_thing`,
   `test_fallback_single_outcome_synthesizes_the_only_outcome`,
   `test_run_reprompt_path_carries_its_own_usage_but_original_model`) exist at
   the referenced locations. The one full-string assertion the design flags
   as needing an update (`test_compose_prompt_unchanged_when_body_absent`,
   `test_agent_behavior.py:430-452`) is confirmed to assert full `==` equality
   today — the design's claim that only this one test needs its expected
   value edited (not its assertion style) is accurate.

## Implementation guidance

No new components. Two edits, in this order (matches the design's own
sequencing):

1. `behaviors/agent.py::compose_prompt` — insert the single-shot-contract
   paragraph + blank line into the existing `lines.extend([...])` call
   between the artifact-write line and `"Finish by choosing exactly one
   outcome:"`.
2. `drivers/claude_cli.py::_verdict_reprompt` — insert the deferral sentence
   between the existing opening sentence and the closing instruction; keep
   the fenced-template tail byte-identical.
3. Tests: update the one full-string test, add the new substring/ordering
   test for FR-1, add the wording test for FR-2, add both FR-3 regression
   tests reusing `_FakeRunProc`/`_FakeCommunicateProc`/`_stream_json`.
4. `.venv/bin/pytest -q` for the full suite.

No prerequisite work is needed — both target functions and all fixtures the
design leans on already exist in the shape the design assumes.

## Risks and mitigations

- **Wording drift vs. test substrings.** The design pins two anchor
  substrings per function (`"single non-interactive turn"` /
  `"Do not launch it in the background"` for FR-1; the deferral sentence for
  FR-2). Low risk: these are implementation-stage copy-editing choices
  already flagged as open by the design itself (plan's "Open questions") —
  the development step should keep whatever exact phrasing it lands on in
  sync between the prompt text and its own test assertions.
- **Prompt-length growth.** One paragraph per turn, no per-outcome
  duplication — negligible token cost, no mitigation needed.
- **False sense of guarantee.** Stating the contract in the prompt is
  prevention, not enforcement — a model can still ignore it (FR-3(b) is the
  designed proof that the harness still fails safely when it does). This is
  the correct trade-off per the plan's explicit non-goal of heuristic
  deferral-detection; no architectural gap here, just a reminder that FR-2's
  re-prompt (not FR-1's prevention) is the actual backstop.

## Prerequisites before implementation begins

None. Proceed directly to development.

```json
{"outcome": "done", "summary": "Verified the design's exact wording/placement claims for compose_prompt and _verdict_reprompt against the current source and referenced tests — all line numbers, control flow, and fixture assumptions check out; no invariant is touched (no new ports, no branching on outcome/agent, no layering violation), no structural changes needed, cleared to proceed straight to development."}
```
