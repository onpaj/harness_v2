import pytest

from harness.models import END, Failed, Finished, MoveTo, Task, Transition, Workflow
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


def task(status=None, last_outcome=None) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
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


def test_missing_edge_fails():
    decision = route(task("plan", "request_changes"), WORKFLOW)

    assert isinstance(decision, Failed)
    assert "plan" in decision.reason
    assert "request_changes" in decision.reason


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
