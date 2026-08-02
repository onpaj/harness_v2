"""GitHub client behind an ABC. Fake for tests, stdlib http for the real run.

No new production dependency — `HttpGithubClient` runs on `urllib.request` +
`json`. If you're tempted to reach for `requests`/`httpx`, don't.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any


class PullRequestNotMergeable(Exception):
    """The server refused to merge the PR as-is: its head moved past the sha
    the caller pinned, a required check is red, or branch protection said no.

    A state of the world, not a fault — `GithubPullRequestMerger` translates it
    into the port's `MergeRefused`. It lives here rather than in `ports/` so
    `GithubClient` stays a self-contained driver with no port import.
    """


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class PullRequestRef:
    """A pull request as the API returns it. `head` is the `owner:branch` form."""

    number: int
    url: str
    head: str


@dataclass(frozen=True)
class PullRequestDetail:
    """A pull request fetched by number: whether it has merged, plus its
    open/closed state and head ref where available."""

    number: int
    url: str
    merged: bool  # true only when GitHub merged it
    head: str = ""
    state: str = "open"  # "open" | "closed", exactly as GitHub's API reports it


@dataclass(frozen=True)
class PullRequestInfo:
    """A pull request as `GithubUnhealthyPrsCheck` sees it — distinct from
    `PullRequestRef` (owned by the forge's find/create pair, which has no
    `mergeable_state`): this answers "is this PR ours, and does it need
    action", not "does a PR already exist for this branch"."""

    number: int
    url: str
    head_branch: str
    head_sha: str
    base_branch: str
    mergeable_state: str  # "behind" | "dirty" | "clean" | "blocked" | "unstable" | "unknown"
    # The fields below serve `GithubMergeableCheck`, which — unlike the
    # conflicts check — has to decide whether a PR is a *candidate for merging*,
    # not merely whether it needs a rebase. All defaulted, so every existing
    # construction site (the conflicts check's tests, the fakes) compiles
    # unchanged.
    title: str = ""
    body: str = ""
    labels: tuple[str, ...] = ()
    draft: bool = False
    # The repo the head branch actually lives in (`owner/name`), which is what
    # distinguishes a fork PR from a same-repo one — `head_branch` alone cannot:
    # a fork PR opened from the contributor's own `main` reads as `"main"`, the
    # base repo's own default branch. `""` means unknown, which GitHub reports
    # as a null `head.repo` once the fork has been deleted. Defaulted like the
    # four fields above, so every existing construction site compiles unchanged
    # — but a caller that acts on a PR must treat the default as "not mine".
    head_repo: str = ""


@dataclass(frozen=True)
class CheckRun:
    """One CI check on a commit, as the unhealthy-PRs check sees it.

    `conclusion` is GitHub's own vocabulary and is `""` while the run is still
    in progress. Only `failure` and `timed_out` are things an agent can fix —
    `cancelled` and `skipped` are states of the world, not defects.
    """

    id: int
    name: str
    conclusion: str
    url: str


FAILING_CONCLUSIONS = frozenset({"failure", "timed_out"})
"""The conclusions that make a check-run worth waking an agent for."""


