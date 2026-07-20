import json

import pytest

from harness.drivers.claude_cli import (
    AgentError,
    VerdictError,
    build_argv,
    parse_verdict,
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


def test_agent_error_is_exception():
    assert issubclass(AgentError, Exception)
    assert issubclass(VerdictError, Exception)
