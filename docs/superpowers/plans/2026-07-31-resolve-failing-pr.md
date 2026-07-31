# Resolve Failing PR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the conflict-only resolver with one process whose check deterministically triages every unhealthy open pull request and whose single agent fixes whatever it finds — merge conflicts, failing check-runs, or both.

**Architecture:** A new `github-unhealthy-prs` check does all triage (no tokens): it partitions PRs by `mergeable_state`, fetches check-runs and tailed logs for red ones, enforces a three-attempt budget via a rolling label, and emits one observation per unhealthy PR carrying a complete brief. A single `unblock-pr` workflow runs one agent against that brief, then lands. The old `github-conflicts` check, `resolver` workflow and `resolve` agent are deleted.

**Tech Stack:** Python 3.12, stdlib `urllib` (no HTTP library), pytest, hexagonal ports/drivers layout. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-31-resolve-failing-pr-design.md`

## Global Constraints

- Branch: `feat/resolve-failing-pr` (already created off `main`; the spec commit is on it).
- Run the full suite with `PYTHONPATH=src python -m pytest -q` from the repo root. Single tests: `PYTHONPATH=src python -m pytest tests/test_x.py::test_y -v`.
- **No driver may import `harness.cli`.** `tests/test_architecture.py` enforces this. Checks take their client and registry through constructor injection; `cli.py` closes over them in a factory.
- **Every module stem under `src/harness/` must appear somewhere in `CLAUDE.md`.** `tests/test_claude_md_module_map.py` fails by name otherwise. Renaming a driver means editing that table.
- **Every `sources=(...)` path in `src/harness_docs_site/architecture.py` must exist on disk.** `validate()` checks it; a rename without an edit there fails `tests/test_architecture_model.py`.
- Default values, copied verbatim from the spec: `head_prefix` `""`, `skip_label` `"harness:no-autofix"`, `give_up_label` `"harness:needs-human"`, `max_attempts` `3`, `log_tail_lines` `200`, attempt label prefix `"harness:autofix-"`.
- Failing check-run conclusions are exactly `{"failure", "timed_out"}`. `cancelled` and `skipped` are not failures.
- **Do not push until Task 8.** A push to `main` triggers a release and the live service self-upgrades within 30 minutes. Because an unknown check name is fatal, the code and `~/harness-root` must change together with the service stopped — Task 8 Step 5 is the whole cutover sequence, and it is the only place in this plan that pushes.

---

## File Structure

**Create:**
- `src/harness/drivers/github_unhealthy_prs_check.py` — the check: decision table, attempt budget, brief rendering
- `src/harness/behaviors/unblock_pr.py` — the `unblock` step's behavior (merge base, brief the agent, commit without artifacts)
- `tests/test_github_unhealthy_prs_check.py`
- `tests/test_unblock_pr_behavior.py`
- `tests/test_unblock_pr_e2e.py`
- `docs/adr/0026-one-agent-unblocks-a-pull-request.md`

**Modify:**
- `src/harness/drivers/github_client.py` — `CheckRun`, `list_check_runs`, `check_run_log` on the ABC, `HttpGithubClient` and `FakeGithubClient`; PR-aware `add_label`/`remove_label` on the fake
- `src/harness/drivers/failed_tasks_check.py:47,71-81` — recursion-guard import
- `src/harness/ports/workspace.py` — `commit(message, *, exclude=())`
- `src/harness/drivers/git_workspace.py:167-176` — pathspec exclusion
- `src/harness/drivers/memory.py:193-195` — matching fake signature
- `src/harness/cli.py:1043-1044,1086-1095,1164-1167` — factory + registry entry
- `src/harness/app.py:74,483,785` — `UNBLOCK_STEP`, credential map, behavior wiring
- `src/harness_docs_site/architecture.py:283-295` — driver node
- `CLAUDE.md` — module map
- `tests/test_github_client.py`, `tests/test_git_workspace.py`, `tests/test_cli.py`, `tests/test_app.py` — follow the renames

**Delete:**
- `src/harness/drivers/github_conflicts_check.py`, `src/harness/behaviors/resolve_conflict.py`
- `tests/test_github_conflicts_check.py`, `tests/test_resolve_conflict_behavior.py`

**Outside the repo (Task 8), in `~/harness-root`:**
- Create `processes/resolve-failing-pr.json`, `workflows/unblock-pr.json`, `agents/unblock.json`
- Delete `processes/resolve-conflicts.json`, `workflows/resolver.json`, `agents/resolve.json`

---

### Task 1: Check-run reads on GithubClient

**Files:**
- Modify: `src/harness/drivers/github_client.py`
- Test: `tests/test_github_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `CheckRun(id: int, name: str, conclusion: str, url: str)`; `GithubClient.list_check_runs(repo: str, sha: str) -> list[CheckRun]`; `GithubClient.check_run_log(repo: str, check_run_id: int) -> str`. Task 2 consumes all three. `FakeGithubClient.add_check_run(sha, run)` and `FakeGithubClient.set_check_run_log(check_run_id, text)` are the test seams Tasks 2, 3 and 7 use.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github_client.py`. `FakeResponse` and `FakeOpener` already exist in that file — `FakeTextResponse` is new because the logs endpoint returns plain text, not JSON.

```python
# --- check runs ------------------------------------------------------------


class FakeTextResponse:
    def __init__(self, text):
        self._body = text.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fake_list_check_runs_is_keyed_by_sha():
    client = FakeGithubClient([])
    client.add_check_run("abc", CheckRun(1, "pytest", "failure", "u1"))
    client.add_check_run("def", CheckRun(2, "lint", "success", "u2"))

    assert [r.id for r in client.list_check_runs("o/r", "abc")] == [1]
    assert client.list_check_runs("o/r", "zzz") == []


def test_fake_check_run_log_defaults_to_empty():
    client = FakeGithubClient([])
    client.set_check_run_log(1, "boom")

    assert client.check_run_log("o/r", 1) == "boom"
    assert client.check_run_log("o/r", 99) == ""


def test_http_list_check_runs_reads_the_commit_endpoint():
    payload = {
        "check_runs": [
            {"id": 7, "name": "pytest (3.12)", "conclusion": "failure",
             "html_url": "https://gh/run/7"},
            {"id": 8, "name": "lint", "conclusion": "success",
             "html_url": "https://gh/run/8"},
        ]
    }
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    runs = client.list_check_runs("o/r", "abc123")

    assert [(r.id, r.name, r.conclusion, r.url) for r in runs] == [
        (7, "pytest (3.12)", "failure", "https://gh/run/7"),
        (8, "lint", "success", "https://gh/run/8"),
    ]
    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r/commits/abc123/check-runs"


def test_http_check_run_log_returns_plain_text():
    class TextOpener:
        def __init__(self):
            self.requests = []

        def open(self, request):
            self.requests.append(request)
            return FakeTextResponse("line one\nline two\n")

    opener = TextOpener()
    client = HttpGithubClient("tok", opener=opener)

    assert client.check_run_log("o/r", 7) == "line one\nline two\n"
    assert opener.requests[0].full_url == (
        "https://api.github.com/repos/o/r/actions/jobs/7/logs"
    )


def test_http_check_run_log_404_is_empty_string():
    class GoneOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=GoneOpener())

    assert client.check_run_log("o/r", 7) == ""
