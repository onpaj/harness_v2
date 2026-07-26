import asyncio
import json
from pathlib import Path

import pytest

from harness.drivers.claude_cli import (
    AgentError,
    ClaudeCliRunner,
    VerdictError,
    _drain,
    _iter_lines,
    _usage_from_result,
    build_argv,
    fallback_verdict,
    parse_stream_line,
    parse_verdict,
    render_stream_line,
    try_verdict,
    verdict_from_final,
)
from harness.models import DONE, REQUEST_CHANGES
from harness.ports.agent import AgentSpec


# --- build_argv --------------------------------------------------------------


def _base_argv(prompt: str) -> list[str]:
    return [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--setting-sources",
        "project",
    ]


def test_build_argv_base_flags_and_persona():
    spec = AgentSpec(name="planner", prompt="You are the planner.")

    argv = build_argv(prompt="do it", spec=spec)

    for token in _base_argv("do it"):
        assert token in argv
    # persona goes through --append-system-prompt
    idx = argv.index("--append-system-prompt")
    assert argv[idx + 1] == "You are the planner."
    # without a model / fallback / tools the flags are not added
    assert "--model" not in argv
    assert "--fallback-model" not in argv
    assert "--allowedTools" not in argv


def test_build_argv_starts_with_claude_p_prompt():
    spec = AgentSpec(name="planner", prompt="p")

    argv = build_argv(prompt="hello", spec=spec)

    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[2] == "hello"


def test_build_argv_respects_output_format_override():
    spec = AgentSpec(name="planner", prompt="p")

    argv = build_argv(prompt="x", spec=spec, output_format="stream-json")

    idx = argv.index("--output-format")
    assert argv[idx + 1] == "stream-json"


def test_build_argv_adds_model_when_set():
    spec = AgentSpec(name="planner", prompt="p", model="opus")

    argv = build_argv(prompt="x", spec=spec)

    idx = argv.index("--model")
    assert argv[idx + 1] == "opus"


def test_build_argv_adds_fallback_model_when_set():
    spec = AgentSpec(name="planner", prompt="p", fallback_model="sonnet")

    argv = build_argv(prompt="x", spec=spec)

    idx = argv.index("--fallback-model")
    assert argv[idx + 1] == "sonnet"


def test_build_argv_adds_allowed_tools_joined():
    spec = AgentSpec(name="dev", prompt="p", allowed_tools=("Read", "Edit", "Bash"))

    argv = build_argv(prompt="x", spec=spec)

    idx = argv.index("--allowedTools")
    assert argv[idx + 1] == "Read,Edit,Bash"


def test_build_argv_empty_tools_omits_flag():
    spec = AgentSpec(name="dev", prompt="p", allowed_tools=())

    argv = build_argv(prompt="x", spec=spec)

    assert "--allowedTools" not in argv


# --- parse_verdict -----------------------------------------------------------


def _envelope(result: str, *, is_error: bool = False) -> str:
    return json.dumps({"result": result, "is_error": is_error})


def test_parse_verdict_reads_fenced_block():
    inner = '```json\n{"outcome": "done", "summary": "ok"}\n```'
    stdout = _envelope(f"Done.\n{inner}")

    run = parse_verdict(stdout, allowed=(DONE,))

    assert run.outcome == DONE
    assert run.summary == "ok"
    assert run.raw == stdout


def test_parse_verdict_request_changes_when_allowed():
    inner = '```json\n{"outcome": "request_changes", "summary": "fix it"}\n```'
    stdout = _envelope(inner)

    run = parse_verdict(
        stdout, allowed=(DONE, REQUEST_CHANGES)
    )

    assert run.outcome == REQUEST_CHANGES
    assert run.summary == "fix it"


def test_parse_verdict_takes_last_fenced_block():
    first = '```json\n{"outcome": "request_changes", "summary": "first"}\n```'
    last = '```json\n{"outcome": "done", "summary": "last"}\n```'
    stdout = _envelope(f"{first}\nin between\n{last}")

    run = parse_verdict(
        stdout, allowed=(DONE, REQUEST_CHANGES)
    )

    assert run.outcome == DONE
    assert run.summary == "last"


