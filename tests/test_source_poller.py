"""SourcePoller — the core that fills the inbox from the source."""

from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue, MemoryTaskSource
from harness.models import Task
from harness.ports.source import TaskSource
from harness.source_poller import SourcePoller


class RaisingSource(TaskSource):
    kind = "boom"

    def poll(self):
        raise RuntimeError("GitHub is down")

    def report_progress(self, task, progress):  # pragma: no cover - not called
        pass

    def finish(self, task, result):  # pragma: no cover - not called
        pass


class ScriptedSource(TaskSource):
    """Returns preset batches of tasks, one per `poll()`. Lets a test replay the
    same source identity twice (a restart, label drift or read-after-write lag),
    which `MemoryTaskSource` can't — it clears its pending set on poll."""

    kind = "github"

    def __init__(self, batches):
        self._batches = list(batches)

    def poll(self):
        return self._batches.pop(0) if self._batches else []

    def report_progress(self, task, progress):  # pragma: no cover - not called
        pass

    def finish(self, task, result):  # pragma: no cover - not called
        pass


def _issue_task(task_id, issue, repo="o/r"):
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        dedup_key=f"github:{repo}:{issue}",
        data={"source": {"kind": "github", "repo": repo, "issue": issue}},
    )


def test_tick_moves_submitted_task_into_inbox_and_emits_ingested():
    source = MemoryTaskSource(clock=FakeClock())
    source.submit("Fix bug")
    inbox = MemoryTaskQueue("tasks")
    events = MemoryEventSink()
    poller = SourcePoller(source=source, inbox=inbox, events=events)

    acted = poller.tick()

    assert acted is True
    tasks = inbox.list()
    assert len(tasks) == 1
    assert tasks[0].data["title"] == "Fix bug"

    ingested = [(name, fields) for name, fields in events.events if name == "ingested"]
    assert len(ingested) == 1
    _, fields = ingested[0]
    assert fields["queue"] == "todo"
    assert fields["task"]["id"] == tasks[0].id


def test_empty_poll_returns_false():
    source = MemoryTaskSource(clock=FakeClock())
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    assert poller.tick() is False
    assert inbox.list() == []


def test_poll_that_raises_returns_false_and_emits_source_error():
    inbox = MemoryTaskQueue("tasks")
    events = MemoryEventSink()
    poller = SourcePoller(source=RaisingSource(), inbox=inbox, events=events)

    assert poller.tick() is False
    errors = [(name, fields) for name, fields in events.events if name == "source_error"]
    assert len(errors) == 1
    _, fields = errors[0]
    assert fields["source"] == "boom"
    assert "GitHub is down" in fields["error"]


def test_same_source_identity_polled_twice_is_ingested_once():
    # The same issue reappears in a later poll (label drift / read-after-write
    # lag). A fresh task id each time — dedup must be by source identity, not id.
    source = ScriptedSource(
        [[_issue_task("tsk_a", issue=1)], [_issue_task("tsk_b", issue=1)]]
    )
    inbox = MemoryTaskQueue("tasks")
    events = MemoryEventSink()
    poller = SourcePoller(source=source, inbox=inbox, events=events)

    first = poller.tick()
    second = poller.tick()

    assert first is True
    assert second is False  # nothing new ingested → the loop sleeps
    assert [task.id for task in inbox.list()] == ["tsk_a"]
    ingested = [name for name, _ in events.events if name == "ingested"]
    assert len(ingested) == 1
    dupes = [f for name, f in events.events if name == "duplicate_ignored"]
    assert len(dupes) == 1 and dupes[0]["task_id"] == "tsk_b"


def test_seed_prevents_reingestion_after_restart():
    # A task for issue #1 is already on disk (survived a restart). Seeding the
    # poller with it must stop the source re-ingesting the same issue.
    source = ScriptedSource([[_issue_task("tsk_new", issue=1)]])
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    poller.seed([_issue_task("tsk_old", issue=1)])

    assert poller.tick() is False
    assert inbox.list() == []


def test_different_issues_are_all_ingested():
    source = ScriptedSource(
        [[_issue_task("tsk_a", issue=1), _issue_task("tsk_b", issue=2)]]
    )
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    assert poller.tick() is True
    assert sorted(task.id for task in inbox.list()) == ["tsk_a", "tsk_b"]


def test_same_issue_number_different_repos_are_distinct():
    # Two repos can share an issue number; the repo is part of the identity.
    source = ScriptedSource(
        [[_issue_task("tsk_a", issue=1, repo="o/a"),
          _issue_task("tsk_b", issue=1, repo="o/b")]]
    )
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    assert poller.tick() is True
    assert sorted(task.id for task in inbox.list()) == ["tsk_a", "tsk_b"]


def test_sourceless_tasks_are_never_deduplicated():
    # `harness submit` tasks carry no source: each is a fresh unit of work, even
    # if two happen to look identical.
    bare = Task(id="tsk_1", workflow_template="default", created="t", data={})
    twin = Task(id="tsk_2", workflow_template="default", created="t", data={})
    source = ScriptedSource([[bare, twin]])
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    poller.seed([bare])  # seeding a sourceless task registers nothing
    assert poller.tick() is True
    assert sorted(task.id for task in inbox.list()) == ["tsk_1", "tsk_2"]