```

Add `CheckRun` to the import block at the top of the file:

```python
from harness.drivers.github_client import (
    SELF_HEAL_LABEL,
    CheckRun,
    FakeGithubClient,
    HttpGithubClient,
    Issue,
    PullRequestInfo,
    PullRequestRef,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_github_client.py -q -k check_run`
Expected: collection error — `ImportError: cannot import name 'CheckRun'`.

- [ ] **Step 3: Add the dataclass and the ABC methods**

In `src/harness/drivers/github_client.py`, after the `PullRequestInfo` dataclass:

```python
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
```

On the `GithubClient` ABC, beside `update_branch`:

```python
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
```

- [ ] **Step 4: Implement on both clients**

`FakeGithubClient.__init__` gains two stores — add these lines beside the existing `self._pull_requests` initialisation:

```python
        self._check_runs: dict[str, list[CheckRun]] = {}
        self._check_run_logs: dict[int, str] = {}
```

and these methods:

```python
    def add_check_run(self, sha: str, run: CheckRun) -> None:
        self._check_runs.setdefault(sha, []).append(run)

    def set_check_run_log(self, check_run_id: int, text: str) -> None:
        self._check_run_logs[check_run_id] = text

    def list_check_runs(self, repo: str, sha: str) -> list[CheckRun]:
        return list(self._check_runs.get(sha, ()))

    def check_run_log(self, repo: str, check_run_id: int) -> str:
        return self._check_run_logs.get(check_run_id, "")
```

`HttpGithubClient`, beside `update_branch`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_github_client.py -q`
Expected: all pass.

- [ ] **Step 6: Make the fake's labels work on pull requests**

`FakeGithubClient.add_label` and `remove_label` currently index `self._issues` only, so labelling a PR raises `KeyError`. Task 3 needs it. Write the failing test first, appended to `tests/test_github_client.py`:

```python
def test_fake_labels_apply_to_pull_requests_too():
    client = FakeGithubClient([])
    client.add_pull_request(
        PullRequestInfo(
            number=42, url="u", head_branch="b", head_sha="s",
            base_branch="main", mergeable_state="unstable",
        )
    )

    client.add_label("o/r", 42, "harness:autofix-1")
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-1",)

    client.remove_label("o/r", 42, "harness:autofix-1")
    assert client.list_pull_requests("o/r")[0].labels == ()
```

Run: `PYTHONPATH=src python -m pytest tests/test_github_client.py::test_fake_labels_apply_to_pull_requests_too -v`
Expected: FAIL with `KeyError: 42`.

Then replace the fake's two label methods:

```python
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
```

GitHub numbers issues and PRs from one sequence, so a number matching both is impossible in reality; touching whichever store holds it is faithful.

Run: `PYTHONPATH=src python -m pytest tests/test_github_client.py -q`
Expected: all pass, including the pre-existing `test_fake_remove_absent_label_is_noop`.

- [ ] **Step 7: Commit**

```bash
git add src/harness/drivers/github_client.py tests/test_github_client.py
git commit -m "feat: read check runs and their logs from GitHub"
```

---

### Task 2: The unhealthy-PRs check — triage and brief

**Files:**
- Create: `src/harness/drivers/github_unhealthy_prs_check.py`
- Test: `tests/test_github_unhealthy_prs_check.py`

**Interfaces:**
- Consumes: `CheckRun`, `FAILING_CONCLUSIONS`, `list_check_runs`, `check_run_log` (Task 1).
- Produces: `SOURCE_KIND = "pull-request-health"`; `SPEC: CheckSpec`; `GithubUnhealthyPrsCheck(*, client, registry, slug_of=None, head_prefix="", skip_label="harness:no-autofix", give_up_label="harness:needs-human", max_attempts=3, log_tail_lines=200)`. Task 3 adds the budget, Task 4 wires it, Task 7 drives it end to end.

This task implements rows 1–7 and 9 of the decision table. Row 8 (the attempt budget) is Task 3, so `attempt` is hardcoded to `1` here and the label is not yet written.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_github_unhealthy_prs_check.py`:

```python
"""GithubUnhealthyPrsCheck — PR triage as a Check (no network)."""

from __future__ import annotations

from pathlib import Path

from harness.drivers.github_client import CheckRun, FakeGithubClient, PullRequestInfo
from harness.drivers.github_unhealthy_prs_check import GithubUnhealthyPrsCheck
from harness.drivers.memory import MemoryRepositoryRegistry


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    slugs = {Path("/repos/harness_v2"): "onpaj/harness_v2"}
    return registry, slugs


def _pr(number, state, *, head="harness/tsk_1", sha="abc123", base="main",
        labels=(), draft=False):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=head,
        head_sha=sha,
        base_branch=base,
        mergeable_state=state,
        labels=labels,
        draft=draft,
    )


def _check(client, **kwargs):
    registry, slugs = _registry_and_slugs()
    return GithubUnhealthyPrsCheck(
        client=client, registry=registry, slug_of=slugs.get, **kwargs
    )


def test_dirty_pr_emits_a_conflict_brief():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(85, "dirty", head="feature/x", sha="3035f7d"))

    (obs,) = _check(client).evaluate()

    assert obs.state_key == "onpaj/harness_v2:85:3035f7d"
    assert obs.repository == "harness_v2"
    assert obs.data["branch"] == "feature/x"
    assert obs.data["title"] == "unblock PR #85"
    assert obs.data["source"] == {
        "kind": "pull-request-health",
        "repo": "onpaj/harness_v2",
        "pr": 85,
        "url": "https://gh/pr/85",
        "base": "main",
    }
    assert obs.data["problem"]["conflicted"] is True
    assert obs.data["problem"]["failing_checks"] == []


def test_unstable_pr_emits_failing_checks_with_tailed_logs():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(7, "unstable", sha="deadbee"))
    client.add_check_run("deadbee", CheckRun(1, "pytest", "failure", "https://gh/run/1"))
    client.add_check_run("deadbee", CheckRun(2, "lint", "success", "https://gh/run/2"))
    client.set_check_run_log(1, "a\nb\nc\nd\n")

    (obs,) = _check(client, log_tail_lines=2).evaluate()

    problem = obs.data["problem"]
    assert problem["conflicted"] is False
    assert problem["failing_checks"] == [
        {"name": "pytest", "url": "https://gh/run/1", "log_tail": "c\nd"}
    ]


def test_dirty_pr_that_is_also_red_reports_both():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(9, "dirty", sha="s9"))
    client.add_check_run("s9", CheckRun(3, "build", "timed_out", "https://gh/run/3"))
    client.set_check_run_log(3, "timeout")

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["conflicted"] is True
    assert [c["name"] for c in obs.data["problem"]["failing_checks"]] == ["build"]


def test_behind_pr_is_updated_and_emits_no_task():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(42, "behind"))

    assert _check(client).evaluate() == []
    assert client.updated_branches == [("onpaj/harness_v2", 42)]


def test_clean_pr_is_left_to_automerge():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, "clean"))

    assert _check(client).evaluate() == []
    assert client.updated_branches == []


def test_blocked_pr_awaiting_review_is_skipped():
    # `blocked` with no failing check-run means a required review is missing —
    # nothing an agent can supply.
    client = FakeGithubClient([])
    client.add_pull_request(_pr(2, "blocked", sha="s2"))
    client.add_check_run("s2", CheckRun(4, "pytest", "success", "u"))

    assert _check(client).evaluate() == []


def test_unknown_state_with_no_failures_is_skipped():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(3, "unknown", sha="s3"))

    assert _check(client).evaluate() == []


def test_cancelled_and_skipped_are_not_failures():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(4, "unstable", sha="s4"))
    client.add_check_run("s4", CheckRun(5, "a", "cancelled", "u"))
    client.add_check_run("s4", CheckRun(6, "b", "skipped", "u"))

    assert _check(client).evaluate() == []


def test_skip_label_and_draft_and_give_up_label_are_ignored():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(10, "dirty", sha="a", labels=("harness:no-autofix",)))
    client.add_pull_request(_pr(11, "dirty", sha="b", labels=("harness:needs-human",)))
    client.add_pull_request(_pr(12, "dirty", sha="c", draft=True))

    assert _check(client).evaluate() == []


def test_head_prefix_narrows_the_scan():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(20, "dirty", head="feature/x", sha="s20"))
    client.add_pull_request(_pr(21, "dirty", head="harness/y", sha="s21"))

    obs = _check(client, head_prefix="harness/").evaluate()

    assert [o.data["branch"] for o in obs] == ["harness/y"]


def test_missing_log_degrades_to_none_rather_than_skipping():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(30, "unstable", sha="s30"))
    client.add_check_run("s30", CheckRun(9, "third-party", "failure", "u"))
    # no set_check_run_log → the fake returns ""

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["failing_checks"][0]["log_tail"] is None


def test_body_renders_the_brief_for_the_prompt():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(40, "unstable", sha="s40"))
    client.add_check_run("s40", CheckRun(11, "pytest", "failure", "u"))
    client.set_check_run_log(11, "AssertionError: nope")

    (obs,) = _check(client).evaluate()

    body = obs.data["body"]
    assert "pytest" in body
    assert "AssertionError: nope" in body
    assert "attempt 1 of 3" in body


def test_one_bad_pr_does_not_sink_the_tick():
    class Exploding(FakeGithubClient):
        def list_check_runs(self, repo, sha):
            if sha == "boom":
                raise RuntimeError("500")
            return super().list_check_runs(repo, sha)

    client = Exploding([])
    client.add_pull_request(_pr(50, "unstable", sha="boom"))
    client.add_pull_request(_pr(51, "dirty", sha="fine"))

    obs = _check(client).evaluate()

    assert [o.data["source"]["pr"] for o in obs] == [51]


