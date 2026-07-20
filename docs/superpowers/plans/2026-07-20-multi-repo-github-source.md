# Multi-repo GitHub source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `harness run` scans **every** repository listed in `repos.json` for GitHub issues labeled `harness:todo`, instead of a single `--github-repo`.

**Architecture:** The core already supports many sources (`build(sources=[...])`, one `SourcePoller` each, `SourceReflectorSink` reflects to all). We derive each repo's GitHub `owner/repo` slug from its clone's git `origin` remote, add repo enumeration to `RepositoryRegistry`, and rewire the CLI to build one `GithubTaskSource` per repo. Each source's `_mine()` guard is scoped to its own repo so N same-`kind` sources don't cross-write labels.

**Tech Stack:** Python 3.11, stdlib only (`subprocess`, `json`, `urllib`), pytest.

## Global Constraints

- **English only** — all code, comments, docstrings, string literals, tests, commit messages (project rule in CLAUDE.md).
- **Python 3.11**, **no production dependencies** — stdlib only.
- **Immutability** — never mutate a `Task`/`Issue`; build new objects.
- **Run tests:** `.venv/bin/pytest -q`.
- **Ports stay driver-blind** — `dispatcher.py`/`consumer.py` must not import drivers or `repos`. `RepositoryRegistry` is touched only by wiring/behavior (invariant #17). Guarded by `tests/test_architecture.py`.
- **A task carries a repo *name*, not a path** (invariant #15). The slug is used only to call GitHub, never stored on the task.
- **Commit messages:** `type: description` (feat/fix/refactor/chore/test/docs). No attribution footer.

---

### Task 1: `git_remote` driver — derive the GitHub slug from a clone

**Files:**
- Create: `src/harness/drivers/git_remote.py`
- Test: `tests/test_git_remote.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_github_slug(remote_url: str) -> str | None` — pure URL → `"owner/repo"` or `None`.
  - `github_slug(path: Path) -> str | None` — runs git in `path`, returns the slug or `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_git_remote.py
import subprocess

from harness.drivers.git_remote import github_slug, parse_github_slug


def test_parse_ssh_form_strips_git_suffix():
    assert parse_github_slug("git@github.com:onpaj/Anela.Heblo.git") == "onpaj/Anela.Heblo"


def test_parse_https_form_strips_git_suffix():
    assert parse_github_slug("https://github.com/onpaj/Anela.Heblo.git") == "onpaj/Anela.Heblo"


def test_parse_https_without_git_suffix():
    assert parse_github_slug("https://github.com/onpaj/Anela.Heblo") == "onpaj/Anela.Heblo"


def test_parse_non_github_host_is_none():
    assert parse_github_slug("git@gitlab.com:foo/bar.git") is None


def test_parse_incomplete_path_is_none():
    assert parse_github_slug("https://github.com/onpaj") is None


def test_parse_garbage_is_none():
    assert parse_github_slug("not-a-url") is None
    assert parse_github_slug("") is None


def test_github_slug_reads_origin(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin",
         "git@github.com:onpaj/Anela.Heblo.git"],
        check=True,
    )
    assert github_slug(tmp_path) == "onpaj/Anela.Heblo"


def test_github_slug_not_a_repo_is_none(tmp_path):
    assert github_slug(tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_git_remote.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.drivers.git_remote'`

- [ ] **Step 3: Write the implementation**

```python
# src/harness/drivers/git_remote.py
"""Derive a GitHub `owner/repo` slug from a clone's git `origin` remote.

`repos.json` maps a repo name to a local path; it holds no GitHub slug. Rather
than duplicate that fact in config, we read it from the clone itself. A repo
whose origin is not a GitHub URL yields None and is simply not scanned.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def parse_github_slug(remote_url: str) -> str | None:
    """Map a git remote URL to `"owner/repo"`, or None if it is not github.com.

    Handles the SSH (`git@github.com:owner/repo.git`) and HTTPS
    (`https://github.com/owner/repo.git`) forms, with or without a `.git`
    suffix.
    """
    url = remote_url.strip()
    if not url:
        return None

    if url.startswith("git@"):
        _, _, rest = url.partition("@")  # github.com:owner/repo.git
        host, _, path = rest.partition(":")
    elif "://" in url:
        _, _, rest = url.partition("://")  # [creds@]github.com/owner/repo.git
        rest = rest.rsplit("@", 1)[-1]  # drop optional credentials
        host, _, path = rest.partition("/")
    else:
        return None

    if host != "github.com":
        return None

    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def github_slug(path: Path) -> str | None:
    """The GitHub slug of the clone at `path`, or None if it has no GitHub
    origin (not a git repo, no `origin`, non-GitHub remote, or git missing)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return parse_github_slug(result.stdout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_git_remote.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/harness/drivers/git_remote.py tests/test_git_remote.py
git commit -m "feat: derive GitHub slug from a clone's git origin"
```

---

### Task 2: `RepositoryRegistry.names()` — enumerate the config

**Files:**
- Modify: `src/harness/ports/repos.py`
- Modify: `src/harness/drivers/fs_repos.py`
- Modify: `src/harness/drivers/memory.py:341-352`
- Test: `tests/test_repos.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RepositoryRegistry.names(self) -> list[str]` on the port and both drivers. Missing/broken config → `[]` (lenient; unlike `resolve`, which raises).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repos.py`:

```python
def test_memory_names_lists_keys():
    registry = MemoryRepositoryRegistry(
        {"harness_v2": Path("/repos/harness_v2"), "heblo": Path("/repos/heblo")}
    )

    assert sorted(registry.names()) == ["harness_v2", "heblo"]


def test_fs_names_lists_keys(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"harness_v2": "/a", "heblo": "/b"}))
    registry = FilesystemRepositoryRegistry(config)

    assert sorted(registry.names()) == ["harness_v2", "heblo"]


def test_fs_names_missing_config_is_empty(tmp_path):
    registry = FilesystemRepositoryRegistry(tmp_path / "missing.json")

    assert registry.names() == []


def test_fs_names_broken_json_is_empty(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text("{not json")
    registry = FilesystemRepositoryRegistry(config)

    assert registry.names() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_repos.py -q`
Expected: FAIL — `AttributeError: 'MemoryRepositoryRegistry' object has no attribute 'names'`

- [ ] **Step 3: Add `names()` to the port**

In `src/harness/ports/repos.py`, add after the `resolve` abstractmethod (inside the class):

```python
    @abstractmethod
    def names(self) -> list[str]:
        """All repo names in the registry. A missing/unreadable registry yields
        an empty list — enumeration is lenient where `resolve` is strict."""
```

- [ ] **Step 4: Implement `names()` in `FilesystemRepositoryRegistry`**

In `src/harness/drivers/fs_repos.py`, add this method to the class (after `resolve`):

```python
    def names(self) -> list[str]:
        try:
            raw = json.loads(self._config.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(raw, dict):
            return []
        return list(raw)
```

- [ ] **Step 5: Implement `names()` in `MemoryRepositoryRegistry`**

In `src/harness/drivers/memory.py`, add this method to `MemoryRepositoryRegistry` (after `resolve`, around line 352):

```python
    def names(self) -> list[str]:
        return list(self._repos)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_repos.py -q`
Expected: PASS (all, including the 4 new)

- [ ] **Step 7: Commit**

```bash
git add src/harness/ports/repos.py src/harness/drivers/fs_repos.py src/harness/drivers/memory.py tests/test_repos.py
git commit -m "feat: add RepositoryRegistry.names() to enumerate repos.json"
```

---

### Task 3: Scope `GithubTaskSource._mine` to its own repo

**Files:**
- Modify: `src/harness/drivers/github_source.py:117-118`
- Test: `tests/test_github_source.py`

**Interfaces:**
- Consumes: `GithubTaskSource` (existing).
- Produces: `_mine(task)` now returns True only when `task.data.source.kind == "github"` **and** `task.data.source.repo == self._repo`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_github_source.py`:

```python
def test_task_from_another_repo_is_not_mine():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:todo",))])
    source = build_source(client)  # repo="o/r"
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="/repos/other",
        worktree="/wt/tsk_x",
        data={
            "source": {
                "kind": "github",
                "repo": "o/other",  # a DIFFERENT github repo
                "issue": 1,
                "url": "u",
            }
        },
    )

    source.report_progress(foreign, Progress(step="development"))
    source.finish(foreign, FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:todo"}  # untouched — not this source's repo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_github_source.py::test_task_from_another_repo_is_not_mine -q`
Expected: FAIL — the label becomes `{"harness:pr-open"}` because the kind-only guard treats the foreign task as its own.

- [ ] **Step 3: Scope the guard to the repo**

In `src/harness/drivers/github_source.py`, replace `_mine`:

```python
    def _mine(self, task: Task) -> bool:
        src = task.data.get("source", {})
        return src.get("kind") == self.kind and src.get("repo") == self._repo
```

- [ ] **Step 4: Run the source tests to verify they pass**

Run: `.venv/bin/pytest tests/test_github_source.py -q`
Expected: PASS (all, including the new one and the existing `test_task_without_source_is_noop`)

- [ ] **Step 5: Commit**

```bash
git add src/harness/drivers/github_source.py tests/test_github_source.py
git commit -m "fix: scope GithubTaskSource._mine to its own repo"
```

---

### Task 4: CLI — `_github_sources` scans every repo in `repos.json`

**Files:**
- Modify: `src/harness/cli.py` — replace `_github_source` (321-351) with `_github_sources`; update `_run` (367, 378); remove `--github-repo`/`--github-repository` argparse (around 464-474).
- Test: `tests/test_cli.py` — update import (7), `_github_args` helper (146-156), replace the two old source tests (159-180).

**Interfaces:**
- Consumes: `github_slug` (Task 1), `RepositoryRegistry.names()` (Task 2), `GithubTaskSource`, `HttpGithubClient`, `DEFAULT_STEP_LABELS`.
- Produces: `_github_sources(args, root, registry, *, slug_of=github_slug, client=None) -> list[TaskSource]`.

- [ ] **Step 1: Update the test import and helper, write the failing tests**

In `tests/test_cli.py`, change the import on line 7 from:

```python
from harness.cli import DEFAULT_WORKFLOW, _github_source, main, serve
```
to:
```python
from harness.cli import DEFAULT_WORKFLOW, _github_sources, main, serve
```

Add these imports near the top of `tests/test_cli.py` (with the other imports):

```python
from pathlib import Path

from harness.drivers.github_client import FakeGithubClient
from harness.drivers.memory import MemoryRepositoryRegistry
```

Replace the `_github_args` helper (currently lines ~146-156) with:

```python
def _github_args(**overrides):
    """The minimal namespace the `run` parser hands to `_github_sources`."""
    base = dict(
        github_workflow="default",
        github_label="harness:todo",
        worktree_root=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)
```

Replace the two old tests (`test_github_source_stamps_repository_name_not_root_path` and `test_github_source_disabled_without_repository_name`, ~lines 159-180) with:

```python
def test_github_sources_builds_one_per_github_repo(monkeypatch, tmp_path):
    """One source per repos.json repo that has a GitHub origin; the task carries
    the repo *name* (invariant 15), not a path."""
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "harness_v2": Path("/repos/harness_v2")}
    )
    slugs = {
        Path("/repos/heblo"): "onpaj/Anela.Heblo",
        Path("/repos/harness_v2"): "onpaj/harness_v2",
    }

    sources = _github_sources(
        _github_args(),
        tmp_path,
        registry,
        slug_of=slugs.get,
        client=FakeGithubClient(),
    )

    assert {s._repository for s in sources} == {"heblo", "harness_v2"}
    assert {s._repo for s in sources} == {"onpaj/Anela.Heblo", "onpaj/harness_v2"}


def test_github_sources_skips_repo_without_github_origin(monkeypatch, tmp_path, capsys):
    """A repo whose origin is not GitHub is skipped with a warning, others build."""
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "local": Path("/repos/local")}
    )
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo", Path("/repos/local"): None}

    sources = _github_sources(
        _github_args(), tmp_path, registry, slug_of=slugs.get, client=FakeGithubClient()
    )

    assert [s._repository for s in sources] == ["heblo"]
    assert "local has no GitHub origin" in capsys.readouterr().err


def test_github_sources_empty_without_token(monkeypatch, tmp_path):
    """No GITHUB_TOKEN → no sources (harness runs on `submit` alone), silently."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})

    assert _github_sources(_github_args(), tmp_path, registry) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: FAIL — `ImportError: cannot import name '_github_sources'`

- [ ] **Step 3: Replace `_github_source` with `_github_sources`**

In `src/harness/cli.py`, add the import near the other driver imports (after line 22):

```python
from harness.drivers.git_remote import github_slug
```

Replace the whole `_github_source` function (lines 321-351) with:

```python
def _github_sources(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    slug_of=github_slug,
    client: GithubClient | None = None,
) -> list[TaskSource]:
    """One `GithubTaskSource` per repo in `repos.json` that has a GitHub origin.

    The slug is derived from each clone's git origin (`slug_of`); a repo with no
    GitHub origin is skipped with a warning. Without `GITHUB_TOKEN` (and no
    injected client) there are no sources and the harness runs on `harness
    submit` alone."""
    if client is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return []
        client = HttpGithubClient(token)

    worktree_root = args.worktree_root or str(root / "worktrees")
    sources: list[TaskSource] = []
    for name in registry.names():
        slug = slug_of(registry.resolve(name))
        if slug is None:
            print(f"warning: {name} has no GitHub origin, not scanned", file=sys.stderr)
            continue
        sources.append(
            GithubTaskSource(
                client=client,
                clock=SystemClock(),
                repo=slug,
                workflow=args.github_workflow,
                repository=name,
                worktree_root=worktree_root,
                select_label=args.github_label,
                step_labels=DEFAULT_STEP_LABELS,
            )
        )
    return sources
```

Add the two imports these need — in `src/harness/cli.py`, extend the github_client import (line 22) and the repos import:

```python
from harness.drivers.github_client import GithubClient, HttpGithubClient
```
and add:
```python
from harness.ports.repos import RepositoryRegistry
```

- [ ] **Step 4: Wire `_run` to the plural sources**

In `src/harness/cli.py` `_run`, replace line 367:

```python
    source = _github_source(args, root)
```
with:
```python
    sources = _github_sources(args, root, registry)
```

and replace the `sources=` argument in the `build(...)` call (line 378):

```python
            sources=[source] if source else None,
```
with:
```python
            sources=sources or None,
```

- [ ] **Step 5: Remove the two obsolete argparse flags**

In `src/harness/cli.py`, delete the `--github-repo` and `--github-repository` `run.add_argument(...)` blocks (around lines 464-474). Keep `--github-label`, `--github-workflow`, and `--worktree-root`. After deletion the remaining GitHub-related args are:

```python
    run.add_argument(
        "--github-label",
        default="harness:todo",
        help="label that selects issues to ingest",
    )
    run.add_argument("--github-workflow", default=DEFAULT_WORKFLOW)
    run.add_argument("--worktree-root", default=None, help="root of the task worktrees")
```

- [ ] **Step 6: Run the CLI tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: PASS (all, including the 3 new source tests)

- [ ] **Step 7: Commit**

```bash
git add src/harness/cli.py tests/test_cli.py
git commit -m "feat: scan every repos.json repo for GitHub issues"
```

---

### Task 5: Integration — two repos, labels never cross

**Files:**
- Create: `tests/test_multi_repo_source.py`

**Interfaces:**
- Consumes: `GithubTaskSource`, `SourceReflectorSink`, `FakeGithubClient`, `FakeClock`.
- Produces: nothing (test-only).

- [ ] **Step 1: Write the failing/regression test**

```python
# tests/test_multi_repo_source.py
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
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_multi_repo_source.py -q`
Expected: PASS (this guards the Task 3 fix end-to-end through the real reflector; on the unfixed `_mine` it would fail with `client_b` issue #1 becoming `{"harness:pr-open"}`)

- [ ] **Step 3: Commit**

```bash
git add tests/test_multi_repo_source.py
git commit -m "test: multi-repo label isolation through the reflector"
```

---

### Task 6: Simplify the Conductor run command + full verification

**Files:**
- Modify: `.conductor/settings.toml:42-43`

**Interfaces:**
- Consumes: the CLI from Task 4 (no more `--github-repo`/`--github-repository`).
- Produces: nothing.

- [ ] **Step 1: Update the run command and its comment**

In `.conductor/settings.toml`, replace the `[scripts.run.loop]` comment + command block with:

```toml
# GitHub source: with GITHUB_TOKEN present, the harness scans EVERY repo in
# repos.json (currently harness_v2 and heblo) for issues labeled `harness:todo`.
# Each repo's owner/name slug is derived from its clone's git origin. The token
# is taken at runtime from `gh` (no secret in the file); without it the harness
# runs on `harness submit` alone. GitHub is polled on its own interval
# (`--source-poll`, default 30 s) so it doesn't hit API rate limits, while the
# internal loop stays fast for a responsive board.
[scripts.run.loop]
command = 'GITHUB_TOKEN="$(gh auth token)" .venv/bin/harness run --root "$CONDUCTOR_WORKSPACE_PATH/.harness"'
default = true
icon = "play"
```

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all; 1 skipped — the opt-in `test_smoke_claude.py`)

- [ ] **Step 3: Confirm no stale references remain**

Run: `grep -rn "_github_source\b\|--github-repo\b\|github_repository" src/ tests/ .conductor/ | grep -v git_remote`
Expected: no output (the only `github`-slug code is the new `git_remote` module).

- [ ] **Step 4: Commit**

```bash
git add .conductor/settings.toml
git commit -m "chore: scan all repos.json repos, drop single-repo flags"
```

---

## Self-Review

**Spec coverage:**
- Slug derivation from git origin → Task 1 ✓
- `repos.json` unchanged (name→path) → no schema task; enumeration via `names()` → Task 2 ✓
- One source per repo, skip non-GitHub, drop flags, injected slug-deriver, shared worktree_root → Task 4 ✓
- `_mine` repo-scoping → Task 3, end-to-end in Task 5 ✓
- `.conductor/settings.toml` simplification → Task 6 ✓
- Error handling (non-git/broken config isolated) → Task 1 (`github_slug` → None), Task 2 (`names` → `[]`), Task 6 verification ✓
- Testing (parser, names, _github_sources, _mine, two-repo integration) → Tasks 1,2,3,4,5 ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `github_slug`/`parse_github_slug` (Task 1) used with the exact signature in Task 4; `names()` (Task 2) called in Task 4; `_github_sources(args, root, registry, *, slug_of, client)` defined in Task 4 and called with those kwargs in the Task 4 tests; `GithubTaskSource` attributes `_repo`/`_repository` asserted in Task 4 tests match the existing constructor.
