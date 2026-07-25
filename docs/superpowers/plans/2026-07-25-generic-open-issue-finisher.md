# Generic `open-issue` Finisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `open-issue` from the healer's hardcoded outbound leg into a real finisher kind driven by its `FinisherBinding.config`, able to file 0..N issues on a repo derived from the task — and make `harness run` serve every workflow file on disk instead of taking `--workflow` / `--all-workflows`.

**Architecture:** `OpenIssueBehavior` stops knowing anything about healing. It reads a fenced ` ```json ` array of issue drafts from a step's artifact (parsing lives in a new pure module, `src/harness/issue_drafts.py`), derives the GitHub slug from `task.repository` through an injected callable, and opens one issue per draft through the existing per-issue `IssueTracker` port. Config selects the shape: `from_step` present → the finisher replaces the bound step's behavior (the healer); absent → it wraps the step's own agent (the arch-review routine). The healer becomes one binding of the generic kind.

**Tech Stack:** Python 3.11, pytest, stdlib only (`json`, `re`, `hashlib`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-25-generic-open-issue-finisher-design.md`

## Global Constraints

- **Project language is English — always.** Code, comments, docstrings, string literals, tests, docs, commit messages. Never another language, even when a prompt or issue is in one.
- **Commit straight into `main`.** No branch, no PR, don't ask. This is the harness's own repo.
- **Conventional commits are load-bearing** — `.github/workflows/release.yml` runs python-semantic-release on every push to `main`. Use `feat:` / `fix:` / `refactor:` / `docs:` / `test:` prefixes. A breaking change needs `!` or a `BREAKING CHANGE:` footer.
- **Run tests with `.venv/bin/pytest -q`** from the repo root (`/Users/rem/harness_v2`). Never `pytest` off `PATH`.
- **Unit and integration tests use in-memory drivers and `FakeClock`** — no disk, no real waiting. Never write a test that sleeps in real time.
- **`behaviors/` must not import `drivers/`.** Guarded by `tests/test_architecture.py::test_behaviors_import_only_ports_not_drivers`. This is why the slug resolution is injected as a callable rather than done in the behavior.
- **Invariants 9/26 hold:** the worker opens the issue, never the LLM. The agent only drafts.
- Every task ends green: `.venv/bin/pytest -q` passes before you commit.

---

### Task 1: Thread `scope_label` through the issue port and its trackers

The label an issue carries — and the label the idempotency search scopes to — is currently the module constant `SELF_HEAL_LABEL`. Make it a per-call value so a binding can choose it.

**Files:**
- Modify: `src/harness/ports/issues.py` (the `open_issue` abstract method)
- Modify: `src/harness/drivers/memory.py:386-410` (`MemoryIssueTracker.open_issue`)
- Modify: `src/harness/drivers/github_issues.py` (whole file)
- Modify: `src/harness/drivers/github_client.py` (`search_issue_by_marker` in the ABC ~line 135, `FakeGithubClient` ~line 296, `HttpGithubClient` ~line 542)
- Test: `tests/test_issue_memory.py`, `tests/test_github_issues.py`, `tests/test_issue_port.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_issue_memory.py`:

```python
def test_scope_label_is_carried_onto_the_opened_issue():
    tracker = MemoryIssueTracker()

    tracker.open_issue(
        "onpaj/harness_v2",
        title="A finding",
        body="body",
        labels=("tech-debt",),
        marker="tsk_1:abcd1234",
        scope_label="arch-review",
    )

    assert tracker.opened[0]["labels"] == ("tech-debt", "arch-review")
    assert tracker.opened[0]["scope_label"] == "arch-review"


def test_the_same_marker_under_a_different_scope_label_is_a_different_issue():
    tracker = MemoryIssueTracker()
    for scope in ("arch-review", "harness:self-heal"):
        tracker.open_issue(
            "onpaj/harness_v2",
            title="A finding",
            body="body",
            labels=(),
            marker="tsk_1:abcd1234",
            scope_label=scope,
        )

    assert len(tracker.opened) == 2
```

Add to `tests/test_github_issues.py`:

```python
def test_the_marker_search_is_scoped_to_the_binding_label():
    client = FakeGithubClient()
    tracker = GithubIssueTracker(client)

    tracker.open_issue(
        "onpaj/harness_v2",
        title="A finding",
        body="body",
        labels=("tech-debt",),
        marker="tsk_1:abcd1234",
        scope_label="arch-review",
    )
    opened = client.list_issues("onpaj/harness_v2", label="arch-review")

    assert len(opened) == 1
    assert "<!-- harness-issue:tsk_1:abcd1234 -->" in opened[0].body
    assert "harness:self-heal" not in opened[0].labels
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_issue_memory.py tests/test_github_issues.py -q`
Expected: FAIL — `open_issue() got an unexpected keyword argument 'scope_label'`

- [ ] **Step 3: Widen the port**

In `src/harness/ports/issues.py`, add the parameter and restate the idempotency contract:

```python
    @abstractmethod
    def open_issue(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...],
        marker: str,
        scope_label: str,
    ) -> IssueRef:
        """Open an issue on `repo`, carrying `scope_label` plus `labels`.

        **Idempotent per `(repo, scope_label, marker)`.** If an open issue on
        `repo` carrying `scope_label` already has the marker, return that one
        instead of creating a second — the twin of `GithubForge` matching an
        existing PR on `head=owner:branch`. `scope_label` is both a label every
        issue from a binding carries and the scope the marker search reads, so
        two bindings filing into the same repo never see each other's issues.
        Raises `IssueError` on failure.
        """
```

- [ ] **Step 4: Update `MemoryIssueTracker`**

In `src/harness/drivers/memory.py`, replace the body of `open_issue`:

```python
    def open_issue(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...],
        marker: str,
        scope_label: str,
    ) -> IssueRef:
        for existing in self.opened:
            if (
                existing["repo"] == repo
                and existing["marker"] == marker
                and existing["scope_label"] == scope_label
            ):
                return existing["ref"]
        number = len(self.opened) + 1
        ref = IssueRef(number=number, url=f"https://forge.local/{repo}/issues/{number}")
        self.opened.append(
            {
                "repo": repo,
                "title": title,
                "body": body,
                "labels": tuple(dict.fromkeys((*labels, scope_label))),
                "marker": marker,
                "scope_label": scope_label,
                "ref": ref,
            }
        )
        return ref
```

- [ ] **Step 5: Update `GithubClient.search_issue_by_marker` in all three implementations**

In `src/harness/drivers/github_client.py`, the ABC:

```python
    @abstractmethod
    def search_issue_by_marker(self, repo: str, marker: str, *, label: str) -> Issue | None:
        """The open issue carrying `label` whose body contains `marker`, or None.

        Keeps issue creation idempotent without the Search API — it scans the
        `label`-carrying open issues and matches the marker in the body.
        """
```

In `FakeGithubClient` and in `HttpGithubClient`, both bodies become:

```python
    def search_issue_by_marker(self, repo: str, marker: str, *, label: str) -> Issue | None:
        for issue in self.list_issues(repo, label=label):
            if marker in issue.body:
                return issue
        return None
```