def test_parse_verdict_falls_back_to_whole_result_json():
    stdout = _envelope('{"outcome": "done", "summary": "no fence"}')

    run = parse_verdict(stdout, allowed=(DONE,))

    assert run.outcome == DONE
    assert run.summary == "no fence"


def test_parse_verdict_missing_summary_defaults_empty():
    inner = '```json\n{"outcome": "done"}\n```'
    stdout = _envelope(inner)

    run = parse_verdict(stdout, allowed=(DONE,))

    assert run.outcome == DONE
    assert run.summary == ""


def test_parse_verdict_outcome_outside_allowed_raises():
    inner = '```json\n{"outcome": "request_changes", "summary": "x"}\n```'
    stdout = _envelope(inner)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(DONE,))


def test_parse_verdict_invalid_outcome_value_raises():
    inner = '```json\n{"outcome": "nonsense", "summary": "x"}\n```'
    stdout = _envelope(inner)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(DONE, REQUEST_CHANGES))


def test_parse_verdict_is_error_true_raises():
    inner = '```json\n{"outcome": "done", "summary": "ok"}\n```'
    stdout = _envelope(inner, is_error=True)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(DONE,))


def test_parse_verdict_unreadable_outer_json_raises():
    with pytest.raises(VerdictError):
        parse_verdict("this is not JSON", allowed=(DONE,))


def test_parse_verdict_missing_result_field_raises():
    stdout = json.dumps({"is_error": False})

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(DONE,))


def test_parse_verdict_no_verdict_block_raises():
    stdout = _envelope("Just chatter, no verdict.")

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(DONE,))


def test_parse_verdict_missing_outcome_key_raises():
    inner = '```json\n{"summary": "missing outcome"}\n```'
    stdout = _envelope(inner)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(DONE,))


def test_build_argv_stream_json_adds_verbose():
    spec = AgentSpec(name="planner", prompt="p")

    argv = build_argv(prompt="x", spec=spec, output_format="stream-json")

    assert "--verbose" in argv


def test_build_argv_json_does_not_add_verbose():
    spec = AgentSpec(name="planner", prompt="p")

    argv = build_argv(prompt="x", spec=spec)

    assert "--verbose" not in argv


def test_agent_error_is_exception():
    assert issubclass(AgentError, Exception)
    assert issubclass(VerdictError, Exception)


# --- parse_stream_line -------------------------------------------------------


def test_parse_stream_line_reads_json_object():
    assert parse_stream_line('{"type": "assistant"}') == {"type": "assistant"}


def test_parse_stream_line_ignores_blank():
    assert parse_stream_line("   ") is None


def test_parse_stream_line_ignores_garbage():
    assert parse_stream_line("not json") is None


def test_parse_stream_line_ignores_non_object():
    assert parse_stream_line("[1, 2, 3]") is None


# --- render_stream_line ------------------------------------------------------


def _assistant(*content) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": list(content)}}


def test_render_assistant_text():
    msg = _assistant({"type": "text", "text": "Working on it.\n"})

    assert render_stream_line(msg) == ["Working on it."]


def test_render_assistant_skips_empty_text():
    msg = _assistant({"type": "text", "text": "   "})

    assert render_stream_line(msg) == []


def test_render_tool_use_bash_shows_command():
    msg = _assistant(
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}
    )

    assert render_stream_line(msg) == ["⏵ Bash: pytest -q"]


def test_render_tool_use_read_shows_file_path():
    msg = _assistant(
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b.py"}}
    )

    assert render_stream_line(msg) == ["⏵ Read: /a/b.py"]


def test_render_assistant_multiple_blocks_in_order():
    msg = _assistant(
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    )

    assert render_stream_line(msg) == ["Let me check.", "⏵ Bash: ls"]


