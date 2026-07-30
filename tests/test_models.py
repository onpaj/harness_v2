from harness.models import (
    DONE,
    END,
    FAILED,
    HEAL_ACTOR,
    HistoryEntry,
    Task,
    failure_trace,
    is_retired_failure,
    resumable_failure,
    REQUEST_CHANGES,
    BehaviorResult,
    Failed,
    FinisherBinding,
    Finished,
    MoveTo,
    Transition,
    Workflow,
    append_history,
)


def test_behavior_result_carries_outcome_and_summary():
    result = BehaviorResult(DONE, summary="added retry with backoff")

    assert result.outcome == DONE
    assert result.summary == "added retry with backoff"


def test_behavior_result_summary_defaults_empty():
    assert BehaviorResult(REQUEST_CHANGES).summary == ""


def test_behavior_result_data_defaults_none():
    assert BehaviorResult(DONE).data is None


def test_behavior_result_carries_data():
    result = BehaviorResult(DONE, data={"pr": {"number": 1}})

    assert result.data == {"pr": {"number": 1}}


def test_behavior_result_tokens_defaults_none():
    assert BehaviorResult(DONE).tokens is None


def test_behavior_result_carries_tokens_separately_from_data():
    result = BehaviorResult(
        DONE, data={"tokens_total": {"input": 1, "output": 2}}, tokens={"input": 1}
    )

    assert result.tokens == {"input": 1}
    assert result.data == {"tokens_total": {"input": 1, "output": 2}}


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


def test_history_entry_roundtrips_tokens():
    entry = HistoryEntry(
        at="t",
        actor="consumer:development",
        from_step="development",
        to_step=None,
        outcome="done",
        tokens={
            "attempt": 1,
            "input": 1234,
            "output": 567,
            "cache_read": 0,
            "cache_creation": 0,
            "total_cost_usd": 0.0231,
            "model": "claude-sonnet-5",
        },
    )

    raw = entry.to_dict()

    assert raw["tokens"]["input"] == 1234
    assert HistoryEntry.from_dict(raw) == entry


def test_history_entry_omits_tokens_when_absent():
    entry = HistoryEntry(at="t", actor="dispatcher", from_step=None, to_step="plan")

    assert "tokens" not in entry.to_dict()


def test_history_entry_from_dict_defaults_tokens_when_absent():
    """Backward compatibility: history written before this field existed has
    no `tokens` key at all — must default to None, not raise KeyError."""
    raw = {"at": "t", "actor": "dispatcher", "from": None, "to": "plan"}

    entry = HistoryEntry.from_dict(raw)

    assert entry.tokens is None


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
        finishers={"publish": FinisherBinding(kind="open-pr")},
    )

    assert workflow.finisher_for("publish") == FinisherBinding(kind="open-pr")
    assert workflow.finisher_for("plan") is None


def test_outcome_values():
    assert DONE == "done"
    assert REQUEST_CHANGES == "request_changes"


def test_transition_hint_defaults_to_empty_string():
    transition = Transition(from_step="plan", on="done", to_step=END)

    assert transition.hint == ""


def test_transition_hint_can_be_set():
    transition = Transition(from_step="plan", on="done", to_step=END, hint="ship it")

    assert transition.hint == "ship it"


def test_workflow_outcomes_for_preserves_definition_order_and_collapses_duplicates():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="backend", to_step="development"),
            Transition(from_step="plan", on="ui", to_step="designer"),
            Transition(from_step="plan", on="backend", to_step="development"),
            Transition(from_step="review", on="done", to_step=END),
        ),
    )

    assert workflow.outcomes_for("plan") == ("backend", "ui")


def test_workflow_outcomes_for_step_with_no_outgoing_transitions_is_empty():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )

    assert workflow.outcomes_for("end") == ()


def test_workflow_outcomes_for_unknown_step_is_empty():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )

    assert workflow.outcomes_for("unknown") == ()


def test_workflow_description_for_absent_step_is_none():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )

    assert workflow.description_for("plan") is None


def test_workflow_description_for_returns_configured_text():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
        descriptions={"plan": "Write the implementation plan."},
    )

    assert workflow.description_for("plan") == "Write the implementation plan."
    assert workflow.description_for("unknown") is None


def test_decisions_carry_their_payload():
    assert MoveTo("design").step == "design"
    assert Failed("nope").reason == "nope"
    assert Finished() == Finished()