def test_seen_ledger_suppresses_a_relist_at_the_same_head():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(60, "dirty", sha="head1"))
    check = _check(client)

    assert len(check.evaluate()) == 1
    assert check.evaluate() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_github_unhealthy_prs_check.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'harness.drivers.github_unhealthy_prs_check'`.

- [ ] **Step 3: Write the check**

Create `src/harness/drivers/github_unhealthy_prs_check.py`:

```python
"""`GithubUnhealthyPrsCheck`: pull-request triage expressed as a `Check`.

The successor to `github_conflicts_check.py`, and the exact complement of
`github_mergeable_check.py`. Both scan the same open PRs and partition them by
`mergeable_state` with no overlap:

- `behind` → **this check** updates the branch server-side (no task)
- `dirty`, or anything with a failing check run → **this check** fires the
  `unblock-pr` workflow
- `clean` → the mergeable check fires the `automerge` workflow

Every yes/no decision lives here rather than in an agent: triage is a pure
function of the PR's state, so it costs no tokens, survives a restart, and is
testable without a model in the loop. By the time a task exists, *what is
wrong with this PR* is already in `data.problem`, and the agent's only job is
fixing it.

`blocked` is the ambiguous state — GitHub uses it both for "a required check
failed" and for "a required review is missing". Only the former is claimed,
which is why the check-run fetch, not `mergeable_state`, is what decides.

Registered into the process build as the `github-unhealthy-prs` check by
closing a `GithubClient` and the repo registry into a factory in `cli.py`;
`BUILTIN_CHECKS` stays client-free. Imports only sibling drivers and the
registry port — never `cli` — so `test_architecture.py` stays green.
"""

from __future__ import annotations

from typing import Any

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import (
    FAILING_CONCLUSIONS,
    GithubClient,
    PullRequestInfo,
)
from harness.ports.repos import RepositoryRegistry
from harness.ports.triggers import Check, CheckSpec, Observation, ParamSpec

SOURCE_KIND = "pull-request-health"
"""The `source.kind` this check stamps on every task it mints. Named so
`failed_tasks_check.py`'s recursion guard (`PR_BORN_SOURCE_KINDS`) can import
it rather than carry a second, independent copy of the literal — a rename here
then either propagates there or breaks an import immediately, instead of
silently disarming the guard."""

DEFAULT_SKIP_LABEL = "harness:no-autofix"
"""The per-PR veto. A human who wants one PR kept out of the agent's reach
adds this label; nothing else about the process needs to change."""

DEFAULT_GIVE_UP_LABEL = "harness:needs-human"
"""Stamped once the attempt budget is spent, and read on every later tick as
"stop touching this". Removing it by hand is how an operator re-arms a PR."""

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LOG_TAIL_LINES = 200

SPEC = CheckSpec(
    name="github-unhealthy-prs",
    label="GitHub unhealthy PRs",
    description=(
        "Detects open pull requests that are conflicted or have failing "
        "checks, so an agent can unblock them. Stale branches are updated "
        "server-side without spending an agent."
    ),
    params=(
        ParamSpec(
            key="head_prefix",
            label="Branch prefix",
            hint=(
                "Only PRs whose head branch starts with this are watched. "
                "Empty means every open PR."
            ),
        ),
        ParamSpec(
            key="skip_label",
            label="Opt-out label",
            placeholder=DEFAULT_SKIP_LABEL,
            hint="A PR carrying this label is never touched.",
        ),
        ParamSpec(
            key="give_up_label",
            label="Give-up label",
            placeholder=DEFAULT_GIVE_UP_LABEL,
            hint="Added once the attempt budget is spent; the PR is then left alone.",
        ),
        ParamSpec(
            key="max_attempts",
            label="Attempts before giving up",
            type="number",
            placeholder=str(DEFAULT_MAX_ATTEMPTS),
        ),
        ParamSpec(
            key="log_tail_lines",
            label="Log tail (lines)",
            type="number",
            placeholder=str(DEFAULT_LOG_TAIL_LINES),
            hint="How much of each failing check's log reaches the agent.",
        ),
    ),
)
"""The action definition for `github-unhealthy-prs`. `cli.py` bundles it with
the factory that closes over a `GithubClient` + the repo registry."""


class GithubUnhealthyPrsCheck(Check):
    def __init__(
        self,
        *,
        client: GithubClient,
        registry: RepositoryRegistry,
        slug_of=None,
        head_prefix: str = "",
        skip_label: str = DEFAULT_SKIP_LABEL,
        give_up_label: str = DEFAULT_GIVE_UP_LABEL,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        log_tail_lines: int = DEFAULT_LOG_TAIL_LINES,
    ) -> None:
        self._client = client
        self._registry = registry
        # Resolve the default at construction (reads the module attribute now)
        # so tests can monkeypatch `github_slug`; an explicit slug_of wins.
        self._slug_of = slug_of or github_slug
        self._head_prefix = head_prefix
        self._skip_label = skip_label
        self._give_up_label = give_up_label
        self._max_attempts = max_attempts
        self._log_tail_lines = log_tail_lines
        # In-process ledger keyed `slug:number:head_sha`. Keying on the head
        # sha is what makes a re-pushed PR a genuinely new candidate while an
        # unchanged one is not re-emitted every tick.
        self._seen: set[str] = set()

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for name in self._registry.names():
            slug = self._slug_of(self._registry.resolve(name))
            if slug is None:
                continue  # not a GitHub repo — nothing to scan
            for pull in self._client.list_pull_requests(
                slug, head_prefix=self._head_prefix or None
            ):
                try:
                    observation = self._triage(name, slug, pull)
                except Exception:  # noqa: BLE001 - isolate one misbehaving PR
                    continue
                if observation is not None:
                    observations.append(observation)
        return observations

    def _triage(
        self, repository: str, slug: str, pull: PullRequestInfo
    ) -> Observation | None:
        """One PR's whole decision, in the order of the spec's table. Returns
        an `Observation` only for row 9; every earlier row returns None, some
        after a side effect."""
        if self._skip_label and self._skip_label in pull.labels:
            return None
        if self._give_up_label and self._give_up_label in pull.labels:
            return None
        if pull.draft:
            return None
        if pull.mergeable_state == "behind":
            # Un-stale the branch server-side, minting no task and spending no
            # agent. Idempotent, so it is safe on every tick.
            self._client.update_branch(slug, pull.number)
            return None
        if pull.mergeable_state == "clean":
            return None  # the mergeable check's candidate, not ours

        conflicted = pull.mergeable_state == "dirty"
        failing = [
            run
            for run in self._client.list_check_runs(slug, pull.head_sha)
            if run.conclusion in FAILING_CONCLUSIONS
        ]
        if not conflicted and not failing:
            # `blocked` awaiting a required review, or `unknown` while GitHub
            # is still computing — nothing here an agent could fix.
            return None

        key = f"{slug}:{pull.number}:{pull.head_sha}"
        if key in self._seen:
            return None
        self._seen.add(key)

        attempt = 1
        failing_checks = [
            {
                "name": run.name,
                "url": run.url,
                "log_tail": self._tail(self._client.check_run_log(slug, run.id)),
            }
            for run in failing
        ]
        problem: dict[str, Any] = {
            "conflicted": conflicted,
            "attempt": attempt,
            "failing_checks": failing_checks,
        }
        return Observation(
            state_key=key,
            repository=repository,
            data={
                "branch": pull.head_branch,
                "title": f"unblock PR #{pull.number}",
                "body": _render_brief(problem, self._max_attempts),
                "problem": problem,
                "source": {
                    "kind": SOURCE_KIND,
                    "repo": slug,
                    "pr": pull.number,
                    "url": pull.url,
                    "base": pull.base_branch,
                },
            },
        )

    def _tail(self, log: str) -> str | None:
        """The last `log_tail_lines` lines, or None when there is no log.

        Tailing happens here rather than in the agent because a multi-megabyte
        log must never reach a prompt."""
        if not log.strip():
            return None
        lines = log.rstrip("\n").split("\n")
        return "\n".join(lines[-self._log_tail_lines :])