def test_render_assistant_multiline_text_splits_into_lines():
    # No rendered line may carry a newline — it would truncate the SSE frame.
    msg = _assistant({"type": "text", "text": "para one\n\npara two"})

    lines = render_stream_line(msg)

    assert lines == ["para one", "", "para two"]
    assert all("\n" not in line for line in lines)


def test_render_tool_use_collapses_multiline_command():
    msg = _assistant(
        {"type": "tool_use", "name": "Bash", "input": {"command": "echo a\necho b"}}
    )

    assert render_stream_line(msg) == ["⏵ Bash: echo a echo b"]


def test_render_tool_result_preview_first_line():
    msg = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "line one\nline two\nline three"}
            ],
        },
    }

    assert render_stream_line(msg) == ["  ⤷ line one"]


def test_render_system_init_shows_session():
    msg = {"type": "system", "subtype": "init", "model": "claude-opus"}

    rendered = render_stream_line(msg)

    assert len(rendered) == 1
    assert "claude-opus" in rendered[0]


def test_render_result_message_is_not_activity():
    msg = {"type": "result", "subtype": "success", "result": "done"}

    assert render_stream_line(msg) == []


def test_render_unknown_type_is_empty():
    assert render_stream_line({"type": "whatever"}) == []


# --- verdict_from_final ------------------------------------------------------


def test_verdict_from_final_parses_result_message():
    inner = '```json\n{"outcome": "done", "summary": "ok"}\n```'
    final = {"type": "result", "subtype": "success", "is_error": False, "result": inner}

    run = verdict_from_final(final, allowed=(DONE,), raw="RAW")

    assert run.outcome == DONE
    assert run.summary == "ok"
    assert run.raw == "RAW"


def test_verdict_from_final_is_error_raises():
    final = {"type": "result", "is_error": True, "result": "boom"}

    with pytest.raises(VerdictError):
        verdict_from_final(final, allowed=(DONE,), raw="RAW")


def test_verdict_from_final_outside_allowed_raises():
    inner = '```json\n{"outcome": "request_changes", "summary": "x"}\n```'
    final = {"type": "result", "is_error": False, "result": inner}

    with pytest.raises(VerdictError):
        verdict_from_final(final, allowed=(DONE,), raw="RAW")


# --- try_verdict (tolerant: the runner's recovery path) ----------------------


def _final(result: str, *, is_error: bool = False) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "result": result,
    }


def test_try_verdict_reads_valid_block():
    run = try_verdict(
        _final('done.\n```json\n{"outcome": "done", "summary": "ok"}\n```'),
        allowed=(DONE,),
        raw="RAW",
    )

    assert run is not None
    assert run.outcome == DONE
    assert run.summary == "ok"
    assert run.raw == "RAW"


def test_try_verdict_none_when_no_block():
    # The exact failure we saw: a prose wrap-up, no verdict block at all.
    run = try_verdict(
        _final("Implementation complete. Summary:\n\nprose, no json."),
        allowed=(DONE,),
        raw="RAW",
    )

    assert run is None


def test_try_verdict_none_when_outcome_missing():
    run = try_verdict(
        _final('```json\n{"summary": "no outcome"}\n```'),
        allowed=(DONE,),
        raw="RAW",
    )

    assert run is None


def test_try_verdict_none_when_outcome_disallowed():
    run = try_verdict(
        _final('```json\n{"outcome": "request_changes"}\n```'),
        allowed=(DONE,),
        raw="RAW",
    )

    assert run is None


def test_try_verdict_none_when_outcome_unknown_value():
    run = try_verdict(
        _final('```json\n{"outcome": "nonsense"}\n```'),
        allowed=(DONE, REQUEST_CHANGES),
        raw="RAW",
    )

    assert run is None


def test_try_verdict_raises_on_is_error():
    # An envelope-level defect is a real failure, never a recoverable miss.
    with pytest.raises(VerdictError):
        try_verdict(_final("x", is_error=True), allowed=(DONE,), raw="RAW")


def test_try_verdict_raises_on_missing_result_field():
    with pytest.raises(VerdictError):
        try_verdict(
            {"type": "result", "is_error": False},
            allowed=(DONE,),
            raw="RAW",
        )


