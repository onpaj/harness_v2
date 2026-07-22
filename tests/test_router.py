import pytest

from harness.models import (
    END,
    FAILED,
    Failed,
    Finished,
    MoveTo,
    Task,
    Transition,
    Workflow,
)
from harness.router import route

WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="design"),
        Transition(from_step="design", on="done", to_step="architecture"),
        Transition(from_step="architecture", on="done", to_step="development"),
        Transition(from_step="development", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
        Transition(from_step="review", on="request_changes", to_step="development"),
    ),
)


def task(status=None, last_outcome=None, step=None, workflow_template="default") -> Task:
    return Task(
        id="tsk_1",
        workflow_template=workflow_template,
        step=step,
        created="2026-07-19T10:00:00Z",
        status=status,
        last_outcome=last_outcome,
    )


def test_new_task_goes_to_start():
    assert route(task(), WORKFLOW) == MoveTo("plan")


@pytest.mark.parametrize(
    ("status", "outcome", "expected"),
    [
        ("plan", "done", "design"),
        ("design", "done", "architecture"),
        ("architecture", "done", "development"),
        ("development", "done", "review"),
    ],
)
def test_forward_edges(status, outcome, expected):
    assert route(task(status, outcome), WORKFLOW) == MoveTo(expected)


def test_backward_edge():
    assert route(task("review", "request_changes"), WORKFLOW) == MoveTo("development")


def test_end_node_finishes():
    assert route(task("review", "done"), WORKFLOW) == Finished()


def test_missing_edge_finishes_by_default():
    """FR-3: a workflow only adds redirections; a known step with no outgoing
    edge for this outcome is a complete unit of work and finishes, rather
    than failing."""
    assert route(task("plan", "request_changes"), WORKFLOW) == Finished()


def test_unknown_status_fails():
    decision = route(task("nonsense", "done"), WORKFLOW)

    assert isinstance(decision, Failed)
    assert "nonsense" in decision.reason


def test_status_without_outcome_is_inconsistent():
    decision = route(task("design", None), WORKFLOW)

    assert isinstance(decision, Failed)
    assert "lastOutcome" in decision.reason


def test_retry_edge_pointing_at_itself():
    workflow = Workflow(
        name="retry",
        start="plan",
        transitions=(Transition(from_step="plan", on="request_changes", to_step="plan"),),
    )

    assert route(task("plan", "request_changes"), workflow) == MoveTo("plan")


# --- Workflow-less routing (FR-1/FR-2/FR-4) ---------------------------------


def test_workflow_less_fresh_task_moves_to_its_step():
    fresh = task(status=None, step="development", workflow_template=None)

    assert route(fresh, None) == MoveTo("development")


def test_workflow_less_task_finishes_after_one_pass_on_any_outcome():
    done = task(status="development", last_outcome="done", workflow_template=None)
    changes = task(
        status="development", last_outcome="request_changes", workflow_template=None
    )

    assert route(done, None) == Finished()
    assert route(changes, None) == Finished()


def test_workflow_less_task_with_no_step_fails():
    broken = task(status=None, step=None, workflow_template=None)

    assert route(broken, None) == Failed("workflow-less task has no usable step")


@pytest.mark.parametrize("reserved", [END, FAILED])
def test_workflow_less_task_with_reserved_step_fails(reserved):
    broken = task(status=None, step=reserved, workflow_template=None)

    decision = route(broken, None)

    assert isinstance(decision, Failed)
    assert decision != MoveTo(reserved)


def test_workflow_less_task_still_requires_last_outcome():
    inconsistent = task(status="development", last_outcome=None, workflow_template=None)

    decision = route(inconsistent, None)

    assert isinstance(decision, Failed)
    assert "lastOutcome" in decision.reason