def _render_brief(problem: dict[str, Any], max_attempts: int) -> str:
    """The markdown `compose_prompt` puts in front of the agent as `data.body`.

    `data.problem` stays the structured record; this is only its rendering, so
    nothing in the prompt machinery has to learn to read a new shape."""
    lines = [
        f"This is attempt {problem['attempt']} of {max_attempts} at unblocking "
        "this pull request.",
        "",
    ]
    if problem["conflicted"]:
        lines += [
            "**The branch conflicts with its base.** The base has already been "
            "merged into your working directory, so the conflict markers are "
            "in the files in front of you.",
            "",
        ]
    if problem["failing_checks"]:
        lines.append("**Failing checks:**")
        lines.append("")
        for check in problem["failing_checks"]:
            lines.append(f"### {check['name']}")
            lines.append(f"<{check['url']}>")
            lines.append("")
            if check["log_tail"] is None:
                lines.append(
                    "_No log could be fetched for this check — work from its "
                    "name and the diff, and say so in your artifact._"
                )
            else:
                lines.append("```")
                lines.append(check["log_tail"])
                lines.append("```")
            lines.append("")
    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_github_unhealthy_prs_check.py -q`
Expected: all 14 pass.

- [ ] **Step 5: Commit**

```bash
git add src/harness/drivers/github_unhealthy_prs_check.py tests/test_github_unhealthy_prs_check.py
git commit -m "feat: triage unhealthy pull requests into a single brief"
```

---

### Task 3: The attempt budget

**Files:**
- Modify: `src/harness/drivers/github_unhealthy_prs_check.py`
- Test: `tests/test_github_unhealthy_prs_check.py`

**Interfaces:**
- Consumes: `GithubUnhealthyPrsCheck` (Task 2), `add_label`/`remove_label` (Task 1 Step 6).
- Produces: `ATTEMPT_LABEL_PREFIX = "harness:autofix-"`. Row 8 of the decision table now holds; `problem["attempt"]` is the real count.

The count lives in a rolling label on the PR because harness's own dedup ledger cannot hold it — that ledger keys on `head_sha`, and every fix push mints a new one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github_unhealthy_prs_check.py`:

```python
def test_first_emit_stamps_attempt_one():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(70, "dirty", sha="s70"))

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 1
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-1",)


def test_existing_attempt_label_is_read_and_rolled_forward():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(71, "dirty", sha="s71", labels=("harness:autofix-1",))
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 2
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-2",)


def test_budget_exhausted_labels_needs_human_and_emits_nothing():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(72, "dirty", sha="s72", labels=("harness:autofix-3",))
    )

    assert _check(client).evaluate() == []
    assert "harness:needs-human" in client.list_pull_requests("o/r")[0].labels


def test_max_attempts_is_configurable():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(73, "dirty", sha="s73", labels=("harness:autofix-1",))
    )

    assert _check(client, max_attempts=1).evaluate() == []
    assert "harness:needs-human" in client.list_pull_requests("o/r")[0].labels


def test_a_malformed_attempt_label_is_treated_as_zero():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(74, "dirty", sha="s74", labels=("harness:autofix-oops",))
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 1


def test_no_label_is_written_for_a_behind_or_clean_pr():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(75, "behind", sha="s75"))
    client.add_pull_request(_pr(76, "clean", sha="s76"))

    _check(client).evaluate()

    for pull in client.list_pull_requests("o/r"):
        assert pull.labels == ()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_github_unhealthy_prs_check.py -q -k "attempt or budget or malformed or no_label"`
Expected: FAIL — `assert () == ('harness:autofix-1',)` on the first, and the budget tests emit an observation instead of skipping.

- [ ] **Step 3: Implement the budget**

Add the constant beside `DEFAULT_MAX_ATTEMPTS` in `github_unhealthy_prs_check.py`:

```python
ATTEMPT_LABEL_PREFIX = "harness:autofix-"
"""The rolling attempt counter, stored on the PR itself.

The count cannot live in harness's own ledger: that keys on `head_sha`, and
every fix push mints a new one, so a counter there would reset exactly when it
matters. On the PR it is free to read (the labels arrive with the detail call
the check already makes), visible to whoever is looking at the PR, and an
operator resets the budget by deleting the label."""
```

Replace the `attempt = 1` line and the block above it in `_triage`. The whole span from `key = ...` down to `attempt = 1` becomes:

```python
        attempt = self._attempt_of(pull) + 1
        if attempt > self._max_attempts:
            # Budget spent. Label it once and never look at it again — the
            # give-up check at the top of this method is what makes that stick.
            self._client.add_label(slug, pull.number, self._give_up_label)
            return None

        key = f"{slug}:{pull.number}:{pull.head_sha}"
        if key in self._seen:
            return None
        self._seen.add(key)

        self._bump_attempt_label(slug, pull, attempt)
```

and add these two methods after `_tail`:

```python
    def _attempt_of(self, pull: PullRequestInfo) -> int:
        """How many attempts this PR has already had, read off its labels. An
        unparseable suffix counts as zero rather than raising — a human editing
        labels by hand must not be able to wedge the check."""
        for label in pull.labels:
            if not label.startswith(ATTEMPT_LABEL_PREFIX):
                continue
            suffix = label[len(ATTEMPT_LABEL_PREFIX) :]
            if suffix.isdigit():
                return int(suffix)
        return 0

    def _bump_attempt_label(
        self, slug: str, pull: PullRequestInfo, attempt: int
    ) -> None:
        for label in pull.labels:
            if label.startswith(ATTEMPT_LABEL_PREFIX):
                self._client.remove_label(slug, pull.number, label)
        self._client.add_label(
            slug, pull.number, f"{ATTEMPT_LABEL_PREFIX}{attempt}"
        )
```

- [ ] **Step 4: Run the whole file to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_github_unhealthy_prs_check.py -q`
Expected: all 20 pass. `test_body_renders_the_brief_for_the_prompt` still asserts `"attempt 1 of 3"`, which holds because that PR carries no attempt label.

- [ ] **Step 5: Commit**

```bash
git add src/harness/drivers/github_unhealthy_prs_check.py tests/test_github_unhealthy_prs_check.py
git commit -m "feat: bound PR autofix to three attempts, then hand it to a human"
```

---

### Task 4: Wire the check in, delete the old one

**Files:**
- Modify: `src/harness/cli.py:1043-1044,1086-1095,1164-1167`, `src/harness/app.py:483`, `src/harness/drivers/failed_tasks_check.py:47,71-81`, `src/harness_docs_site/architecture.py:283-295`, `CLAUDE.md`
- Delete: `src/harness/drivers/github_conflicts_check.py`, `tests/test_github_conflicts_check.py`
- Test: `tests/test_failed_tasks_check.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `SPEC`, `SOURCE_KIND`, `GithubUnhealthyPrsCheck` (Tasks 2–3).
- Produces: the action name `"github-unhealthy-prs"` in the check registry, usable from a `processes/*.json` file. Task 8's process file names it.

- [ ] **Step 1: Write the failing test for the recursion guard**

The healer's one-hop limit must keep covering PR-born tasks. Append to `tests/test_failed_tasks_check.py`:

```python
def test_pr_health_tasks_are_recognised_as_pr_born():
    from harness.drivers.failed_tasks_check import PR_BORN_SOURCE_KINDS
    from harness.drivers.github_unhealthy_prs_check import SOURCE_KIND

    assert SOURCE_KIND in PR_BORN_SOURCE_KINDS
```

Run: `PYTHONPATH=src python -m pytest tests/test_failed_tasks_check.py::test_pr_health_tasks_are_recognised_as_pr_born -v`
Expected: FAIL — `assert 'pull-request-health' in frozenset({'mergeability', 'pull-request'})`.

- [ ] **Step 2: Update the guard**

In `src/harness/drivers/failed_tasks_check.py`, replace the import on line 47:

```python
from harness.drivers.github_unhealthy_prs_check import SOURCE_KIND as PR_HEALTH_SOURCE_KIND
```

and line 71 with:

```python
PR_BORN_SOURCE_KINDS = frozenset({PR_HEALTH_SOURCE_KIND, PULL_REQUEST_SOURCE_KIND})
```

In the docstring beneath it, replace the sentence naming `GithubConflictsCheck` and `"mergeability"` with:

```
harness's own pull requests rather than a filed issue: `GithubUnhealthyPrsCheck`
(`drivers/github_unhealthy_prs_check.py`) stamps its own `SOURCE_KIND`
(`"pull-request-health"`) on unblock tasks, `GithubMergeableCheck`
```

Also update the reference in `_born_from_a_harness_pull_request`'s docstring (line ~356): `a resolver task (\`GithubConflictsCheck\`)` becomes `an unblock task (\`GithubUnhealthyPrsCheck\`)`.

