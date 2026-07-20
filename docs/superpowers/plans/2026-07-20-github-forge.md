# GitHub Forge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `land` step push the task branch and open a real GitHub pull request instead of appending a record to `prs.json`.

**Architecture:** `WorkspaceHandle` gains `push()` so all git stays in the workspace driver. A new `GithubForge` driver implements the existing `Forge` port over the existing `GithubClient`, resolving each task's `owner/repo` slug from its worktree's git origin. `FakeForge` survives behind `--forge fake` for tests and offline runs.

**Tech Stack:** Python 3.11, stdlib only (`urllib.request`, `json`, `subprocess`), pytest.

## Global Constraints

- **Project language is English** — code, comments, docstrings, string literals, tests, docs and commit messages. No exceptions.
- **The runtime has no production dependencies.** The GitHub calls run on stdlib `urllib.request`. Do not add `requests`/`httpx`/`PyGithub`.
- **Python is 3.11**, at `.venv/bin/python`. Run tests as `.venv/bin/pytest`.
- **Invariant #1:** `dispatcher.py` and `consumer.py` must not import from `drivers/`. Wiring lives only in `app.py` / `cli.py`. Guarded by `tests/test_architecture.py`.
- **Invariant #11:** `Workspace`/`Forge`/`ArtifactStore` are touched only by behaviors and wiring.
- **Invariant #3:** a behavior signals failure by raising. There is no `Outcome.FAILED` — the consumer catches the exception and writes the task into `failed/`.
- **Commit straight into the current branch.** Per `CLAUDE.md` this repo does not use feature branches for its own work.
- Do not modify `tests/test_smoke*.py` beyond what Task 5 specifies; their real-FS/real-git coverage is deliberate.

## File Structure

| File | Responsibility |
|---|---|
| `src/harness/ports/workspace.py` | *Modify* — add `WorkspaceHandle.push()` |
| `src/harness/drivers/git_workspace.py` | *Modify* — `push()` via `git push --force-with-lease` |
| `src/harness/drivers/memory.py` | *Modify* — `MemoryWorkspaceHandle.push()` records the call |
| `src/harness/drivers/github_client.py` | *Modify* — `PullRequestRef` + three PR verbs on ABC, fake and http |
| `src/harness/drivers/github_forge.py` | *Create* — `GithubForge`, `ForgeError` |
| `src/harness/behaviors/landing.py` | *Modify* — push before opening the PR |
| `src/harness/cli.py` | *Modify* — `--forge {github,fake}`, default `github` |
| `CLAUDE.md` | *Modify* — module map, gotchas |
| `tests/test_github_forge.py` | *Create* — forge unit tests |

---

### Task 1: `push()` on the workspace port

**Files:**
- Modify: `src/harness/ports/workspace.py`
- Modify: `src/harness/drivers/git_workspace.py`
- Modify: `src/harness/drivers/memory.py`
- Test: `tests/test_git_workspace.py`, `tests/test_workspace_memory.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `WorkspaceHandle.push() -> None` on both drivers. `MemoryWorkspaceHandle.pushes: list[str]` records each push's branch — Task 4's tests read it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_git_workspace.py`:

```python
def _make_bare_remote(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "--bare"], path)


def test_push_publishes_the_task_branch_to_origin(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    remote = tmp_path / "remote.git"
    _make_bare_remote(remote)
    _git(["remote", "add", "origin", str(remote)], repo)
    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root=tmp_path / "wt")

    handle = workspace.attach(_make_task())
    handle.write("app.py", "print('hi')\n")
    handle.commit("work")
    handle.push()

    branches = _git(["branch", "--list"], remote)
    assert "harness/tsk_1" in branches


def test_push_twice_is_a_noop(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    remote = tmp_path / "remote.git"
    _make_bare_remote(remote)
    _git(["remote", "add", "origin", str(remote)], repo)
    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root=tmp_path / "wt")

    handle = workspace.attach(_make_task())
    handle.write("app.py", "print('hi')\n")
    handle.commit("work")
    handle.push()
    handle.push()  # must not raise

    assert "harness/tsk_1" in _git(["branch", "--list"], remote)


def test_push_without_a_remote_raises(tmp_path):
    workspace = _workspace(tmp_path)  # no origin configured

    handle = workspace.attach(_make_task())
    handle.write("app.py", "x\n")
    handle.commit("work")

    with pytest.raises(GitError):
        handle.push()
```

