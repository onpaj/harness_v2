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
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from harness.ports.agent import AgentRun, AgentRunner, AgentSpec

# Last fenced ```json ... ``` block in the agent's final text.
_FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)

# Keys carrying a tool call's gist, in the order we prefer to show them.
_TOOL_SUMMARY_KEYS = ("command", "file_path", "path", "pattern", "url", "query")

# One rendered line is a live tail, not a transcript — keep it glanceable.
_LINE_MAX = 160

# Bytes read per pull from the subprocess pipe when splitting stream-json lines.
_CHUNK = 65536


class AgentError(Exception):
    """The `claude` process failed — non-zero exit, crash, or timeout."""


class VerdictError(Exception):
    """`claude -p` output carries an unreadable, missing, or disallowed verdict."""


def build_argv(
    *,
    prompt: str,
    spec: AgentSpec,
    output_format: str = "json",
    resume: str | None = None,
) -> list[str]:
    """Assemble the argv for `claude -p`. A pure function, no I/O.

    The base flags are always present; `--model`, `--fallback-model` and
    `--allowedTools` are added only when the spec carries them. The persona goes
    through `--append-system-prompt`. `stream-json` additionally requires
    `--verbose` (claude refuses `-p --output-format stream-json` without it).

    With `resume` set the call re-enters an existing session (`--resume`) — used
    by the verdict re-prompt (fix C). The persona already lives in that session,
    so `--append-system-prompt` is dropped in favour of `--resume`.
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
    ]
    if resume is None:
        argv += ["--append-system-prompt", spec.prompt]
    else:
        argv += ["--resume", resume]
    if output_format == "stream-json":
        argv.append("--verbose")
    if spec.model is not None:
        argv += ["--model", spec.model]
    if spec.fallback_model is not None:
        argv += ["--fallback-model", spec.fallback_model]
    if spec.allowed_tools:
        argv += ["--allowedTools", ",".join(spec.allowed_tools)]
    return argv


def _extract_verdict(result: str) -> dict | None:
    """The `{outcome, summary}` dict from the agent's final text, or `None`.

    Takes the last fenced ```json``` block; if there is none, tries the whole
    text as JSON. Unreadable JSON, or JSON that isn't an object, → `None` — the
    caller decides whether a missing verdict is a hard failure (`parse_verdict`,
    `verdict_from_final`) or a recoverable miss (`try_verdict`).
    """
    blocks = _FENCED_JSON.findall(result)
    candidate = blocks[-1] if blocks else result
    try:
        verdict = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return verdict if isinstance(verdict, dict) else None


def _require_result(envelope: object, *, raw: str) -> str:
    """The agent's final text from a `result`/`is_error` envelope.

    Envelope-level failures — not an object, `is_error` set, no `result` field —
    are process defects and always raise. This is deliberately distinct from a
    merely missing verdict *block*, which the tolerant path treats as recoverable.
    """
    if not isinstance(envelope, dict):
        raise VerdictError(f"claude envelope is not an object: {envelope!r}")
    if envelope.get("is_error"):
        raise VerdictError(f"claude reported an error: {raw!r}")
    if "result" not in envelope:
        raise VerdictError(f"claude envelope has no 'result' field: {raw!r}")
    return envelope["result"]


def _verdict_from_envelope(
    envelope: dict, *, allowed: tuple[str, ...], raw: str
) -> AgentRun:
    """Map an envelope carrying `result` (+`is_error`) onto an `AgentRun`.

    The strict reading: a missing/unreadable/disallowed verdict raises. Shared by
    the legacy one-shot JSON envelope (`parse_verdict`) and the terminal `result`
    message of the stream-json stream (`verdict_from_final`) — both carry the same
    two keys, so the verdict is read through one code path.
    """
    result = _require_result(envelope, raw=raw)
    verdict = _extract_verdict(result)
    if verdict is None:
        raise VerdictError(f"verdict is not readable JSON: {result!r}")
    if "outcome" not in verdict:
        raise VerdictError(f"verdict has no 'outcome' field: {verdict!r}")
    outcome = verdict["outcome"]
    if not isinstance(outcome, str) or not outcome:
        raise VerdictError(f"unknown outcome {outcome!r}")
    if outcome not in allowed:
        raise VerdictError(
            f"outcome {outcome!r} is not in allowed {allowed!r}"
        )
    return AgentRun(outcome, summary=verdict.get("summary", ""), raw=raw)


def try_verdict(
    envelope: object, *, allowed: tuple[str, ...], raw: str
) -> AgentRun | None:
    """A readable, allowed verdict as an `AgentRun`, else `None`.

    The tolerant sibling of `_verdict_from_envelope`, used by the runner's
    recovery path. Envelope-level failures still raise (see `_require_result`) —
    only a verdict the model forgot, garbled, or set outside `allowed` yields the
    recoverable `None` the caller re-prompts or falls back on.
    """
    result = _require_result(envelope, raw=raw)
    verdict = _extract_verdict(result)
    if verdict is None:
        return None
    outcome = verdict.get("outcome")
    if not isinstance(outcome, str) or not outcome:
        return None
    if outcome not in allowed:
        return None
    return AgentRun(outcome, summary=verdict.get("summary", ""), raw=raw)


def fallback_verdict(
    result_text: str, *, allowed: tuple[str, ...], raw: str
) -> AgentRun | None:
    """Rescue a finished step whose single allowed outcome makes it unambiguous.

    When the agent ran to completion but skipped the verdict block, a one-outcome
    step (development, plan, design, architecture) has exactly one thing it could
    have meant — take it, and keep the agent's final text as the summary. A
    multi-outcome step (review: done / request_changes) is genuinely ambiguous,
    so this returns `None` and the miss stays a failure.
    """
    if len(allowed) == 1:
        return AgentRun(allowed[0], summary=result_text.strip(), raw=raw)
    return None


def _verdict_reprompt(allowed: tuple[str, ...]) -> str:
    """The follow-up prompt for a resumed session that skipped its verdict."""
    names = ", ".join(allowed)
    return (
        "Your previous message did not end with the required machine-readable "
        "verdict, so the harness could not read a result. Reply with ONLY the "
        "verdict now — a single fenced json block and nothing else:\n"
        "```json\n"
        '{"outcome": "<one of: ' + names + '>", "summary": "<short summary>"}\n'
        "```"
    )


def parse_verdict(stdout: str, *, allowed: tuple[str, ...]) -> AgentRun:
    """Pull the verdict out of the `claude -p --output-format json` envelope.

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
    return _verdict_from_envelope(envelope, allowed=allowed, raw=stdout)


