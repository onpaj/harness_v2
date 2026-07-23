"""PrWatcher — the core that archives a landed task once its PR resolves."""

from dataclasses import replace

from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryForge, MemoryTaskQueue
from harness.models import ARCHIVED, Task
from harness.ports.forge import Forge, PullRequestState
from harness.pr_watcher import PrWatcher


def _landed_task(task_id="tsk_1", branch=None) -> Task:
    branch = branch or f"harness/{task_id}"
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        status="end",
        data={"pr": {"number": 1, "url": "https://forge.local/pr/1", "branch": branch}},
    )


def _build(forge=None):
    done = MemoryTaskQueue("done")
    archived = MemoryTaskQueue("archived")
    events = MemoryEventSink()
    watcher = PrWatcher(
        done=done, archived=archived, forge=forge or MemoryForge(), events=events, clock=FakeClock()
    )
    return watcher, done, archived, events


def test_task_without_pr_reference_is_skipped():
    watcher, done, archived, _ = _build()
    done.put(Task(id="tsk_1", workflow_template="default", created="t", status="end"))

    assert watcher.tick() is False
    assert done.list()[0].id == "tsk_1"
    assert archived.list() == []


def test_open_pr_leaves_task_untouched():
    forge = MemoryForge()
    forge.open_pull_request(_landed_task(), branch="harness/tsk_1", title="T", body="B")
    watcher, done, archived, _ = _build(forge)
    done.put(_landed_task())

    assert watcher.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []


def test_merged_pr_archives_the_task():
    forge = MemoryForge()
    forge.open_pull_request(_landed_task(), branch="harness/tsk_1", title="T", body="B")
    forge.close("harness/tsk_1", merged=True)
    watcher, done, archived, events = _build(forge)
    done.put(_landed_task())

    assert watcher.tick() is True
    assert done.list() == []
    archived_task = archived.list()[0]
    assert archived_task.id == "tsk_1"
    assert archived_task.status == ARCHIVED
    assert archived_task.lock_id is None

    entry = archived_task.history[-1]
    assert entry.actor == "pr_watcher"
    assert entry.to_step is None
    assert entry.reason == "pr merged"

    names = [name for name, _ in events.events]
    assert "archived" in names
    _, fields = next(item for item in events.events if item[0] == "archived")
    assert fields["resolution"] == "merged"
    assert fields["queue"] == "archived"
    assert fields["task_id"] == "tsk_1"


def test_closed_unmerged_pr_archives_the_task_with_closed_reason():
    forge = MemoryForge()
    forge.open_pull_request(_landed_task(), branch="harness/tsk_1", title="T", body="B")
    forge.close("harness/tsk_1", merged=False)
    watcher, done, archived, events = _build(forge)
    done.put(_landed_task())

    assert watcher.tick() is True
    entry = archived.list()[0].history[-1]
    assert entry.reason == "pr closed"
    _, fields = next(item for item in events.events if item[0] == "archived")
    assert fields["resolution"] == "closed"


class RaisingForge(Forge):
    def open_pull_request(self, task, *, branch, title, body):  # pragma: no cover - not called
        raise NotImplementedError

    def base_branch(self, task):  # pragma: no cover - not called
        raise NotImplementedError

    def pull_request_state(self, task):
        raise RuntimeError("GitHub is down")


def test_forge_error_is_isolated_per_task_and_does_not_stop_the_tick():
    forge = MemoryForge()
    forge.open_pull_request(
        _landed_task("tsk_ok"), branch="harness/tsk_ok", title="T", body="B"
    )
    forge.close("harness/tsk_ok", merged=True)

    class MixedForge(Forge):
        def open_pull_request(self, task, *, branch, title, body):  # pragma: no cover
            raise NotImplementedError

        def base_branch(self, task):  # pragma: no cover - not called
            raise NotImplementedError

        def pull_request_state(self, task):
            if task.id == "tsk_bad":
                raise RuntimeError("GitHub is down")
            return forge.pull_request_state(task)

    watcher, done, archived, events = _build(MixedForge())
    done.put(_landed_task("tsk_bad", branch="harness/tsk_bad"))
    done.put(_landed_task("tsk_ok", branch="harness/tsk_ok"))

    assert watcher.tick() is True

    assert [t.id for t in done.list()] == ["tsk_bad"]
    assert [t.id for t in archived.list()] == ["tsk_ok"]

    errors = [(name, fields) for name, fields in events.events if name == "pr_watch_error"]
    assert len(errors) == 1
    assert errors[0][1]["task_id"] == "tsk_bad"
    assert "GitHub is down" in errors[0][1]["error"]


def test_empty_done_queue_is_a_cheap_noop():
    watcher, done, archived, _ = _build()

    assert watcher.tick() is False
    assert done.list() == []
    assert archived.list() == []


class RaceQueue(MemoryTaskQueue):
    """A queue that lists a task but always loses the claim race — simulating
    another actor (a concurrent tick) claiming it first, in between list() and
    claim() within the same tick()."""

    def claim(self, task, lock_id):
        return None


def test_lost_claim_race_does_not_count_as_archived():
    forge = MemoryForge()
    forge.open_pull_request(_landed_task(), branch="harness/tsk_1", title="T", body="B")
    forge.close("harness/tsk_1", merged=True)
    done = RaceQueue("done")
    archived = MemoryTaskQueue("archived")
    watcher = PrWatcher(
        done=done, archived=archived, forge=forge, events=MemoryEventSink(), clock=FakeClock()
    )
    done.put(_landed_task())

    assert watcher.tick() is False
    assert archived.list() == []