Add the two imports at the top of that file:

```python
import pytest

from harness.drivers.git_workspace import GitError, GitWorkspace
```

Append to `tests/test_workspace_memory.py`:

```python
def test_memory_handle_records_pushes():
    workspace = MemoryWorkspace()
    handle = workspace.attach(
        Task(
            id="tsk_1",
            workflow_template="default",
            created="2026-07-20T10:00:00Z",
            repository="app",
        )
    )

    handle.push()
    handle.push()

    assert handle.pushes == ["harness/tsk_1", "harness/tsk_1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_git_workspace.py tests/test_workspace_memory.py -q`
Expected: FAIL — `AttributeError: 'GitWorkspaceHandle' object has no attribute 'push'` (and the same for the memory handle).

- [ ] **Step 3: Add the abstract method to the port**

In `src/harness/ports/workspace.py`, add to `WorkspaceHandle` after `commit`:

```python
    @abstractmethod
    def push(self) -> None:
        """Publish the task branch to `origin`.

        Landing calls this before proposing a PR — a forge cannot open one for
        a ref the remote has never seen. Idempotent: pushing an already-current
        branch is a no-op.
        """
```

- [ ] **Step 4: Implement it on the git driver**

In `src/harness/drivers/git_workspace.py`, add to `GitWorkspaceHandle` after `commit`:

```python
    def push(self) -> None:
        # --force-with-lease, not --force: reset-on-reattach rewrites the task
        # branch on a re-run, so a plain push would be rejected as non-fast-
        # forward — but the lease still refuses to clobber a ref someone else
        # moved out from under us.
        _git(
            [
                "-C",
                str(self._path),
                "push",
                "--force-with-lease",
                "-u",
                "origin",
                self._branch,
            ]
        )
```

- [ ] **Step 5: Implement it on the memory driver**

In `src/harness/drivers/memory.py`, in `MemoryWorkspaceHandle.__init__` add:

```python
        self.pushes: list[str] = []
```

and after `commit`:

```python
    def push(self) -> None:
        self.pushes.append(self._branch)
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, no regressions. `test_architecture.py` must stay green.

- [ ] **Step 7: Commit**

```bash
git add src/harness/ports/workspace.py src/harness/drivers/git_workspace.py \
        src/harness/drivers/memory.py tests/test_git_workspace.py tests/test_workspace_memory.py
git commit -m "feat: WorkspaceHandle.push() publishes the task branch to origin"
```

---

### Task 2: Pull-request verbs on `GithubClient`

**Files:**
- Modify: `src/harness/drivers/github_client.py`
- Test: `tests/test_github_client.py`

**Interfaces:**
- Consumes: the existing `GithubClient` ABC, `FakeGithubClient`, `HttpGithubClient`, `_headers()`.
- Produces:
  - `PullRequestRef(number: int, url: str, head: str)` — frozen dataclass.
  - `GithubClient.default_branch(repo: str) -> str`
  - `GithubClient.find_pull_request(repo: str, *, head: str) -> PullRequestRef | None`
  - `GithubClient.create_pull_request(repo: str, *, head: str, base: str, title: str, body: str) -> PullRequestRef`
  - `FakeGithubClient(issues=None, *, default_branch="main")`, with `.pulls: list[PullRequestRef]` and `.created: list[dict]`.

`head` is always the cross-repo form `owner:branch` — that is what the GitHub API expects on both the query and the create payload.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github_client.py`:

