# github-issues Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the hardcoded `harness:todo` GitHub trigger authorable as a `processes/*.json` by building ADR-0015's deferred inbound `github-issues` action.

**Architecture:** A new `GithubIssuesCheck` (`Check`) scans issues by label across the repo registry, performs the claim label-swap, and emits one provenance-stamped `Observation` per issue. It is registered into the process build as `github-issues` by closing a `GithubClient` + registry into a `CheckFactory` at wiring time (no `CheckFactory` signature change). A new generic `Observation.repository` field lets one multi-repo process stamp each task with its own repo. A `--no-github-source` run flag disables the built-in `GithubTaskSource` ingestion so the process owns it. Ingestion-only: the process `sink` stays `none`, so outbound status labels are dropped (accepted).

**Tech Stack:** Python 3.11, pytest (+ pytest-asyncio), stdlib only. Hexagonal architecture: ports (`src/harness/ports/`) + drivers (`src/harness/drivers/`); orchestration must never import drivers or learn the word "process."

## Global Constraints

- Work in the worktree `/Users/rem/harness_v2-gh-issues-action` on branch `feature/github-issues-action` (off `origin/main`, 0.12.0). Activate its venv first: `source /Users/rem/harness_v2-gh-issues-action/.venv/bin/activate`.
- No new production dependencies (stdlib only in drivers/ports).
- `BUILTIN_CHECKS` stays client-free — `github-issues` is registered only at wiring time in `cli.py`, never added to the static dict.
- Drivers must not import from `cli.py`. `github_issues_check.py` may import `github_slug` from `drivers/git_remote` (driver→driver, allowed) and the `RepositoryRegistry` port.
- `data.source` stamped by the check must be byte-identical in shape to `GithubTaskSource.poll()`: `{"kind": "github", "repo": <slug>, "issue": <number>, "url": <url>}`.
- `test_architecture.py` must stay green: `scheduled_trigger.py` still imports only ports/models/ids; `github_issues_check.py` is a driver touched only by `cli.py` wiring.
- Run the full suite before the final task's completion: `python -m pytest -q`.
- End every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: `Observation.repository` + `ScheduledTrigger` honours it

**Files:**
- Modify: `src/harness/ports/triggers.py` (the `Observation` dataclass)
- Modify: `src/harness/drivers/scheduled_trigger.py` (`_task_for`)
- Test: `tests/test_triggers_port.py`, `tests/test_scheduled_trigger.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Observation(state_key=None, data={}, repository=None)` — a new optional third field `repository: str | None`. `ScheduledTrigger` emits `Task.repository = obs.repository or <trigger's own repository>`.

- [ ] **Step 1: Write the failing test for the new field default**

In `tests/test_triggers_port.py`, extend `test_observation_defaults`:

```python
def test_observation_defaults() -> None:
    obs = Observation()
    assert obs.state_key is None
    assert obs.data == {}
    assert obs.repository is None
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_triggers_port.py::test_observation_defaults -v`
Expected: FAIL — `AttributeError: 'Observation' object has no attribute 'repository'`.

- [ ] **Step 3: Add the field**

In `src/harness/ports/triggers.py`, in the `Observation` dataclass, add the field and extend the docstring:

```python
@dataclass(frozen=True)
class Observation:
    """One reason a `Check` fired.

    `state_key` feeds `per-state` dedup — two observations with the same key are
    the same standing reason and must not yield two tasks. `data` is shallow-merged
    into the emitted task's `data`. `repository` names the repo the emitted task
    belongs to (a multi-repo check stamps it per issue); when None the trigger's
    own `repository` is used.
    """

    state_key: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    repository: str | None = None
```

- [ ] **Step 4: Run it, verify it passes**

Run: `python -m pytest tests/test_triggers_port.py::test_observation_defaults -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for the trigger honouring it**

In `tests/test_scheduled_trigger.py`, add:

```python
def test_observation_repository_overrides_trigger_repository() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="multi",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation(state_key="a", repository="heblo")]),
        workflow="wf",
        repository="fallback",
        dedup="per-state",
    )

    task = trigger.poll()[0]
    assert task.repository == "heblo"


