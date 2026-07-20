import json

import pytest

from harness.drivers.claude_cli import (
    AgentError,
    VerdictError,
    _drain,
    _iter_lines,
    build_argv,
    parse_stream_line,
    parse_verdict,
    render_stream_line,
    verdict_from_final,
)
from harness.models import Outcome
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

    run = parse_verdict(stdout, allowed=(Outcome.DONE,))

    assert run.outcome is Outcome.DONE
    assert run.summary == "ok"
    assert run.raw == stdout


def test_parse_verdict_request_changes_when_allowed():
    inner = '```json\n{"outcome": "request_changes", "summary": "fix it"}\n```'
    stdout = _envelope(inner)

    run = parse_verdict(
        stdout, allowed=(Outcome.DONE, Outcome.REQUEST_CHANGES)
    )

    assert run.outcome is Outcome.REQUEST_CHANGES
    assert run.summary == "fix it"


def test_parse_verdict_takes_last_fenced_block():
    first = '```json\n{"outcome": "request_changes", "summary": "first"}\n```'
    last = '```json\n{"outcome": "done", "summary": "last"}\n```'
    stdout = _envelope(f"{first}\nin between\n{last}")

    run = parse_verdict(
        stdout, allowed=(Outcome.DONE, Outcome.REQUEST_CHANGES)
    )

    assert run.outcome is Outcome.DONE
    assert run.summary == "last"


def test_parse_verdict_falls_back_to_whole_result_json():
    stdout = _envelope('{"outcome": "done", "summary": "no fence"}')

    run = parse_verdict(stdout, allowed=(Outcome.DONE,))

    assert run.outcome is Outcome.DONE
    assert run.summary == "no fence"


def test_parse_verdict_missing_summary_defaults_empty():
    inner = '```json\n{"outcome": "done"}\n```'
    stdout = _envelope(inner)

    run = parse_verdict(stdout, allowed=(Outcome.DONE,))

    assert run.outcome is Outcome.DONE
    assert run.summary == ""


def test_parse_verdict_outcome_outside_allowed_raises():
    inner = '```json\n{"outcome": "request_changes", "summary": "x"}\n```'
    stdout = _envelope(inner)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(Outcome.DONE,))


def test_parse_verdict_invalid_outcome_value_raises():
    inner = '```json\n{"outcome": "nonsense", "summary": "x"}\n```'
    stdout = _envelope(inner)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(Outcome.DONE, Outcome.REQUEST_CHANGES))


def test_parse_verdict_is_error_true_raises():
    inner = '```json\n{"outcome": "done", "summary": "ok"}\n```'
    stdout = _envelope(inner, is_error=True)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(Outcome.DONE,))


def test_parse_verdict_unreadable_outer_json_raises():
    with pytest.raises(VerdictError):
        parse_verdict("this is not JSON", allowed=(Outcome.DONE,))


def test_parse_verdict_missing_result_field_raises():
    stdout = json.dumps({"is_error": False})

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(Outcome.DONE,))


def test_parse_verdict_no_verdict_block_raises():
    stdout = _envelope("Just chatter, no verdict.")

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(Outcome.DONE,))


def test_parse_verdict_missing_outcome_key_raises():
    inner = '```json\n{"summary": "missing outcome"}\n```'
    stdout = _envelope(inner)

    with pytest.raises(VerdictError):
        parse_verdict(stdout, allowed=(Outcome.DONE,))


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

    run = verdict_from_final(final, allowed=(Outcome.DONE,), raw="RAW")

    assert run.outcome is Outcome.DONE
    assert run.summary == "ok"
    assert run.raw == "RAW"


def test_verdict_from_final_is_error_raises():
    final = {"type": "result", "is_error": True, "result": "boom"}

    with pytest.raises(VerdictError):
        verdict_from_final(final, allowed=(Outcome.DONE,), raw="RAW")


def test_verdict_from_final_outside_allowed_raises():
    inner = '```json\n{"outcome": "request_changes", "summary": "x"}\n```'
    final = {"type": "result", "is_error": False, "result": inner}

    with pytest.raises(VerdictError):
        verdict_from_final(final, allowed=(Outcome.DONE,), raw="RAW")


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

    final, raw, stderr = await _drain(proc, captured.append)

    assert captured == ["● session started (opus)", "Listing files.", "⏵ Bash: ls"]
    assert stderr == "warn"
    assert final is not None and final["type"] == "result"
    run = verdict_from_final(final, allowed=(Outcome.DONE,), raw=raw)
    assert run.outcome is Outcome.DONE
    assert run.summary == "ok"