class GithubClient(ABC):
    """The minimal GitHub API the connector needs."""

    @abstractmethod
    def list_issues(self, repo: str, *, label: str) -> list[Issue]:
        """Open issues with the given label (no PRs)."""

    @abstractmethod
    def get_issue_state(self, repo: str, number: int) -> str | None:
        """The issue's state — "open" or "closed" — or None when it is gone (404).

        Deliberately does not raise on a missing issue: a deleted issue is a
        legitimate "no longer open" answer for the reconciler, not a transient
        failure. Every other error path (network, auth, 5xx) still propagates.
        """

    @abstractmethod
    def get_issue(self, repo: str, number: int) -> Issue | None:
        """The issue itself, by number, regardless of any label — or None when
        it is gone (404). The point-lookup counterpart to `list_issues`'
        label scan: used by manual issue import, where an operator names a
        specific issue that may carry no label at all. Same 404→None idiom as
        `get_issue_state`; every other error path still propagates.
        """

    @abstractmethod
    def add_label(self, repo: str, number: int, label: str) -> None:
        """Add a label. Adding one that is already set is a no-op."""

    @abstractmethod
    def remove_label(self, repo: str, number: int, label: str) -> None:
        """Remove a label. A missing one is a no-op (idempotent)."""

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

    @abstractmethod
    def get_pull_request(self, repo: str, number: int) -> PullRequestDetail:
        """Fetch a PR by number: its open/closed state and whether it has merged."""

    @abstractmethod
    def list_pull_requests(
        self, repo: str, *, head_prefix: str | None = None
    ) -> list[PullRequestInfo]:
        """Open PRs, optionally restricted to a head-branch prefix."""

    @abstractmethod
    def update_branch(self, repo: str, number: int) -> None:
        """Merge base into head server-side (GitHub's "Update branch" button).

        Idempotent: a PR that is already up to date / not behind is a no-op,
        not a failure — safe to call every tick.
        """

    @abstractmethod
    def list_check_runs(self, repo: str, sha: str) -> list[CheckRun]:
        """Every check run GitHub has recorded against this commit."""

    @abstractmethod
    def check_run_log(self, repo: str, check_run_id: int) -> str:
        """The run's plain-text log, or "" when there is none to fetch.

        Only GitHub Actions check runs have a log at this endpoint (their
        check-run ids *are* job ids). A third-party check, or a log that has
        aged out, yields "" rather than raising — an absent log degrades the
        brief, it does not fail the tick.
        """

    @abstractmethod
    def merge_pull_request(
        self,
        repo: str,
        number: int,
        *,
        method: str,
        sha: str,
        title: str = "",
        body: str = "",
    ) -> str:
        """Merge PR `number`, but only while its head is exactly `sha`.

        Returns the merge commit's sha. `sha` is not an optimization — it is
        the guarantee that only reviewed code merges (see `ports/pr_merge.py`);
        a driver must pass it through to the API rather than dropping it.

        Raises `PullRequestNotMergeable` when the server refuses (the head
        moved, a check is red, branch protection said no) — a state of the
        world, not a fault. Every other error path propagates unchanged.
        """

    @abstractmethod
    def create_issue(
        self, repo: str, *, title: str, body: str, labels: tuple[str, ...]
    ) -> Issue:
        """Open a fresh issue on `repo`."""

    @abstractmethod
    def search_issue_by_marker(self, repo: str, marker: str, *, label: str) -> Issue | None:
        """The open issue carrying `label` whose body contains `marker`, or None.

        Keeps issue creation idempotent without the Search API — it scans the
        `label`-carrying open issues and matches the marker in the body.
        """


SELF_HEAL_LABEL = "harness:self-heal"
"""The label the seeded heal workflow's open-issue binding uses."""