Leave `SELF_HEAL_LABEL = "harness:self-heal"` defined, but change its docstring to `"""The label the seeded heal workflow's open-issue binding uses."""` — it is no longer forced onto anything by the driver.

- [ ] **Step 6: Update `GithubIssueTracker`**

Replace `src/harness/drivers/github_issues.py` in full:

```python
"""GithubIssueTracker — opens an issue on GitHub, idempotently.

Reuses `GithubClient` (the same low-level client the source and forge drivers
use). Idempotency is by an embedded marker: a per-issue key written into the
body as an HTML comment. Before creating, it searches the open issues carrying
`scope_label` for that marker and returns the existing one if found — the twin
of `GithubForge` matching an existing PR on `head=owner:branch`.
"""

from __future__ import annotations

import urllib.error

from harness.drivers.github_client import GithubClient
from harness.ports.issues import IssueError, IssueRef, IssueTracker


def marker_comment(marker: str) -> str:
    """The hidden idempotency marker embedded in an opened issue's body."""
    return f"<!-- harness-issue:{marker} -->"


class GithubIssueTracker(IssueTracker):
    def __init__(self, client: GithubClient) -> None:
        self._client = client

    def open_issue(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...],
        marker: str,
        scope_label: str,
    ) -> IssueRef:
        try:
            existing = self._client.search_issue_by_marker(
                repo, marker_comment(marker), label=scope_label
            )
            if existing is not None:
                return IssueRef(number=existing.number, url=existing.url)

            # Always carry the scope label (the search scopes to it) plus the
            # hidden marker, so a re-run finds this issue instead of opening a second.
            all_labels = tuple(dict.fromkeys((*labels, scope_label)))
            issue = self._client.create_issue(
                repo,
                title=title,
                body=f"{body}\n\n{marker_comment(marker)}\n",
                labels=all_labels,
            )
            return IssueRef(number=issue.number, url=issue.url)
        except urllib.error.HTTPError as error:
            raise IssueError(f"opening the issue on {repo} failed: {error}") from error
        except urllib.error.URLError as error:
            raise IssueError(f"opening the issue on {repo} failed: {error}") from error
```

- [ ] **Step 7: Fix every remaining call site**

Run: `grep -rn "open_issue(\|search_issue_by_marker" src tests`

Add the literal `scope_label="harness:self-heal"` to every existing `open_issue(` call in `src/` and `tests/` that does not yet pass it — call sites live in `tests/test_issue_port.py`, `tests/test_github_issue_checker.py`, `tests/test_issue_memory.py`, `tests/test_github_issues.py`, `tests/test_open_issue_behavior.py` and `src/harness/behaviors/open_issue.py`. The behavior is fully rewritten in Task 3; here it only needs to keep compiling, so pass the literal rather than importing `SELF_HEAL_LABEL`. Add `label="harness:self-heal"` to every existing `search_issue_by_marker(` call in `tests/test_github_issues.py` and `tests/test_github_client.py`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/harness/ports/issues.py src/harness/drivers/memory.py src/harness/drivers/github_issues.py src/harness/drivers/github_client.py src/harness/behaviors/open_issue.py tests/
git commit -m "refactor!: IssueTracker.open_issue takes an explicit scope_label

The label an issue carries, and the label the marker search scopes to, stop
being the SELF_HEAL_LABEL constant. The marker prefix becomes harness-issue:.
Preparation for a generic open-issue finisher.

BREAKING CHANGE: IssueTracker.open_issue and GithubClient.search_issue_by_marker
gained a required keyword argument."
```

---

### Task 2: A pure `issue_drafts` module

The parsing of the fenced JSON array and the marker derivation are pure functions with no I/O. They live in their own top-level module — the precedent is `artifacts_layout.py` / `ids.py`, which import nothing from the `harness` package.

**Files:**
- Create: `src/harness/issue_drafts.py`
- Test: `tests/test_issue_drafts.py`

**Interfaces:**
- Produces: `IssueDraft(title: str, body: str = "", labels: tuple[str, ...] = ())`; `parse_drafts(artifact: str) -> list[IssueDraft]`; `marker_for(task_id: str, title: str) -> str`; `DraftError(ValueError)`. Task 3 consumes all four.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_issue_drafts.py`:

```python
"""`issue_drafts` — the fenced-JSON draft contract, parsed."""

import pytest

from harness.issue_drafts import DraftError, IssueDraft, marker_for, parse_drafts

ARTIFACT = """# Architecture Review: Analytics

Some prose the human reads.

```json
[
  {"title": "Analytics: handler does too much", "body": "## Finding\\n...", "labels": ["tech-debt"]},
  {"title": "Analytics: DTO is a record", "body": "breaks client generation"}
]
```
"""


def test_parses_every_draft_in_the_last_fenced_block():
    drafts = parse_drafts(ARTIFACT)

    assert drafts == [
        IssueDraft(
            title="Analytics: handler does too much",
            body="## Finding\n...",
            labels=("tech-debt",),
        ),
        IssueDraft(
            title="Analytics: DTO is a record",
            body="breaks client generation",
            labels=(),
        ),
    ]


def test_an_empty_array_is_a_valid_zero_issue_report():
    assert parse_drafts("All clean.\n\n```json\n[]\n```\n") == []


def test_an_empty_artifact_is_zero_drafts_not_an_error():
    """The step wrote no file at all — heal's `skip` path."""
    assert parse_drafts("") == []
    assert parse_drafts("   \n") == []


def test_a_report_with_no_fenced_block_is_an_error():
    with pytest.raises(DraftError, match="no fenced json block"):
        parse_drafts("# A report with no machine-readable block\n")


def test_a_block_that_is_not_an_array_is_an_error():
    with pytest.raises(DraftError, match="must be a JSON array"):
        parse_drafts('```json\n{"title": "not an array"}\n```')


def test_broken_json_is_an_error():
    with pytest.raises(DraftError, match="is not valid JSON"):
        parse_drafts("```json\n[{,}]\n```")


def test_a_draft_without_a_title_is_an_error():
    with pytest.raises(DraftError, match="draft 1 has no title"):
        parse_drafts('```json\n[{"title": "ok"}, {"body": "no title"}]\n```')


def test_the_last_block_wins():
    """An agent that showed an example earlier in its report must not confuse us."""
    artifact = (
        '```json\n[{"title": "an example, not a finding"}]\n```\n'
        "\nActual findings:\n\n"
        '```json\n[{"title": "the real one"}]\n```\n'
    )

    assert [d.title for d in parse_drafts(artifact)] == ["the real one"]


def test_the_marker_is_task_scoped_and_title_content_scoped():
    first = marker_for("tsk_abc", "A finding")
    again = marker_for("tsk_abc", "A finding")
    other_title = marker_for("tsk_abc", "A different finding")
    other_task = marker_for("tsk_xyz", "A finding")

    assert first == again
    assert first.startswith("tsk_abc:")
    assert first != other_title
    assert first != other_task
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_issue_drafts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.issue_drafts'`

