"""The Healer loop — an agent assigned to the failed queue."""

from pathlib import Path

from harness.drivers.fifo_strategy import FifoStrategy
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryEventSink,
    MemoryIssueTracker,
    MemoryTaskQueue,
)
from harness.healer import Healer, heal_prompt
from harness.models import DONE, FAILED, HEALED, REQUEST_CHANGES, HistoryEntry, Task
from harness.ports.agent import AgentRun, AgentSpec
from harness.ports.issues import IssueError, IssueTracker

HEALER_SPEC = AgentSpec(
    name="healer",
    prompt="persona",
    allowed_outcomes=(DONE, REQUEST_CHANGES),
)


def failed_task(task_id: str = "tsk_boom", *, reason: str = "boom") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-21T10:00:00Z",
        repository="app",
        status=FAILED,
        data={"request": "Do the thing"},
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


def make_healer(
    *,
    failed: MemoryTaskQueue,
    healed: MemoryTaskQueue,
    runner: FakeAgentRunner,
    tracker: IssueTracker,
    events: MemoryEventSink,
    scratch: Path,
) -> Healer:
    return Healer(
        failed=failed,
        healed=healed,
        runner=runner,
        spec=HEALER_SPEC,
        tracker=tracker,
        repo="onpaj/harness_v2",
        scratch_root=scratch,
        strategy=FifoStrategy(),
        events=events,
        clock=FakeClock(),
    )


async def test_done_verdict_files_an_issue_and_settles_to_healed(tmp_path):
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    tracker = MemoryIssueTracker()
    runner = FakeAgentRunner(
        runs={"healer": AgentRun(DONE, "Fix the driver")},
        writes={"healer": {"issue.md": "# Fix the driver\n\nthe driver mis-handles X"}},
    )
    events = MemoryEventSink()
    healer = make_healer(
        failed=failed, healed=healed, runner=runner, tracker=tracker,
        events=events, scratch=tmp_path / "heal",
    )

    assert await healer.tick() is True

    # one issue, keyed by the failed task id, on the harness repo
    assert len(tracker.opened) == 1
    opened = tracker.opened[0]
    assert opened["repo"] == "onpaj/harness_v2"
    assert opened["marker"] == "tsk_boom"
    assert opened["title"] == "Fix the driver"  # from the '# ' heading

    # the task left failed/ for healed/
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1 and settled[0].status == HEALED
    assert "opened issue" in settled[0].history[-1].summary
    assert "healed" in events.names()


async def test_request_changes_settles_without_an_issue(tmp_path):
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    tracker = MemoryIssueTracker()
    runner = FakeAgentRunner(
        runs={"healer": AgentRun(REQUEST_CHANGES, "external flake, not a bug")}
    )
    events = MemoryEventSink()
    healer = make_healer(
        failed=failed, healed=healed, runner=runner, tracker=tracker,
        events=events, scratch=tmp_path / "heal",
    )

    assert await healer.tick() is True

    assert tracker.opened == []  # nothing filed
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1 and settled[0].status == HEALED
    assert "no action" in settled[0].history[-1].summary


async def test_agent_error_settles_to_healed_and_does_not_loop(tmp_path):
    class BoomRunner(FakeAgentRunner):
        async def run(self, **kwargs):
            raise RuntimeError("claude timed out")

    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    events = MemoryEventSink()
    healer = make_healer(
        failed=failed, healed=healed, runner=BoomRunner(), tracker=MemoryIssueTracker(),
        events=events, scratch=tmp_path / "heal",
    )

    assert await healer.tick() is True

    # never returned to failed/, settled to healed/ with a heal-failed note
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1 and settled[0].status == HEALED
    assert "heal-failed" in settled[0].history[-1].summary
    assert "heal_error" in events.names()


async def test_issue_error_settles_to_healed_and_does_not_loop(tmp_path):
    class RaisingTracker(IssueTracker):
        def open_issue(self, repo, *, title, body, labels, marker):
            raise IssueError("no token")

    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    runner = FakeAgentRunner(
        runs={"healer": AgentRun(DONE, "Fix it")},
        writes={"healer": {"issue.md": "# Fix it\n\nbody"}},
    )
    events = MemoryEventSink()
    healer = make_healer(
        failed=failed, healed=healed, runner=runner, tracker=RaisingTracker(),
        events=events, scratch=tmp_path / "heal",
    )

    assert await healer.tick() is True

    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1 and settled[0].status == HEALED
    assert "heal-failed" in settled[0].history[-1].summary


async def test_empty_failed_queue_is_a_noop(tmp_path):
    healer = make_healer(
        failed=MemoryTaskQueue("failed"),
        healed=MemoryTaskQueue("healed"),
        runner=FakeAgentRunner(),
        tracker=MemoryIssueTracker(),
        events=MemoryEventSink(),
        scratch=tmp_path / "heal",
    )

    assert await healer.tick() is False


async def test_lost_claim_race_is_a_noop(tmp_path):
    class NeverClaims(MemoryTaskQueue):
        def claim(self, task, lock_id):
            return None  # someone else grabbed it first

    failed = NeverClaims("failed")
    failed.put(failed_task())
    healed = MemoryTaskQueue("healed")
    tracker = MemoryIssueTracker()
    healer = make_healer(
        failed=failed, healed=healed, runner=FakeAgentRunner(), tracker=tracker,
        events=MemoryEventSink(), scratch=tmp_path / "heal",
    )

    assert await healer.tick() is False
    assert tracker.opened == []
    assert healed.list() == []


async def test_second_heal_of_the_same_marker_returns_the_existing_issue(tmp_path):
    tracker = MemoryIssueTracker()
    runner = FakeAgentRunner(
        runs={"healer": AgentRun(DONE, "Fix it")},
        writes={"healer": {"issue.md": "# Fix it\n\nbody"}},
    )

    for _ in range(2):
        failed = MemoryTaskQueue("failed")
        healed = MemoryTaskQueue("healed")
        failed.put(failed_task())  # same id both rounds → same marker
        healer = make_healer(
            failed=failed, healed=healed, runner=runner, tracker=tracker,
            events=MemoryEventSink(), scratch=tmp_path / "heal",
        )
        await healer.tick()

    assert len(tracker.opened) == 1  # marker dedup held across both


def test_heal_prompt_carries_the_failure_report():
    prompt = heal_prompt(failed_task(reason="no edge from review"), spec=HEALER_SPEC)

    assert "tsk_boom" in prompt
    assert "no edge from review" in prompt
    assert "Do the thing" in prompt
    assert "issue.md" in prompt
    assert "done" in prompt and "request_changes" in prompt
