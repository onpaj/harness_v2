"""Real `AgentRunner` around `claude -p` (headless CLI).

The driver is made of two pure functions and a thin subprocess shell:

- `build_argv` assembles the argv for `claude -p` from the persona and the
  `AgentSpec`. A pure function — testable without a subprocess.
- `parse_verdict` pulls the agent's final text out of the `claude -p` JSON
  envelope and from it a machine-readable verdict `{outcome, summary}`. A pure
  function.
- `ClaudeCliRunner` ties them together: assembles the argv, runs `claude` in
  `cwd`, watches the timeout and exit code, and returns `parse_verdict` of the
  result.

Calls the system `claude` via `asyncio.create_subprocess_exec` — no new
production dependency. The real `claude` DOES NOT RUN in the test suite; the
pure functions are tested directly and the subprocess shell is covered by an
opt-in smoke test.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from harness.models import Outcome
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec

# Last fenced ```json ... ``` block in the agent's final text.
_FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)


class AgentError(Exception):
    """The `claude` process failed — non-zero exit, crash, or timeout."""


class VerdictError(Exception):
    """`claude -p` output carries an unreadable, missing, or disallowed verdict."""


def build_argv(
    *, prompt: str, spec: AgentSpec, output_format: str = "json"
) -> list[str]:
    """Assemble the argv for `claude -p`. A pure function, no I/O.

    The base flags are always present; `--model`, `--fallback-model` and
    `--allowedTools` are added only when the spec carries them. The persona goes
    through `--append-system-prompt`.
    """
    argv = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        output_format,
        "--permission-mode",
        "bypassPermissions",
        "--setting-sources",
        "project",
        "--append-system-prompt",
        spec.prompt,
    ]
    if spec.model is not None:
        argv += ["--model", spec.model]
    if spec.fallback_model is not None:
        argv += ["--fallback-model", spec.fallback_model]
    if spec.allowed_tools:
        argv += ["--allowedTools", ",".join(spec.allowed_tools)]
    return argv


def _extract_verdict(result: str) -> dict:
    """Pull `{outcome, summary}` out of the agent's final text.

    Takes the last fenced ```json``` block; if there is none, tries to parse the
    whole text as JSON. Unreadable → `VerdictError`.
    """
    blocks = _FENCED_JSON.findall(result)
    candidate = blocks[-1] if blocks else result
    try:
        verdict = json.loads(candidate)
    except (json.JSONDecodeError, ValueError) as error:
        raise VerdictError(
            f"verdict is not readable JSON: {candidate!r}"
        ) from error
    if not isinstance(verdict, dict):
        raise VerdictError(f"verdict is not an object: {verdict!r}")
    return verdict


def parse_verdict(stdout: str, *, allowed: tuple[Outcome, ...]) -> AgentRun:
    """Pull the verdict out of the `claude -p` JSON envelope and map it to `AgentRun`.

    The outer JSON carries `result` (the agent's final text) and `is_error`. If
    `result` is missing, if `is_error` is set, or if the envelope/verdict is
    unreadable or the outcome is outside `allowed` → `VerdictError`. `raw`
    carries the whole `stdout`.
    """
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as error:
        raise VerdictError(
            f"claude output is not readable JSON: {stdout!r}"
        ) from error
    if not isinstance(envelope, dict):
        raise VerdictError(f"claude envelope is not an object: {envelope!r}")
    if envelope.get("is_error"):
        raise VerdictError(f"claude reported an error: {stdout!r}")
    if "result" not in envelope:
        raise VerdictError(f"claude envelope has no 'result' field: {stdout!r}")

    verdict = _extract_verdict(envelope["result"])
    if "outcome" not in verdict:
        raise VerdictError(f"verdict has no 'outcome' field: {verdict!r}")
    try:
        outcome = Outcome(verdict["outcome"])
    except ValueError as error:
        raise VerdictError(
            f"unknown outcome {verdict['outcome']!r}"
        ) from error
    if outcome not in allowed:
        raise VerdictError(
            f"outcome {outcome.value!r} is not in allowed {allowed!r}"
        )
    return AgentRun(outcome, summary=verdict.get("summary", ""), raw=stdout)


class ClaudeCliRunner(AgentRunner):
    """Run `claude -p` in `cwd` and return the verdict.

    A timeout leads to `AgentError` (not `VerdictError`): elapsed time is a
    process failure, not a defect in its output — symmetric with a non-zero exit
    code. The verdict does not exist yet, so there is nothing to assert about it.
    Both end up in `failed/` via `_fail`, just with a clearer error type.
    """

    async def run(
        self, *, prompt: str, spec: AgentSpec, cwd: Path, timeout: float
    ) -> AgentRun:
        argv = build_argv(prompt=prompt, spec=spec)
        # `bypassPermissions` (headless autonomy, no human at the console) maps
        # to `--dangerously-skip-permissions`, which claude running as root
        # refuses until `IS_SANDBOX=1` confirms it is running in an isolated
        # environment. The harness runs agents exactly there (container/CI), so
        # we set it here, not on the caller.
        env = {**os.environ, "IS_SANDBOX": "1"}
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout
            )
        except asyncio.TimeoutError as error:
            proc.kill()
            await proc.wait()
            raise AgentError(f"claude timed out after {timeout}s") from error
        if proc.returncode != 0:
            raise AgentError(
                f"claude exited with {proc.returncode}: "
                f"{stderr.decode().strip()}"
            )
        return parse_verdict(stdout.decode(), allowed=spec.allowed_outcomes)
