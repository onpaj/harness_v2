"""`LabelIssueBehavior` — wraps a step's own behavior and applies an outcome ->
label mapping to the task's source GitHub issue after it returns."""

from __future__ import annotations

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.label_issue import LabelIssueBehavior
from harness.models import DONE, REQUEST_CHANGES, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior


class StubInner(ConsumerBehavior):
    """A fixed inner behavior — stands in for the persona-driven
    ClaudeCliBehavior a real triage step would run."""

    def __init__(self, result: BehaviorResult) -> None:
        self._result = result
        self.calls: list[Task] = []

    async def run(self, task: Task) -> BehaviorResult:
        self.calls.append(task)
        return self._result


def make_task(data: dict | None = None) -> Task:
    return Task(
        id="tsk_triage_1",
        workflow_template="triage",
        created="2026-07-23T10:00:00Z",
        repository="app",
        status="triage",
        data=data if data is not None else {
            "title": "some issue",
            "source": {
                "kind": "github",
                "repo": "onpaj/harness_v2",
                "issue": 42,
                "url": "https://github.com/onpaj/harness_v2/issues/42",
            },
        },
    )


LABELS = {"done": "harness:todo", "request_changes": "harness:needs-info"}


async def test_approve_path_adds_the_mapped_label_and_keeps_the_outcome():
    client = FakeGithubClient(
        [Issue(42, "some issue", "body", "https://gh/i/42", ())]
    )
    inner = StubInner(BehaviorResult(DONE, summary="looks good"))
    behavior = LabelIssueBehavior(inner=inner, client=client, labels=LABELS)
    task = make_task()

    result = await behavior.run(task)

    assert result.outcome is DONE
    assert result.summary == "looks good"
    (issue,) = client.list_issues("onpaj/harness_v2", label="harness:todo")
    assert issue.number == 42


async def test_reject_path_adds_the_mapped_label():
    client = FakeGithubClient(
        [Issue(42, "some issue", "body", "https://gh/i/42", ())]
    )
    inner = StubInner(BehaviorResult(REQUEST_CHANGES, summary="needs: scope"))
    behavior = LabelIssueBehavior(inner=inner, client=client, labels=LABELS)
    task = make_task()

    result = await behavior.run(task)

    assert result.outcome is REQUEST_CHANGES
    (issue,) = client.list_issues("onpaj/harness_v2", label="harness:needs-info")
    assert issue.number == 42


async def test_inner_runs_exactly_as_it_would_unbound():
    client = FakeGithubClient([Issue(42, "x", "y", "u", ())])
    inner = StubInner(BehaviorResult(DONE))
    behavior = LabelIssueBehavior(inner=inner, client=client, labels=LABELS)
    task = make_task()

    await behavior.run(task)

    assert inner.calls == [task]


async def test_missing_data_source_is_a_no_op_with_a_note_not_a_crash():
    client = FakeGithubClient()
    inner = StubInner(BehaviorResult(DONE, summary="done"))
    behavior = LabelIssueBehavior(inner=inner, client=client, labels=LABELS)
    task = make_task(data={"title": "no provenance"})

    result = await behavior.run(task)

    assert result.outcome is DONE
    assert "no data.source" in result.summary
    assert "done" in result.summary


async def test_unmapped_outcome_is_a_no_op_with_a_note_not_a_crash():
    client = FakeGithubClient([Issue(42, "x", "y", "u", ())])
    inner = StubInner(BehaviorResult(DONE, summary="done"))
    behavior = LabelIssueBehavior(inner=inner, client=client, labels={"request_changes": "harness:needs-info"})
    task = make_task()

    result = await behavior.run(task)

    assert result.outcome is DONE
    assert "no mapped label" in result.summary
    assert client.list_issues("onpaj/harness_v2", label="harness:todo") == []
    assert client.list_issues("onpaj/harness_v2", label="harness:needs-info") == []


async def test_routing_data_is_untouched_when_a_label_is_applied():
    """The finisher labels the issue; it never reroutes the task — the
    dispatcher still routes purely on (status, lastOutcome), invariant #8."""
    client = FakeGithubClient([Issue(42, "x", "y", "u", ())])
    inner = StubInner(BehaviorResult(DONE, summary="done", data={"extra": 1}))
    behavior = LabelIssueBehavior(inner=inner, client=client, labels=LABELS)
    task = make_task()

    result = await behavior.run(task)

    assert result.data == {"extra": 1}
