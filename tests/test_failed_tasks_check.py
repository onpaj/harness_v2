"""`FailedTasksCheck` — the self-heal action (ADR-0018).

Migrated from the old `Healer` loop's `tests/test_healer.py`: same behavioral
guarantees (claim + settle exactly once, empty/lost-claim no-ops, the
recursion guard), now exercised directly against the `Check` port.
"""

from harness.drivers.failed_tasks_check import FailedTasksCheck
from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.models import FAILED, HEALED, HistoryEntry, Task


def failed_task(
    task_id: str = "tsk_boom",
    *,
    reason: str = "boom",
    data: dict | None = None,
) -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-21T10:00:00Z",
        repository="app",
        status=FAILED,
        data=data if data is not None else {"request": "Do the thing"},
        history=(
            HistoryEntry(
                at="2026-07-21T10:00:00Z",
                actor="consumer:development",
                from_step="development",
                to_step=FAILED,
                reason=reason,
            ),
        ),
    )


def make_check(*, failed, healed, events=None, clock=None) -> FailedTasksCheck:
    return FailedTasksCheck(
        failed=failed,
        healed=healed,
        events=events or MemoryEventSink(),
        clock=clock or FakeClock(),
    )


def test_empty_failed_queue_is_a_noop():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    check = make_check(failed=failed, healed=healed)

    assert check.evaluate() == []
    assert healed.list() == []


def test_lost_claim_race_is_a_noop():
    class NeverClaims(MemoryTaskQueue):
        def claim(self, task, lock_id):
            return None  # someone else grabbed it first

    failed = NeverClaims("failed")
    failed.put(failed_task())
    healed = MemoryTaskQueue("healed")
    check = make_check(failed=failed, healed=healed)

    assert check.evaluate() == []
    assert healed.list() == []


def test_two_failed_tasks_yield_two_observations_and_drain_failed():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_a"))
    failed.put(failed_task("tsk_b"))
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert {obs.state_key for obs in observations} == {"tsk_a", "tsk_b"}
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 2
    assert all(task.status == HEALED for task in settled)
    assert all("queued for healing" in task.history[-1].summary for task in settled)


def test_observation_carries_the_recursion_guard_marker():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_boom"))
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.data["heal"] == {"of": "tsk_boom"}


def test_observation_body_contains_the_rendered_failure_report():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task(reason="no edge from review"))
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    body = observation.data["body"]
    assert "tsk_boom" in body
    assert "no edge from review" in body
    assert "## Failure report" in body
    # structured fields are present *alongside* the rendered body, not instead
    assert observation.data["reason"] == "no edge from review"
    assert observation.data["history"] == []


def test_observation_request_is_synthesized_distinct_from_original_request():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.data["original_request"] == "Do the thing"
    assert observation.data["request"] != "Do the thing"
    assert "tsk_boom" in observation.data["request"]


def test_observation_carries_source_when_the_original_task_has_one():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task(data={"request": "x", "source": {"url": "https://gh/i/1"}}))
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.data["source"] == {"url": "https://gh/i/1"}


def test_observation_omits_source_when_the_original_task_has_none():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert "source" not in observation.data


def test_recursion_guard_skips_a_heal_task_and_settles_without_an_observation():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert observations == []
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1
    assert settled[0].status == HEALED
    assert "heal-failed" in settled[0].history[-1].summary


def test_healing_and_healed_events_are_emitted():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    events = MemoryEventSink()
    check = make_check(failed=failed, healed=healed, events=events)

    check.evaluate()

    assert "healing" in events.names()
    assert "healed" in events.names()


def test_marker_prefix_is_the_opening_of_a_rendered_marker():
    """The brake matches on MARKER_PREFIX; this pins it to the real marker's
    spelling, so changing `marker_comment` can never silently disarm it."""
    from harness.drivers.github_issues import MARKER_PREFIX, marker_comment

    assert marker_comment("tsk_boom").startswith(MARKER_PREFIX)


def test_one_hop_brake_declines_a_failed_self_heal_fix():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(
        failed_task(
            "tsk_fix_1",
            data={
                "request": "Fix the driver contract",
                "body": "## Symptom\nboom\n\n<!-- harness-issue:tsk_boom:ab12cd34 -->\n",
            },
        )
    )
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert observations == []
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1
    assert settled[0].status == HEALED
    assert "heal-declined" in settled[0].history[-1].summary


def test_an_unmarked_issue_task_is_still_healed():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(
        failed_task(
            "tsk_plain",
            data={"request": "Add a feature", "body": "## Context\nno marker here\n"},
        )
    )
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.state_key == "tsk_plain"
    assert "queued for healing" in healed.list()[0].history[-1].summary


def test_the_two_recursion_guards_record_distinct_notes():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    failed.put(
        failed_task(
            "tsk_fix_1", data={"body": "<!-- harness-issue:tsk_boom:ab12cd34 -->"}
        )
    )
    check = make_check(failed=failed, healed=healed)

    assert check.evaluate() == []

    notes = {task.id: task.history[-1].summary for task in healed.list()}
    assert "heal-failed" in notes["tsk_heal_1"]
    assert "heal-declined" in notes["tsk_fix_1"]


def test_brake_declines_a_failed_resolver_task_born_from_a_harness_pr():
    """`GithubConflictsCheck` mints resolver tasks with `source.kind ==
    "mergeability"` and no body/`data.heal` — the brake must still catch
    them, or the resolver half of the cycle in the review's diagram is
    unbounded."""
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(
        failed_task(
            "tsk_resolver_1",
            data={"request": "resolve merge conflict", "source": {"kind": "mergeability"}},
        )
    )
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert observations == []
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1
    assert settled[0].status == HEALED
    assert "heal-declined" in settled[0].history[-1].summary


def test_brake_declines_a_failed_automerge_review_task_born_from_a_harness_pr():
    """`GithubMergeableCheck` mints automerge-review tasks with `source.kind
    == "pull-request"` and no body/`data.heal` — the brake's twin case."""
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(
        failed_task(
            "tsk_automerge_1",
            data={"request": "review PR for automatic merge", "source": {"kind": "pull-request"}},
        )
    )
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert observations == []
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1
    assert settled[0].status == HEALED
    assert "heal-declined" in settled[0].history[-1].summary


def test_an_ordinary_github_issue_sourced_task_is_still_healed():
    """`GithubIssuesCheck` stamps ordinary issue-borne tasks with
    `source.kind == "github"` and no marker — the widened guard must not
    swallow those; only the two PR-borne kinds are declined."""
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(
        failed_task(
            "tsk_issue_1",
            data={"request": "fix the bug", "source": {"kind": "github"}},
        )
    )
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.state_key == "tsk_issue_1"
    assert "queued for healing" in healed.list()[0].history[-1].summary