def _timed_out_at_development(status=END, extra=()):
    """A task that passed plan/design/architecture and timed out in development.

    `status=END` plus the trailing `failed-tasks` entry is the retired-failure
    shape; `status=FAILED` without it is the shape a declined failure has.
    """
    history = (
        HistoryEntry(
            at="2026-07-28T19:15:29Z", actor="dispatcher", from_step=None, to_step="plan"
        ),
        HistoryEntry(
            at="2026-07-28T20:02:05Z",
            actor="consumer:plan",
            from_step="plan",
            to_step=None,
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-28T20:02:05Z",
            actor="dispatcher",
            from_step="plan",
            to_step="design",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-28T20:31:29Z",
            actor="dispatcher",
            from_step="design",
            to_step="architecture",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-28T20:31:29Z",
            actor="dispatcher",
            from_step="architecture",
            to_step="development",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-29T00:05:45Z",
            actor="consumer:development",
            from_step="development",
            to_step=FAILED,
            reason="behavior raised an exception: claude timed out after 1800.0s",
        ),
    ) + tuple(extra)
    return Task(
        id="tsk_1",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=status,
        last_outcome="done",
        history=history,
    )


RETIRED_STAMP = HistoryEntry(
    at="2026-07-29T00:06:06Z",
    actor=HEAL_ACTOR,
    from_step=FAILED,
    to_step=END,
    summary="queued for healing",
)


def test_failure_trace_reads_the_failed_step_reason_and_rewind_pair():
    trace = failure_trace(_timed_out_at_development(extra=(RETIRED_STAMP,)))

    assert trace is not None
    assert trace.failed_step == "development"
    assert trace.reason == "behavior raised an exception: claude timed out after 1800.0s"
    assert trace.resume_status == "architecture"
    assert trace.resume_outcome == "done"


def test_failure_trace_is_none_when_the_task_never_failed():
    task = Task(
        id="tsk_2",
        created="2026-07-28T19:14:54Z",
        status=END,
        last_outcome="done",
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step="land",
                to_step=END,
                outcome="done",
            ),
        ),
    )

    assert failure_trace(task) is None


def test_failure_trace_rewind_pair_is_none_when_the_start_step_failed():
    """A first-step failure has no prior hop — the dispatcher entry reads
    None -> plan, so there is nothing to rewind to and route() falls back to
    the workflow's start."""
    task = Task(
        id="tsk_3",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=END,
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step=None,
                to_step="plan",
            ),
            HistoryEntry(
                at="2026-07-28T19:45:29Z",
                actor="consumer:plan",
                from_step="plan",
                to_step=FAILED,
                reason="behavior raised an exception: boom",
            ),
            RETIRED_STAMP,
        ),
    )

    trace = failure_trace(task)

    assert trace is not None
    assert trace.failed_step == "plan"
    assert trace.resume_status is None
    assert trace.resume_outcome is None


def test_failure_trace_is_none_for_a_failure_with_no_step_to_return_to():
    """A dispatcher failure of a task that was never in a step (status None):
    there is no step to resume into, so this is a restart case, not a resume."""
    task = Task(
        id="tsk_4",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step=None,
                to_step=FAILED,
                reason="workflow-less task has no usable step",
            ),
        ),
    )

    assert failure_trace(task) is None


def test_is_retired_failure_recognises_the_healer_stamp():
    assert is_retired_failure(_timed_out_at_development(extra=(RETIRED_STAMP,))) is True


def test_is_retired_failure_rejects_an_ordinary_completion():
    task = Task(
        id="tsk_5",
        created="2026-07-28T19:14:54Z",
        status=END,
        last_outcome="done",
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step="land",
                to_step=END,
                outcome="done",
            ),
        ),
    )

    assert is_retired_failure(task) is False


def test_is_retired_failure_rejects_a_task_still_sitting_in_failed():
    assert is_retired_failure(_timed_out_at_development(status=FAILED)) is False


def test_resumable_failure_admits_a_retired_failure():
    trace = resumable_failure(_timed_out_at_development(extra=(RETIRED_STAMP,)))

    assert trace is not None
    assert trace.failed_step == "development"


def test_resumable_failure_admits_a_declined_failure_still_in_failed():
    trace = resumable_failure(_timed_out_at_development(status=FAILED))

    assert trace is not None
    assert trace.failed_step == "development"


def test_resumable_failure_rejects_a_completion_that_failed_earlier_in_its_life():
    """Failed at development, was restarted, then ran through to `end`. Its
    current terminal position was NOT reached by failing, so resuming it would
    re-run a step of an already-finished task."""
    task = _timed_out_at_development(
        extra=(
            HistoryEntry(
                at="2026-07-29T01:00:00Z",
                actor="operator",
                from_step=FAILED,
                to_step=None,
                reason="restarted by operator",
            ),
            HistoryEntry(
                at="2026-07-29T02:00:00Z",
                actor="dispatcher",
                from_step="land",
                to_step=END,
                outcome="done",
            ),
        )
    )

    assert resumable_failure(task) is None