def test_task_repository_falls_back_to_trigger_when_observation_has_none() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="single",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation()]),
        workflow="wf",
        repository="fallback",
    )

    task = trigger.poll()[0]
    assert task.repository == "fallback"
```

- [ ] **Step 6: Run them, verify they fail**

Run: `python -m pytest tests/test_scheduled_trigger.py -k repository -v`
Expected: FAIL — `test_observation_repository_overrides_trigger_repository` gets `"fallback"` (the trigger ignores `obs.repository`).

- [ ] **Step 7: Honour `obs.repository` in `_task_for`**

In `src/harness/drivers/scheduled_trigger.py`, change `_task_for` so the task's repository prefers the observation's:

```python
    def _task_for(self, obs: Observation, bucket: int, now: str) -> Task:
        task_id = new_task_id()
        return Task(
            id=task_id,
            created=now,
            workflow_template=self._workflow,
            step=self._step,
            repository=obs.repository or self._repository,
            worktree=(f"{self._worktree_root}/{task_id}" if self._worktree_root else None),
            dedup_key=self._dedup_key(bucket, obs),
            data={**obs.data},
        )
```

- [ ] **Step 8: Run the whole trigger + port suite, verify green**

Run: `python -m pytest tests/test_scheduled_trigger.py tests/test_triggers_port.py -q`
Expected: PASS (all, including the pre-existing tests — `obs.repository` defaults to None so existing behaviour is unchanged).

- [ ] **Step 9: Commit**

```bash
git add src/harness/ports/triggers.py src/harness/drivers/scheduled_trigger.py tests/test_triggers_port.py tests/test_scheduled_trigger.py
git commit -m "feat(triggers): Observation.repository, honoured by ScheduledTrigger

A multi-repo check stamps each observation with its repo; the emitted task
takes obs.repository or the trigger's own. Behaviour-preserving (defaults None).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `GithubIssuesCheck` driver

**Files:**
- Create: `src/harness/drivers/github_issues_check.py`
- Test: `tests/test_github_issues_check.py`

**Interfaces:**
- Consumes: `Observation` (with `repository`, from Task 1); `RepositoryRegistry` (`.names()`, `.resolve(name) -> Path`); `github_slug(path) -> str | None` from `drivers/git_remote`; a `GithubClient` (`list_issues(repo, *, label)`, `add_label`, `remove_label`).
- Produces: `class GithubIssuesCheck(Check)` with `__init__(self, *, client, registry, slug_of=github_slug, label="harness:todo", claimed_label="harness:queued")` and `evaluate() -> list[Observation]`. Emits one `Observation(state_key="<slug>:<number>", repository="<registry name>", data={"title", "body", "source": {"kind": "github", "repo": <slug>, "issue": <number>, "url": <url>}})` per labelled issue, after swapping `label -> claimed_label` on the issue; an in-instance `_claimed` set of `(slug, number)` suppresses re-listed issues within the process.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_github_issues_check.py`:

```python
"""GithubIssuesCheck — the inbound harness:todo scan as a Check (no network)."""

from __future__ import annotations

from pathlib import Path

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_issues_check import GithubIssuesCheck
from harness.drivers.memory import MemoryRepositoryRegistry


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "harness_v2": Path("/repos/harness_v2")}
    )
    slugs = {
        Path("/repos/heblo"): "onpaj/Anela.Heblo",
        Path("/repos/harness_v2"): "onpaj/harness_v2",
    }
    return registry, slugs


def test_emits_one_observation_per_labelled_issue_with_provenance():
    client = FakeGithubClient(
        [Issue(7, "Fix bug", "the body", "https://gh/i/7", ("harness:todo",))]
    )
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert len(obs) == 1
    (o,) = obs
    assert o.state_key == "onpaj/Anela.Heblo:7"
    assert o.repository == "heblo"
    assert o.data["title"] == "Fix bug"
    assert o.data["body"] == "the body"
    assert o.data["source"] == {
        "kind": "github",
        "repo": "onpaj/Anela.Heblo",
        "issue": 7,
        "url": "https://gh/i/7",
    }


