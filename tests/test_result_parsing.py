import json
from datetime import datetime, timezone

from agentharness.models import Task
from agentharness.runner.executor import ExecResult
from agentharness.runner.result import parse_result


def task() -> Task:
    return Task(
        task_id="t_1",
        trace_id="tr_1",
        agent="writer",
        intent="draft",
        idempotency_key="k",
        created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )


def write_result(worktree, payload):
    t = task()
    d = worktree / t.artifact_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(payload if isinstance(payload, str) else json.dumps(payload))


def ok_exec(**over) -> ExecResult:
    base = dict(exit_code=0, is_error=False, result_text="cli said done")
    base.update(over)
    return ExecResult(**base)


def test_valid_result_json_parses_cleanly(tmp_path):
    write_result(tmp_path, {"status": "ok", "summary": "did it", "outputs": ["draft.md"]})
    parsed = parse_result(tmp_path, task(), ok_exec())

    assert parsed.degraded is False
    assert parsed.result.status == "ok"
    assert parsed.result.outputs == ["draft.md"]


def test_handoffs_survive_the_round_trip(tmp_path):
    write_result(
        tmp_path,
        {
            "status": "ok",
            "handoffs": [
                {"agent": "reviewer", "intent": "review", "artifacts": {"inputs": ["draft.md"]}}
            ],
        },
    )
    parsed = parse_result(tmp_path, task(), ok_exec())

    assert parsed.degraded is False
    assert parsed.result.handoffs[0].agent == "reviewer"
    assert parsed.result.handoffs[0].artifacts.inputs == ["draft.md"]


def test_missing_file_degrades_to_the_cli_result_text(tmp_path):
    parsed = parse_result(tmp_path, task(), ok_exec())

    assert parsed.degraded is True
    assert parsed.result.status == "ok"
    assert parsed.result.summary == "cli said done"
    assert "not written" in parsed.reason


def test_invalid_json_degrades_with_its_own_reason(tmp_path):
    write_result(tmp_path, "{not json")
    parsed = parse_result(tmp_path, task(), ok_exec())

    assert parsed.degraded is True
    assert "valid JSON" in parsed.reason


def test_schema_violation_degrades_with_its_own_reason(tmp_path):
    write_result(tmp_path, {"status": "maybe"})
    parsed = parse_result(tmp_path, task(), ok_exec())

    assert parsed.degraded is True
    assert "schema validation" in parsed.reason


def test_fallback_status_is_failed_when_the_cli_errored(tmp_path):
    parsed = parse_result(tmp_path, task(), ok_exec(is_error=True))

    assert parsed.degraded is True
    assert parsed.result.status == "failed"


def test_a_written_result_is_trusted_even_when_the_cli_errored(tmp_path):
    """The file is authoritative; the caller decides whether the run failed."""
    write_result(tmp_path, {"status": "ok", "summary": "wrote it anyway"})
    parsed = parse_result(tmp_path, task(), ok_exec(is_error=True))

    assert parsed.degraded is False
    assert parsed.result.status == "ok"
