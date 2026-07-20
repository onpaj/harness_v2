"""Two GitHub sources in one harness: ingestion and labels stay per-repo.

The reflector calls finish() on ALL sources; each source's repo-scoped _mine()
must keep it from labelling another repo's issue (which may share a number)."""

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_source import GithubTaskSource
from harness.drivers.memory import FakeClock
from harness.drivers.source_reflector import SourceReflectorSink


def _source(client, repo, repository):
    return GithubTaskSource(
        client=client,
        clock=FakeClock(),
        repo=repo,
        repository=repository,
        worktree_root="/wt",
    )


def test_reflector_does_not_cross_repos():
    client_a = FakeGithubClient([Issue(1, "A", "", "ua", ("harness:todo",))])
    client_b = FakeGithubClient([Issue(1, "B", "", "ub", ("harness:todo",))])
    source_a = _source(client_a, "o/a", "a")
    source_b = _source(client_b, "o/b", "b")

    [task_a] = source_a.poll()
    source_b.poll()  # claims b#1 → harness:queued

    sink = SourceReflectorSink([source_a, source_b])
    sink.emit("finished", task=task_a.to_dict())

    assert set(client_a._issues[1].labels) == {"harness:pr-open"}  # A finished
    assert set(client_b._issues[1].labels) == {"harness:queued"}  # B untouched
