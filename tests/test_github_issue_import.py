"""GithubIssueImportService — the IssueImport driver behind the Ahanas
board's manual "Add issue" button (no network)."""

from __future__ import annotations

from pathlib import Path

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_issue_import import GithubIssueImportService
from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryRepositoryRegistry,
    MemoryTaskQueue,
)


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "harness_v2": Path("/repos/harness_v2")}
    )
    slugs = {
        Path("/repos/heblo"): "onpaj/Anela.Heblo",
        Path("/repos/harness_v2"): "onpaj/harness_v2",
    }
    return registry, slugs


def build(client=None, registry=None, slug_of=None, **kwargs):
    client = client or FakeGithubClient(
        [Issue(42, "Fix bug", "the body", "https://gh/i/42", ())]
    )
    if registry is None:
        registry, slugs = _registry_and_slugs()
        slug_of = slugs.get
    inbox = MemoryTaskQueue("tasks")
    step_queues = {"development": MemoryTaskQueue("development")}
    done = MemoryTaskQueue("done")
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    archived = MemoryTaskQueue("archived")
    events = MemoryEventSink()
    clock = FakeClock()
    service = GithubIssueImportService(
        client=client,
        registry=registry,
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        healed=healed,
        archived=archived,
        events=events,
        clock=clock,
        worktree_root="/wt",
        slug_of=slug_of or (lambda path: None),
        **kwargs,
    )
    return service, {
        "inbox": inbox,
        "step_queues": step_queues,
        "done": done,
        "failed": failed,
        "healed": healed,
        "archived": archived,
        "events": events,
        "client": client,
    }


# --- happy path --------------------------------------------------------------


def test_add_creates_a_task_with_no_label_required():
    service, env = build()

    result = service.add("onpaj/harness_v2#42")

    assert result.ok is True
    assert result.already_queued is False
    assert result.task_id is not None

    [task] = env["inbox"].list()
    assert task.id == result.task_id
    assert task.data["source"] == {
        "kind": "github",
        "repo": "onpaj/harness_v2",
        "issue": 42,
        "url": "https://gh/i/42",
    }
    assert task.data["title"] == "Fix bug"
    assert task.dedup_key == "github:onpaj/harness_v2:42"
    assert task.repository == "harness_v2"
    assert task.worktree == f"/wt/{task.id}"


def test_add_accepts_a_full_issue_url():
    service, env = build()

    result = service.add("https://github.com/onpaj/harness_v2/issues/42")

    assert result.ok is True
    [task] = env["inbox"].list()
    assert task.data["source"]["repo"] == "onpaj/harness_v2"
    assert task.data["source"]["issue"] == 42


def test_add_emits_the_ingested_event_in_the_todo_column():
    service, env = build()

    result = service.add("onpaj/harness_v2#42")

    ingested = [(n, f) for n, f in env["events"].events if n == "ingested"]
    assert len(ingested) == 1
    _, fields = ingested[0]
    assert fields["task_id"] == result.task_id
    assert fields["queue"] == "todo"
    assert fields["task"]["id"] == result.task_id


def test_add_claims_the_issue_with_the_claimed_label():
    service, env = build()

    service.add("onpaj/harness_v2#42")

    issue = env["client"].get_issue("onpaj/harness_v2", 42)
    assert "harness:queued" in issue.labels


def test_add_uses_the_workflow_and_step_target():
    service, _ = build(workflow="custom", step=None)

    result = service.add("onpaj/harness_v2#42")

    assert result.ok is True


def test_add_workflow_less_target_uses_step():
    service, env = build(workflow=None, step="development")

    service.add("onpaj/harness_v2#42")

    [task] = env["inbox"].list()
    assert task.workflow_template is None
    assert task.step == "development"


# --- error paths (never raise, ok=False + a clear message) -------------------


def test_add_malformed_ref_is_a_clean_error():
    service, env = build()

    result = service.add("not-a-ref")

    assert result.ok is False
    assert "not a valid" in result.error
    assert env["inbox"].list() == []


def test_add_ref_missing_issue_number_is_a_clean_error():
    service, _ = build()

    result = service.add("onpaj/harness_v2")

    assert result.ok is False


def test_add_unregistered_repo_is_a_clean_error():
    service, env = build()

    result = service.add("onpaj/not-a-repo#9")

    assert result.ok is False
    assert "not-a-repo" in result.error
    assert env["inbox"].list() == []


def test_add_unknown_issue_is_a_clean_error():
    service, env = build()

    result = service.add("onpaj/harness_v2#999")

    assert result.ok is False
    assert "999" in result.error
    assert env["inbox"].list() == []


def test_add_client_error_is_a_clean_error_not_a_crash():
    class BoomClient(FakeGithubClient):
        def get_issue(self, repo, number):
            raise RuntimeError("network is down")

    service, env = build(client=BoomClient())

    result = service.add("onpaj/harness_v2#42")

    assert result.ok is False
    assert "network is down" in result.error
    assert env["inbox"].list() == []


def test_add_label_failure_does_not_prevent_the_task_from_being_queued():
    class NoLabelClient(FakeGithubClient):
        def add_label(self, repo, number, label):
            raise RuntimeError("no permission")

    service, env = build(client=NoLabelClient([Issue(42, "t", "b", "u", ())]))

    result = service.add("onpaj/harness_v2#42")

    assert result.ok is True
    assert len(env["inbox"].list()) == 1


# --- idempotency ---------------------------------------------------------


def test_add_already_queued_task_reports_success_without_duplicating():
    service, env = build()
    first = service.add("onpaj/harness_v2#42")

    second = service.add("onpaj/harness_v2#42")

    assert second.ok is True
    assert second.already_queued is True
    assert second.task_id == first.task_id
    assert len(env["inbox"].list()) == 1


def test_add_finds_a_duplicate_already_in_a_step_queue():
    service, env = build()
    result = service.add("onpaj/harness_v2#42")
    [task] = env["inbox"].list()
    env["inbox"].claim(task, "lck_1")
    env["step_queues"]["development"].put(task)

    again = service.add("onpaj/harness_v2#42")

    assert again.ok is True
    assert again.already_queued is True
    assert again.task_id == result.task_id


def test_add_finds_a_duplicate_already_healed():
    service, env = build()
    result = service.add("onpaj/harness_v2#42")
    [task] = env["inbox"].list()
    env["inbox"].claim(task, "lck_1")
    env["healed"].put(task)

    again = service.add("onpaj/harness_v2#42")

    assert again.ok is True
    assert again.already_queued is True
    assert again.task_id == result.task_id


def test_add_finds_a_duplicate_already_archived():
    service, env = build()
    result = service.add("onpaj/harness_v2#42")
    [task] = env["inbox"].list()
    env["inbox"].claim(task, "lck_1")
    env["archived"].put(task)

    again = service.add("onpaj/harness_v2#42")

    assert again.ok is True
    assert again.already_queued is True
    assert again.task_id == result.task_id


def test_add_same_batch_duplicates_the_second_call_sees_the_first():
    """Sequential per-ref processing (the route's contract) means the second
    call in the same batch already sees the first's task in the inbox."""
    service, env = build()

    first = service.add("onpaj/harness_v2#42")
    second = service.add("onpaj/harness_v2#42")

    assert first.ok is True and first.already_queued is False
    assert second.ok is True and second.already_queued is True
    assert second.task_id == first.task_id
    assert len(env["inbox"].list()) == 1