Run: `PYTHONPATH=src python -m pytest tests/test_failed_tasks_check.py -q`
Expected: all pass.

- [ ] **Step 3: Swap the factory in cli.py**

Replace the two imports at `src/harness/cli.py:1043-1044`:

```python
    from harness.drivers.github_unhealthy_prs_check import SPEC as GITHUB_UNHEALTHY_PRS_SPEC
    from harness.drivers.github_unhealthy_prs_check import GithubUnhealthyPrsCheck
```

Replace `github_conflicts_factory` (lines 1086-1095) with:

```python
    def github_unhealthy_prs_factory(params: dict) -> GithubUnhealthyPrsCheck:
        if client is None:
            raise MissingCredential(
                "github-unhealthy-prs action requires GITHUB_TOKEN", field="check"
            )
        max_attempts = params.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
        log_tail_lines = params.get("log_tail_lines", DEFAULT_LOG_TAIL_LINES)
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ProcessValidationError(
                "github-unhealthy-prs action requires params.max_attempts to be "
                "a positive integer",
                field="params",
            )
        if not isinstance(log_tail_lines, int) or log_tail_lines < 1:
            raise ProcessValidationError(
                "github-unhealthy-prs action requires params.log_tail_lines to be "
                "a positive integer",
                field="params",
            )
        return GithubUnhealthyPrsCheck(
            client=client,
            registry=registry,
            head_prefix=params.get("head_prefix", ""),
            skip_label=params.get("skip_label", DEFAULT_SKIP_LABEL),
            give_up_label=params.get("give_up_label", DEFAULT_GIVE_UP_LABEL),
            max_attempts=max_attempts,
            log_tail_lines=log_tail_lines,
        )
```

Add the four defaults to the import at line 1043:

```python
    from harness.drivers.github_unhealthy_prs_check import (
        DEFAULT_GIVE_UP_LABEL,
        DEFAULT_LOG_TAIL_LINES,
        DEFAULT_MAX_ATTEMPTS,
        DEFAULT_SKIP_LABEL,
        SPEC as GITHUB_UNHEALTHY_PRS_SPEC,
        GithubUnhealthyPrsCheck,
    )
```

(and drop the two-line form from the start of this step — this single import replaces it).

Replace the registry entry at lines 1164-1167:

```python
        "github-unhealthy-prs": CheckDefinition(
            spec=GITHUB_UNHEALTHY_PRS_SPEC, factory=github_unhealthy_prs_factory
        ),
```

Note `DEFAULT_SKIP_LABEL` is also exported by `github_mergeable_check.py` with a different value (`harness:no-automerge`). If `cli.py` already imports that name, alias this one — `DEFAULT_SKIP_LABEL as UNHEALTHY_SKIP_LABEL` — rather than shadowing it. Check with:

```bash
grep -n "DEFAULT_SKIP_LABEL" src/harness/cli.py
```

- [ ] **Step 4: Update the credential map and the prose references**

`src/harness/app.py:483` — replace the `"github-conflicts"` key:

```python
    "github-unhealthy-prs": "GITHUB_TOKEN",
```

Then fix the comments that name the old action, at `src/harness/cli.py:1027,2331,2437` and `src/harness/app.py:847`: each lists `github-issues`/`github-conflicts` as the credential-gated pair — change `github-conflicts` to `github-unhealthy-prs` in all four.

- [ ] **Step 5: Delete the old check and its tests**

```bash
git rm src/harness/drivers/github_conflicts_check.py tests/test_github_conflicts_check.py
```

- [ ] **Step 6: Update the docs-site node and CLAUDE.md**

In `src/harness_docs_site/architecture.py`, replace the `github-conflicts-check` Driver (lines 283-295):

```python
                Driver(
                    id="github-unhealthy-prs-check",
                    name="GithubUnhealthyPrsCheck (github-unhealthy-prs)",
                    tagline="Pull-request triage as a Process action.",
                    description=(
                        "Lists open PRs across the registry; a PR merely behind "
                        "its base is updated server-side, a conflicted or "
                        "red-CI one becomes an observation for the unblock-pr "
                        "workflow, keyed per head commit and bounded by an "
                        "attempt budget held in a label on the PR."
                    ),
                    sources=("src/harness/drivers/github_unhealthy_prs_check.py",),
                ),
```

In `CLAUDE.md`'s module map, replace the `github_conflicts_check` stem with `github_unhealthy_prs_check` (brace notation — find the row listing the github drivers).

- [ ] **Step 7: Run the full suite**

Run: `PYTHONPATH=src python -m pytest -q`
Expected: pass, except `tests/test_resolve_conflict_behavior.py` and anything referencing `RESOLVE_STEP` — those are Task 6. If `tests/test_cli.py` or `tests/test_fs_processes.py` name `github-conflicts` in a fixture, update the literal to `github-unhealthy-prs`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: replace the github-conflicts action with github-unhealthy-prs"
```

---

### Task 5: Commit with a pathspec exclusion

**Files:**
- Modify: `src/harness/ports/workspace.py:39`, `src/harness/drivers/git_workspace.py:167-176`, `src/harness/drivers/memory.py:193-195`
- Test: `tests/test_git_workspace.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `WorkspaceHandle.commit(message: str, *, exclude: tuple[str, ...] = ()) -> str | None`. Task 6 calls it with `exclude=(".artifacts",)`. `MemoryWorkspaceHandle.commit_excludes: list[tuple[str, ...]]` records what each commit excluded, for Task 6's tests.