def verdict_from_final(
    message: dict, *, allowed: tuple[str, ...], raw: str
) -> AgentRun:
    """The verdict carried by the terminal `type == "result"` stream-json message.

    That message has the same `result`/`is_error` shape as the one-shot envelope,
    so it goes through the shared `_verdict_from_envelope`. `raw` is the joined
    stream (for the audit trail on `AgentRun.raw`).
    """
    return _verdict_from_envelope(message, allowed=allowed, raw=raw)


# --- stream-json rendering (pure) --------------------------------------------


def parse_stream_line(line: str) -> dict | None:
    """One NDJSON line → a message dict, or `None` for a blank/garbage/non-object."""
    line = line.strip()
    if not line:
        return None
    try:
        message = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return message if isinstance(message, dict) else None


def render_stream_line(message: dict) -> list[str]:
    """A stream-json message → zero or more human-readable activity lines.

    Assistant text is shown verbatim, tool calls as a concise `⏵ Name: gist`,
    tool results as a truncated `  ⤷ preview`. The terminal `result` message is
    the verdict, not activity, so it renders to nothing.
    """
    kind = message.get("type")
    if kind == "system" and message.get("subtype") == "init":
        model = message.get("model")
        return [f"● session started ({model})" if model else "● session started"]
    if kind == "assistant":
        rendered = _render_blocks(
            _content_blocks(message.get("message")), _assistant_line
        )
    elif kind == "user":
        rendered = _render_blocks(
            _content_blocks(message.get("message")), _result_line
        )
    else:
        return []
    return _split_newlines(rendered)


def _split_newlines(lines: list[str]) -> list[str]:
    """Split embedded newlines so one rendered line never spans two.

    Assistant text arrives as a whole (multi-paragraph) block; downstream every
    line becomes one SSE frame, and an embedded newline would truncate that frame
    mid-field. Splitting here keeps the "one line = one frame" invariant true for
    the whole pipeline. Blank segments are kept — a paragraph break stays visible.
    """
    out: list[str] = []
    for line in lines:
        out.extend(segment.rstrip("\r") for segment in line.split("\n"))
    return out


def _render_blocks(
    blocks: list, render: Callable[[dict], str | None]
) -> list[str]:
    lines = []
    for block in blocks:
        if isinstance(block, dict):
            rendered = render(block)
            if rendered:
                lines.append(rendered)
    return lines


def _assistant_line(block: dict) -> str | None:
    if block.get("type") == "text":
        text = (block.get("text") or "").strip()
        return text or None
    if block.get("type") == "tool_use":
        name = block.get("name") or "tool"
        gist = _summarize_tool(block.get("input"))
        return f"⏵ {name}: {gist}" if gist else f"⏵ {name}"
    return None


def _result_line(block: dict) -> str | None:
    if block.get("type") != "tool_result":
        return None
    text = _content_to_text(block.get("content")).strip()
    if not text:
        return None
    first = text.splitlines()[0]
    return f"  ⤷ {_truncate(first, _LINE_MAX)}"


def _content_blocks(message: object) -> list:
    """Anthropic message `content`: a list of blocks, or a bare string (→ text)."""
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return content
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
    return []


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


