"""GithubForge — the real forge driven by FakeGithubClient (no network)."""

import json
from pathlib import Path

import pytest

from harness.drivers.github_client import FakeGithubClient
from harness.drivers.github_forge import ForgeError, GithubForge
from harness.models import Task


def make_task(**data) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
        worktree="/work/tsk_1",
        data=data,
    )


def build(client=None, slug="onpaj/harness_v2"):
    client = client if client is not None else FakeGithubClient()
    forge = GithubForge(client, slug_of=lambda path: slug)
    return forge, client


def test_opens_a_pull_request_against_the_default_branch():
    forge, client = build(FakeGithubClient(default_branch="trunk"))

    pull = forge.open_pull_request(
        make_task(), branch="harness/tsk_1", title="T", body="B"
    )

    assert pull.number == 1
    assert pull.branch == "harness/tsk_1"
    assert pull.title == "T"
    assert pull.url == "https://github.com/onpaj/harness_v2/pull/1"
    assert client.created[0]["head"] == "onpaj:harness/tsk_1"
    assert client.created[0]["base"] == "trunk"


def test_second_call_returns_the_existing_pull_request():
    forge, client = build()
    task = make_task()

    first = forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")
    second = forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")

    assert first.number == second.number
    assert len(client.created) == 1


def test_default_branch_is_fetched_once_per_slug():
    class CountingClient(FakeGithubClient):
        def __init__(self):
            super().__init__()
            self.branch_calls = 0

        def default_branch(self, repo):
            self.branch_calls += 1
            return super().default_branch(repo)

    forge, client = build(CountingClient())

    forge.open_pull_request(make_task(), branch="harness/a", title="T", body="B")
    forge.open_pull_request(make_task(), branch="harness/b", title="T", body="B")

    assert client.branch_calls == 1


def test_appends_closes_for_a_matching_github_source():
    forge, client = build()
    task = make_task(
        source={"kind": "github", "repo": "onpaj/harness_v2", "issue": 14}
    )

    forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")

    assert client.created[0]["body"].endswith("Closes #14\n")


def test_no_closes_for_a_task_without_a_github_source():
    forge, client = build()

    forge.open_pull_request(make_task(), branch="harness/tsk_1", title="T", body="B")

    assert "Closes" not in client.created[0]["body"]


def test_no_closes_when_the_source_repo_differs():
    forge, client = build()
    task = make_task(source={"kind": "github", "repo": "other/repo", "issue": 3})

    forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")

    assert "Closes" not in client.created[0]["body"]


def test_missing_token_fails_loudly():
    forge = GithubForge(None, slug_of=lambda path: "onpaj/harness_v2")

    with pytest.raises(ForgeError, match="GITHUB_TOKEN"):
        forge.open_pull_request(
            make_task(), branch="harness/tsk_1", title="T", body="B"
        )


def test_non_github_origin_fails_loudly():
    forge, _ = build(slug=None)

    with pytest.raises(ForgeError, match="no GitHub origin"):
        forge.open_pull_request(
            make_task(), branch="harness/tsk_1", title="T", body="B"
        )


def test_task_without_a_worktree_resolves_through_the_registry():
    """`harness submit` leaves `worktree` unset — the repo name plus the registry
    is what locates the clone (invariant 15). This reached `land` and failed in a
    live run before the registry was wired in."""
    from harness.drivers.memory import MemoryRepositoryRegistry

    client = FakeGithubClient()
    forge = GithubForge(
        client,
        registry=MemoryRepositoryRegistry({"app": Path("/repos/app")}),
        slug_of=lambda path: "onpaj/harness_v2" if path == Path("/repos/app") else None,
    )
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
    )

    pull = forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")

    assert pull.number == 1
    assert client.created[0]["head"] == "onpaj:harness/tsk_1"


def test_unlocatable_repository_fails_loudly():
    forge, _ = build()
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
    )

    with pytest.raises(ForgeError, match="cannot locate repository"):
        forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")


def test_registry_wins_over_the_task_worktree():
    """Both present: the registry is authoritative — the worktree is a fallback."""
    from harness.drivers.memory import MemoryRepositoryRegistry

    seen = []

    def slug_of(path):
        seen.append(path)
        return "onpaj/harness_v2"

    forge = GithubForge(
        FakeGithubClient(),
        registry=MemoryRepositoryRegistry({"app": Path("/repos/app")}),
        slug_of=slug_of,
    )

    forge.open_pull_request(make_task(), branch="harness/tsk_1", title="T", body="B")

    assert seen == [Path("/repos/app")]


def test_api_error_becomes_a_forge_error():
    class BrokenClient(FakeGithubClient):
        def create_pull_request(self, repo, *, head, base, title, body):
            raise RuntimeError("422 Unprocessable Entity")

    forge, _ = build(BrokenClient())

    with pytest.raises(ForgeError, match="422"):
        forge.open_pull_request(
            make_task(), branch="harness/tsk_1", title="T", body="B"
        )


def test_slug_is_resolved_from_the_task_worktree():
    seen = []

    def slug_of(path):
        seen.append(path)
        return "onpaj/harness_v2"

    forge = GithubForge(FakeGithubClient(), slug_of=slug_of)
    forge.open_pull_request(make_task(), branch="harness/tsk_1", title="T", body="B")

    assert seen == [Path("/work/tsk_1")]


def test_api_error_surfaces_githubs_own_explanation():
    """urllib's HTTPError str() is just "HTTP Error 422: Unprocessable Entity".
    GitHub puts the actual reason in the body — a live run hit exactly this and
    the message told us nothing."""
    import io
    import urllib.error

    class RefusingClient(FakeGithubClient):
        def create_pull_request(self, repo, *, head, base, title, body):
            raise urllib.error.HTTPError(
                "https://api.github.com/repos/o/r/pulls",
                422,
                "Unprocessable Entity",
                {},
                io.BytesIO(
                    json.dumps(
                        {
                            "message": "Validation Failed",
                            "errors": [
                                {"message": "No commits between main and harness/tsk_1"}
                            ],
                        }
                    ).encode("utf-8")
                ),
            )

    forge, _ = build(RefusingClient())

    with pytest.raises(ForgeError) as error:
        forge.open_pull_request(make_task(), branch="harness/tsk_1", title="T", body="B")

    message = str(error.value)
    assert "No commits between main and harness/tsk_1" in message
    assert "Validation Failed" in message


def test_api_error_without_a_body_still_reports_the_status():
    class RefusingClient(FakeGithubClient):
        def create_pull_request(self, repo, *, head, base, title, body):
            raise RuntimeError("connection reset")

    forge, _ = build(RefusingClient())

    with pytest.raises(ForgeError, match="connection reset"):
        forge.open_pull_request(make_task(), branch="harness/tsk_1", title="T", body="B")