# --- fallback_verdict (single-outcome rescue, fix A) -------------------------


def test_fallback_single_outcome_synthesizes_the_only_outcome():
    run = fallback_verdict(
        "  Implementation complete. Summary: prose.  ",
        allowed=(DONE,),
        raw="RAW",
    )

    assert run is not None
    assert run.outcome == DONE
    assert run.summary == "Implementation complete. Summary: prose."
    assert run.raw == "RAW"


def test_fallback_multi_outcome_is_ambiguous_returns_none():
    run = fallback_verdict(
        "prose",
        allowed=(DONE, REQUEST_CHANGES),
        raw="RAW",
    )

    assert run is None


# --- build_argv --resume (fix C) ---------------------------------------------


def test_build_argv_resume_adds_resume_and_omits_persona():
    spec = AgentSpec(name="dev", prompt="PERSONA")

    argv = build_argv(prompt="give me the verdict", spec=spec, resume="sess-123")

    idx = argv.index("--resume")
    assert argv[idx + 1] == "sess-123"
    # the persona already lives in the resumed session
    assert "--append-system-prompt" not in argv


def test_build_argv_without_resume_keeps_persona():
    spec = AgentSpec(name="dev", prompt="PERSONA")

    argv = build_argv(prompt="x", spec=spec)

    assert "--resume" not in argv
    assert "--append-system-prompt" in argv


# --- _iter_lines / _drain (the subprocess shell, with a fake process) --------


class _FakeReader:
    """Minimal StreamReader: hands back up to `n` bytes per `read`, EOF as b""."""

    def __init__(self, data: bytes, chunk: int | None = None) -> None:
        self._data = data
        self._chunk = chunk

    async def read(self, n: int = -1) -> bytes:
        limit = len(self._data) if n < 0 else n
        if self._chunk is not None:
            limit = min(limit, self._chunk)
        chunk, self._data = self._data[:limit], self._data[limit:]
        return chunk


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"") -> None:
        self.stdout = _FakeReader(stdout)
        self.stderr = _FakeReader(stderr)


async def _collect(stream) -> list[bytes]:
    return [line async for line in stream]


async def test_iter_lines_splits_across_chunks_and_keeps_trailing():
    # Tiny chunks force the buffer to span reads; last line has no newline.
    reader = _FakeReader(b"one\ntwo\nthree", chunk=2)

    assert await _collect(_iter_lines(reader)) == [b"one", b"two", b"three"]


async def test_drain_renders_activity_and_captures_verdict():
    stream = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "model": "opus"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Listing files."},
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        ],
                    },
                }
            ),
            "",  # a blank line in the stream must be tolerated
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": '```json\n{"outcome": "done", "summary": "ok"}\n```',
                }
            ),
        ]
    )
    proc = _FakeProc((stream + "\n").encode(), stderr=b"warn")
    captured: list[str] = []

    final, raw, stderr, model = await _drain(proc, captured.append)

    assert captured == ["● session started (opus)", "Listing files.", "⏵ Bash: ls"]
    assert stderr == "warn"
    assert final is not None and final["type"] == "result"
    assert model == "opus"
    run = verdict_from_final(final, allowed=(DONE,), raw=raw)
    assert run.outcome == DONE
    assert run.summary == "ok"


# --- _usage_from_result (pure, tolerant) -------------------------------------


def test_usage_from_result_reads_all_fields():
    message = {
        "type": "result",
        "total_cost_usd": 0.0231,
        "usage": {
            "input_tokens": 1234,
            "output_tokens": 567,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 20,
        },
    }

    usage = _usage_from_result(message)

    assert usage == {
        "input_tokens": 1234,
        "output_tokens": 567,
        "cache_read_tokens": 10,
        "cache_creation_tokens": 20,
        "total_cost_usd": 0.0231,
    }


def test_usage_from_result_degrades_when_usage_missing():
    usage = _usage_from_result({"type": "result"})

    assert usage == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "total_cost_usd": None,
    }


