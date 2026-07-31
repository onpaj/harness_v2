# Design — state the single-shot contract explicitly; harden the re-prompt

No user interface is involved (this is prompt/error-text composed for the
agent CLI subprocess and consumed by pure parsing functions) — the UX/UI
section is omitted per the plan.

## Overview

Two existing pure text-composition functions each grow by one paragraph/
sentence; no new functions, no new call sites, no new control flow. This
document pins the exact wording, exact placement, and the exact test
additions so FR-1/FR-2/FR-3 from `plan-01.md` are unambiguous to implement.

```
behaviors/agent.py :: compose_prompt        (FR-1 — prevent the deferral)
drivers/claude_cli.py :: _verdict_reprompt   (FR-2 — recover from it anyway)
tests/test_agent_behavior.py                 (FR-1 tests)
tests/test_claude_cli.py                     (FR-2 + FR-3 tests)
```

Both functions stay pure (task/allowed-outcomes/etc. in, `str` out) — the
change is confined to the string they return. `ClaudeCliRunner.run`'s control
flow (`try_verdict` → resume re-prompt for multi-outcome → `fallback_verdict`
→ strict raise) is untouched, matching the plan's non-functional requirement
that this is wording-only.

## Component design

### FR-1 — `compose_prompt` (`behaviors/agent.py:147`)

**Placement.** Insert a new paragraph into the existing `lines` list, after
the artifact-framing block (`"Write your output for this step to the file
{artifact_relpath}."` + the blank line that follows it) and before the
`"Finish by choosing exactly one outcome:"` line. It reads as scene-setting
that applies uniformly, ahead of the per-outcome list — not appended as an
afterthought near the closing verdict block.

**Exact text** (own list item between the current lines 187 and 189 of
`agent.py`, i.e. right after the artifact-write line, still inside the same
`lines.extend([...])` call so it participates in the existing single
`"\n".join(lines)`):

```python
"This is a single non-interactive turn: there is no follow-up turn, and "
"nothing you start now will be resumed or checked later. If your verdict "
"depends on a command's result — a build, a test run, a formatter, "
"anything — run it to completion in this turn before you answer. Do not "
"launch it in the background and end the turn expecting to be notified "
"when it finishes; that will not happen.",
"",
```

**Why this wording.** It satisfies the plan's constraints directly:
- Names no tool/command specifics ("a build, a test run, a formatter,
  anything" is illustrative, not an enumerated allow-list) — holds for every
  persona per invariant #14.
- States the mechanism, not just the rule ("there is no follow-up turn...
  that will not happen") so an agent that's tempted to defer understands
  *why* deferring fails, not just that it's forbidden.
- Uses the same declarative, unambiguous register as the existing verdict
  paragraph ("MUST end with exactly this fenced verdict block") rather than
  a soft suggestion.
- Two stable substrings anchor the test: `"single non-interactive turn"` and
  `"Do not launch it in the background"`.

**Unaffected.** `compose_prompt`'s signature, its four other line groups
(role/description, task/body, artifact framing, verdict block), and every
existing assertion that checks *presence* of a substring (task text, hints,
outcomes, artifact paths) are untouched — the new paragraph sits strictly
between two already-distinct sections.

**One test needs a full-string update, not a loss of coverage.**
`test_compose_prompt_unchanged_when_body_absent` (`test_agent_behavior.py:430`)
asserts the *entire* rendered prompt via `==`. That test's expected string
gains the new paragraph verbatim (inserted at the same position as the real
change) and keeps every other line as-is — it stays a full-fidelity
characterization test, just of the new full output.

### FR-2 — `_verdict_reprompt` (`drivers/claude_cli.py:194`)

**Placement.** One new sentence inserted into the existing returned string,
between the current opening sentence ("Your previous message did not end
with the required machine-readable verdict...") and the closing instruction
("Reply with ONLY the verdict now..."). This keeps the function's shape
(intro → instruction → fenced template) but adds the deferral-specific
middle clause only this path needs — the resumed session is precisely the
case where the agent might be mid-wait on a background command it started
in the turn now being re-prompted.

**Exact text:**

```python
def _verdict_reprompt(allowed: tuple[str, ...]) -> str:
    """The follow-up prompt for a resumed session that skipped its verdict."""
    names = ", ".join(allowed)
    return (
        "Your previous message did not end with the required machine-readable "
        "verdict, so the harness could not read a result. If you were waiting "
        "on a background or long-running command, stop waiting: this process "
        "will not check on it or resume this session again. Check its result "
        "now if it already finished, or decide based on what you already "
        "know — either way, this turn ends with a verdict. Reply with ONLY "
        "the verdict now — a single fenced json block and nothing else:\n"
        "```json\n"
        '{"outcome": "<one of: ' + names + '>", "summary": "<short summary>"}\n'
        "```"
    )
```

**Why this wording.** Directly answers the reported failure ("Waiting for
the two background verification commands... will resume once notified"):
tells the agent (a) the wait is over, (b) what to do instead (check now, or
decide from what's already known), (c) that there is no third turn either.
No behavior branch or heuristic text-sniffing is added — this text is always
part of the one existing re-prompt, matching the plan's explicit non-goal of
detecting deferral narration to trigger a special path.

**Unaffected.** The function's signature, its fenced-template tail (byte-
identical), the call site (`_reprompt_verdict` at `claude_cli.py:505`), retry
count, and timeout handling are untouched.

## Data schemas

None. No changes to `Task`, `AgentSpec`, `AgentRun`, `BehaviorResult`, the
verdict JSON shape (`{"outcome": ..., "summary": ...}`), or any port
signature. This is a pure string-content change inside two functions whose
inputs/outputs are otherwise unchanged.

## Test design

### FR-1 — `tests/test_agent_behavior.py`

- Update `test_compose_prompt_unchanged_when_body_absent` (full-string
  equality) to include the new paragraph at its exact position, per above.
- New test `test_compose_prompt_states_the_single_shot_contract`:
  ```python
  def test_compose_prompt_states_the_single_shot_contract():
      prompt = compose_prompt(
          make_task(status="development"),
          step="development",
          artifact_relpath=".artifacts/tsk_1/development-01.md",
          outcomes=(DONE,),
          hints={},
      )

      assert "single non-interactive turn" in prompt
      assert "Do not launch it in the background" in prompt
      # scene-setting, not an afterthought: appears before the outcome list
      assert prompt.index("single non-interactive turn") < prompt.index(
          "Finish by choosing exactly one outcome"
      )
  ```
- No existing assertion in `test_compose_prompt_mentions_task_artifacts_and_
  allowed_outcomes`, `test_compose_prompt_renders_hint_and_description`,
  `test_compose_prompt_includes_issue_body_when_present`,
  `test_compose_prompt_treats_whitespace_only_body_as_absent`,
  `test_compose_prompt_does_not_duplicate_body_equal_to_request`, or
  `test_compose_prompt_demands_the_verdict_block_as_the_last_thing` changes —
  none of them assert full-string equality or a fixed line count.

### FR-2 — `tests/test_claude_cli.py`

- Add `_verdict_reprompt` to the existing `from harness.drivers.claude_cli
  import (...)` block (it is not currently imported there).
- New test, next to the fix-A/fix-C tests:
  ```python
  def test_verdict_reprompt_tells_the_agent_not_to_wait_on_background_work():
      text = _verdict_reprompt((DONE, REQUEST_CHANGES))

      assert "will not check on it or resume this session again" in text
      # unchanged: still ends with the same fenced template
      assert text.endswith(
          '```json\n'
          '{"outcome": "<one of: done, request_changes>", "summary": "<short summary>"}\n'
          '```'
      )
  ```

### FR-3 — regression tests in `tests/test_claude_cli.py`

Both live near `test_run_reprompt_path_carries_its_own_usage_but_original_
model` (`claude_cli.py:771`) and reuse its exact fixtures (`_FakeRunProc`,
`_FakeCommunicateProc`, `_stream_json`, the `monkeypatch.setattr(asyncio,
"create_subprocess_exec", fake_exec)` pattern) — no new test infrastructure.

**(a) single-outcome deferral is rescued today — characterization test:**
```python
async def test_run_rescues_single_outcome_deferral_narration(monkeypatch):
    stdout = _stream_json(
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": (
                "Waiting for the two background verification commands "
                "(dotnet format --verify-no-changes and the targeted test "
                "run) to finish — will resume once notified."
            ),
            "session_id": "sess-1",
        },
    )
    proc = _FakeRunProc(stdout)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    runner = ClaudeCliRunner()
    spec = AgentSpec(name="development", prompt="you develop")  # allowed=(DONE,)
    run = await runner.run(prompt="do it", spec=spec, cwd=Path("."), timeout=5.0)

    assert run.outcome == DONE
    assert "will resume once notified" in run.summary