class FakeGithubClient(GithubClient):
    """Issues in a dict. For unit/e2e and smoke — no network."""

    def __init__(
        self, issues: list[Issue] | None = None, *, default_branch: str = "main"
    ) -> None:
        self._issues: dict[int, Issue] = {i.number: i for i in (issues or [])}
        # Issue numbers explicitly closed via `close_issue`. An issue absent from
        # `_issues` reads as gone (404); one present but here reads as "closed".
        self._closed_issues: set[int] = set()
        self._default_branch = default_branch
        self.pulls: list[PullRequestRef] = []
        self.created: list[dict] = []
        # (state, merged) per PR number. A freshly created pull defaults to
        # ("open", False); close_pull_request flips it for tests that simulate
        # GitHub resolving the PR.
        self._pr_state: dict[int, tuple[str, bool]] = {}
        # `mark_merged` (the merge-reconciler's test helper) records merged PR
        # numbers here; `get_pull_request` unions it with `_pr_state`. The real
        # `merge_pull_request` verb writes here too, so a PR merged through the
        # port reads back as merged exactly as one merged out-of-band does.
        self.merged: set[int] = set()
        # Every merge performed through `merge_pull_request`, in order — what
        # the automerge tests assert on (method, pinned sha, commit message).
        self.merges: list[dict[str, Any]] = []
        # Separate store from `pulls`: `PullRequestInfo` answers "is this PR
        # ours, does it need action" (mergeable_state), a different question
        # from `pulls`' "does a PR already exist for this branch".
        self._pull_requests: dict[int, PullRequestInfo] = {}
        self.updated_branches: list[tuple[str, int]] = []
        self._check_runs: dict[str, list[CheckRun]] = {}
        self._check_run_logs: dict[int, str] = {}

    def add_issue(self, issue: Issue) -> None:
        self._issues[issue.number] = issue

    def close_issue(self, number: int, *, deleted: bool = False) -> None:
        """Test helper: simulate GitHub closing (or deleting) the issue."""
        if deleted:
            self._issues.pop(number, None)
            self._closed_issues.discard(number)
        else:
            self._closed_issues.add(number)

    def get_issue_state(self, repo: str, number: int) -> str | None:
        if number not in self._issues:
            return None
        return "closed" if number in self._closed_issues else "open"

    def get_issue(self, repo: str, number: int) -> Issue | None:
        return self._issues.get(number)

    def list_issues(self, repo: str, *, label: str) -> list[Issue]:
        # Mirror GitHub's `state=open` filter: a closed issue never appears here.
        return [
            i
            for i in self._issues.values()
            if label in i.labels and i.number not in self._closed_issues
        ]

    def add_label(self, repo: str, number: int, label: str) -> None:
        issue = self._issues.get(number)
        if issue is not None and label not in issue.labels:
            self._issues[number] = replace(issue, labels=issue.labels + (label,))
        pull = self._pull_requests.get(number)
        if pull is not None and label not in pull.labels:
            self._pull_requests[number] = replace(
                pull, labels=pull.labels + (label,)
            )

    def remove_label(self, repo: str, number: int, label: str) -> None:
        issue = self._issues.get(number)
        if issue is not None and label in issue.labels:
            self._issues[number] = replace(
                issue, labels=tuple(l for l in issue.labels if l != label)
            )
        pull = self._pull_requests.get(number)
        if pull is not None and label in pull.labels:
            self._pull_requests[number] = replace(
                pull, labels=tuple(l for l in pull.labels if l != label)
            )

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
        self._pr_state[number] = ("open", False)
        return pull

    def get_pull_request(self, repo: str, number: int) -> PullRequestDetail:
        pull = next((p for p in self.pulls if p.number == number), None)
        if pull is None:
            raise KeyError(f"no such pull request: {repo}#{number}")
        state, merged = self._pr_state.get(number, ("open", False))
        merged = merged or number in self.merged
        if merged:
            state = "closed"
        return PullRequestDetail(
            number=pull.number, url=pull.url, head=pull.head, state=state, merged=merged
        )

    def close_pull_request(self, number: int, *, merged: bool) -> None:
        """Test helper: simulate GitHub resolving the PR (merged or closed unmerged)."""
        self._pr_state[number] = ("closed", merged)
        if merged:
            self.merged.add(number)

    def mark_merged(self, number: int) -> None:
        """Test helper: mark a PR as merged (the merge-reconciler's simulation).

        Renamed from `merge_pull_request` when that name became a real verb of
        the `GithubClient` ABC — this one fakes GitHub merging a PR *for*
        reasons outside the harness (a human clicked the button), which is a
        different thing from the harness merging it through the port.
        """
        self.merged.add(number)
        self._pr_state[number] = ("closed", True)

    def merge_pull_request(
        self,
        repo: str,
        number: int,
        *,
        method: str,
        sha: str,
        title: str = "",
        body: str = "",
    ) -> str:
        # Already merged → return the existing merge rather than refusing, the
        # idempotency the port's contract requires of every driver.
        if number in self.merged:
            return f"merged{number}"

        info = self._pull_requests.get(number)
        if info is not None:
            if sha and info.head_sha != sha:
                raise PullRequestNotMergeable(
                    f"{repo}#{number}: head moved from {sha} to {info.head_sha}"
                )
            if info.mergeable_state not in ("clean", "unstable", "has_hooks"):
                raise PullRequestNotMergeable(
                    f"{repo}#{number}: mergeable_state is {info.mergeable_state!r}"
                )

        self.merges.append(
            {
                "repo": repo,
                "number": number,
                "method": method,
                "sha": sha,
                "title": title,
                "body": body,
            }
        )
        self.merged.add(number)
        self._pr_state[number] = ("closed", True)
        return f"merged{number}"

    def add_pull_request(self, info: PullRequestInfo) -> None:
        """Test helper: register a PR the watcher will see via `list_pull_requests`."""
        self._pull_requests[info.number] = info

    def list_pull_requests(
        self, repo: str, *, head_prefix: str | None = None
    ) -> list[PullRequestInfo]:
        infos = self._pull_requests.values()
        if head_prefix is not None:
            infos = (i for i in infos if i.head_branch.startswith(head_prefix))
        return list(infos)

    def update_branch(self, repo: str, number: int) -> None:
        self.updated_branches.append((repo, number))
        # Simulate a successful server-side update so e2e tests can drive both
        # branches (behind → clean) without touching real git.
        info = self._pull_requests.get(number)
        if info is not None:
            self._pull_requests[number] = replace(info, mergeable_state="clean")

    def add_check_run(self, sha: str, run: CheckRun) -> None:
        self._check_runs.setdefault(sha, []).append(run)

    def set_check_run_log(self, check_run_id: int, text: str) -> None:
        self._check_run_logs[check_run_id] = text

    def list_check_runs(self, repo: str, sha: str) -> list[CheckRun]:
        return list(self._check_runs.get(sha, ()))

    def check_run_log(self, repo: str, check_run_id: int) -> str:
        return self._check_run_logs.get(check_run_id, "")

    def create_issue(
        self, repo: str, *, title: str, body: str, labels: tuple[str, ...]
    ) -> Issue:
        number = (max(self._issues) if self._issues else 0) + 1
        issue = Issue(
            number=number,
            title=title,
            body=body,
            url=f"https://github.com/{repo}/issues/{number}",
            labels=tuple(labels),
        )
        self._issues[number] = issue
        return issue

    def search_issue_by_marker(self, repo: str, marker: str, *, label: str) -> Issue | None:
        for issue in self.list_issues(repo, label=label):
            if marker in issue.body:
                return issue
        return None


