import json
import os
import stat
from pathlib import Path

import pytest

from agentharness.runner.executor import (
    FORBIDDEN_FLAGS,
    ExecRequest,
    ExecResult,
    FakeExecutor,
    LocalExecutor,
    fake_ok,
)


def req(**over) -> ExecRequest:
    base = dict(prompt="do the thing", cwd=Path("/tmp/ws"), max_turns=7)
    base.update(over)
    return ExecRequest(**base)


def argv(**over) -> list[str]:
    return LocalExecutor().build_argv(req(**over))


def pair(a: list[str], flag: str) -> str | None:
    return a[a.index(flag) + 1] if flag in a else None


def test_argv_starts_with_binary_dash_p_and_prompt():
    a = argv()
    assert a[:3] == ["claude", "-p", "do the thing"]


def test_argv_requests_json_output():
    assert pair(argv(), "--output-format") == "json"


def test_argv_carries_permission_mode():
    assert pair(argv(permission_mode="plan"), "--permission-mode") == "plan"


def test_argv_carries_max_turns():
    assert pair(argv(), "--max-turns") == "7"


def test_argv_confines_to_the_workspace():
    assert pair(argv(cwd=Path("/tmp/run1")), "--add-dir") == "/tmp/run1"


def test_argv_joins_allowed_tools_with_commas():
    assert pair(argv(allowed_tools=["Read", "Write"]), "--allowedTools") == "Read,Write"


def test_argv_omits_allowed_tools_when_empty():
    assert "--allowedTools" not in argv(allowed_tools=[])


def test_argv_includes_disallowed_tools_only_when_set():
    assert "--disallowedTools" not in argv()
    assert pair(argv(disallowed_tools=["Bash"]), "--disallowedTools") == "Bash"


def test_argv_includes_model_only_when_set():
    assert "--model" not in argv()
    assert pair(argv(model="claude-sonnet-4-5"), "--model") == "claude-sonnet-4-5"


def test_argv_includes_mcp_config_only_when_set():
    assert "--mcp-config" not in argv()
    assert pair(argv(mcp_config=Path("/m.json")), "--mcp-config") == "/m.json"


def test_argv_includes_system_prompt_only_when_set():
    assert "--append-system-prompt" not in argv()
    assert pair(argv(system_prompt="You are X"), "--append-system-prompt") == "You are X"


@pytest.mark.parametrize("flag", FORBIDDEN_FLAGS)
def test_argv_never_resumes_a_session(flag):
    """Statelessness is structural: no run may inherit another run's session."""
    full = argv(
        system_prompt="s",
        model="m",
        mcp_config=Path("/m.json"),
        allowed_tools=["Read"],
        disallowed_tools=["Bash"],
    )
    assert flag not in full


def fake_binary(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake-claude"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_run_parses_the_cli_json_envelope(tmp_path):
    envelope = {
        "result": "all done",
        "is_error": False,
        "session_id": "sess_1",
        "num_turns": 4,
        "total_cost_usd": 0.42,
    }
    binary = fake_binary(tmp_path, f"cat <<'EOF'\n{json.dumps(envelope)}\nEOF\n")
    out = LocalExecutor(binary).run(req(cwd=tmp_path))

    assert out.exit_code == 0
    assert out.is_error is False
    assert out.session_id == "sess_1"
    assert out.num_turns == 4
    assert out.total_cost_usd == 0.42
    assert out.result_text == "all done"
    assert out.cli_json == envelope


def test_run_flags_is_error_from_the_envelope_even_on_exit_zero(tmp_path):
    binary = fake_binary(tmp_path, 'echo \'{"is_error": true, "result": "nope"}\'\n')
    assert LocalExecutor(binary).run(req(cwd=tmp_path)).is_error is True


def test_run_reports_non_zero_exit_as_error(tmp_path):
    binary = fake_binary(tmp_path, "echo boom >&2\nexit 3\n")
    out = LocalExecutor(binary).run(req(cwd=tmp_path))
    assert out.exit_code == 3
    assert out.is_error is True
    assert "boom" in out.stderr


def test_run_tolerates_malformed_stdout(tmp_path):
    binary = fake_binary(tmp_path, "echo 'not json at all'\n")
    out = LocalExecutor(binary).run(req(cwd=tmp_path))
    assert out.cli_json is None
    assert out.is_error is False


def test_run_kills_a_process_that_outlives_its_timeout(tmp_path):
    binary = fake_binary(tmp_path, "sleep 30\n")
    out = LocalExecutor(binary).run(req(cwd=tmp_path, timeout_seconds=1))
    assert out.timed_out is True
    assert out.is_error is True


def test_fake_executor_records_requests():
    fake = FakeExecutor()
    r = req()
    fake.run(r)
    assert fake.requests == [r]


def test_fake_ok_writes_result_json_into_the_artifact_dir(tmp_path):
    payload = {"status": "ok", "summary": "did it", "outputs": ["draft.md"]}
    fake = FakeExecutor(fake_ok(payload, ".harness/runs/tr_1/t_1"))
    out = fake.run(req(cwd=tmp_path))

    written = tmp_path / ".harness/runs/tr_1/t_1/result.json"
    assert json.loads(written.read_text()) == payload
    assert out.total_cost_usd == 0.01
    assert out.is_error is False


def test_no_module_outside_executor_imports_an_anthropic_sdk():
    """The CLI-only constraint, enforced by a test rather than a comment."""
    src = Path(__file__).parent.parent / "src" / "agentharness"
    offenders = []
    for path in src.rglob("*.py"):
        text = path.read_text()
        if "anthropic" in text.lower() and path.name != "executor.py":
            offenders.append(str(path))
    assert offenders == []