def test_claims_by_swapping_the_label():
    client = FakeGithubClient(
        [Issue(7, "t", "b", "u", ("harness:todo", "bug"))]
    )
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    check.evaluate()

    # todo removed, queued added, foreign label untouched.
    remaining = client.list_issues("onpaj/Anela.Heblo", label="harness:queued")
    assert [i.number for i in remaining] == [7]
    assert client.list_issues("onpaj/Anela.Heblo", label="harness:todo") == []
    (issue,) = client.list_issues("onpaj/Anela.Heblo", label="bug")
    assert set(issue.labels) == {"harness:queued", "bug"}


def test_claimed_ledger_suppresses_a_relisted_issue_within_the_process():
    # An issue that (because of read-after-write lag) still lists under the
    # select label on a second evaluate() must not produce a second task.
    class LaggyClient(FakeGithubClient):
        def remove_label(self, repo, number, label):  # no-op: simulate lag
            return None

    client = LaggyClient([Issue(7, "t", "b", "u", ("harness:todo",))])
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    first = check.evaluate()
    second = check.evaluate()

    assert len(first) == 1
    assert second == []


def test_skips_a_repo_without_a_github_origin_and_scans_the_rest():
    client = FakeGithubClient([Issue(1, "t", "b", "u", ("harness:todo",))])
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "local": Path("/repos/local")}
    )
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo", Path("/repos/local"): None}
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert [o.repository for o in obs] == ["heblo"]


def test_no_labelled_issues_yields_no_observations():
    client = FakeGithubClient([Issue(1, "t", "b", "u", ("bug",))])
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    assert check.evaluate() == []
```

- [ ] **Step 2: Run them, verify they fail**

Run: `python -m pytest tests/test_github_issues_check.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.drivers.github_issues_check'`.

- [ ] **Step 3: Implement the driver**

Create `src/harness/drivers/github_issues_check.py`:

```python
"""`GithubIssuesCheck`: the inbound `harness:todo` scan expressed as a `Check`.

The third distinct GitHub-issue concern in the codebase, kept in its own module:
`github_issues.py` is the self-heal `GithubIssueTracker`, `github_issue_checker.py`
is the `is_open` reconciler. This one is the *ingestion action* — it lists issues
by label across every repo in the registry, claims each by swapping the label
(the at-most-once side effect, exactly as `GithubTaskSource.poll`), and returns
one `Observation` per issue carrying `data.source` provenance so downstream
reconcilers/reflectors recognise the task.

It is registered into the process build as the `github-issues` check by closing a
`GithubClient` and the repo registry into a factory in `cli.py`; `BUILTIN_CHECKS`
stays client-free. Imports only sibling drivers and the registry port — never
`cli` — so `test_architecture.py` stays green.
"""

from __future__ import annotations

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.ports.repos import RepositoryRegistry
from harness.ports.triggers import Check, Observation


class GithubIssuesCheck(Check):
    def __init__(
        self,
        *,
        client: GithubClient,
        registry: RepositoryRegistry,
        slug_of=None,
        label: str = "harness:todo",
        claimed_label: str = "harness:queued",
    ) -> None:
        self._client = client
        self._registry = registry
        # Resolve the default at construction time (reads the module attribute
        # now) so tests can monkeypatch `github_slug`; an explicit slug_of wins.
        self._slug_of = slug_of or github_slug
        self._label = label
        self._claimed_label = claimed_label
        # In-process ledger of already-claimed (slug, number) pairs — the label
        # swap gives at-most-once across restarts, but `list_issues` reads with
        # read-after-write lag, so a fast re-evaluate can still see the issue
        # under the select label. This cuts that off within the process.
        self._claimed: set[tuple[str, int]] = set()

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for name in self._registry.names():
            slug = self._slug_of(self._registry.resolve(name))
            if slug is None:
                continue  # not a GitHub repo — nothing to scan
            for issue in self._client.list_issues(slug, label=self._label):
                key = (slug, issue.number)
                if key in self._claimed:
                    continue
                self._claimed.add(key)
                # Claim: swap the label before the task heads to the inbox.
                self._client.remove_label(slug, issue.number, self._label)
                self._client.add_label(slug, issue.number, self._claimed_label)
                observations.append(
                    Observation(
                        state_key=f"{slug}:{issue.number}",
                        repository=name,
                        data={
                            "title": issue.title,
                            "body": issue.body,
                            "source": {
                                "kind": "github",
                                "repo": slug,
                                "issue": issue.number,
                                "url": issue.url,
                            },
                        },
                    )
                )
        return observations
```