**Why this and not the spec's `.git/info/exclude`.** The spec specified writing `.artifacts/` into `.git/info/exclude`. That does not work here: worktrees are created with `git worktree add` (`git_workspace.py:5`), and git reads `info/exclude` from the *common* dir — the main clone's `.git/info/exclude`, shared by every worktree and by the operator's own checkout. A per-commit pathspec is local to the one commit that needs it. Update the spec's *Artifacts* section to match when this task lands.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_git_workspace.py`. `_workspace(tmp_path)` and `_make_task()` are existing helpers at the top of that file (lines 33 and 40); `subprocess` is already imported there via `_git`, but add a top-level `import subprocess` if it is not.

```python
def test_commit_can_exclude_a_pathspec(tmp_path):
    handle = _workspace(tmp_path).attach(_make_task())

    (handle.path / "keep.txt").write_text("kept\n")
    (handle.path / ".artifacts").mkdir(exist_ok=True)
    (handle.path / ".artifacts" / "note.md").write_text("scratch\n")

    handle.commit("with exclusion", exclude=(".artifacts",))

    tracked = subprocess.run(
        ["git", "-C", str(handle.path), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "keep.txt" in tracked
    assert not any(p.startswith(".artifacts") for p in tracked)


def test_commit_without_exclude_still_stages_everything(tmp_path):
    handle = _workspace(tmp_path).attach(_make_task())

    (handle.path / ".artifacts").mkdir(exist_ok=True)
    (handle.path / ".artifacts" / "note.md").write_text("scratch\n")

    handle.commit("no exclusion")

    tracked = subprocess.run(
        ["git", "-C", str(handle.path), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert ".artifacts/note.md" in tracked
```

Run: `PYTHONPATH=src python -m pytest tests/test_git_workspace.py -q -k exclude`
Expected: FAIL — `TypeError: commit() got an unexpected keyword argument 'exclude'`.

- [ ] **Step 2: Widen the port**

In `src/harness/ports/workspace.py`, replace the `commit` abstract method:

```python
    @abstractmethod
    def commit(self, message: str, *, exclude: tuple[str, ...] = ()) -> str | None:
        """Stage everything and commit; None when there was nothing to commit.

        `exclude` names paths to leave unstaged — used to keep a step's own
        scratch output off a branch it does not own. It is a pathspec
        exclusion on this one commit, not a persistent ignore rule.
        """
```

- [ ] **Step 3: Implement in GitWorkspace**

Replace `git_workspace.py:167-176`:

```python
    def commit(self, message: str, *, exclude: tuple[str, ...] = ()) -> str | None:
        add = ["-C", str(self._path), "add", "-A"]
        if exclude:
            # `git add -A -- . ':(exclude)<path>'` — the pathspec applies to
            # this staging call alone, unlike .gitignore or info/exclude (the
            # latter lives in the *common* dir and would leak across every
            # worktree of this clone).
            add += ["--", "."] + [f":(exclude){path}" for path in exclude]
        _git(add)
        status = _git(["-C", str(self._path), "status", "--porcelain"])
        if status.strip() == "":
            return None
        _git(
            ["-C", str(self._path), "commit", "-m", message],
            env_extra=_IDENTITY,
        )
        return _git(["-C", str(self._path), "rev-parse", "HEAD"]).strip()
```

Note the `status` check still sees the excluded files as untracked, so it will not report "nothing to commit" when only an artifact changed. That is correct: `git commit` with an empty index would fail, and the pre-existing behavior of returning None only applies when the tree is genuinely clean. If a run produces *only* an excluded artifact, `git commit` finds nothing staged and `_git` raises — guard it by re-reading the staged diff instead:

```python
        staged = _git(["-C", str(self._path), "diff", "--cached", "--name-only"])
        if staged.strip() == "":
            return None
```

Use that in place of the `status` check.

- [ ] **Step 4: Match the fake**

`src/harness/drivers/memory.py`, replacing lines 193-195:

```python
    def commit(self, message: str, *, exclude: tuple[str, ...] = ()) -> str | None:
        self.commits.append(message)
        self.commit_excludes.append(exclude)
        return f"sha{len(self.commits)}"
```

and initialise the new list alongside `self.commits` in `MemoryWorkspaceHandle.__init__`:

```python
        self.commit_excludes: list[tuple[str, ...]] = []
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src python -m pytest tests/test_git_workspace.py tests/test_consumer.py -q`
Expected: pass. The `staged` change alters when `commit` returns None; if a test asserted None on a clean tree it still holds.

- [ ] **Step 6: Commit**

```bash
git add src/harness/ports/workspace.py src/harness/drivers/git_workspace.py src/harness/drivers/memory.py tests/test_git_workspace.py
git commit -m "feat: let a commit exclude a pathspec"
```

---

### Task 6: UnblockPrBehavior

**Files:**
- Create: `src/harness/behaviors/unblock_pr.py`
- Delete: `src/harness/behaviors/resolve_conflict.py`, `tests/test_resolve_conflict_behavior.py`
- Modify: `src/harness/app.py:74,785`, `CLAUDE.md`
- Test: `tests/test_unblock_pr_behavior.py`

**Interfaces:**
- Consumes: `commit(..., exclude=...)` (Task 5); `data.problem` and `data.body` (Task 2).
- Produces: `UnblockPrBehavior(*, clock, workspace, runner, spec, events, timeout=600.0)`; `app.UNBLOCK_STEP = "unblock"`. Task 7 drives it.

Two behavioural changes from `ResolveConflictBehavior`: the clean-merge early return now requires that nothing is red as well, and the commit excludes `.artifacts`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_unblock_pr_behavior.py`. This mirrors the structure of the `tests/test_resolve_conflict_behavior.py` it replaces — note that file has no `@pytest.mark.asyncio` decorators (asyncio mode is configured globally), so this one has none either.

```python
"""UnblockPrBehavior — merge base, brief the agent, commit without artifacts."""

from __future__ import annotations

from harness.behaviors.unblock_pr import UnblockPrBehavior
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryEventSink,
    MemoryWorkspace,
)
from harness.models import DONE, BehaviorResult, Task
from harness.ports.agent import AgentRun, AgentSpec


def make_task(*, conflicted: bool, failing_checks: list) -> Task:
    return Task(
        id="tsk_unblock_1",
        workflow_template="unblock-pr",
        created="2026-07-31T10:00:00Z",
        repository="app-backend",
        status="unblock",
        data={
            "branch": "feature/x",
            "title": "unblock PR #42",
            "body": "the brief",
            "problem": {
                "conflicted": conflicted,
                "attempt": 1,
                "failing_checks": failing_checks,
            },
            "source": {
                "kind": "pull-request-health",
                "repo": "o/r",
                "pr": 42,
                "url": "https://github.com/o/r/pull/42",
                "base": "main",
            },
        },
    )


def build(*, runner=None, spec=None):
    workspace = MemoryWorkspace()
    events = MemoryEventSink()
    spec = spec or AgentSpec(name="unblock", prompt="unblock the PR")
    runner = runner or FakeAgentRunner(
        runs={"unblock": AgentRun(DONE, "unblock: fixed it")}
    )
    behavior = UnblockPrBehavior(
        clock=FakeClock(), workspace=workspace, runner=runner, spec=spec, events=events
    )
    return behavior, workspace, runner, events


async def test_clean_merge_and_nothing_red_skips_the_agent():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])

    result = await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.merges == ["main"]
    assert runner.calls == []
    assert handle.commits == ["[unblock] merge main — nothing left to fix"]
    assert result.outcome == DONE
    assert "nothing to fix" in result.summary


async def test_clean_merge_but_red_checks_still_calls_the_agent():
    behavior, workspace, runner, _ = build()
    task = make_task(
        conflicted=False,
        failing_checks=[{"name": "pytest", "url": "u", "log_tail": "boom"}],
    )

    await behavior.run(task)

    assert len(runner.calls) == 1


async def test_real_conflict_calls_the_agent_and_commits_its_summary():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    handle = workspace.handles[task.id]
    assert len(runner.calls) == 1
    assert handle.commits == ["unblock: fixed it"]
    assert result == BehaviorResult(DONE, "unblock: fixed it")


async def test_the_agent_commit_excludes_artifacts():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.commit_excludes[-1] == (".artifacts",)


async def test_the_brief_reaches_the_prompt():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    prompt = runner.calls[0]["prompt"]
    assert "the brief" in prompt
    assert ".artifacts/tsk_unblock_1/unblock-01.md" in prompt
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_unblock_pr_behavior.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'harness.behaviors.unblock_pr'`.

- [ ] **Step 3: Write the behavior**

Create `src/harness/behaviors/unblock_pr.py` as a copy of `resolve_conflict.py` with the class renamed to `UnblockPrBehavior`, the module docstring rewritten, and `run()` replaced by:

```python
    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or ""
        handle = self._workspace.attach(task)
        base = task.data["source"]["base"]
        problem = task.data.get("problem") or {}
        failing = problem.get("failing_checks") or []

        conflicted = handle.merge(base)
        if not conflicted and not failing:
            # The conflict resolved itself between the check emitting and this
            # task running (someone else pushed, or GitHub updated the branch),
            # and nothing was red to begin with. Commit the clean merge; no
            # agent call spent.
            handle.commit(f"[{step}] merge {base} — nothing left to fix")
            return BehaviorResult(DONE, f"merged {base} cleanly, nothing to fix")

        attempt, relpath = next_attempt(handle.path, task.id, step)
        prompt = compose_prompt(
            task,
            step=step,
            artifact_relpath=relpath,
            outcomes=self._spec.allowed_outcomes,
            hints={},
            description=None,
        )

        def on_output(line: str) -> None:
            self._events.emit(
                "stage_output",
                task_id=task.id,
                step=step,
                attempt=attempt,
                line=line,
            )

        run = await self._runner.run(
            prompt=prompt,
            spec=self._spec,
            cwd=handle.path,
            timeout=self._timeout,
            on_output=on_output,
        )

        # The worker commits, never the agent (invariant 9). `git commit` with
        # MERGE_HEAD present produces the two-parent merge commit — no special
        # flag needed. `.artifacts` is excluded because this branch may belong
        # to a human: the agent's write-up belongs in the task record, not in
        # somebody else's pull request.
        handle.commit(run.summary, exclude=(".artifacts",))
        return BehaviorResult(run.outcome, run.summary)
```

Module docstring:

```python
"""`UnblockPrBehavior` — merges the base branch, hands the agent whatever is
wrong with the pull request, then commits (invariant 9: the worker commits).

The unblock task's own PR branch (`task.data["branch"]`) is already checked out
by `GitWorkspace.attach` before `run()` is called. This behavior adds the
merge-then-brief-then-commit step in front of the same
`AgentRunner`/`AgentSpec`/artifact machinery `ClaudeCliBehavior` uses, so it
stays a dedicated class instead of a branch inside the generic one
(invariant 14: persona is data, not control flow).

The brief itself needs no code here: `GithubUnhealthyPrsCheck` renders it into
`data.body`, which `compose_prompt` already puts in front of the agent.
"""
```

- [ ] **Step 4: Rewire app.py**

Replace `RESOLVE_STEP` at `src/harness/app.py:74`:

```python
UNBLOCK_STEP = "unblock"
"""The step to which the wiring assigns UnblockPrBehavior, when a catalog is
configured — the unblock-pr workflow's first step."""
```

At line 785, replace the conditional and the import at line 12:

```python
from harness.behaviors.unblock_pr import UnblockPrBehavior
```

```python
        if step == UNBLOCK_STEP and catalog is not None:
            return UnblockPrBehavior(
```

- [ ] **Step 5: Delete the old behavior and its tests**

```bash
git rm src/harness/behaviors/resolve_conflict.py tests/test_resolve_conflict_behavior.py
```

Update `CLAUDE.md`'s module map: `resolve_conflict` → `unblock_pr`.

- [ ] **Step 6: Run the full suite**

Run: `PYTHONPATH=src python -m pytest -q`
Expected: all pass. If `tests/test_app.py` references `RESOLVE_STEP` or the `"resolve"` literal, update it to `UNBLOCK_STEP` / `"unblock"`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: one behavior unblocks a PR, whatever is wrong with it"
```

---

### Task 7: End-to-end

**Files:**
- Create: `tests/test_unblock_pr_e2e.py`
- Test: itself

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: nothing.

This mirrors `tests/test_automerge_e2e.py` — same `drive_until_quiet` loop, same `build(...)` call shape, same `github_slug` monkeypatch. One difference to note before writing: `build(behavior=...)` installs **one** behavior for every step, so this e2e uses a capturing stand-in for both `unblock` and `land` rather than the real `UnblockPrBehavior`. That is the right seam here — Task 6 already proves the brief reaches the prompt at unit level; what this file proves is that a real PR travels check → workflow → end, and that the give-up path emits nothing.

Note also that the multi-round budget loop is deliberately *not* driven by advancing the clock across three ticks. The trigger's interval gating against `FakeClock` makes that brittle, and Task 3 already covers the counter arithmetic. Seeding a PR that already carries `harness:autofix-3` proves the same closure in one tick.

- [ ] **Step 1: Write the tests**

Create `tests/test_unblock_pr_e2e.py`:

```python
"""unblock-pr, end to end: a real PR travels scan → unblock → land → end, and
a PR that has spent its attempt budget travels no distance at all.

Mirrors `test_automerge_e2e.py`'s style, but proves the property the unit tests
can only prove piecewise: that `GithubUnhealthyPrsCheck`'s brief survives the
dispatcher/consumer loop intact, and that the give-up label actually stops the
process rather than merely being written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.drivers.github_client import CheckRun, FakeGithubClient, PullRequestInfo
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryRepositoryRegistry,
    MemoryWorkspace,
)
from harness.models import DONE, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior

MAX_STEPS = 1000
SLUG = "onpaj/harness_v2"

UNBLOCK_WORKFLOW = {
    "name": "unblock-pr",
    "start": "unblock",
    "transitions": [
        {"from": "unblock", "on": "done", "to": "land"},
        {"from": "unblock", "on": "stuck", "to": "end"},
        {"from": "land", "on": "done", "to": "end"},
    ],
}


async def drive_until_quiet(harness) -> int:
    for step in range(MAX_STEPS):
        acted = False
        for poller in harness.pollers:
            if poller.tick():
                acted = True
        if harness.dispatcher.tick():
            acted = True
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return step
    raise AssertionError("loop did not settle")


class CapturingBehavior(ConsumerBehavior):
    """Stands in for every step: records the task it saw and passes it on."""

    def __init__(self) -> None:
        self.seen: list[Task] = []

    async def run(self, task: Task) -> BehaviorResult:
        self.seen.append(task)
        return BehaviorResult(DONE, f"{task.status}: ok")


def _pr(number, sha, *, state, labels=()):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=f"feature/{number}",
        head_sha=sha,
        base_branch="main",
        mergeable_state=state,
        title=f"PR {number}",
        labels=tuple(labels),
    )


async def _run(tmp_path, prs, check_runs=(), logs=()):
    from harness.cli import _process_check_factories
    import harness.drivers.github_unhealthy_prs_check as up_mod
    from harness.app import HarnessLayout, build

    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "unblock-pr.json").write_text(json.dumps(UNBLOCK_WORKFLOW))
    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "resolve-failing-pr.json").write_text(
        json.dumps(
            {
                "trigger": {"interval": "60s"},
                "action": {
                    "check": "github-unhealthy-prs",
                    "params": {"head_prefix": ""},
                },
                "target": {"workflow": "unblock-pr"},
                "dedup": "per-state",
                "sink": {"kind": "none"},
            }
        )
    )

    client = FakeGithubClient([])
    for pr in prs:
        client.add_pull_request(pr)
    for sha, run in check_runs:
        client.add_check_run(sha, run)
    for run_id, text in logs:
        client.set_check_run_log(run_id, text)

    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    original_slug = up_mod.github_slug
    up_mod.github_slug = lambda path: SLUG  # type: ignore[assignment]

    behavior = CapturingBehavior()
    try:
        args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
        harness = build(
            tmp_path,
            "unblock-pr",
            events=MemoryEventSink(),
            clock=FakeClock("2026-07-31T10:00:00Z"),
            behavior=behavior,
            workspace=MemoryWorkspace(),
            artifacts=MemoryArtifactStore(),
            forge=MemoryForge(),
            delay=0.0,
            extra_checks=_process_check_factories(args, registry, client=client),
        )
        await drive_until_quiet(harness)
    finally:
        up_mod.github_slug = original_slug  # type: ignore[assignment]
    return behavior, client


async def test_a_conflicted_pr_travels_unblock_then_land(tmp_path):
    behavior, client = await _run(tmp_path, [_pr(85, "abc123", state="dirty")])

    steps = [task.status for task in behavior.seen]
    assert steps == ["unblock", "land"]
    problem = behavior.seen[0].data["problem"]
    assert problem["conflicted"] is True
    assert problem["attempt"] == 1
    assert client.list_pull_requests(SLUG)[0].labels == ("harness:autofix-1",)


async def test_a_red_pr_carries_its_log_tail_through_the_loop(tmp_path):
    behavior, _ = await _run(
        tmp_path,
        [_pr(7, "deadbee", state="unstable")],
        check_runs=[("deadbee", CheckRun(1, "pytest", "failure", "https://gh/run/1"))],
        logs=[(1, "E   AssertionError: nope\n")],
    )

    problem = behavior.seen[0].data["problem"]
    assert problem["conflicted"] is False
    assert problem["failing_checks"][0]["name"] == "pytest"
    assert "AssertionError: nope" in problem["failing_checks"][0]["log_tail"]


async def test_a_pr_that_spent_its_budget_is_labelled_and_never_dispatched(tmp_path):
    behavior, client = await _run(
        tmp_path,
        [_pr(9, "s9", state="dirty", labels=("harness:autofix-3",))],
    )

    assert behavior.seen == []
    assert "harness:needs-human" in client.list_pull_requests(SLUG)[0].labels


async def test_an_opted_out_pr_is_not_touched_at_all(tmp_path):
    behavior, client = await _run(
        tmp_path,
        [_pr(10, "s10", state="dirty", labels=("harness:no-autofix",))],
    )

    assert behavior.seen == []
    assert client.list_pull_requests(SLUG)[0].labels == ("harness:no-autofix",)
```

If `build()`'s signature has drifted from what `test_automerge_e2e.py` passes, copy that file's call verbatim and adjust — it is the reference, not this snippet.

- [ ] **Step 2: Run them**

Run: `PYTHONPATH=src python -m pytest tests/test_unblock_pr_e2e.py -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_unblock_pr_e2e.py
git commit -m "test: unblock-pr end to end, including the give-up path"
```

---

### Task 8: The ADR and the harness-root migration

**Files:**
- Create: `docs/adr/0026-one-agent-unblocks-a-pull-request.md`
- Modify: `docs/superpowers/specs/2026-07-31-resolve-failing-pr-design.md` (the *Artifacts* section, per Task 5)
- Outside the repo: `~/harness-root/{processes,workflows,agents}/`

**Interfaces:**
- Consumes: the action name `github-unhealthy-prs` (Task 4), the step name `unblock` (Task 6).
- Produces: a running process.

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0026-one-agent-unblocks-a-pull-request.md`. `tests/test_adr_docs.py` requires the filename to match `NNNN-slug.md`, a `# ADR-0026: ...` title and a `Status:` line.

```markdown
# ADR-0026: One agent unblocks a pull request, and the check decides why

Status: Accepted (2026-07-31)

## Context

`github-conflicts` handled two of GitHub's `mergeable_state` values —
`behind` (updated server-side, no task) and `dirty` (the resolver workflow).
`unstable` and `blocked` were left alone, so a pull request with red CI sat
untouched until a human noticed. `GithubClient` had no check-run API, so there
was nothing to act on even if the check had wanted to.

Every process was also gated to the `harness/` branch prefix, so none of this
reached a human-authored PR.

## Decision

**Triage is deterministic and lives in the check.** `GithubUnhealthyPrsCheck`
partitions every open PR by `mergeable_state` and, for the ambiguous states,
by whether a failing check-run actually exists on the head sha. It fetches the
failing runs' logs, tails them, and emits one observation carrying a complete
brief. No agent is spawned to work out what is wrong.

The rejected alternative was a triage *step* — an agent that reads the PR and
routes to a specialist. It buys flexibility a check cannot have (noticing a
flaky test worth re-running rather than fixing) at the cost of one agent run
per unhealthy PR per push, and it moves the interesting logic somewhere no
test can reach without a model in the loop.

**One agent fixes whatever the brief describes.** There is no resolve/fix-CI
split. A PR that is both conflicted and red is one problem and gets one run.

**The process is scoped to all open PRs, not the harness's own.** This is the
sharp edge: the harness pushes commits onto branches humans have checked out.
Three things contain it — it never force-pushes, `harness:no-autofix` is a
per-PR veto needing no config change, and a three-attempt budget held in a
`harness:autofix-<n>` label ends in `harness:needs-human` rather than looping.

The budget lives on the PR because harness's own dedup ledger keys on
`head_sha`, and every fix push mints a new one — a counter there would reset
exactly when it matters.

## Consequences

- `blocked` is claimed only when a failing check-run exists. A PR merely
  awaiting a required review is left alone; no agent can supply one.
- The check now makes network calls per red PR (check-runs, then one log per
  failing run). The per-`head_sha` dedup key holds this to once per push.
- A rename of `SOURCE_KIND` must propagate to `PR_BORN_SOURCE_KINDS` in
  `failed_tasks_check.py`, which imports it precisely so that a rename breaks
  loudly instead of silently disarming the healer's one-hop guard.
- Reverting the blast radius is a one-line config change: set `head_prefix`
  back to `harness/`. No code change.
```

Run: `PYTHONPATH=src python -m pytest tests/test_adr_docs.py -q`
Expected: pass.

- [ ] **Step 2: Correct the spec's Artifacts section**

Replace the `.git/info/exclude` decision in `docs/superpowers/specs/2026-07-31-resolve-failing-pr-design.md` with the pathspec-exclusion one, and say why: worktrees share `info/exclude` through the common dir, so it would leak across every worktree of the clone.

- [ ] **Step 3: Run the full suite one more time**

Run: `PYTHONPATH=src python -m pytest -q`
Expected: all pass. Do not proceed to Step 4 until it does — Step 4 changes the live installation.

- [ ] **Step 4: Commit the repo side**

```bash
git add -A
git commit -m "docs: ADR-0026, one agent unblocks a pull request"
```

- [ ] **Step 5: Understand the cutover before touching anything**

**This is the one step that can take the live harness down, and the usual "migrate `~/harness-root` before pushing" advice in `~/CLAUDE.md` is not sufficient here.** An unknown check name is a fatal `ProcessValidationError`, not a skipped-with-warning one (`fs_processes.py:255-259`). That makes *both* naive orders fatal:

- **Config first.** `resolve-failing-pr.json` names `github-unhealthy-prs`, which the currently-running release does not register → the running service fails on its next process build.
- **Push first.** The release lands, the service self-upgrades within 30 minutes, and the still-present `resolve-conflicts.json` names `github-conflicts`, which the new build no longer registers → same fatality, now on a service that upgraded itself while nobody was watching.

So code and config must change together, with the service stopped across the gap:

1. Stop the service: `launchctl bootout gui/$(id -u)/com.harness`
2. Push the branch and merge it; wait for the release to publish.
3. Upgrade the install by hand rather than waiting for autoupdate: `~/harness-app/.venv/bin/harness update`
4. Migrate `~/harness-root` (Step 6).
5. Start the service: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.harness.plist`

Confirm the service label and plist path first — `launchctl list | grep -i harness` — rather than trusting the names above.

Do not start step 1 until Task 8 Step 4 is committed and the full suite is green.

- [ ] **Step 6: Migrate ~/harness-root**

```bash
rm ~/harness-root/processes/resolve-conflicts.json \
   ~/harness-root/workflows/resolver.json \
   ~/harness-root/agents/resolve.json
```

Create `~/harness-root/processes/resolve-failing-pr.json`:

```json
{
  "name": "resolve-failing-pr",
  "trigger": {"interval": "60s"},
  "action": {"check": "github-unhealthy-prs", "params": {
    "head_prefix": "",
    "skip_label": "harness:no-autofix",
    "give_up_label": "harness:needs-human",
    "max_attempts": 3,
    "log_tail_lines": 200
  }},
  "target": {"workflow": "unblock-pr"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

Create `~/harness-root/workflows/unblock-pr.json`:

```json
{
  "name": "unblock-pr",
  "start": "unblock",
  "transitions": [
    {"from": "unblock", "on": "done", "to": "land",
     "hint": "the conflict is resolved and/or the failing checks should now pass"},
    {"from": "unblock", "on": "stuck", "to": "end",
     "hint": "you could not fix this from what you were given — push nothing"},
    {"from": "land", "on": "done", "to": "end"}
  ],
  "descriptions": {
    "unblock": "fix whatever is blocking this pull request — merge conflicts, failing checks, or both",
    "land": "commit the fix and push it to the pull request's branch"
  },
  "max_parallel": {"unblock": 2}
}
```

Create `~/harness-root/agents/unblock.json`. The `prompt` value is the text below with real newlines encoded as `\n` — write the file with a JSON serializer rather than by hand:

```json
{
  "prompt": "You are a senior developer whose only job right now is to get one pull request unblocked. The working directory is a checkout of the PR's own branch, and the base branch has already been merged in — so if there was a conflict, the files with <<<<<<< ======= >>>>>>> markers are in front of you now.\n\nYour brief above says what is wrong: a conflict, one or more failing checks with the tail of their logs, or both. Fix all of it.\n\nFor a conflict: read each conflicted file, understand both sides from the surrounding code and tests, and produce a resolution that preserves the combined intent. Remove every marker.\n\nFor a failing check: the log tail tells you what failed, not always why. Read the code the failure points at before you change it. Then run the relevant tests yourself and confirm they pass — a fix you have not run is a guess, and pushing a guess costs another round of CI.\n\nA log tail may be absent for checks whose logs this harness cannot fetch. Say so and work from the check's name and the diff rather than inventing what it said.\n\nDo not widen the scope. You are fixing what is broken, not improving what happens to be nearby — an unrelated change here lands on someone else's PR.\n\nIf you cannot fix it from what you have — the failure is environmental, the log is uninformative, or the right fix is a judgement call that is not yours to make — choose \"stuck\" and explain why in your artifact. Stuck is a perfectly good answer and costs a human one glance. A speculative push costs a full CI run and burns one of three attempts.\n\nDo not commit, push, create a branch, or open a worktree — the harness does all of that.",
  "model": "sonnet",
  "fallback_model": null,
  "allowed_tools": ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
  "allowed_outcomes": ["done", "stuck"],
  "timeout": null
}
```

- [ ] **Step 7: Verify the migrated root builds**

There is no `validate` subcommand — `harness` exposes `init`, `submit`, `run`, `agent`, `service`, `update`. Compile the process files directly instead:

```bash
cd ~/harness_v2 && PYTHONPATH=src python -c "
from pathlib import Path
from harness.drivers.fs_processes import FilesystemProcessRepository
repo = FilesystemProcessRepository(Path.home() / 'harness-root' / 'processes')
print(repo)
"
```

Read `fs_processes.py`'s `build()` signature and pass what it needs (it takes the check registry and the workflow names). If wiring that up by hand is more trouble than it is worth, the equivalent smoke test is Step 5's restart: start the service and check `~/harness-root/logs/harness.log` — **not** `~/harness-root/harness.log`, which is stale since 2026-07-20 and reads as a live OAuth outage. A `MissingCredential` warning for `github-unhealthy-prs` is expected and harmless when `GITHUB_TOKEN` is absent from the service environment; an "unknown check" line is the failure this step exists to catch.

- [ ] **Step 8: Hand back**

Report to the operator: the branch is merged and released, `~/harness-root` is migrated, the service is back up, and the first live tick will scan **every** open PR across all three repos in `repos.json` — `Anela.Heblo`, `harness_v2`, `personal_assistant`.
