"""`FailedTasksCheck` — the self-heal action (ADR-0018, ADR-0024).

Migrated from the old `Healer` loop's `tests/test_healer.py`: same behavioral
guarantees (claim + settle exactly once, empty/lost-claim no-ops, the
recursion guard), now exercised directly against the `Check` port.

Since ADR-0024 the two outcomes land in the two columns an operator already
reads: a healed failure is retired into `done/`, a declined one stays in
`failed/` carrying one history line that says why.
"""

from harness.drivers.failed_tasks_check import FailedTasksCheck
from harness.drivers.github_mergeable_check import SOURCE_KIND as PULL_REQUEST_SOURCE_KIND
from harness.drivers.github_unhealthy_prs_check import SOURCE_KIND as PR_HEALTH_SOURCE_KIND
from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.models import END, FAILED, HistoryEntry, Task


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


def make_check(*, failed, done, events=None, clock=None) -> FailedTasksCheck:
    return FailedTasksCheck(
        failed=failed,
        done=done,
        events=events or MemoryEventSink(),
        clock=clock or FakeClock(),
    )


def test_empty_failed_queue_is_a_noop():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    check = make_check(failed=failed, done=done)

    assert check.evaluate() == []
    assert done.list() == []


def test_lost_claim_race_is_a_noop():
    class NeverClaims(MemoryTaskQueue):
        def claim(self, task, lock_id):
            return None  # someone else grabbed it first

    failed = NeverClaims("failed")
    failed.put(failed_task())
    done = MemoryTaskQueue("done")
    check = make_check(failed=failed, done=done)

    assert check.evaluate() == []
    assert done.list() == []


def test_two_failed_tasks_yield_two_observations_and_drain_failed():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task("tsk_a"))
    failed.put(failed_task("tsk_b"))
    check = make_check(failed=failed, done=done)

    observations = check.evaluate()

    assert {obs.state_key for obs in observations} == {"tsk_a", "tsk_b"}
    assert failed.list() == []
    settled = done.list()
    assert len(settled) == 2
    assert all(task.status == END for task in settled)
    assert all("queued for healing" in task.history[-1].summary for task in settled)


def test_the_healer_is_named_in_the_history_of_the_task_it_retired():
    """The only thing that distinguishes a healed failure from an ordinary
    completion, now that both sit in `done` (ADR-0024)."""
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task())
    check = make_check(failed=failed, done=done)

    check.evaluate()

    entry = done.list()[0].history[-1]
    assert entry.actor == "failed-tasks"
    assert (entry.from_step, entry.to_step) == (FAILED, END)


def test_observation_carries_the_recursion_guard_marker():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task("tsk_boom"))
    check = make_check(failed=failed, done=done)

    (observation,) = check.evaluate()

    assert observation.data["heal"] == {"of": "tsk_boom"}


def test_observation_body_contains_the_rendered_failure_report():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task(reason="no edge from review"))
    check = make_check(failed=failed, done=done)

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
    done = MemoryTaskQueue("done")
    failed.put(failed_task())
    check = make_check(failed=failed, done=done)

    (observation,) = check.evaluate()

    assert observation.data["original_request"] == "Do the thing"
    assert observation.data["request"] != "Do the thing"
    assert "tsk_boom" in observation.data["request"]


def test_observation_carries_source_when_the_original_task_has_one():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task(data={"request": "x", "source": {"url": "https://gh/i/1"}}))
    check = make_check(failed=failed, done=done)

    (observation,) = check.evaluate()

    assert observation.data["source"] == {"url": "https://gh/i/1"}


def test_observation_omits_source_when_the_original_task_has_none():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task())
    check = make_check(failed=failed, done=done)

    (observation,) = check.evaluate()

    assert "source" not in observation.data


def test_recursion_guard_leaves_a_failed_heal_task_in_failed_with_a_note():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    check = make_check(failed=failed, done=done)

    observations = check.evaluate()

    assert observations == []
    assert done.list() == []
    (declined,) = failed.list()
    assert declined.status == FAILED
    assert "heal-failed" in declined.history[-1].summary


def test_a_declined_task_is_noted_once_and_then_left_alone():
    """The note is the marker: a later tick must not claim, re-note or
    re-observe a failure the healer has already declined."""

    class CountingQueue(MemoryTaskQueue):
        claims = 0

        def claim(self, task, lock_id):
            CountingQueue.claims += 1
            return super().claim(task, lock_id)

    failed = CountingQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    check = make_check(failed=failed, done=done)

    assert check.evaluate() == []
    assert check.evaluate() == []
    assert check.evaluate() == []

    assert CountingQueue.claims == 1
    (declined,) = failed.list()
    notes = [entry for entry in declined.history if entry.actor == "failed-tasks"]
    assert len(notes) == 1


def test_healing_and_healed_events_are_emitted():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task())
    events = MemoryEventSink()
    check = make_check(failed=failed, done=done, events=events)

    check.evaluate()

    assert "healing" in events.names()
    assert "healed" in events.names()


def test_a_decline_emits_its_own_event_pointing_back_at_the_failed_column():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    events = MemoryEventSink()
    check = make_check(failed=failed, done=done, events=events)

    check.evaluate()

    (fields,) = [fields for name, fields in events.events if name == "heal-declined"]
    assert fields["queue"] == "failed"
    assert fields["task_id"] == "tsk_heal_1"


def test_marker_prefix_is_the_opening_of_a_rendered_marker():
    """The brake matches on MARKER_PREFIX; this pins it to the real marker's
    spelling, so changing `marker_comment` can never silently disarm it."""
    from harness.drivers.github_issues import MARKER_PREFIX, marker_comment

    assert marker_comment("tsk_boom").startswith(MARKER_PREFIX)


