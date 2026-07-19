from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agentharness.models import AgentDef, Handoff, Result, RetryPolicy, Task, TaskArtifacts


def make_task(**over) -> Task:
    base = dict(
        task_id="t_1",
        trace_id="tr_1",
        parent_task_id=None,
        agent="writer",
        repo="app",
        intent="draft",
        payload={"topic": "x"},
        artifacts=TaskArtifacts(base_ref="abc123", inputs=["brief.md"]),
        idempotency_key="writer:draft:tr_1",
        created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    base.update(over)
    return Task(**base)


def test_task_defaults():
    t = make_task()
    assert t.priority == 5
    assert t.attempt == 1
    assert t.schedule_id is None


def test_task_artifact_dir():
    assert make_task().artifact_dir == ".harness/runs/tr_1/t_1"


def test_task_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        make_task(bogus="nope")


def test_task_roundtrips_through_json():
    t = make_task()
    assert Task.model_validate_json(t.model_dump_json()) == t


def test_agent_def_defaults():
    a = AgentDef(name="writer", description="writes", allowed_tools=["Read", "Write"])
    assert a.permission_mode == "acceptEdits"
    assert a.concurrency == 1
    assert a.retries == RetryPolicy()
    assert a.can_handoff_to == []


def test_agent_def_rejects_bad_permission_mode():
    with pytest.raises(ValidationError):
        AgentDef(name="w", description="d", allowed_tools=[], permission_mode="yolo")


def test_agent_def_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AgentDef(name="w", description="d", allowed_tools=[], typo_field=1)


def test_result_defaults_to_no_handoffs():
    r = Result(status="ok")
    assert r.handoffs == []
    assert r.outputs == []


def test_result_parses_handoffs():
    r = Result.model_validate(
        {
            "status": "ok",
            "summary": "done",
            "outputs": ["draft.md"],
            "handoffs": [
                {"agent": "reviewer", "intent": "review", "artifacts": {"inputs": ["draft.md"]}}
            ],
        }
    )
    assert r.handoffs[0] == Handoff(
        agent="reviewer", intent="review", artifacts=TaskArtifacts(inputs=["draft.md"])
    )


def test_result_rejects_bad_status():
    with pytest.raises(ValidationError):
        Result(status="maybe")