def test_usage_from_result_degrades_on_malformed_fields():
    message = {"usage": {"input_tokens": "not a number"}, "total_cost_usd": "free"}

    usage = _usage_from_result(message)

    assert usage["input_tokens"] == 0
    assert usage["total_cost_usd"] is None


def test_usage_from_result_ignores_non_dict_usage_block():
    usage = _usage_from_result({"usage": "oops"})

    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0


# --- ClaudeCliRunner.run — usage/model threading (fake subprocess) -----------


class _FakeReadStream:
    """Minimal StreamReader double used by the runner's own stdout/stderr."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, n: int = -1) -> bytes:
        limit = len(self._data) if n < 0 else n
        chunk, self._data = self._data[:limit], self._data[limit:]
        return chunk


class _FakeRunProc:
    """Double for `asyncio.subprocess.Process`, enough for `ClaudeCliRunner.run`."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = _FakeReadStream(stdout)
        self.stderr = _FakeReadStream(stderr)
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class _FakeCommunicateProc:
    """Double for the reprompt's one-shot `communicate()` call."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _stream_json(*messages: dict) -> bytes:
    return ("\n".join(json.dumps(message) for message in messages) + "\n").encode()


async def test_run_populates_usage_and_resolved_model_from_the_stream(monkeypatch):
    stdout = _stream_json(
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Working."}],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '```json\n{"outcome": "done", "summary": "ok"}\n```',
            "session_id": "sess-1",
            "total_cost_usd": 0.0231,
            "usage": {
                "input_tokens": 1234,
                "output_tokens": 567,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 20,
            },
        },
    )
    proc = _FakeRunProc(stdout)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    runner = ClaudeCliRunner()
    spec = AgentSpec(name="dev", prompt="you are a developer")
    run = await runner.run(prompt="do it", spec=spec, cwd=Path("."), timeout=5.0)

    assert run.outcome == DONE
    assert run.summary == "ok"
    assert run.input_tokens == 1234
    assert run.output_tokens == 567
    assert run.cache_read_tokens == 10
    assert run.cache_creation_tokens == 20
    assert run.total_cost_usd == 0.0231
    assert run.model == "claude-sonnet-5"


async def test_run_missing_usage_degrades_to_zero_defaults(monkeypatch):
    stdout = _stream_json(
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '```json\n{"outcome": "done", "summary": "ok"}\n```',
        },
    )
    proc = _FakeRunProc(stdout)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    runner = ClaudeCliRunner()
    spec = AgentSpec(name="dev", prompt="you are a developer")
    run = await runner.run(prompt="do it", spec=spec, cwd=Path("."), timeout=5.0)

    assert run.outcome == DONE
    assert run.input_tokens == 0
    assert run.output_tokens == 0
    assert run.total_cost_usd is None
    assert run.model == "claude-sonnet-5"


async def test_run_reprompt_path_carries_its_own_usage_but_original_model(
    monkeypatch,
):
    # First call: the agent finishes but skips the verdict block; multi-outcome
    # spec makes the miss ambiguous, so the runner re-prompts the session.
    first_stdout = _stream_json(
        {"type": "system", "subtype": "init", "model": "claude-opus"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Done, no verdict block.",
            "session_id": "sess-1",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.01,
        },
    )
    first_proc = _FakeRunProc(first_stdout)
    reprompt_stdout = json.dumps(
        {
            "result": '```json\n{"outcome": "done", "summary": "recovered"}\n```',
            "is_error": False,
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "total_cost_usd": 0.001,
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
    run = await runner.run(prompt="do it", spec=spec, cwd=Path("."), timeout=5.0)

    assert len(calls) == 2  # the original call plus the resume re-prompt
    assert run.outcome == DONE
    assert run.summary == "recovered"
    # The reprompt's own usage, not the original call's.
    assert run.input_tokens == 20
    assert run.output_tokens == 10
    assert run.total_cost_usd == 0.001
    # The resolved model can only come from the original call's system/init —
    # a resumed session doesn't re-emit it.
    assert run.model == "claude-opus"