```python
# --- pull requests, fake ---------------------------------------------------


def test_fake_find_pull_request_misses_then_hits():
    client = FakeGithubClient()

    assert client.find_pull_request("o/r", head="o:harness/tsk_1") is None

    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert client.find_pull_request("o/r", head="o:harness/tsk_1") == created


def test_fake_create_pull_request_records_the_call():
    client = FakeGithubClient()

    client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="trunk", title="T", body="B"
    )

    assert client.created == [
        {
            "repo": "o/r",
            "head": "o:harness/tsk_1",
            "base": "trunk",
            "title": "T",
            "body": "B",
        }
    ]


def test_fake_default_branch_is_configurable():
    assert FakeGithubClient().default_branch("o/r") == "main"
    assert FakeGithubClient(default_branch="trunk").default_branch("o/r") == "trunk"


# --- pull requests, http ---------------------------------------------------


def test_http_default_branch_reads_the_repo():
    opener = FakeOpener({"default_branch": "trunk"})
    client = HttpGithubClient("tok", opener=opener)

    assert client.default_branch("o/r") == "trunk"

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r"


def test_http_find_pull_request_queries_by_head():
    payload = [{"number": 7, "html_url": "https://github.com/o/r/pull/7"}]
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    found = client.find_pull_request("o/r", head="o:harness/tsk_1")

    assert found.number == 7
    assert found.url == "https://github.com/o/r/pull/7"

    req = opener.requests[0]
    assert req.full_url.startswith("https://api.github.com/repos/o/r/pulls")
    assert "head=o%3Aharness%2Ftsk_1" in req.full_url
    assert "state=open" in req.full_url


def test_http_find_pull_request_returns_none_when_empty():
    client = HttpGithubClient("tok", opener=FakeOpener([]))

    assert client.find_pull_request("o/r", head="o:harness/tsk_1") is None


def test_http_create_pull_request_posts_the_payload():
    opener = FakeOpener({"number": 12, "html_url": "https://github.com/o/r/pull/12"})
    client = HttpGithubClient("tok", opener=opener)

    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert created.number == 12
    assert created.url == "https://github.com/o/r/pull/12"

    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.github.com/repos/o/r/pulls"
    assert json.loads(req.data.decode("utf-8")) == {
        "head": "o:harness/tsk_1",
        "base": "main",
        "title": "T",
        "body": "B",
    }
```

Extend the import at the top of that file to:

```python
from harness.drivers.github_client import (
    FakeGithubClient,
    HttpGithubClient,
    Issue,
    PullRequestRef,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_github_client.py -q`
Expected: FAIL — `ImportError: cannot import name 'PullRequestRef'`.

- [ ] **Step 3: Add `PullRequestRef` and the ABC methods**

In `src/harness/drivers/github_client.py`, after the `Issue` dataclass:

```python
@dataclass(frozen=True)
class PullRequestRef:
    """A pull request as the API returns it. `head` is the `owner:branch` form."""

    number: int
    url: str
    head: str
```

Add to the `GithubClient` ABC, after `remove_label`:

```python
    @abstractmethod
    def default_branch(self, repo: str) -> str:
        """The repo's default branch — what a PR is opened against."""

    @abstractmethod
    def find_pull_request(self, repo: str, *, head: str) -> PullRequestRef | None:
        """The open PR for `head` (`owner:branch`), or None."""

    @abstractmethod
    def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        """Open a PR from `head` into `base`."""
```

- [ ] **Step 4: Implement them on `FakeGithubClient`**

Replace `FakeGithubClient.__init__` with:

```python
    def __init__(
        self, issues: list[Issue] | None = None, *, default_branch: str = "main"
    ) -> None:
        self._issues: dict[int, Issue] = {i.number: i for i in (issues or [])}
        self._default_branch = default_branch
        self.pulls: list[PullRequestRef] = []
        self.created: list[dict] = []
```

and append to the class:

```python
    def default_branch(self, repo: str) -> str:
        return self._default_branch

    def find_pull_request(self, repo: str, *, head: str) -> PullRequestRef | None:
        for pull in self.pulls:
            if pull.head == head:
                return pull
        return None

    def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        number = len(self.pulls) + 1
        pull = PullRequestRef(
            number=number,
            url=f"https://github.com/{repo}/pull/{number}",
            head=head,
        )
        self.pulls.append(pull)
        self.created.append(
            {"repo": repo, "head": head, "base": base, "title": title, "body": body}
        )
        return pull
```

- [ ] **Step 5: Implement them on `HttpGithubClient`**