- [ ] **Step 3: Write the module**

Create `src/harness/issue_drafts.py`:

```python
"""The issue drafts a step's artifact carries — parsed. A pure domain utility.

A step writes an ordinary markdown report and ends it with a fenced ```json
block holding an **array** of issue drafts. This module turns that text into
`IssueDraft`s and derives each draft's idempotency marker.

Two deliberate asymmetries:

- **An empty artifact is zero drafts, not an error.** "The step wrote no file"
  is a legitimate report (the healer's `skip` path). A *non-empty* artifact
  with no readable block is an error — a persona that wrote a report but
  malformed its block is a real fault worth surfacing.
- **The last fenced block wins**, mirroring `_extract_verdict`'s rule for the
  agent's final message, so an example earlier in the report cannot be
  mistaken for the findings.

The module imports nothing from the `harness` package — like `models`,
`ids` and `artifacts_layout`. That is also why `_FENCED_JSON` is a local copy
of the regex `drivers/claude_cli.py` uses: the convention is shared by design,
but this module must stay driver-free.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha1

_FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class IssueDraft:
    """One issue a step proposes. `labels` are the agent's suggestions — the
    finisher filters them against its binding's allowlist before sending."""

    title: str
    body: str = ""
    labels: tuple[str, ...] = ()


class DraftError(ValueError):
    """The artifact does not carry a readable array of drafts."""


def parse_drafts(artifact: str) -> list[IssueDraft]:
    """The drafts in `artifact`'s last fenced json block.

    An empty/blank artifact yields `[]`. Anything else that cannot be read as
    an array of `{title, body?, labels?}` objects raises `DraftError`.
    """
    if not artifact.strip():
        return []

    blocks = _FENCED_JSON.findall(artifact)
    if not blocks:
        raise DraftError("the artifact has no fenced json block of issue drafts")

    try:
        raw = json.loads(blocks[-1])
    except (json.JSONDecodeError, ValueError) as error:
        raise DraftError(f"the artifact's json block is not valid JSON: {error}") from None

    if not isinstance(raw, list):
        raise DraftError(
            f"the artifact's json block must be a JSON array of drafts, got "
            f"{type(raw).__name__}"
        )

    drafts = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DraftError(f"draft {index} is not an object")
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise DraftError(f"draft {index} has no title")
        body = item.get("body", "")
        labels = item.get("labels", [])
        drafts.append(
            IssueDraft(
                title=title.strip(),
                body=body if isinstance(body, str) else "",
                labels=tuple(str(label) for label in labels)
                if isinstance(labels, list)
                else (),
            )
        )
    return drafts


def marker_for(task_id: str, title: str) -> str:
    """A draft's idempotency marker: `<task id>:<8 hex of sha1(title)>`.

    Task-scoped, so a re-run of the same task re-finds the issues it already
    opened. Content-scoped within the task, so reordered findings still match
    the right issue — which a positional `task:index` key would not.
    """
    digest = sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{task_id}:{digest}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_issue_drafts.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/harness/issue_drafts.py tests/test_issue_drafts.py
git commit -m "feat: parse a step's issue drafts from a fenced json array"
```

---

### Task 3: Rewrite `OpenIssueBehavior` as a config-driven finisher

**Files:**
- Modify: `src/harness/behaviors/open_issue.py` (full rewrite)
- Test: `tests/test_open_issue_behavior.py` (full rewrite)

**Interfaces:**
- Consumes: `IssueDraft`, `parse_drafts`, `marker_for`, `DraftError` from Task 2; `IssueTracker.open_issue(..., scope_label=)` from Task 1.
- Produces: `OpenIssueBehavior(*, tracker: IssueTracker, artifacts: ArtifactView, slug_for: Callable[[str | None], str], label: str, from_step: str | None = None, allowed_labels: tuple[str, ...] = (), inner: ConsumerBehavior | None = None)`. Task 4 constructs it.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_open_issue_behavior.py` in full:

```python
"""`OpenIssueBehavior` — the generic `open-issue` finisher kind.

Config drives everything: which step's artifact to read (`from_step`, whose
presence also selects replace-vs-wrap), which label to carry and scope the
marker search to, and which per-draft labels are allowed through.
"""

import pytest

from harness.behaviors.open_issue import OpenIssueBehavior
from harness.drivers.memory import MemoryArtifactStore, MemoryIssueTracker
from harness.issue_drafts import marker_for
from harness.models import DONE, REQUEST_CHANGES, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.issues import IssueError, IssueTracker


def block(*drafts: str) -> str:
    return "# A report\n\nprose\n\n```json\n[" + ", ".join(drafts) + "]\n```\n"


DRAFT = '{"title": "A finding", "body": "the body", "labels": ["tech-debt"]}'


def task(task_id="tsk_1", *, step="review", repository="Anela.Heblo") -> Task:
    return Task(
        id=task_id,
        created="2026-07-25T06:00:00Z",
        workflow_template="arch-review",
        status=step,
        repository=repository,
    )


def slug_for(name):
    if name is None:
        raise IssueError("the task has no repository")
    return f"onpaj/{name}"


def make(*, tracker, artifacts, label="arch-review", **kwargs) -> OpenIssueBehavior:
    return OpenIssueBehavior(
        tracker=tracker,
        artifacts=artifacts,
        slug_for=slug_for,
        label=label,
        **kwargs,
    )


class StubInner(ConsumerBehavior):
    """Stands in for the step's own agent behavior in the wrap shape."""

    def __init__(self, outcome=DONE, *, artifacts=None, task_id="tsk_1", text=block(DRAFT)):
        self.outcome = outcome
        self.ran = False
        self._artifacts = artifacts
        self._task_id = task_id
        self._text = text

    async def run(self, task):
        self.ran = True
        if self._artifacts is not None:
            self._artifacts.begin(self._task_id, "review").put("review-01.md", self._text)
        return BehaviorResult(self.outcome, "the agent ran")


# --- wrap shape (no from_step) ------------------------------------------


async def test_wrap_runs_the_step_then_files_what_it_wrote():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    inner = StubInner(artifacts=artifacts)
    behavior = make(tracker=tracker, artifacts=artifacts, inner=inner)

    result = await behavior.run(task())

    assert inner.ran
    assert result.outcome == DONE
    assert len(tracker.opened) == 1
    opened = tracker.opened[0]
    assert opened["repo"] == "onpaj/Anela.Heblo"
    assert opened["title"] == "A finding"
    assert opened["marker"].startswith("tsk_1:")
    assert opened["scope_label"] == "arch-review"


async def test_wrap_returns_the_inner_outcome_unchanged():
    """Routing is the workflow's business — the finisher never rewrites it."""
    artifacts = MemoryArtifactStore()
    inner = StubInner(REQUEST_CHANGES, artifacts=artifacts)
    behavior = make(tracker=MemoryIssueTracker(), artifacts=artifacts, inner=inner)

    result = await behavior.run(task())

    assert result.outcome == REQUEST_CHANGES


async def test_wrap_files_several_issues_from_one_report():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    text = block(DRAFT, '{"title": "Another finding"}', '{"title": "A third"}')
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts, text=text)
    )

    result = await behavior.run(task())

    assert len(tracker.opened) == 3
    assert "3 issues" in result.summary


async def test_an_empty_array_files_nothing_and_still_succeeds():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts, text=block())
    )

    result = await behavior.run(task())

    assert tracker.opened == []
    assert result.outcome == DONE
    assert "no issues to file" in result.summary


async def test_an_inner_failure_propagates_and_files_nothing():
    class Boom(ConsumerBehavior):
        async def run(self, task):
            raise RuntimeError("the agent died")

    tracker = MemoryIssueTracker()
    behavior = make(tracker=tracker, artifacts=MemoryArtifactStore(), inner=Boom())

    with pytest.raises(RuntimeError):
        await behavior.run(task())
    assert tracker.opened == []


# --- replace shape (from_step) -------------------------------------------


async def test_replace_reads_a_named_earlier_step_and_never_runs_an_agent():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_heal", "heal").put("heal-01.md", block('{"title": "Fix the driver"}'))
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker,
        artifacts=artifacts,
        label="harness:self-heal",
        from_step="heal",
    )

    result = await behavior.run(task("tsk_heal", step="file-issue", repository="harness_v2"))

    assert result.outcome == DONE
    assert tracker.opened[0]["title"] == "Fix the driver"
    assert tracker.opened[0]["scope_label"] == "harness:self-heal"


async def test_replace_with_no_artifact_files_nothing():
    """The healer's `skip` path: the persona wrote no file at all."""
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker,
        artifacts=MemoryArtifactStore(),
        label="harness:self-heal",
        from_step="heal",
    )

    result = await behavior.run(task("tsk_heal", step="file-issue"))

    assert tracker.opened == []
    assert result.outcome == DONE


async def test_replace_reads_the_latest_attempt():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_heal", "heal").put("heal-01.md", block('{"title": "first pass"}'))
    artifacts.begin("tsk_heal", "heal").put("heal-02.md", block('{"title": "second pass"}'))
    tracker = MemoryIssueTracker()
    behavior = make(tracker=tracker, artifacts=artifacts, from_step="heal")

    await behavior.run(task("tsk_heal", step="file-issue"))

    assert tracker.opened[0]["title"] == "second pass"


# --- labels, markers, errors ---------------------------------------------


async def test_per_draft_labels_are_filtered_against_the_allowlist():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    text = block('{"title": "A finding", "labels": ["tech-debt", "invented-label"]}')
    behavior = make(
        tracker=tracker,
        artifacts=artifacts,
        allowed_labels=("tech-debt", "refactoring"),
        inner=StubInner(artifacts=artifacts, text=text),
    )

    result = await behavior.run(task())

    assert tracker.opened[0]["labels"] == ("tech-debt", "arch-review")
    assert "invented-label" in result.summary


async def test_with_no_allowlist_no_per_draft_label_gets_through():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts)
    )

    await behavior.run(task())

    assert tracker.opened[0]["labels"] == ("arch-review",)


async def test_a_rerun_with_the_same_titles_opens_nothing_new():
    tracker = MemoryIssueTracker()
    for _ in range(2):
        artifacts = MemoryArtifactStore()
        behavior = make(
            tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts)
        )
        await behavior.run(task())

    assert len(tracker.opened) == 1


async def test_a_rerun_after_a_partial_failure_resumes():
    """Issue 1 was opened before issue 2 blew up; the re-run must not duplicate it."""
    tracker = MemoryIssueTracker()
    artifacts = MemoryArtifactStore()
    text = block('{"title": "first"}', '{"title": "second"}')
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts, text=text)
    )
    tracker.open_issue(
        "onpaj/Anela.Heblo",
        title="first",
        body="",
        labels=(),
        marker=marker_for("tsk_1", "first"),
        scope_label="arch-review",
    )

    await behavior.run(task())

    assert len(tracker.opened) == 2
    assert [o["title"] for o in tracker.opened] == ["first", "second"]


async def test_a_malformed_block_raises_issue_error():
    artifacts = MemoryArtifactStore()
    behavior = make(
        tracker=MemoryIssueTracker(),
        artifacts=artifacts,
        inner=StubInner(artifacts=artifacts, text="# A report with no block\n"),
    )

    with pytest.raises(IssueError, match="fenced json block"):
        await behavior.run(task())


async def test_an_unresolvable_repository_raises_issue_error():
    artifacts = MemoryArtifactStore()
    behavior = make(
        tracker=MemoryIssueTracker(),
        artifacts=artifacts,
        inner=StubInner(artifacts=artifacts),
    )

    with pytest.raises(IssueError, match="no repository"):
        await behavior.run(task(repository=None))


async def test_a_tracker_error_propagates_uncaught():
    """`Consumer.tick` turns this into `failed/`; the recursion guard, not
    in-behavior handling, is what stops a loop."""

    class RaisingTracker(IssueTracker):
        def open_issue(self, repo, *, title, body, labels, marker, scope_label):
            raise IssueError("no token")

    artifacts = MemoryArtifactStore()
    behavior = make(
        tracker=RaisingTracker(),
        artifacts=artifacts,
        inner=StubInner(artifacts=artifacts),
    )

    with pytest.raises(IssueError):
        await behavior.run(task())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_open_issue_behavior.py -q`
Expected: FAIL — `OpenIssueBehavior.__init__() got an unexpected keyword argument 'slug_for'`

- [ ] **Step 3: Rewrite the behavior**

Replace `src/harness/behaviors/open_issue.py` in full:

```python
"""`OpenIssueBehavior` — the `open-issue` finisher kind (ADR-0016, ADR-0018).

The worker half of "an agent drafts, the harness files" (invariants 9/26): a
step's persona writes issue drafts into its artifact and returns a verdict;
this finisher reads them and calls `IssueTracker.open_issue`. The persona
never opens anything itself.

Everything about *which* issues, on *which* repo, under *which* label is the
binding's `config` — this class has no notion of healing, reviewing, or any
other purpose:

- `from_step` names the step whose artifact holds the drafts. Its **presence
  selects the shape**: given, the finisher fully *replaces* the bound step's
  behavior (the healer's agent-less `file-issue` step); omitted, it *wraps*
  the step's own behavior — `inner` runs first, then its artifact is filed.
- `label` is carried by every issue and is the scope the idempotency search
  reads.
- `allowed_labels` is the allowlist a draft's own labels are filtered against,
  so a hallucinated label cannot 422 the whole step.

The repository is derived from the *task*, not from wiring: `slug_for` is an
injected callable (`task.repository` → `owner/repo`). It is injected rather
than imported because resolving a slug reads a clone's git remote — a driver —
and `behaviors/` may not import `drivers/` (`test_architecture.py`).

No error handling of its own: `Consumer.tick()` already wraps `behavior.run()`
in a blanket `except Exception` -> `_fail`, so an `IssueError` here lands the
task in `failed/` exactly like an agent exception does.
"""

from __future__ import annotations

from typing import Callable

from harness.issue_drafts import DraftError, marker_for, parse_drafts
from harness.models import DONE, BehaviorResult, Task
from harness.ports.artifacts import ArtifactView
from harness.ports.behavior import ConsumerBehavior
from harness.ports.issues import IssueError, IssueTracker


class OpenIssueBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        tracker: IssueTracker,
        artifacts: ArtifactView,
        slug_for: Callable[[str | None], str],
        label: str,
        from_step: str | None = None,
        allowed_labels: tuple[str, ...] = (),
        inner: ConsumerBehavior | None = None,
    ) -> None:
        self._tracker = tracker
        self._artifacts = artifacts
        self._slug_for = slug_for
        self._label = label
        self._from_step = from_step
        self._allowed_labels = allowed_labels
        self._inner = inner

    async def run(self, task: Task) -> BehaviorResult:
        inner_result = None
        if self._inner is not None:
            inner_result = await self._inner.run(task)

        step = self._from_step or task.status or ""
        repo = self._slug_for(task.repository)

        try:
            drafts = parse_drafts(self._latest_artifact(task.id, step))
        except DraftError as error:
            raise IssueError(f"step {step!r} of task {task.id}: {error}") from None

        refs = []
        dropped: list[str] = []
        for draft in drafts:
            allowed = tuple(
                label for label in draft.labels if label in self._allowed_labels
            )
            dropped.extend(
                label for label in draft.labels if label not in self._allowed_labels
            )
            refs.append(
                self._tracker.open_issue(
                    repo,
                    title=draft.title,
                    body=draft.body,
                    labels=allowed,
                    marker=marker_for(task.id, draft.title),
                    scope_label=self._label,
                )
            )

        outcome = inner_result.outcome if inner_result is not None else DONE
        return BehaviorResult(outcome, _summary(refs, dropped))

    def _latest_artifact(self, task_id: str, step: str) -> str:
        """The step's highest-attempt artifact, or "" when it wrote none."""
        refs = [ref for ref in self._artifacts.list(task_id) if ref.step == step]
        if not refs:
            return ""
        latest = max(refs, key=lambda ref: ref.attempt)
        return self._artifacts.read(task_id, step, latest.attempt, latest.name) or ""


def _summary(refs, dropped: list[str]) -> str:
    if not refs:
        summary = "no issues to file"
    else:
        numbers = ", ".join(f"#{ref.number}" for ref in refs)
        noun = "issue" if len(refs) == 1 else "issues"
        summary = f"opened {len(refs)} {noun}: {numbers}"
    if dropped:
        unique = ", ".join(sorted(set(dropped)))
        summary = f"{summary} (dropped labels outside the allowlist: {unique})"
    return summary
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_open_issue_behavior.py -q`
Expected: PASS (16 tests)

- [ ] **Step 5: Verify the architecture guard still holds**

Run: `.venv/bin/pytest tests/test_architecture.py -q`
Expected: PASS. If `test_behaviors_import_only_ports_not_drivers` fails, the behavior imported a driver — the slug must stay injected.

- [ ] **Step 6: Commit**

```bash
git add src/harness/behaviors/open_issue.py tests/test_open_issue_behavior.py
git commit -m "feat: open-issue finisher files 0..N issues, driven by its binding config

from_step selects replace-vs-wrap, label scopes the idempotency search,
allowed_labels filters the agent's per-draft labels, and the repo is derived
from task.repository through an injected slug resolver."
```

---

### Task 4: Wire the config-driven factory and rebind the healer

**Files:**
- Modify: `src/harness/cli.py:152` (`HEAL_DEFINITION["finishers"]`)
- Modify: `src/harness/cli.py:410-440` (`_HEALER_PERSONA`)
- Modify: `src/harness/cli.py:1662-1745` (the finisher wiring block)
- Test: `tests/test_cli.py`, `tests/test_self_heal_e2e.py`

**Interfaces:**
- Consumes: `OpenIssueBehavior(...)` from Task 3.
- Produces: `_slug_resolver(registry) -> Callable[[str | None], str]` in `cli.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_open_issue_is_registered_without_any_heal_configuration(monkeypatch, tmp_path):
    """The finisher kind no longer depends on a heal repo — serving the seeded
    heal workflow must not need any flag or env var."""
    main(["init", "--root", str(tmp_path)])

    async def fake_serve(harness, port, poll_interval, source_interval=30.0,
                         pr_poll_interval=0.0, reconcile_interval=300.0, registry=None):
        return None

    monkeypatch.setattr("harness.cli.serve", fake_serve)
    monkeypatch.delenv("HARNESS_HEAL_REPO", raising=False)

    assert main(["run", "--root", str(tmp_path)]) == 0


def test_a_binding_without_a_label_fails_the_build(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    path = tmp_path / "workflows" / "review.json"
    path.write_text(json.dumps({
        "name": "review",
        "start": "review",
        "transitions": [{"from": "review", "on": "done", "to": "end"}],
        "finishers": {"review": {"kind": "open-issue"}},
    }))

    assert main(["run", "--root", str(tmp_path)]) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -k "open_issue_is_registered or without_a_label" -q`
Expected: FAIL — the first exits 2 (unknown finisher kind `open-issue`), the second exits 0.

- [ ] **Step 3: Add the slug resolver to `cli.py`**

Add near the other module-level helpers in `src/harness/cli.py`:

```python
def _slug_resolver(registry: RepositoryRegistry) -> Callable[[str | None], str]:
    """`task.repository` → `owner/repo`, the way every other GitHub-touching
    driver does it: resolve the name to a clone through the registry, then read
    the slug off that clone's `origin`. `repos.json` holds paths only — the
    slug is never duplicated in config. Every failure is an `IssueError`, so it
    lands the task in `failed/` with a message naming the cause."""

    def slug_for(name: str | None) -> str:
        if not name:
            raise IssueError(
                "the task has no repository, so no GitHub repo can be resolved "
                "— set the process's params.repository"
            )
        try:
            path = registry.resolve(name)
        except RepositoryNotFound as error:
            raise IssueError(str(error)) from None
        slug = github_slug(path)
        if slug is None:
            raise IssueError(f"repo {name!r} ({path}) has no github.com origin remote")
        return slug

    return slug_for
```

Add `from harness.ports.issues import IssueError` and `from typing import Callable` to the imports if not already present.

- [ ] **Step 4: Replace the finisher wiring**

In `src/harness/cli.py`, delete the whole `if heal_repo:` block (currently ~1694-1742) and put this immediately after `artifact_view = WorktreeArtifactView(layout.worktrees)` (line 1662):

```python
    # The `open-issue` finisher kind, registered unconditionally: it derives
    # its repo from `task.repository` and takes its label from the binding, so
    # it needs no wiring-time configuration at all. That is what lets a root
    # serve the seeded `heal` workflow without any heal-specific setup.
    issue_token = os.environ.get("GITHUB_TOKEN")
    issue_tracker = (
        GithubIssueTracker(HttpGithubClient(issue_token))
        if issue_token
        else MemoryIssueTracker()
    )
    slug_for = _slug_resolver(registry)

    def _open_issue(step, config, inner):
        label = config.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(
                f"step {step!r} binds the 'open-issue' finisher without a "
                f"'label' — it is both the label every issue carries and the "
                f"scope of the idempotency search"
            )
        from_step = config.get("from_step")
        return OpenIssueBehavior(
            tracker=issue_tracker,
            artifacts=artifact_view,
            slug_for=slug_for,
            label=label,
            from_step=from_step,
            allowed_labels=tuple(config.get("allowed_labels", ())),
            # Replace shape when a step is named; wrap shape otherwise. The
            # thunk is only called in the wrap shape, so a step bound in the
            # replace shape never triggers `catalog.get` (ADR-0018).
            inner=None if from_step else inner(),
        )

    finishers: dict[
        str, Callable[[str, dict, Callable[[], ConsumerBehavior]], ConsumerBehavior]
    ] = {"open-issue": _open_issue}
```

The local is named `issue_token`, not `token`, on purpose: `_run` already binds `token = os.environ.get("GITHUB_TOKEN")` further down (~line 1764) for the `github_client` that threads into the process check factories. Leave that one exactly as it is — two reads of the same env var in one function is the existing "one client per wiring site" shape, and shadowing it here would be a subtle reordering hazard.

- [ ] **Step 5: Rebind the healer and update its persona**

In `src/harness/cli.py`, change `HEAL_DEFINITION`'s last key (line 152):

```python
    "finishers": {
        "file-issue": {
            "kind": "open-issue",
            "from_step": "heal",
            "label": "harness:self-heal",
        }
    },
```

In `_HEALER_PERSONA`, replace the drafting paragraph (the one beginning `"For a harness bug or an operational/tuning problem, draft a proposed "`) with:

```python
    "For a harness bug or an operational/tuning problem, write your diagnosis "
    "to the file the harness told you to write your output to above, and end "
    "that file with a fenced ```json block holding a one-element array:\n"
    '```json\n'
    '[{"title": "<concise title>", "body": "<diagnosis, then a concrete '
    'proposed change>"}]\n'
    "```\n"
    "The harness reads that block by machine and opens the issue itself — you "
    "must never open one. For an operational/tuning problem, recommend "
    "diagnostically rather than prescriptively: name the exceeded budget and "
    "the two levers available — raising the step's per-agent `timeout`, or "
    "decomposing the step into smaller ones — without prescribing a specific "
    "number. Then finish with the outcome that files it.\n\n"
```

- [ ] **Step 6: Run the full suite and fix fallout**

Run: `.venv/bin/pytest -q`

`tests/test_self_heal_e2e.py` carries a **`@pytest.mark.xfail(strict=True)`** tripwire added during Task 3's fix wave: its real assertions (one issue opened, the marker, the `Origin:` footer) were restored but expected to fail, because the heal persona still wrote prose. This task is what closes that gap, so:

1. Update the artifact the e2e's fake agent writes to the fenced-JSON form. **The body must carry the `Origin:` line** — the restored assertion requires `"Origin: https://gh/i/9"` in the filed body, and origin-linking left the generic behavior when it stopped being heal-specific, so the drafted body is now the only thing that can supply it:

   ```
   # Fix it

   ```json
   [{"title": "Fix it", "body": "diagnosis\n\nOrigin: https://gh/i/9"}]
   ```
   ```

2. **Remove the `xfail` marker.** Strict mode means the suite FAILS with `XPASS(strict)` the moment the test starts passing — that failure is the tripwire firing correctly, not a regression. Delete the marker and its Task-4 reason; the test must end this task passing normally.
3. The marker assertion keys off the *running heal task's* id (read out of `done/`), not the original failed task's — already correct in the test, don't change it.
4. **Fix the test's docstring while you are there.** It currently claims the fake runner "couldn't write the artifact where `OpenIssueBehavior` would find it" and that the fresh heal task's id is unknowable. Task 3's fix wave disproved both: recover the id from the `cwd` the runner is handed (`/memory/worktrees/<id>`) and write into the shared `MemoryArtifactStore`. Replace the "couldn't" framing with that recipe.

This is a solved problem, not a dead end — the recipe above was demonstrated end to end. Do not re-narrow the assertions and do not re-add the marker.

- [ ] **Step 7: Run the full suite again**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/harness/cli.py tests/
git commit -m "feat: register open-issue unconditionally and rebind the healer to it

The healer becomes one binding of the generic kind
({kind, from_step: heal, label: harness:self-heal}); its persona now emits the
fenced json draft array the finisher reads."
```

---

### Task 5: Serve every workflow on disk; delete `--workflow` and `--all-workflows`

**Files:**
- Modify: `src/harness/cli.py:1363-1391` (`_resolve_served_workflows`)
- Modify: `src/harness/cli.py:1955-1969` (the two `run` arguments)
- Modify: `src/harness/cli.py:1678-1679` (resolver force-add), and the `heal` force-add if Task 4 left one
- Test: `tests/test_cli.py:945, 974, 1001-1050, 1065`

- [ ] **Step 1: Write the failing tests**

Replace the four flag tests in `tests/test_cli.py` (`test_run_all_workflows_serves_every_definition_found`, `test_run_all_workflows_without_heal_repo_fails_fast_on_the_heal_workflow`, `test_run_rejects_workflow_and_all_workflows_together`, `test_run_all_workflows_with_no_definitions_is_a_startup_error`) with:

```python
def test_run_serves_every_workflow_definition_on_disk(monkeypatch, tmp_path):
    """No flag: the served set is exactly what `workflows/` holds."""
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0,
                         pr_poll_interval=0.0, reconcile_interval=300.0, registry=None):
        captured["harness"] = harness

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path)]) == 0
    assert set(captured["harness"].workflows) == {
        "development", "hotfix", "resolver", "heal"
    }


def test_run_with_no_workflow_definitions_is_workflow_less_not_an_error(monkeypatch, tmp_path):
    """FR-6: an empty workflows/ runs the catalog agents directly."""
    (tmp_path / "workflows").mkdir(parents=True)
    (tmp_path / "agents").mkdir(parents=True)
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0,
                         pr_poll_interval=0.0, reconcile_interval=300.0, registry=None):
        captured["harness"] = harness

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path)]) == 0
    assert tuple(captured["harness"].workflows) == ()


def test_the_workflow_selection_flags_are_gone(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit):
        main(["run", "--root", str(tmp_path), "--workflow", "development"])
    with pytest.raises(SystemExit):
        main(["run", "--root", str(tmp_path), "--all-workflows"])
```

Also fix `tests/test_cli.py:945` (`main(["run", ..., "--workflow", "nonexistent"]) == 2`) and the `--workflow hotfix` cases at 974 and 1065 and 1097 — they must drop the flag; where a test needed a *narrowed* set, give the tmp root only the workflow files it wants.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: FAIL — the flags still parse.

- [ ] **Step 3: Collapse `_resolve_served_workflows`**

Replace the whole function in `src/harness/cli.py`:

```python
def _resolve_served_workflows(layout: HarnessLayout) -> tuple[str, ...]:
    """The set of workflow names `harness run` serves: every definition under
    `<root>/workflows/`.

    Serving is data, not configuration — dropping a workflow file into the root
    serves it, and removing it stops serving it. An empty or missing directory
    is workflow-less mode (FR-6): no workflow is served and the catalog agents
    run directly, rather than a startup error.
    """
    return FilesystemWorkflowRepository(layout.workflows).names()
```

Update its one call site (`served_names = _resolve_served_workflows(args, layout)`) to `served_names = _resolve_served_workflows(layout)` and delete the `if served_names is None: return 2` guard that followed it.

- [ ] **Step 4: Delete the two flags and the two force-adds**

In the `run` subparser, delete both `run.add_argument("--workflow", ...)` and `run.add_argument("--all-workflows", ...)`. Leave `init --workflow`, `agent init --workflow` and `submit --workflow`/`--step` untouched — different flags on different subcommands.

Delete the resolver force-add:

```python
    if resolver_defined and args.resolver_workflow not in served_names:
        served_names = [*served_names, args.resolver_workflow]
```

and, if Task 4 left one, the `heal` force-add (`if DEFAULT_HEAL_WORKFLOW not in served_names: ...`). Both are now subsumed: a root that has the file serves it.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/harness/cli.py tests/test_cli.py
git commit -m "feat!: serve every workflow on disk; drop --workflow and --all-workflows

Serving becomes data: the served set is exactly what workflows/ holds, and an
empty directory is workflow-less mode rather than an error. The resolver and
heal force-adds were hand-rolled approximations of that rule and are deleted.

BREAKING CHANGE: \`harness run --workflow\` and \`--all-workflows\` are removed.
Remove a workflow's file to stop serving it."
```

---

### Task 6: Remove `--heal-repo` / `HARNESS_HEAL_REPO`; seed the autoheal process at `init`

**Files:**
- Modify: `src/harness/cli.py` (`_ensure_autoheal_process` ~956, its call site, the `--heal-repo` argument ~2029, `_init`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_init_seeds_the_autoheal_process(tmp_path):
    main(["init", "--root", str(tmp_path)])

    definition = json.loads((tmp_path / "processes" / "autoheal.json").read_text())

    assert definition["action"]["check"] == "failed-tasks"
    assert definition["target"] == {"workflow": "heal"}
    assert definition["action"]["params"] == {}


def test_init_never_clobbers_an_existing_autoheal_process(tmp_path):
    (tmp_path / "processes").mkdir(parents=True)
    (tmp_path / "processes" / "autoheal.json").write_text('{"mine": true}')

    main(["init", "--root", str(tmp_path)])

    assert json.loads((tmp_path / "processes" / "autoheal.json").read_text()) == {"mine": True}


def test_the_heal_repo_flag_is_gone(tmp_path):
    main(["init", "--root", str(tmp_path)])

    with pytest.raises(SystemExit):
        main(["run", "--root", str(tmp_path), "--heal-repo", "onpaj/harness_v2"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k "autoheal or heal_repo_flag" -q`
Expected: FAIL — `init` writes no autoheal process and `--heal-repo` still parses.

- [ ] **Step 3: Move the seeding into `_init`**

Change `_ensure_autoheal_process` to take no repo and call it from `_init` instead of `_run`:

```python
def _ensure_autoheal_process(layout: HarnessLayout) -> None:
    """Seed `processes/autoheal.json` unless one already exists — never
    clobbering an operator's hand-edited file.

    Self-healing is configured like every other Process: this file. Its
    `action.params.repository` is deliberately empty here — an operator points
    it at a registered repo (by name, as in `repos.json`) by editing this file
    or through the dashboard's process editor. Until they do, a heal task is
    repository-less, and the `open-issue` finisher fails it with a message
    saying exactly that.

    Written directly (like `_init`'s `HEAL_DEFINITION`/`RESOLVER_DEFINITION`),
    **not** through `FilesystemProcessAdmin.write`: validating `"failed-tasks"`
    needs the merged registry `app.build()` assembles, which does not exist at
    init time. The real validation happens when `build()` compiles it.
    """
    path = layout.processes / "autoheal.json"
    if path.exists():
        return
    layout.processes.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(AUTOHEAL_PROCESS_DEFINITION, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
```

Call `_ensure_autoheal_process(layout)` from `_init`, next to where `HEAL_DEFINITION` is written. Delete the call from `_run`.

- [ ] **Step 4: Delete the flag and the env var**

Delete `run.add_argument("--heal-repo", dest="heal_repo", ...)` (~line 2029) and every remaining reference to `args.heal_repo`, `heal_repo` and `HARNESS_HEAL_REPO` in `src/harness/cli.py` — including the `registry.resolve(heal_repo)` warning block and the `--heal-repo needs --agent claude` error.

Run: `grep -rn "heal_repo\|HARNESS_HEAL_REPO" src/ tests/` and confirm the only remaining hits are in tests you are updating.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/harness/cli.py tests/test_cli.py
git commit -m "feat!: configure self-healing through its process file only

--heal-repo/HARNESS_HEAL_REPO gated nothing left: not serving the heal
workflow, not registering the finisher kind, not the issue repo. \`harness init\`
now seeds processes/autoheal.json and the operator sets its
action.params.repository to a registered repo name.

BREAKING CHANGE: --heal-repo and HARNESS_HEAL_REPO are removed. Set
action.params.repository in processes/autoheal.json instead — and note it is a
repos.json *name* now, not an owner/repo slug."
```

---

### Task 7: Document the decision

**Files:**
- Create: `docs/adr/0020-open-issue-is-a-generic-finisher.md`
- Modify: `CLAUDE.md` (invariants 24, 26, 39)

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0020-open-issue-is-a-generic-finisher.md`:

```markdown
# ADR-0020: `open-issue` is a generic finisher; serving is data

Status: Accepted

## Context

ADR-0016 made a step's finishing behavior data — a kind resolved against a
registry. `open-issue` was the one kind that did not honour it: it read its
marker from `task.data["heal"]["of"]`, scanned artifacts for a step literally
named `heal`, took its repo from a wiring-time `--heal-repo`, forced
`harness:self-heal` onto every issue, and filed exactly one. It could not be
bound to any step that was not the healer's.

A second consumer — a daily rotating architecture review filing 0–5 issues per
run — differs from the healer only in repository, label, step name and
cardinality: exactly what was hardcoded.

Separately, `--heal-repo`'s value had to be both a GitHub `owner/repo` slug and
a `repos.json` key. On the reference install those disagreed, so self-healing
was silently inert.

## Decision

- `OpenIssueBehavior` is driven by its `FinisherBinding.config`: `label`
  (carried by every issue, and the scope of the idempotency search),
  `from_step` (whose *presence* selects replace-vs-wrap), `allowed_labels`
  (the allowlist a draft's own labels are filtered against).
- A step's artifact carries its drafts as a fenced ```json array; parsing is a
  pure module, `issue_drafts.py`, and the **last** block wins — the same rule
  `_extract_verdict` applies to the agent's final message. An empty artifact
  is zero drafts; a non-empty one with no readable array is an error.
- A draft's marker is `<task id>:<sha1(title)[:8]>` — task-scoped so a re-run
  re-finds what it already filed, content-scoped so reordered findings match
  the right issue.
- The repository is derived from `task.repository` through the registry and
  the clone's `origin` remote, as `GithubForge` already does. `repos.json`
  keeps holding paths only. The resolver is *injected* into the behavior,
  because `behaviors/` may not import `drivers/`.
- `IssueTracker.open_issue` gains `scope_label`; idempotency is per
  `(repo, scope_label, marker)`.
- `harness run` serves every `workflows/*.json`; `--workflow` and
  `--all-workflows` are removed, as are the resolver and heal force-adds that
  approximated the same rule. An empty `workflows/` is workflow-less mode.
- `--heal-repo`/`HARNESS_HEAL_REPO` are removed. `harness init` seeds
  `processes/autoheal.json`; its `action.params.repository` is the one place
  self-healing is pointed at a repo, and it is a registry *name*.

## Consequences

- The healer is one binding of a generic kind. That is the test of the
  abstraction: had heal needed a special case, the generalization would be
  wrong.
- Serving-everything is only safe *because* of the generalization: the
  finisher kind used to be registered only when `--heal-repo` was set, so
  serving the seeded `heal` workflow without it exited 2 at build.
- A stale file in `workflows/` now gets live queues and joins the
  cross-workflow finisher-conflict check, so an incoherent leftover fails the
  build instead of being ignored. Intended — fail fast on incoherent data.
- The `workflow 'resolver' does not exist` crash-loop class is gone: serving
  what exists cannot name a file that is absent.
```

- [ ] **Step 2: Update `CLAUDE.md`**

- **Invariant 24:** replace "an action of an operator-authored Process, typically `processes/autoheal.json`" with a note that `harness init` seeds that file and its `action.params.repository` is the only place self-healing names a repo.
- **Invariant 26:** rewrite the `file-issue` sentence — the `open-issue` finisher is now generic, bound as `{"kind": "open-issue", "from_step": "heal", "label": "harness:self-heal"}`, reads a fenced json draft array, and can file 0..N issues. Keep the "never the LLM" claim; it is unchanged and is the point.
- **Invariant 39:** the sentence about `build()` losing `heal`/`issue_tracker` stands, but drop any reference to `--heal-repo` gating.
- Search for and fix every other mention: `grep -rn "heal-repo\|HARNESS_HEAL_REPO\|--all-workflows\|--workflow" CLAUDE.md README.md docs/`.
- **Fix the `issue_drafts.py` module-map line.** Task 2 added a bullet saying it "knows only `models`" — inaccurate: the module imports nothing from the `harness` package at all. Reword to say exactly that, matching how `models`/`ids`/`artifacts_layout` are described.
- **Also sweep the docs site**, which the module map does not cover: `grep -rn "harness-heal\|heal-repo\|HARNESS_HEAL_REPO" src/harness_docs_site/`. Task 1's reviewer found `src/harness_docs_site/architecture.py:949` still describing `GithubIssueTracker` as deduping "by an embedded `harness-heal` marker" — the prefix is now `harness-issue:`. Fix that line and any sibling it finds.

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0020-open-issue-is-a-generic-finisher.md CLAUDE.md README.md docs/
git commit -m "docs: ADR-0020, generic open-issue finisher and data-driven serving"
```

---

### Task 8: Migrate the live install

This task touches `~/harness-root`, not the repo. Do it only after Tasks 1–7 are committed and `harness update` has shipped them.

**Files:**
- Modify: `~/harness-root/secrets.env`
- Modify: `~/harness-root/processes/autoheal.json`
- Modify: `~/harness-root/workflows/heal.json`
- Modify: `~/harness-root/agents/heal.json`

- [ ] **Step 1: Ship the new version**

```bash
harness update --restart --only-if-idle
```

- [ ] **Step 2: Drop the dead env var**

Remove the `HARNESS_HEAL_REPO=onpaj/harness_v2` line from `~/harness-root/secrets.env`. Leave `CLAUDE_CODE_OAUTH_TOKEN` alone — the service cannot run agents without it.

- [ ] **Step 3: Point self-healing at the repo by registry name**

Set `action.params.repository` in `~/harness-root/processes/autoheal.json` to `"harness_v2"` — the `repos.json` key, **not** the `onpaj/harness_v2` slug. This is the fix for the 29 "heal repo is not registered" warnings in `logs/harness.error.log`.

```json
{
  "trigger": { "interval": "30s" },
  "action": { "check": "failed-tasks", "params": { "repository": "harness_v2" } },
  "target": { "workflow": "heal" },
  "dedup": "per-state",
  "sink": { "kind": "none" }
}
```

- [ ] **Step 4: Rebind the heal workflow**

In `~/harness-root/workflows/heal.json`, change `"finishers"` to:

```json
"finishers": {
  "file-issue": {
    "kind": "open-issue",
    "from_step": "heal",
    "label": "harness:self-heal"
  }
}
```

- [ ] **Step 5: Update the live heal persona**

Bring `~/harness-root/agents/heal.json`'s `prompt` in line with the seeded `_HEALER_PERSONA` from Task 4 — it must instruct the fenced json draft array. The live file predates the three-outcome triage rewrite, so copy the current seeded persona rather than patching the old text.

- [ ] **Step 6: Restart and verify**

```bash
launchctl kickstart -k gui/501/com.harness
```

Then confirm the service came up clean and the old warning is gone:

```bash
tail -20 ~/harness-root/logs/harness.error.log
```

Expected: no `heal repo ... is not registered` line after the restart timestamp. The board answers on http://127.0.0.1:8420/.

- [ ] **Step 7: Confirm the wrapper needed no edit**

```bash
grep -c "workflow" ~/harness-root/harness-run.sh
```

Expected: `0`. If it is not zero, a stale `--workflow` flag survived a `harness service install` and will now crash the service with "unrecognized arguments" — remove it.

---

## Self-Review

**Spec coverage:** §1 artifact contract → Task 2. §2 binding config → Tasks 3, 4. §3 two shapes → Task 3 (behavior), Task 4 (factory). §4 repo identity → Task 4 (`_slug_resolver`). §5 markers/labels/port changes → Tasks 1, 2, 3. §6 data-driven serving → Task 5; `--heal-repo` removal → Task 6. §7 error handling → Task 3 tests. §8 testing → Tasks 1–6. §9 migration → Tasks 7, 8. §10 arch-review data files → deliberately out of scope, as the spec states.

**Type consistency:** `IssueDraft`/`parse_drafts`/`marker_for`/`DraftError` are defined in Task 2 and used with those exact names in Task 3. `OpenIssueBehavior`'s keyword-only signature is defined in Task 3 and constructed with those exact keywords in Task 4. `scope_label` is added in Task 1 and used in Tasks 3 and 4. `_slug_resolver` returns the `Callable[[str | None], str]` that Task 3's `slug_for` parameter expects.