- [ ] **Step 4: Run them, verify they pass**

Run: `python -m pytest tests/test_github_issues_check.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify architecture tests still pass**

Run: `python -m pytest tests/test_architecture.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/harness/drivers/github_issues_check.py tests/test_github_issues_check.py
git commit -m "feat(github): GithubIssuesCheck — harness:todo scan as a Check

Lists issues by label across the registry, claims each via the label swap, and
emits one provenance-stamped Observation per issue. Mirrors GithubTaskSource.poll
inbound; kept client-free of BUILTIN_CHECKS.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Register `github-issues` in the process build + `--no-github-source` flag

**Files:**
- Modify: `src/harness/cli.py` (`_process_sources`, the `run` argparse block, `_run` source assembly)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `GithubIssuesCheck` (Task 2); `FilesystemProcessRepository.build(..., checks=...)`; `ProcessValidationError` (`from harness.drivers.fs_processes import ProcessValidationError`).
- Produces: `_process_sources(args, root, registry, *, clock, known_targets, client=None)` — now registers `github-issues` via a closed-over factory. A `--no-github-source` run flag (`args.no_github_source: bool`, default False) that suppresses `_github_sources` in `_run`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add (near the other `_process_sources` / `_github_sources` tests; reuse the existing imports `_process_sources`, `MemoryRepositoryRegistry`, `FakeGithubClient`, `argparse`, `Path`):

```python
def _process_args(**overrides):
    """Minimal namespace `_process_sources` reads (worktree_root + github_label)."""
    base = dict(worktree_root=None, github_label="harness:todo")
    base.update(overrides)
    return argparse.Namespace(**base)


def test_process_sources_builds_a_github_issues_process(tmp_path):
    from harness.drivers.memory import FakeClock

    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "harness-todo.json").write_text(
        '{"trigger": {"interval": "30s"},'
        ' "action": {"check": "github-issues", "params": {"label": "harness:todo"}},'
        ' "target": {"workflow": "default"}, "dedup": "per-state",'
        ' "sink": {"kind": "none"}}'
    )
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})

    sources = _process_sources(
        _process_args(),
        tmp_path,
        registry,
        clock=FakeClock("2026-07-22T10:00:00Z"),
        known_targets={"default"},
        client=FakeGithubClient(),
    )

    assert len(sources) == 1
    assert sources[0].kind == "scheduled:harness-todo"


def test_process_sources_github_issues_fails_fast_without_a_client(tmp_path, monkeypatch):
    from harness.drivers.fs_processes import ProcessValidationError
    from harness.drivers.memory import FakeClock

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "harness-todo.json").write_text(
        '{"trigger": {"interval": "30s"},'
        ' "action": {"check": "github-issues"},'
        ' "target": {"workflow": "default"}}'
    )
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})

    with pytest.raises(ProcessValidationError) as exc:
        _process_sources(
            _process_args(),
            tmp_path,
            registry,
            clock=FakeClock("2026-07-22T10:00:00Z"),
            known_targets={"default"},
            client=None,
        )
    assert "GITHUB_TOKEN" in str(exc.value)


def test_run_has_a_no_github_source_flag_defaulting_off():
    parser = _build_run_parser_namespace()
    assert parser.no_github_source is False
```

Add this helper next to the new tests (parses a bare `run` invocation to read defaults):