```
Single call only (no `--resume` attempted — `len(allowed) == 1` skips the
resume branch in `ClaudeCliRunner.run`), rescued by `fallback_verdict`. This
is exactly the reported production shape, confirming the existing fix-A
rescue already covers a single-outcome step; FR-1's prompt paragraph makes
this rarer going forward but this path must not regress.

**(b) multi-outcome deferral, still deferring on the re-prompt, still fails:**
```python
async def test_run_raises_when_multi_outcome_deferral_persists_through_reprompt(
    monkeypatch,
):
    first_stdout = _stream_json(
        {"type": "system", "subtype": "init", "model": "claude-opus"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Waiting for the background test run to finish.",
            "session_id": "sess-1",
        },
    )
    first_proc = _FakeRunProc(first_stdout)
    reprompt_stdout = json.dumps(
        {
            "result": "Still waiting on that background command.",
            "is_error": False,
        }
    ).encode()
    calls: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return first_proc
        return _FakeCommunicateProc(reprompt_stdout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    runner = ClaudeCliRunner()
    spec = AgentSpec(
        name="review", prompt="you review", allowed_outcomes=(DONE, REQUEST_CHANGES)
    )

    with pytest.raises(VerdictError):
        await runner.run(prompt="do it", spec=spec, cwd=Path("."), timeout=5.0)

    assert len(calls) == 2  # original call + the one resume re-prompt, no more
```
Confirms the resume path is attempted (per fix C) and that a step still
unable to produce a verdict after both the strengthened prompt and the
strengthened re-prompt correctly terminates in `VerdictError` (→ `failed/`
via the behavior's own exception handling, invariant #3) rather than
looping — matching the plan's explicit non-goal of a second retry.

## Interfaces

No new ports, endpoints, events, or signature changes. `compose_prompt` and
`_verdict_reprompt` keep their existing parameter lists; only their returned
`str` content grows.

## Sequencing

1. `compose_prompt` paragraph (FR-1) + its tests.
2. `_verdict_reprompt` sentence (FR-2) + its test.
3. FR-3 regression tests (a) and (b).
4. `.venv/bin/pytest -q` — full suite green, only the one full-string test
   (`test_compose_prompt_unchanged_when_body_absent`) needed its expected
   value updated; everything else is additive.

```json
{"outcome": "done", "summary": "Design specifies exact wording and placement for the compose_prompt single-shot-contract paragraph (FR-1) and the _verdict_reprompt deferral sentence (FR-2), plus concrete FR-3 regression tests reusing existing test fixtures — no data model, port, or control-flow changes; no UI."}
```