Append to the class:

```python
    def default_branch(self, repo: str) -> str:
        url = f"{self._api}/repos/{repo}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            return json.loads(response.read())["default_branch"]

    def find_pull_request(self, repo: str, *, head: str) -> PullRequestRef | None:
        query = urllib.parse.urlencode({"state": "open", "head": head})
        url = f"{self._api}/repos/{repo}/pulls?{query}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            raw = json.loads(response.read())
        for item in raw:
            return PullRequestRef(
                number=item["number"], url=item.get("html_url", ""), head=head
            )
        return None

    def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        url = f"{self._api}/repos/{repo}/pulls"
        payload = json.dumps(
            {"head": head, "base": base, "title": title, "body": body}
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST",
        )
        with self._opener.open(request) as response:
            item = json.loads(response.read())
        return PullRequestRef(
            number=item["number"], url=item.get("html_url", ""), head=head
        )
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_github_client.py tests/test_github_source.py tests/test_multi_repo_source.py -q`
Expected: PASS. (The source tests share `FakeGithubClient` — they must not regress.)

- [ ] **Step 7: Commit**

```bash
git add src/harness/drivers/github_client.py tests/test_github_client.py
git commit -m "feat: pull-request verbs on GithubClient (default branch, find, create)"
```

---

### Task 3: The `GithubForge` driver

**Files:**
- Create: `src/harness/drivers/github_forge.py`
- Test: `tests/test_github_forge.py`

**Interfaces:**
- Consumes: `Forge`/`PullRequest` from `ports/forge.py`; `GithubClient`/`PullRequestRef` from Task 2; `github_slug` from `drivers/git_remote.py`.
- Produces: `GithubForge(client: GithubClient | None, *, slug_of=github_slug)` and `ForgeError(RuntimeError)`.

`client=None` models "no `GITHUB_TOKEN`" — the failure surfaces at `land`, on the task, not at process start.

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_forge.py`:

```python
"""GithubForge — the real forge driven by FakeGithubClient (no network)."""

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


def test_task_without_a_worktree_fails_loudly():
    forge, _ = build()
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
    )

    with pytest.raises(ForgeError, match="worktree"):
        forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")


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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_github_forge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.drivers.github_forge'`.

- [ ] **Step 3: Write the driver**

Create `src/harness/drivers/github_forge.py`:

```python
"""Forge against real GitHub — landing's outward half.

The slug is resolved **per task** from its worktree's git origin, the same way
`GithubTaskSource` resolves it per repo: the forge is constructed once but
serves every repo in `repos.json`, and `repos.json` holds no GitHub slug.

Every failure raises `ForgeError`. The consumer turns that into `failed/`, so a
task that reports "opened PR" always means a pull request that exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.models import Task
from harness.ports.forge import Forge, PullRequest


class ForgeError(RuntimeError):
    """The forge could not open a pull request."""


class GithubForge(Forge):
    """Opens PRs on github.com. `client=None` means no `GITHUB_TOKEN`."""

    def __init__(
        self,
        client: GithubClient | None,
        *,
        slug_of: Callable[[Path], str | None] = github_slug,
    ) -> None:
        self._client = client
        self._slug_of = slug_of
        self._base: dict[str, str] = {}

    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        client = self._client
        if client is None:
            raise ForgeError(
                "GITHUB_TOKEN is not set — cannot open a pull request. "
                "Export it, or run with --forge fake."
            )
        if not task.worktree:
            raise ForgeError(
                f"task {task.id} has no worktree — cannot resolve its GitHub repository"
            )
        slug = self._slug_of(Path(task.worktree))
        if slug is None:
            raise ForgeError(
                f"{task.repository} has no GitHub origin — cannot open a pull request"
            )

        head = f"{slug.split('/')[0]}:{branch}"
        try:
            existing = client.find_pull_request(slug, head=head)
            if existing is not None:
                return PullRequest(
                    number=existing.number,
                    url=existing.url,
                    branch=branch,
                    title=title,
                )
            created = client.create_pull_request(
                slug,
                head=head,
                base=self._default_branch(client, slug),
                title=title,
                body=self._body(task, slug, body),
            )
        except ForgeError:
            raise
        except Exception as error:  # noqa: BLE001 - any API failure is a forge failure
            raise ForgeError(
                f"GitHub refused to open a pull request for {slug}: {error}"
            ) from error

        return PullRequest(
            number=created.number, url=created.url, branch=branch, title=title
        )

    def _default_branch(self, client: GithubClient, slug: str) -> str:
        """The repo's default branch, fetched once per slug per process."""
        if slug not in self._base:
            self._base[slug] = client.default_branch(slug)
        return self._base[slug]

    @staticmethod
    def _body(task: Task, slug: str, body: str) -> str:
        """Append `Closes #n` when the task came from an issue on this repo."""
        source = task.data.get("source")
        if not isinstance(source, dict):
            return body
        if source.get("kind") != "github" or source.get("repo") != slug:
            return body
        number = source.get("issue")
        if not isinstance(number, int):
            return body
        return f"{body.rstrip()}\n\nCloses #{number}\n"
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_github_forge.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/harness/drivers/github_forge.py tests/test_github_forge.py
git commit -m "feat: GithubForge opens real pull requests, failing loudly"
```

---

### Task 4: Landing pushes before it proposes

**Files:**
- Modify: `src/harness/behaviors/landing.py:51`
- Test: `tests/test_landing_behavior.py`

**Interfaces:**
- Consumes: `WorkspaceHandle.push()` (Task 1), `MemoryWorkspaceHandle.pushes` (Task 1).
- Produces: no new API. `LandingBehavior.run` now calls `push()` before `open_pull_request`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_landing_behavior.py`:

```python
async def test_pushes_the_branch_before_opening_the_pull_request():
    behavior, workspace, _, forge = build()

    await behavior.run(make_task())

    handle = workspace.handles["tsk_1"]
    assert handle.pushes == ["harness/tsk_1"]
    assert len(forge.opened) == 1


async def test_no_pull_request_when_the_push_fails():
    behavior, workspace, _, forge = build()

    class Boom(RuntimeError):
        pass

    def explode():
        raise Boom("remote rejected")

    workspace.attach(make_task()).push = explode

    with pytest.raises(Boom):
        await behavior.run(make_task())

    assert forge.opened == []
```

Add to the top of that file:

```python
import pytest
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_landing_behavior.py -q`
Expected: FAIL — `assert [] == ['harness/tsk_1']`, and the second test fails because a PR is opened anyway.

- [ ] **Step 3: Add the push to landing**

In `src/harness/behaviors/landing.py`, immediately before the `pull = self._forge.open_pull_request(` call:

```python
        # The forge cannot open a PR for a ref the remote has never seen. A
        # failure here raises, and the consumer writes the task into `failed/`.
        handle.push()

```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_landing_behavior.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. The phase 2/3 e2e and smoke tests use `MemoryWorkspace`/`GitWorkspace`, both of which now have `push()`.

- [ ] **Step 6: Commit**

```bash
git add src/harness/behaviors/landing.py tests/test_landing_behavior.py
git commit -m "feat: land pushes the task branch before opening the PR"
```

---

### Task 5: Wire it up — `--forge`, docs

**Files:**
- Modify: `src/harness/cli.py` (imports, `_run`, the `run` subparser near line 461)
- Modify: `CLAUDE.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `GithubForge` (Task 3), `HttpGithubClient` (Task 2).
- Produces: `harness run --forge {github,fake}`, default `github`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_run_rejects_an_unknown_forge():
    import pytest

    with pytest.raises(SystemExit):
        main(["run", "--forge", "bogus"])


def test_build_forge_returns_fake_when_asked(tmp_path):
    from harness.cli import _build_forge
    from harness.drivers.fake_forge import FakeForge

    forge = _build_forge("fake", tmp_path)

    assert isinstance(forge, FakeForge)


def test_build_forge_without_a_token_still_returns_a_github_forge(tmp_path, monkeypatch):
    from harness.cli import _build_forge
    from harness.drivers.github_forge import GithubForge

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    forge = _build_forge("github", tmp_path)

    assert isinstance(forge, GithubForge)
    assert forge._client is None  # fails at land, not at startup


def test_build_forge_with_a_token_wires_the_http_client(tmp_path, monkeypatch):
    from harness.cli import _build_forge
    from harness.drivers.github_client import HttpGithubClient

    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    forge = _build_forge("github", tmp_path)

    assert isinstance(forge._client, HttpGithubClient)
```