def test_one_hop_brake_declines_a_failed_self_heal_fix():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(
        failed_task(
            "tsk_fix_1",
            data={
                "request": "Fix the driver contract",
                "body": "## Symptom\nboom\n\n<!-- harness-issue:tsk_boom:ab12cd34 -->\n",
            },
        )
    )
    check = make_check(failed=failed, done=done)

    observations = check.evaluate()

    assert observations == []
    assert done.list() == []
    (declined,) = failed.list()
    assert declined.status == FAILED
    assert "heal-declined" in declined.history[-1].summary


def test_an_unmarked_issue_task_is_still_healed():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(
        failed_task(
            "tsk_plain",
            data={"request": "Add a feature", "body": "## Context\nno marker here\n"},
        )
    )
    check = make_check(failed=failed, done=done)

    (observation,) = check.evaluate()

    assert observation.state_key == "tsk_plain"
    assert "queued for healing" in done.list()[0].history[-1].summary


def test_the_two_recursion_guards_record_distinct_notes():
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    failed.put(
        failed_task(
            "tsk_fix_1", data={"body": "<!-- harness-issue:tsk_boom:ab12cd34 -->"}
        )
    )
    check = make_check(failed=failed, done=done)

    assert check.evaluate() == []

    notes = {task.id: task.history[-1].summary for task in failed.list()}
    assert "heal-failed" in notes["tsk_heal_1"]
    assert "heal-declined" in notes["tsk_fix_1"]


def test_brake_declines_a_failed_unblock_task_born_from_a_harness_pr():
    """`GithubUnhealthyPrsCheck` mints unblock tasks with `source.kind ==
    github_unhealthy_prs_check.SOURCE_KIND` ("pull-request-health") and no
    body/`data.heal` — the brake must still catch them, or the unblock half
    of the cycle in the review's diagram is unbounded. Sourcing the literal
    from the owning driver's own constant means renaming it there fails
    this test instead of silently disarming the guard."""
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(
        failed_task(
            "tsk_unblock_1",
            data={
                "request": "unblock PR #7",
                "source": {"kind": PR_HEALTH_SOURCE_KIND},
            },
        )
    )
    check = make_check(failed=failed, done=done)

    observations = check.evaluate()

    assert observations == []
    assert done.list() == []
    (declined,) = failed.list()
    assert declined.status == FAILED
    assert "heal-declined" in declined.history[-1].summary


def test_brake_declines_a_failed_automerge_review_task_born_from_a_harness_pr():
    """`GithubMergeableCheck` mints automerge-review tasks with `source.kind
    == github_mergeable_check.SOURCE_KIND` ("pull-request") and no body/
    `data.heal` — the brake's twin case. Sourcing the literal from the
    owning driver's own constant means renaming it there fails this test
    instead of silently disarming the guard."""
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(
        failed_task(
            "tsk_automerge_1",
            data={
                "request": "review PR for automatic merge",
                "source": {"kind": PULL_REQUEST_SOURCE_KIND},
            },
        )
    )
    check = make_check(failed=failed, done=done)

    observations = check.evaluate()

    assert observations == []
    assert done.list() == []
    (declined,) = failed.list()
    assert declined.status == FAILED
    assert "heal-declined" in declined.history[-1].summary


def test_an_ordinary_github_issue_sourced_task_is_still_healed():
    """`GithubIssuesCheck` stamps ordinary issue-borne tasks with
    `source.kind == "github"` and no marker — the widened guard must not
    swallow those; only the two PR-borne kinds are declined."""
    failed = MemoryTaskQueue("failed")
    done = MemoryTaskQueue("done")
    failed.put(
        failed_task(
            "tsk_issue_1",
            data={"request": "fix the bug", "source": {"kind": "github"}},
        )
    )
    check = make_check(failed=failed, done=done)

    (observation,) = check.evaluate()

    assert observation.state_key == "tsk_issue_1"
    assert "queued for healing" in done.list()[0].history[-1].summary


def test_diagnostic_request_names_the_step_that_failed_not_the_word_failed():
    from harness.drivers.failed_tasks_check import _diagnostic_request
    from harness.models import FAILED, HistoryEntry, Task

    task = Task(
        id="tsk_1",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
        history=(
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
        ),
    )

    request = _diagnostic_request(task)

    assert "'development'" in request
    assert "'failed'" not in request


def test_diagnostic_request_falls_back_to_status_when_there_is_no_trace():
    from harness.drivers.failed_tasks_check import _diagnostic_request
    from harness.models import FAILED, Task

    task = Task(
        id="tsk_2",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
    )

    assert "'failed'" in _diagnostic_request(task)


def test_render_failure_report_names_the_step_that_failed_not_the_word_failed():
    from harness.drivers.failed_tasks_check import _render_failure_report
    from harness.models import FAILED, HistoryEntry, Task

    task = Task(
        id="tsk_1",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
        history=(
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
        ),
    )

    report = _render_failure_report(task)

    assert "- failing step: development" in report
    assert "- failing step: failed" not in report


def test_render_failure_report_falls_back_to_status_when_there_is_no_trace():
    from harness.drivers.failed_tasks_check import _render_failure_report
    from harness.models import FAILED, Task

    task = Task(
        id="tsk_2",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
    )

    assert "- failing step: failed" in _render_failure_report(task)


def test_pr_health_tasks_are_recognised_as_pr_born():
    from harness.drivers.failed_tasks_check import PR_BORN_SOURCE_KINDS
    from harness.drivers.github_unhealthy_prs_check import SOURCE_KIND

    assert SOURCE_KIND in PR_BORN_SOURCE_KINDS