class HttpGithubClient(GithubClient):
    """Real client against `api.github.com`. `opener` is injectable so it can be
    tested without a network; the default is stdlib `urllib.request.build_opener()`."""

    def __init__(
        self,
        token: str,
        *,
        api: str = "https://api.github.com",
        opener: Any = None,
    ) -> None:
        self._token = token
        self._api = api.rstrip("/")
        self._opener = opener or urllib.request.build_opener()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }

    def _json_headers(self) -> dict[str, str]:
        """Headers for a request that carries a JSON body (POST/PATCH)."""
        return {**self._headers(), "Content-Type": "application/json"}

    @staticmethod
    def _pr_head(item: dict, fallback: str) -> str:
        """The `owner:branch` label as the server reported it.

        Falls back to the caller's `head` argument when the response is missing
        the field or it isn't shaped as expected, so a partial response degrades
        instead of raising.
        """
        head = item.get("head")
        if isinstance(head, dict):
            label = head.get("label")
            if isinstance(label, str):
                return label
        return fallback

    def list_issues(self, repo: str, *, label: str) -> list[Issue]:
        query = urllib.parse.urlencode({"state": "open", "labels": label})
        url = f"{self._api}/repos/{repo}/issues?{query}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            raw = json.loads(response.read())

        issues: list[Issue] = []
        for item in raw:
            if "pull_request" in item:  # PRs are also "issues" — filter them out
                continue
            issues.append(
                Issue(
                    number=item["number"],
                    title=item.get("title", ""),
                    body=item.get("body") or "",
                    url=item.get("html_url", ""),
                    labels=tuple(l["name"] for l in item.get("labels", [])),
                )
            )
        return issues

    def get_issue_state(self, repo: str, number: int) -> str | None:
        url = f"{self._api}/repos/{repo}/issues/{number}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with self._opener.open(request) as response:
                item = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:  # the issue was deleted → gone, not an error
                return None
            raise
        return item.get("state")

    def get_issue(self, repo: str, number: int) -> Issue | None:
        url = f"{self._api}/repos/{repo}/issues/{number}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with self._opener.open(request) as response:
                item = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:  # gone → None, not an error
                return None
            raise
        return Issue(
            number=item["number"],
            title=item.get("title", ""),
            body=item.get("body") or "",
            url=item.get("html_url", ""),
            labels=tuple(l["name"] for l in item.get("labels", [])),
        )

    def add_label(self, repo: str, number: int, label: str) -> None:
        url = f"{self._api}/repos/{repo}/issues/{number}/labels"
        body = json.dumps({"labels": [label]}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers=self._json_headers(), method="POST"
        )
        self._opener.open(request)

    def remove_label(self, repo: str, number: int, label: str) -> None:
        quoted = urllib.parse.quote(label, safe="")
        url = f"{self._api}/repos/{repo}/issues/{number}/labels/{quoted}"
        request = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        try:
            self._opener.open(request)
        except urllib.error.HTTPError as error:
            if error.code == 404:  # label is already gone → nothing to do
                return
            raise

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
                number=item["number"],
                url=item.get("html_url", ""),
                head=self._pr_head(item, head),
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
            headers=self._json_headers(),
            method="POST",
        )
        with self._opener.open(request) as response:
            item = json.loads(response.read())
        return PullRequestRef(
            number=item["number"],
            url=item.get("html_url", ""),
            head=self._pr_head(item, head),
        )

    def get_pull_request(self, repo: str, number: int) -> PullRequestDetail:
        url = f"{self._api}/repos/{repo}/pulls/{number}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            item = json.loads(response.read())
        return PullRequestDetail(
            number=item["number"],
            url=item.get("html_url", ""),
            merged=bool(item.get("merged", False)),
            head=self._pr_head(item, f"?:{number}"),
            state=item.get("state", "open"),
        )

    def list_pull_requests(
        self, repo: str, *, head_prefix: str | None = None
    ) -> list[PullRequestInfo]:
        # `GET .../pulls` does not return `mergeable_state` — that field is only
        # computed and returned by the single-PR endpoint. So this is two-tier:
        # one cheap list call, then one detail call per matching PR only.
        query = urllib.parse.urlencode({"state": "open"})
        url = f"{self._api}/repos/{repo}/pulls?{query}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            raw = json.loads(response.read())

        matching = []
        for item in raw:
            head = item.get("head") or {}
            branch = head.get("ref", "")
            if head_prefix is not None and not branch.startswith(head_prefix):
                continue
            matching.append((item["number"], branch, item))

        infos: list[PullRequestInfo] = []
        for number, branch, item in matching:
            detail_url = f"{self._api}/repos/{repo}/pulls/{number}"
            detail_request = urllib.request.Request(
                detail_url, headers=self._headers(), method="GET"
            )
            with self._opener.open(detail_request) as response:
                detail = json.loads(response.read())
            base = item.get("base") or detail.get("base") or {}
            head_detail = detail.get("head") or item.get("head") or {}
            # `head.repo` is null once a fork has been deleted, so `or {}` is
            # load-bearing rather than defensive: the read must degrade to the
            # unknown-repo default, never raise.
            head_repo = (head_detail.get("repo") or {}).get("full_name") or ""
            infos.append(
                PullRequestInfo(
                    number=number,
                    url=item.get("html_url", ""),
                    head_branch=branch,
                    head_sha=head_detail.get("sha", ""),
                    base_branch=base.get("ref", ""),
                    mergeable_state=detail.get("mergeable_state") or "unknown",
                    title=detail.get("title") or item.get("title") or "",
                    body=detail.get("body") or item.get("body") or "",
                    labels=tuple(
                        label["name"]
                        for label in (detail.get("labels") or item.get("labels") or [])
                        if isinstance(label, dict) and "name" in label
                    ),
                    draft=bool(detail.get("draft", item.get("draft", False))),
                    head_repo=head_repo,
                )
            )
        return infos

    def merge_pull_request(
        self,
        repo: str,
        number: int,
        *,
        method: str,
        sha: str,
        title: str = "",
        body: str = "",
    ) -> str:
        url = f"{self._api}/repos/{repo}/pulls/{number}/merge"
        payload: dict[str, Any] = {"merge_method": method}
        # `sha` is the safety pin: GitHub 409s if the head has moved past it.
        if sha:
            payload["sha"] = sha
        if title:
            payload["commit_title"] = title
        if body:
            payload["commit_message"] = body
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._json_headers(),
            method="PUT",
        )
        try:
            with self._opener.open(request) as response:
                item = json.loads(response.read())
        except urllib.error.HTTPError as error:
            # 405 "not mergeable", 409 "head sha mismatch"/"not up to date",
            # 422 "invalid merge method for this repo" — all states of the
            # world, none a fault to fail the task over.
            if error.code in (405, 409, 422):
                raise PullRequestNotMergeable(
                    f"{repo}#{number}: GitHub refused the merge ({error.code})"
                ) from None
            raise
        return item.get("sha", "")

    def update_branch(self, repo: str, number: int) -> None:
        url = f"{self._api}/repos/{repo}/pulls/{number}/update-branch"
        request = urllib.request.Request(
            url, data=b"{}", headers=self._json_headers(), method="PUT"
        )
        try:
            self._opener.open(request)
        except urllib.error.HTTPError as error:
            if error.code == 422:  # not behind / already up to date
                return
            raise

    def list_check_runs(self, repo: str, sha: str) -> list[CheckRun]:
        url = f"{self._api}/repos/{repo}/commits/{sha}/check-runs"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            raw = json.loads(response.read())
        return [
            CheckRun(
                id=int(item["id"]),
                name=item.get("name", ""),
                conclusion=item.get("conclusion") or "",
                url=item.get("html_url", ""),
            )
            for item in raw.get("check_runs", [])
        ]

    def check_run_log(self, repo: str, check_run_id: int) -> str:
        # An Actions check-run id is its job id; the endpoint 302s to a signed
        # URL that the opener follows for us, and the body is plain text.
        url = f"{self._api}/repos/{repo}/actions/jobs/{check_run_id}/logs"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with self._opener.open(request) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            # 404: not an Actions run, or the log has aged out. 410: expired.
            # Neither is a fault — the brief simply carries no log.
            if error.code in (404, 410):
                return ""
            raise

    def create_issue(
        self, repo: str, *, title: str, body: str, labels: tuple[str, ...]
    ) -> Issue:
        url = f"{self._api}/repos/{repo}/issues"
        payload = json.dumps(
            {"title": title, "body": body, "labels": list(labels)}
        ).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, headers=self._json_headers(), method="POST"
        )
        with self._opener.open(request) as response:
            item = json.loads(response.read())
        return Issue(
            number=item["number"],
            title=item.get("title", title),
            body=item.get("body") or body,
            url=item.get("html_url", ""),
            labels=tuple(l["name"] for l in item.get("labels", [])),
        )

    def search_issue_by_marker(self, repo: str, marker: str, *, label: str) -> Issue | None:
        for issue in self.list_issues(repo, label=label):
            if marker in issue.body:
                return issue
        return None