```python
def _build_run_parser_namespace():
    import harness.cli as cli

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    # Re-enter main()'s parser build indirectly: parse a minimal run line.
    return _parse_run(["run"])


def _parse_run(argv):
    """Parse an argv through the real `main` parser and return the Namespace,
    intercepting before the handler runs."""
    import harness.cli as cli

    captured = {}

    def fake_run(args):
        captured["args"] = args
        return 0

    orig = cli._run
    cli._run = fake_run  # the run handler
    try:
        cli.main(argv)
    finally:
        cli._run = orig
    return captured["args"]
```

> Note: if the repo already has a helper that parses a `run` argv to a Namespace (search `tests/test_cli.py` for an existing `_parse` / `monkeypatch.setattr(..., "_run")` pattern), use that instead of adding `_parse_run`, to stay DRY.

- [ ] **Step 2: Run them, verify they fail**

Run: `python -m pytest tests/test_cli.py -k "process_sources_builds_a_github_issues or fails_fast_without_a_client or no_github_source" -v`
Expected: FAIL — `_process_sources` doesn't accept `client=`; `github-issues` is an unknown check; `no_github_source` attribute missing.

- [ ] **Step 3: Widen `_process_sources` to register the check**

In `src/harness/cli.py`, replace the body of `_process_sources` (currently around lines 652–677). Add the `client` keyword and register the factory:

