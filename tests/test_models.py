from harness.models import (
    END,
    BehaviorResult,
    Failed,
    Finished,
    HistoryEntry,
    MoveTo,
    Outcome,
    Task,
    Transition,
    Workflow,
    append_history,
)


def test_behavior_result_carries_outcome_and_summary():
    result = BehaviorResult(Outcome.DONE, summary="added retry with backoff")

    assert result.outcome is Outcome.DONE
    assert result.summary == "added retry with backoff"


def test_behavior_result_summary_defaults_empty():
    assert BehaviorResult(Outcome.REQUEST_CHANGES).summary == ""


def test_behavior_result_data_defaults_none():
    assert BehaviorResult(Outcome.DONE).data is None


def test_behavior_result_carries_data():
    result = BehaviorResult(Outcome.DONE, data={"pr": {"number": 1}})

    assert result.data == {"pr": {"number": 1}}


def test_history_entry_roundtrips_summary():
    entry = HistoryEntry(
        at="t",
        actor="consumer:design",
        from_step="design",
        to_step=None,
        outcome="done",
        summary="done",
    )

    raw = entry.to_dict()

    assert raw["summary"] == "done"
    assert HistoryEntry.from_dict(raw) == entry


def test_history_entry_omits_summary_when_absent():
    entry = HistoryEntry(at="t", actor="dispatcher", from_step=None, to_step="plan")

    assert "summary" not in entry.to_dict()


def test_task_roundtrips_through_camelcase_json():
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="app-backend",
        status="design",
        last_outcome="done",
        lock_id="lck_1",
        dedup_key="github:o/r:42",
        data={"request": "add rate limiting"},
    )

    raw = task.to_dict()

    assert raw["workflowTemplate"] == "default"
    assert raw["lastOutcome"] == "done"
    assert raw["lockId"] == "lck_1"
    assert raw["dedupKey"] == "github:o/r:42"
    assert Task.from_dict(raw) == task


def test_workflow_less_task_roundtrips_through_json():
    task = Task(
        id="tsk_1",
        workflow_template=None,
        step="development",
        created="2026-07-19T10:00:00Z",
    )

    raw = task.to_dict()

    assert raw["workflowTemplate"] is None
    assert raw["step"] == "development"
    assert Task.from_dict(raw) == task


def test_task_from_dict_defaults_step_when_absent():
    """Backward compatibility: an existing task file on disk never carries a
    `step` key. `from_dict` must default it to None, not raise KeyError."""
    raw = {
        "id": "tsk_1",
        "workflowTemplate": "default",
        "created": "2026-07-19T10:00:00Z",
    }

    task = Task.from_dict(raw)

    assert task.step is None
    assert task.workflow_template == "default"


def test_task_from_dict_defaults_workflow_template_when_absent():
    """A workflow-less task written before this change never had a
    `workflowTemplate` key either — must default to None, not KeyError."""
    raw = {"id": "tsk_1", "created": "2026-07-19T10:00:00Z"}

    task = Task.from_dict(raw)

    assert task.workflow_template is None
    assert task.step is None


def test_new_task_has_null_status_and_empty_history():
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-19T10:00:00Z")

    assert task.status is None
    assert task.last_outcome is None
    assert task.lock_id is None
    assert task.dedup_key is None
    assert task.history == ()
    assert task.data == {}


def test_history_entry_uses_reserved_json_keys():
    entry = HistoryEntry(
        at="2026-07-19T10:00:05Z",
        actor="dispatcher",
        from_step="design",
        to_step="architecture",
        outcome="done",
    )

    raw = entry.to_dict()

    assert raw["from"] == "design"
    assert raw["to"] == "architecture"
    assert HistoryEntry.from_dict(raw) == entry


def test_history_entry_omits_reason_when_absent():
    entry = HistoryEntry(at="t", actor="dispatcher", from_step=None, to_step="plan")

    assert "reason" not in entry.to_dict()


def test_append_history_returns_new_task():
    task = Task(id="tsk_1", workflow_template="default", created="t")
    entry = HistoryEntry(at="t", actor="dispatcher", from_step=None, to_step="plan")

    updated = append_history(task, entry)

    assert updated.history == (entry,)
    assert task.history == ()


def test_workflow_target_finds_transition():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="done", to_step="design"),
            Transition(from_step="review", on="request_changes", to_step="development"),
        ),
    )

    assert workflow.target("plan", "done") == "design"
    assert workflow.target("review", "request_changes") == "development"
    assert workflow.target("plan", "request_changes") is None


def test_workflow_steps_excludes_end():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="done", to_step="review"),
            Transition(from_step="review", on="done", to_step=END),
        ),
    )

    assert workflow.steps() == ("plan", "review")


def test_workflow_max_parallel_for_defaults_to_one():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )

    assert workflow.max_parallel_for("plan") == 1
    assert workflow.max_parallel_for("unknown") == 1


def test_workflow_max_parallel_for_reads_configured_limit():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="done", to_step="review"),
            Transition(from_step="review", on="done", to_step=END),
        ),
        max_parallel={"review": 3},
    )

    assert workflow.max_parallel_for("review") == 3
    assert workflow.max_parallel_for("plan") == 1


def test_workflow_finisher_for_defaults_to_none():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )

    assert workflow.finisher_for("plan") is None
    assert workflow.finisher_for("unknown") is None


def test_workflow_finisher_for_reads_configured_kind():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="done", to_step="publish"),
            Transition(from_step="publish", on="done", to_step=END),
        ),
        finishers={"publish": "open-pr"},
    )

    assert workflow.finisher_for("publish") == "open-pr"
    assert workflow.finisher_for("plan") is None


def test_outcome_values():
    assert Outcome.DONE.value == "done"
    assert Outcome.REQUEST_CHANGES.value == "request_changes"


def test_decisions_carry_their_payload():
    assert MoveTo("design").step == "design"
    assert Failed("nope").reason == "nope"
    assert Finished() == Finished()