Note: `cli.py` has no parser factory — the parser is built inline in `main()`, so the flag itself is exercised through `main()` and the wiring is tested directly on `_build_forge`. Do not refactor `main()` to extract a parser; that is out of scope.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'forge'` and `ImportError: cannot import name '_build_forge'`.

- [ ] **Step 3: Add the CLI flag**

In `src/harness/cli.py`, alongside the other `run.add_argument` calls (near line 481):

```python
    run.add_argument(
        "--forge",
        choices=("github", "fake"),
        default="github",
        help="where landing proposes the change (default: real GitHub)",
    )
```

- [ ] **Step 4: Add the forge factory and use it**

Add the imports at the top of `src/harness/cli.py`:

```python
from harness.drivers.github_forge import GithubForge
```

(`HttpGithubClient` and `FakeForge` are already imported.)

Add above `_run`:

```python
def _build_forge(kind: str, root: Path):
    """The forge for a real run. `fake` writes into `<root>/forge/prs.json`.

    `github` without a `GITHUB_TOKEN` yields a forge that fails at `land` rather
    than one that refuses to start: the harness stays usable for `harness
    submit` and the operator sees exactly which task needs the token.
    """
    if kind == "fake":
        return FakeForge(root / "forge")
    token = os.environ.get("GITHUB_TOKEN")
    return GithubForge(HttpGithubClient(token) if token else None)
```

In `_run`, replace `forge = FakeForge(root / "forge")` with:

```python
    forge = _build_forge(args.forge, root)
```

and update the stale comment four lines above it — replace `fake forge (PR into prs.json). The GitHub driver is a clean follow-up — a swap of the forge driver.` with:

```python
    # artifacts versioned in the worktree, and a real GitHub forge (`--forge
    # fake` swaps in prs.json for offline runs).
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Update `CLAUDE.md`**

Three edits:

1. In the **Module map** drivers row, add `github_forge` to the list.
2. Under the module bullets, after the `drivers/github_source.py` line, add:

```markdown
- `drivers/github_forge.py` — `GithubForge`: opens the real PR; slug per task from the worktree's origin, base = the repo's default branch, `Closes #n` for an issue-born task
```

3. In **Gotchas**, replace the `**Landing is idempotent.**` bullet with:

```markdown
- **Landing is idempotent.** For an existing PR on a branch the forge returns the
  existing one (`GithubForge` matches on `head=owner:branch`). So a re-run after a
  crash won't open a second PR. The push is `--force-with-lease` for the same
  reason — reset-on-reattach rewrites the branch.
- **A failed PR fails the task.** `GithubForge` raises `ForgeError` on a missing
  `GITHUB_TOKEN`, a non-GitHub origin or an API error, and the task lands in
  `failed/`. That is deliberate: before this, `land` reported success while only
  writing to `prs.json`. Offline or in tests, use `--forge fake`.
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, everything green (the pre-existing count was 408 passed / 1 skipped, plus this plan's new tests).

- [ ] **Step 8: Commit**

```bash
git add src/harness/cli.py tests/test_cli.py CLAUDE.md
git commit -m "feat: harness run defaults to the real GitHub forge (--forge fake to opt out)"
```

---

## Verification

After Task 5, confirm the wiring end to end without touching real GitHub:

```bash
.venv/bin/pytest -q
.venv/bin/pytest tests/test_architecture.py -q
.venv/bin/harness run --help | grep -A2 forge
```

Expected: suite green; the architecture guards green (no new `drivers/` import in `dispatcher.py`/`consumer.py`); `--forge` documented with `github` as the default.

A live check is out of band and belongs to the operator: export `GITHUB_TOKEN`, restart the stranded task, and confirm a PR appears on `onpaj/harness_v2`.