def _summarize_tool(tool_input: object) -> str:
    if not isinstance(tool_input, dict):
        return _truncate(" ".join(str(tool_input).split()), _LINE_MAX) if tool_input else ""
    for key in _TOOL_SUMMARY_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            # Collapse whitespace/newlines — a tool gist is one glanceable line.
            return _truncate(" ".join(value.split()), _LINE_MAX)
    if not tool_input:
        return ""
    return _truncate(json.dumps(tool_input, ensure_ascii=False), _LINE_MAX)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ClaudeCliRunner(AgentRunner):
    """Run `claude -p --output-format stream-json` in `cwd`, streaming and verdict.

    Reads the NDJSON stream line by line so a caller can watch the agent live via
    `on_output`; the final `type == "result"` message carries the verdict. A
    timeout leads to `AgentError` (not `VerdictError`): elapsed time is a process
    failure, not a defect in its output — symmetric with a non-zero exit code.
    Both end up in `failed/` via `_fail`, just with a clearer error type.
    """

    async def run(
        self,
        *,
        prompt: str,
        spec: AgentSpec,
        cwd: Path,
        timeout: float,
        on_output: Callable[[str], None] | None = None,
    ) -> AgentRun:
        argv = build_argv(prompt=prompt, spec=spec, output_format="stream-json")
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
            final, raw, stderr = await asyncio.wait_for(
                _drain(proc, on_output), timeout
            )
        except asyncio.TimeoutError as error:
            proc.kill()
            await proc.wait()
            raise AgentError(f"claude timed out after {timeout}s") from error
        returncode = await proc.wait()
        if returncode != 0:
            raise AgentError(f"claude exited with {returncode}: {stderr.strip()}")
        if final is None:
            raise VerdictError(f"claude produced no result message: {raw!r}")

        allowed = spec.allowed_outcomes
        run = try_verdict(final, allowed=allowed, raw=raw)
        if run is not None:
            return run

        # The agent finished but skipped its verdict block — recover rather than
        # throw away a completed run. A multi-outcome step (review) is ambiguous,
        # so re-prompt the same session for just the verdict (fix C); a
        # single-outcome step is not, so synthesize it directly, no second call
        # (fix A). Both keep a forgotten closing format from failing the task.
        session_id = final.get("session_id")
        if len(allowed) > 1 and isinstance(session_id, str):
            run = await self._reprompt_verdict(
                session_id=session_id,
                spec=spec,
                cwd=cwd,
                timeout=timeout,
                allowed=allowed,
            )
            if run is not None:
                return run

        run = fallback_verdict(final.get("result", ""), allowed=allowed, raw=raw)
        if run is not None:
            return run

        # Nothing recovered it — raise the precise strict error for the log.
        return verdict_from_final(final, allowed=allowed, raw=raw)

    async def _reprompt_verdict(
        self,
        *,
        session_id: str,
        spec: AgentSpec,
        cwd: Path,
        timeout: float,
        allowed: tuple[str, ...],
    ) -> AgentRun | None:
        """One cheap `claude -p --resume` that asks only for the verdict block.

        Best-effort: any failure — bad exit, timeout, still no readable verdict —
        returns `None` so the caller can fall back. The re-prompt must never turn
        an already-finished run into a hard error of its own.
        """
        argv = build_argv(
            prompt=_verdict_reprompt(allowed),
            spec=spec,
            output_format="json",
            resume=session_id,
        )
        env = {**os.environ, "IS_SANDBOX": "1"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return None
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        if proc.returncode != 0:
            return None
        text = stdout.decode(errors="replace")
        try:
            envelope = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        try:
            return try_verdict(envelope, allowed=allowed, raw=text)
        except VerdictError:
            return None


async def _drain(
    proc: asyncio.subprocess.Process, on_output: Callable[[str], None] | None
) -> tuple[dict | None, str, str]:
    """Consume the subprocess: render each line via `on_output`, keep the verdict.

    Returns `(final_result_message, raw_stdout, stderr)`. stderr is read
    concurrently so a full stderr pipe can't deadlock the stdout read.
    """
    assert proc.stdout is not None and proc.stderr is not None
    stderr_task = asyncio.ensure_future(proc.stderr.read())
    try:
        final: dict | None = None
        raw_parts: list[str] = []
        async for raw_line in _iter_lines(proc.stdout):
            text = raw_line.decode(errors="replace")
            raw_parts.append(text)
            message = parse_stream_line(text)
            if message is None:
                continue
            if message.get("type") == "result":
                final = message
            elif on_output is not None:
                for line in render_stream_line(message):
                    on_output(line)
        stderr = (await stderr_task).decode(errors="replace")
        return final, "\n".join(raw_parts), stderr
    finally:
        # On the timeout path `_drain` is cancelled mid-read; cancel the stderr
        # reader too and await it so it doesn't linger as a pending task.
        if not stderr_task.done():
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)


async def _iter_lines(stream: asyncio.StreamReader) -> AsyncIterator[bytes]:
    """Yield newline-delimited lines without `StreamReader`'s per-line size limit.

    stream-json messages (a big tool result, a long final text) can exceed the
    64 KiB default of `readline`; splitting raw chunks ourselves avoids the
    `LimitOverrunError` that would abort an otherwise healthy run.
    """
    buffer = b""
    while True:
        chunk = await stream.read(_CHUNK)
        if not chunk:
            if buffer:
                yield buffer
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line