```python
def _process_sources(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    clock: Clock,
    known_targets: set[str] | None,
    client: GithubClient | None = None,
) -> list[TaskSource]:
    """Processes declared under `<root>/processes/*.json`.

    Compiles each into a `ScheduledTrigger` (see `_scheduled_sources`), and
    additionally registers the `github-issues` action: a `GithubIssuesCheck`
    closed over a `GithubClient` + the repo registry. The client comes from the
    caller (tests) or `GITHUB_TOKEN`. `BUILTIN_CHECKS` stays client-free; the
    github-issues factory is added only here at wiring time. A `github-issues`
    process without a client fails fast at build (`ProcessValidationError`)."""
    from harness.drivers.checks import BUILTIN_CHECKS
    from harness.drivers.fs_processes import (
        FilesystemProcessRepository,
        ProcessValidationError,
    )
    from harness.drivers.github_issues_check import GithubIssuesCheck

    if client is None:
        token = os.environ.get("GITHUB_TOKEN")
        client = HttpGithubClient(token) if token else None

    def github_issues_factory(params: dict) -> GithubIssuesCheck:
        if client is None:
            raise ProcessValidationError(
                "github-issues action requires GITHUB_TOKEN", field="check"
            )
        return GithubIssuesCheck(
            client=client,
            registry=registry,
            label=params.get("label", args.github_label),
        )

    checks = {**BUILTIN_CHECKS, "github-issues": github_issues_factory}
    repo = FilesystemProcessRepository(root / "processes")
    worktree_root = args.worktree_root or str(root / "worktrees")
    return repo.build(
        clock=clock,
        checks=checks,
        repository=None,
        worktree_root=worktree_root,
        known_targets=known_targets,
    )
```

Ensure `GithubClient` and `HttpGithubClient` are imported at the top of `cli.py` (search for existing `from harness.drivers.github_client import` — add `GithubClient`/`HttpGithubClient` to it if not already present; `HttpGithubClient` is already imported for `_github_sources`).

- [ ] **Step 4: Add the `--no-github-source` flag**

In `src/harness/cli.py`, in the `run` subparser block (near `--github-label`, around line 985), add:

```python
    run.add_argument(
        "--no-github-source",
        action="store_true",
        dest="no_github_source",
        help="skip the built-in GithubTaskSource ingestion (use when a "
        "github-issues process owns it) — avoids double-claiming the same issue",
    )
```

- [ ] **Step 5: Honour the flag in `_run` and pass the client to `_process_sources`**

In `src/harness/cli.py` `_run`, change the `_github_sources` line (around 1366):

```python
    github = [] if args.no_github_source else _github_sources(args, root, registry)
    sources = github + mergeability
```

And the `_process_sources` call (around 1394) — pass no explicit client so it resolves `GITHUB_TOKEN` itself:

```python
    sources = sources + _process_sources(
        args, root, registry, clock=SystemClock(), known_targets=known_targets
    )
```

(no change needed here beyond confirming it still compiles; `client` defaults to `None` → token path).

- [ ] **Step 6: Run the new tests, verify they pass**

Run: `python -m pytest tests/test_cli.py -k "process_sources_builds_a_github_issues or fails_fast_without_a_client or no_github_source" -v`
Expected: PASS.

- [ ] **Step 7: Run the full cli + processes suite**

Run: `python -m pytest tests/test_cli.py tests/test_fs_processes.py tests/test_processes_e2e.py tests/test_architecture.py -q`
Expected: PASS (all).

- [ ] **Step 8: Commit**

```bash
git add src/harness/cli.py tests/test_cli.py
git commit -m "feat(cli): register github-issues action + --no-github-source flag

_process_sources closes a GithubClient + registry into a github-issues factory
(BUILTIN_CHECKS stays client-free); no token + such a process fails fast.
--no-github-source suppresses the built-in ingestion so a process owns it.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: End-to-end — a process ingests a labelled issue once per bucket

**Files:**
- Test: `tests/test_processes_e2e.py` (extend)

**Interfaces:**
- Consumes: `_process_sources` (Task 3), `FakeClock`, `FakeGithubClient`, `MemoryRepositoryRegistry`.
- Produces: nothing (a coverage test proving the whole compile→poll→task path).

- [ ] **Step 1: Read the existing e2e style**

Run: `sed -n '1,60p' tests/test_processes_e2e.py`
Note the imports and how it drives a `FakeClock` across buckets; reuse them.

- [ ] **Step 2: Write the failing e2e test**

Append to `tests/test_processes_e2e.py` (adjust imports to match the file's existing ones):

```python
def test_github_issues_process_ingests_a_labelled_issue_once_per_bucket(tmp_path):
    from pathlib import Path

    from harness.cli import _process_sources
    from harness.drivers.github_client import FakeGithubClient, Issue
    from harness.drivers.memory import FakeClock, MemoryRepositoryRegistry

    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "harness-todo.json").write_text(
        '{"trigger": {"interval": "30s"},'
        ' "action": {"check": "github-issues", "params": {"label": "harness:todo"}},'
        ' "target": {"workflow": "default"}, "dedup": "per-state",'
        ' "sink": {"kind": "none"}}'
    )
    client = FakeGithubClient(
        [Issue(42, "Do the thing", "body", "https://gh/i/42", ("harness:todo",))]
    )
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo"}
    clock = FakeClock("2026-07-22T10:00:00Z")

    import argparse

    args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
    # Inject the slug resolver via a subclassed check factory path: pass a client
    # and rely on the registry; slug resolution uses git origin in production, so
    # monkeypatch github_slug for the test.
    import harness.drivers.github_issues_check as mod

    orig = mod.github_slug
    mod.github_slug = slugs.get  # type: ignore[assignment]
    try:
        (source,) = _process_sources(
            args, tmp_path, registry,
            clock=clock, known_targets={"default"}, client=client,
        )

        first = source.poll()
        assert len(first) == 1
        task = first[0]
        assert task.workflow_template == "default"
        assert task.repository == "heblo"
        assert task.data["source"] == {
            "kind": "github", "repo": "onpaj/Anela.Heblo",
            "issue": 42, "url": "https://gh/i/42",
        }

        # Same 30s bucket → no re-fire.
        clock.instant = "2026-07-22T10:00:20Z"
        assert source.poll() == []

        # The issue was claimed (label swapped) → next bucket sees nothing new.
        clock.instant = "2026-07-22T10:01:00Z"
        assert source.poll() == []
    finally:
        mod.github_slug = orig  # type: ignore[assignment]
```

> Note: this monkeypatch works because Task 2's `GithubIssuesCheck.__init__` resolves `self._slug_of = slug_of or github_slug` at construction time (reading the module attribute *now*), and `_process_sources` constructs the check without an explicit `slug_of`. So patching `mod.github_slug` before `_process_sources` runs takes effect.

- [ ] **Step 3: Run it, verify it fails first (if implementation gap), then passes**

Run: `python -m pytest tests/test_processes_e2e.py -k github_issues -v`
Expected: PASS.

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (whole suite green).

- [ ] **Step 5: Commit**

```bash
git add tests/test_processes_e2e.py
git commit -m "test(e2e): github-issues process ingests a labelled issue once per bucket

Drives _process_sources -> ScheduledTrigger(github-issues) over a FakeClock +
FakeGithubClient: one task with data.source + repository, claimed so it does not
re-fire.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Author the runtime process file + migrate the service wiring (operator-side)

> This task touches `~/harness-root` (the running install), NOT the repo. Do it only after Tasks 1–4 are merged/verified and a build carrying them is installed. No repo commit here; the deliverable is a loading, running process.

**Files:**
- Create: `/Users/rem/harness-root/processes/harness-todo.json`
- Modify: `/Users/rem/harness-root/harness-run.sh` (the `exec harness run …` line)

- [ ] **Step 1: Confirm the installed harness carries the new action**

Run: `~/.local/bin/harness --version` and confirm it is a build that includes this branch (after the branch is merged and `harness update` has run). If not yet installed, STOP — do not author the runtime file against an old binary (it would fail with "unknown check github-issues").

- [ ] **Step 2: Write the process file**

Create `/Users/rem/harness-root/processes/harness-todo.json`:

```json
{
  "name": "harness-todo",
  "trigger": { "interval": "30s" },
  "action": { "check": "github-issues", "params": { "label": "harness:todo" } },
  "target": { "workflow": "default" },
  "dedup": "per-state",
  "sink": { "kind": "none" }
}
```

- [ ] **Step 3: Add `--no-github-source` to the service invocation**

Edit `/Users/rem/harness-root/harness-run.sh`, change the final line to:

```bash
exec "/Users/rem/.local/bin/harness" run --root "/Users/rem/harness-root" --api-port 8420 --no-github-source
```

> Note: `harness-run.sh` is regenerated by `harness service install`. The durable fix is a `service install` flag; a direct edit is the immediate step and will be overwritten on the next reinstall — record this so the flag gets added to `service install` later (out of scope here).

- [ ] **Step 4: Restart the service and verify the process loaded**

Run: `launchctl kickstart -k gui/$(id -u)/com.harness` (confirm the exact service label first with `launchctl list | grep -i harness`).
Then check the log for a clean start with no "unknown check" / `ProcessValidationError`:
Run: `tail -40 ~/harness-root/harness.log` (or the service's stdout/stderr path).
Expected: harness starts; the `harness-todo` process is served; no validation error.

- [ ] **Step 5: Smoke-verify ingestion (optional, live)**

Label a throwaway GitHub issue `harness:todo` in a repo from `repos.json`; within ~30s confirm the label swaps to `harness:queued` and a task appears on the board. (This is a live check; skip if not appropriate to run against real repos.)

---

## Self-Review Notes

- **Spec coverage:** §Design 1 → Task 2; §Design 2 → Task 3; §Design 3 → Task 1; §Design 4 → Task 3; §Design 5 → Task 5; §Testing → Tasks 1–4; §Error states (no token) → Task 3 fail-fast test. All covered.
- **Out-of-scope items** (outbound sink, ProcessAdmin UI knowing github-issues) are deliberately not tasked — documented in the spec.
- **Type consistency:** `Observation(state_key, data, repository)` used identically in Tasks 1/2/4; `GithubIssuesCheck.__init__` signature in Task 2 matches its construction in Task 3; `_process_sources(..., client=None)` signature matches its calls in Tasks 3/4.
- **Known risk flagged inline:** the `slug_of` default-binding subtlety for the e2e monkeypatch (Task 4 Step 2 note), with the concrete `slug_of=None` fallback fix.
