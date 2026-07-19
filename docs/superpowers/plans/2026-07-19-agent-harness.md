# Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stateless, CLI-driven multi-agent orchestration platform where each agent invocation is one `claude -p` subprocess operating in its own git worktree.

**Architecture:** An actor model over one-shot CLI processes. Each agent has a durable filesystem queue; an asyncio dispatcher leases tasks, a Runner prepares a git worktree off a bare mirror, spawns `claude -p`, parses `result.json`, commits the worktree, and enqueues validated handoffs. Dependencies flow strictly downward: `models` → `git`/`queue`/`store` → `runner` → `dispatch` → `cli`/`web`/`scheduler`.

**Tech Stack:** Python 3.11, pydantic v2, PyYAML, Typer, structlog, croniter, FastAPI + Jinja2, SQLite (stdlib `sqlite3`), pytest + pytest-asyncio.

## Global Constraints

- Python **3.11** exactly. The interpreter is `/Users/rem/.local/bin/python3.11`. There is no `uv` binary on this machine — use `python3.11 -m venv .venv` and `pip`.
- **CLI-only.** Nothing in this codebase may import an Anthropic SDK or make an HTTP request to an Anthropic endpoint. The only contact with Claude is `subprocess` spawning the `claude` binary, and it lives in exactly one file: `src/agentharness/runner/executor.py`.
- **Statelessness.** The argv built for `claude -p` must never contain `--resume` or `--continue`. This is asserted by a test.
- **Zero subscription usage in tests.** Every test except those marked `@pytest.mark.live` must use `FakeExecutor`. `pytest` default config excludes `live`.
- Harness home defaults to `~/.agentharness`, overridden by the `AGENTHARNESS_HOME` environment variable. Every test must set it to a `tmp_path`.
- Harness artifacts inside a target repo always live at `.harness/runs/<trace_id>/<task_id>/`.
- Run branches are always named `run/<task_id>`. The scratch repo id is always the literal `_scratch`.
- The harness never writes to `main`. Merges stop at the repo's `integration_branch` (default `harness/integration`).
- Global concurrency default is **3**; branch retention default is **30** days; lease visibility timeout default is **1800** seconds.
- All git subprocess calls go through the helper in `git/mirror.py` — no module may call `subprocess` on `git` directly.
- All ref-mutating git operations (worktree add/remove, branch create/delete, merge, push) must be wrapped in `repo_lock`.
- Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, pytest config |
| `src/agentharness/config.py` | `Config` model, `harness_home()`, `load_config()` |
| `src/agentharness/ids.py` | ULID-based `new_task_id/new_trace_id/new_run_id` |
| `src/agentharness/models.py` | Every shared pydantic type. Depends on nothing. |
| `src/agentharness/registry/agents.py` | Load/validate agent YAML; routing allow-list |
| `src/agentharness/registry/repos.py` | Managed repo registration, mirrors, scratch repo |
| `src/agentharness/git/lock.py` | `repo_lock` — flock serialising ref mutations |
| `src/agentharness/git/mirror.py` | `git()` helper + bare mirror/ref primitives |
| `src/agentharness/git/worktree.py` | Worktree add/commit/remove/prune |
| `src/agentharness/git/merge.py` | Leaf `run/*` branches → integration branch |
| `src/agentharness/queue/base.py` | `Queue` ABC |
| `src/agentharness/queue/filesystem.py` | FS queue: atomic rename, visibility timeout, DLQ |
| `src/agentharness/store/db.py` | SQLite schema + migrations |
| `src/agentharness/store/runs.py` | `RunStore` — tasks, runs, handoffs, events, traces |
| `src/agentharness/runner/executor.py` | `Executor` ABC, `LocalExecutor`, `FakeExecutor` |
| `src/agentharness/runner/prompt.py` | Protocol preamble + prompt composition |
| `src/agentharness/runner/result.py` | `result.json` parsing with degraded fallback |
| `src/agentharness/runner/runner.py` | The run lifecycle |
| `src/agentharness/dispatch/routing.py` | Handoff validation → child tasks |
| `src/agentharness/dispatch/retry.py` | Backoff policy |
| `src/agentharness/dispatch/limits.py` | Global concurrency + rate-limit pause gate |
| `src/agentharness/dispatch/dispatcher.py` | Lease loop, semaphores, trace completion |
| `src/agentharness/scheduler/scheduler.py` | croniter-based durable scheduler |
| `src/agentharness/obs/logging.py` | structlog JSON configuration |
| `src/agentharness/obs/metrics.py` | Queue depth, latency, cost aggregation |
| `src/agentharness/web/app.py` | Read-only FastAPI + Jinja2 dashboard |
| `src/agentharness/cli.py` | Typer entrypoint |

### Deviation from the spec

The spec named APScheduler. This plan uses **croniter plus a `schedules` table in `runs.db`** instead: durability comes from the table's `next_fire_at` column, which is simpler than APScheduler's SQLAlchemy job store and drops two dependencies. The behaviour the spec requires — durable schedules that survive restarts, `schedule_id` stamped on every task — is unchanged. Update spec §4 and §12 when this lands.

---

## Phase 1 — Foundations

### Task 1: Project scaffold, config, and ids

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/agentharness/__init__.py`, `src/agentharness/config.py`, `src/agentharness/ids.py`
- Create: `tests/conftest.py`, `tests/test_config.py`, `tests/test_ids.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `harness_home() -> Path` — reads `AGENTHARNESS_HOME`, else `~/.agentharness`.
  - `class Config(BaseModel)` with fields `home: Path`, `max_concurrency: int = 3`, `lease_timeout_seconds: int = 1800`, `poll_interval_seconds: float = 1.0`, `branch_retention_days: int = 30`, `claude_binary: str = "claude"`, `default_integration_branch: str = "harness/integration"`, `default_base_branch: str = "main"`, `rate_limit_patterns: list[str]`, `rate_limit_initial_backoff_seconds: int = 60`, `rate_limit_max_backoff_seconds: int = 3600`, `commit_author_name: str = "agentharness"`, `commit_author_email: str = "agentharness@localhost"`.
  - `Config.ensure_dirs() -> None` — creates `agents/`, `repos/`, `queues/`, `worktrees/`, `locks/`, `logs/` under `home`.
  - `load_config(home: Path | None = None) -> Config` — merges `<home>/config.yaml` over defaults.
  - `new_task_id() -> str` (`t_` prefix), `new_trace_id() -> str` (`tr_`), `new_run_id() -> str` (`r_`).
  - `tests/conftest.py` fixture `home(tmp_path, monkeypatch) -> Path` setting `AGENTHARNESS_HOME`.

- [ ] **Step 1: Create the virtualenv and `pyproject.toml`**

```bash
cd /Users/rem/harness_v2
/Users/rem/.local/bin/python3.11 -m venv .venv
.venv/bin/pip install -q --upgrade pip
```

`pyproject.toml`:

```toml
[project]
name = "agentharness"
version = "0.1.0"
description = "Stateless, CLI-driven multi-agent orchestration over claude -p"
requires-python = ">=3.11,<3.12"
dependencies = [
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "typer>=0.12",
  "structlog>=24.1",
  "croniter>=2.0",
  "python-ulid>=2.2",
  "fastapi>=0.110",
  "uvicorn>=0.29",
  "jinja2>=3.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27"]

[project.scripts]
agentharness = "agentharness.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = ["live: hits the real claude CLI and consumes subscription usage"]
addopts = "-m 'not live'"
```

`.gitignore`:

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 2: Install**

Run: `.venv/bin/pip install -q -e ".[dev]"`
Expected: no error; `.venv/bin/pytest --version` prints a version.

- [ ] **Step 3: Write the failing tests**

`tests/conftest.py`:

```python
import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "harness"
    h.mkdir()
    monkeypatch.setenv("AGENTHARNESS_HOME", str(h))
    return h
```

`tests/test_config.py`:

```python
from pathlib import Path

from agentharness.config import Config, harness_home, load_config


def test_harness_home_reads_env(home):
    assert harness_home() == home


def test_harness_home_defaults_to_dot_agentharness(monkeypatch):
    monkeypatch.delenv("AGENTHARNESS_HOME", raising=False)
    assert harness_home() == Path.home() / ".agentharness"


def test_load_config_defaults(home):
    cfg = load_config()
    assert cfg.home == home
    assert cfg.max_concurrency == 3
    assert cfg.branch_retention_days == 30
    assert cfg.default_integration_branch == "harness/integration"


def test_load_config_overrides_from_yaml(home):
    (home / "config.yaml").write_text("max_concurrency: 7\nclaude_binary: /usr/bin/claude\n")
    cfg = load_config()
    assert cfg.max_concurrency == 7
    assert cfg.claude_binary == "/usr/bin/claude"


def test_ensure_dirs_creates_layout(home):
    cfg = load_config()
    cfg.ensure_dirs()
    for name in ("agents", "repos", "queues", "worktrees", "locks", "logs"):
        assert (home / name).is_dir()
```

`tests/test_ids.py`:

```python
from agentharness.ids import new_run_id, new_task_id, new_trace_id


def test_ids_are_prefixed():
    assert new_task_id().startswith("t_")
    assert new_trace_id().startswith("tr_")
    assert new_run_id().startswith("r_")


def test_ids_are_unique():
    assert len({new_task_id() for _ in range(100)}) == 100


def test_ids_sort_chronologically():
    ids = [new_task_id() for _ in range(20)]
    assert ids == sorted(ids)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_ids.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentharness.config'`

- [ ] **Step 5: Implement `src/agentharness/ids.py`**

```python
"""Sortable, prefixed identifiers for tasks, traces, and runs."""

from ulid import ULID


def _new(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def new_task_id() -> str:
    return _new("t")


def new_trace_id() -> str:
    return _new("tr")


def new_run_id() -> str:
    return _new("r")
```

- [ ] **Step 6: Implement `src/agentharness/config.py`**

```python
"""Global harness configuration and the on-disk home layout."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "usage limit",
    "too many requests",
    "429",
]

SUBDIRS = ("agents", "repos", "queues", "worktrees", "locks", "logs")


def harness_home() -> Path:
    env = os.environ.get("AGENTHARNESS_HOME")
    return Path(env).expanduser() if env else Path.home() / ".agentharness"


class Config(BaseModel):
    home: Path
    max_concurrency: int = 3
    lease_timeout_seconds: int = 1800
    poll_interval_seconds: float = 1.0
    branch_retention_days: int = 30
    claude_binary: str = "claude"
    default_integration_branch: str = "harness/integration"
    default_base_branch: str = "main"
    rate_limit_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_RATE_LIMIT_PATTERNS))
    rate_limit_initial_backoff_seconds: int = 60
    rate_limit_max_backoff_seconds: int = 3600
    commit_author_name: str = "agentharness"
    commit_author_email: str = "agentharness@localhost"

    @property
    def db_path(self) -> Path:
        return self.home / "runs.db"

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    @property
    def queues_dir(self) -> Path:
        return self.home / "queues"

    @property
    def worktrees_dir(self) -> Path:
        return self.home / "worktrees"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRS:
            (self.home / name).mkdir(exist_ok=True)


def load_config(home: Path | None = None) -> Config:
    root = home or harness_home()
    data: dict = {}
    cfg_file = root / "config.yaml"
    if cfg_file.exists():
        data = yaml.safe_load(cfg_file.read_text()) or {}
    data["home"] = root
    return Config(**data)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py tests/test_ids.py -v`
Expected: PASS, 8 passed.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "feat: project scaffold, config, and id generation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Shared domain models

**Files:**
- Create: `src/agentharness/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (models depend on no other harness module).
- Produces — every type below is imported by name across the rest of the plan:
  - Literals: `PermissionMode = Literal["default","plan","acceptEdits","bypassPermissions"]`, `BackoffKind = Literal["exponential","linear","fixed"]`, `ResultStatus = Literal["ok","failed","needs_input"]`, `RunStatus = Literal["ok","failed","timeout"]`, `TaskStatus = Literal["pending","leased","running","done","failed","dead","blocked"]`.
  - `RetryPolicy(max_attempts: int = 3, backoff: BackoffKind = "exponential")`
  - `AgentDef` — `name, description, model: str|None, permission_mode: PermissionMode = "acceptEdits", allowed_tools: list[str], disallowed_tools: list[str] = [], mcp_config: str|None, system_prompt_file: str|None, max_turns: int = 25, timeout_seconds: int = 900, concurrency: int = 1, retries: RetryPolicy, repos: list[str] = [], can_handoff_to: list[str] = []`. `model_config = ConfigDict(extra="forbid")`.
  - `TaskArtifacts(base_ref: str|None = None, inputs: list[str] = [])`
  - `Task` — every field from spec §5; `model_config = ConfigDict(extra="forbid")`; helper `Task.artifact_dir -> str` returning `.harness/runs/{trace_id}/{task_id}`.
  - `Handoff(agent: str, intent: str, payload: dict = {}, artifacts: TaskArtifacts = TaskArtifacts())`
  - `Result(status: ResultStatus, summary: str = "", outputs: list[str] = [], handoffs: list[Handoff] = [], metrics: dict = {})`
  - `RunRecord` — every field from spec §5 plus `degraded: bool = False` and `branch: str|None = None`.
  - `RepoDef(repo_id: str, url: str, integration_branch: str, base_branch: str)`
  - `ScheduleDef(schedule_id: str, cron: str, agent: str, intent: str, repo: str|None = None, payload: dict = {}, enabled: bool = True)`
  - `SCRATCH_REPO_ID = "_scratch"`

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:

```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agentharness.models import AgentDef, Handoff, Result, RetryPolicy, Task, TaskArtifacts


def make_task(**over) -> Task:
    base = dict(
        task_id="t_1",
        trace_id="tr_1",
        parent_task_id=None,
        agent="writer",
        repo="app",
        intent="draft",
        payload={"topic": "x"},
        artifacts=TaskArtifacts(base_ref="abc123", inputs=["brief.md"]),
        idempotency_key="writer:draft:tr_1",
        created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    base.update(over)
    return Task(**base)


def test_task_defaults():
    t = make_task()
    assert t.priority == 5
    assert t.attempt == 1
    assert t.schedule_id is None


def test_task_artifact_dir():
    assert make_task().artifact_dir == ".harness/runs/tr_1/t_1"


def test_task_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        make_task(bogus="nope")


def test_task_roundtrips_through_json():
    t = make_task()
    assert Task.model_validate_json(t.model_dump_json()) == t


def test_agent_def_defaults():
    a = AgentDef(name="writer", description="writes", allowed_tools=["Read", "Write"])
    assert a.permission_mode == "acceptEdits"
    assert a.concurrency == 1
    assert a.retries == RetryPolicy()
    assert a.can_handoff_to == []


def test_agent_def_rejects_bad_permission_mode():
    with pytest.raises(ValidationError):
        AgentDef(name="w", description="d", allowed_tools=[], permission_mode="yolo")


def test_agent_def_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AgentDef(name="w", description="d", allowed_tools=[], typo_field=1)


def test_result_defaults_to_no_handoffs():
    r = Result(status="ok")
    assert r.handoffs == []
    assert r.outputs == []


def test_result_parses_handoffs():
    r = Result.model_validate(
        {
            "status": "ok",
            "summary": "done",
            "outputs": ["draft.md"],
            "handoffs": [{"agent": "reviewer", "intent": "review", "artifacts": {"inputs": ["draft.md"]}}],
        }
    )
    assert r.handoffs[0] == Handoff(
        agent="reviewer", intent="review", artifacts=TaskArtifacts(inputs=["draft.md"])
    )


def test_result_rejects_bad_status():
    with pytest.raises(ValidationError):
        Result(status="maybe")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentharness.models'`

- [ ] **Step 3: Implement `src/agentharness/models.py`**

```python
"""Every type shared across the harness. This module imports nothing from agentharness."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCRATCH_REPO_ID = "_scratch"

PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions"]
BackoffKind = Literal["exponential", "linear", "fixed"]
ResultStatus = Literal["ok", "failed", "needs_input"]
RunStatus = Literal["ok", "failed", "timeout"]
TaskStatus = Literal["pending", "leased", "running", "done", "failed", "dead", "blocked"]


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 3
    backoff: BackoffKind = "exponential"


class AgentDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    model: str | None = None
    permission_mode: PermissionMode = "acceptEdits"
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    mcp_config: str | None = None
    system_prompt_file: str | None = None
    max_turns: int = 25
    timeout_seconds: int = 900
    concurrency: int = 1
    retries: RetryPolicy = Field(default_factory=RetryPolicy)
    repos: list[str] = Field(default_factory=list)
    can_handoff_to: list[str] = Field(default_factory=list)


class TaskArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_ref: str | None = None
    inputs: list[str] = Field(default_factory=list)


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    parent_task_id: str | None = None
    agent: str
    repo: str | None = None
    intent: str
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: TaskArtifacts = Field(default_factory=TaskArtifacts)
    idempotency_key: str
    priority: int = 5
    attempt: int = 1
    created_at: datetime
    schedule_id: str | None = None

    @property
    def artifact_dir(self) -> str:
        return f".harness/runs/{self.trace_id}/{self.task_id}"


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    intent: str
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: TaskArtifacts = Field(default_factory=TaskArtifacts)


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    summary: str = ""
    outputs: list[str] = Field(default_factory=list)
    handoffs: list[Handoff] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str
    trace_id: str
    agent: str
    attempt: int
    status: RunStatus
    exit_code: int | None = None
    is_error: bool = False
    degraded: bool = False
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    claude_session_id: str | None = None
    num_turns: int | None = None
    total_cost_usd: float | None = None
    workspace_path: str | None = None
    output_ref: str | None = None
    branch: str | None = None
    stdout_log: str | None = None
    stderr_log: str | None = None


class RepoDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    url: str
    integration_branch: str = "harness/integration"
    base_branch: str = "main"


class ScheduleDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: str
    cron: str
    agent: str
    intent: str
    repo: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS, 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agentharness/models.py tests/test_models.py
git commit -m "feat: shared domain models for tasks, agents, results, and runs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---
### Task 3: Agent registry

**Files:**
- Create: `src/agentharness/registry/__init__.py`, `src/agentharness/registry/agents.py`
- Create: `tests/test_agent_registry.py`

**Interfaces:**
- Consumes: `AgentDef` from `models`.
- Produces:
  - `class AgentValidationError(Exception)`
  - `class AgentRegistry` with:
    - `AgentRegistry.load(agents_dir: Path, known_repos: set[str] | None = None) -> AgentRegistry`
    - `.get(name: str) -> AgentDef` (raises `KeyError`)
    - `.names() -> list[str]` (sorted)
    - `.can_handoff(src: str, dst: str) -> bool`
    - `.system_prompt(name: str) -> str | None` — reads `system_prompt_file` relative to `agents_dir`

**Validation rules (each gets a test):** filename stem must equal `name`; every entry in `can_handoff_to` must be a loaded agent; every entry in `repos` must be in `known_repos` when supplied; `system_prompt_file` must exist; duplicate names reject; unknown YAML keys reject (inherited from `extra="forbid"`).

- [ ] **Step 1: Write failing tests** covering: loads two agents from a dir; `get` returns the parsed `AgentDef`; `names()` sorted; `can_handoff` true for declared target and false otherwise; `can_handoff` false for unknown source; unknown handoff target raises `AgentValidationError` naming the offender; unknown repo raises; filename/name mismatch raises; missing system prompt file raises; `system_prompt` returns file contents.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_agent_registry.py -v` — expect FAIL (no module).
- [ ] **Step 3: Implement** `agents.py`: glob `*.yaml`, `yaml.safe_load`, `AgentDef.model_validate`, collect into a dict, then run cross-reference validation in a second pass (so forward references between agents work). Raise `AgentValidationError` with the agent name and the offending value in the message.
- [ ] **Step 4: Run** the same command — expect PASS.
- [ ] **Step 5: Commit** `feat: agent registry with routing allow-list validation`

---

### Task 4: Repo registry

**Files:**
- Create: `src/agentharness/registry/repos.py`
- Create: `tests/test_repo_registry.py`

**Interfaces:**
- Consumes: `RepoDef`, `SCRATCH_REPO_ID`, `Config`.
- Produces:
  - `class RepoRegistry(config: Config)` with:
    - `.add(repo_id: str, url: str, integration_branch: str | None = None, base_branch: str | None = None) -> RepoDef` — writes `<home>/repos.yaml` and creates the bare mirror
    - `.get(repo_id: str) -> RepoDef` (raises `KeyError`)
    - `.list() -> list[RepoDef]`
    - `.mirror_path(repo_id: str) -> Path` — `<home>/repos/<repo_id>.git`
    - `.sync(repo_id: str) -> None` — `git remote update --prune` under `repo_lock`
    - `.ensure_scratch() -> RepoDef` — idempotently creates `<home>/repos/_scratch.git` as a bare repo with an empty root commit on `main`, registered with `url=""`
    - `.resolve(repo_id: str | None) -> RepoDef` — `None` maps to the scratch repo

**Note:** `ensure_scratch` must produce a real starting commit so `base_ref` is never empty. Create it with `git commit-tree $(git hash-object -t tree /dev/null) -m "root"` and point `refs/heads/main` at the result.

- [ ] **Step 1: Write failing tests**: `add` creates the mirror dir and persists to `repos.yaml`; a second `RepoRegistry` on the same home sees it; `get` on unknown id raises `KeyError`; `mirror_path` shape; `ensure_scratch` is idempotent and leaves `refs/heads/main` resolvable; `resolve(None)` returns the scratch repo; `resolve("app")` returns the registered repo; `add` of a local path url produces a mirror whose `HEAD` resolves.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.** Depends on `git/mirror.py` primitives; if Task 6 has not landed, implement `clone_mirror`/`init_bare` inline in `mirror.py` first — this task and Task 6 may be merged if executed by one agent.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: managed repo registry with bare mirrors and scratch repo`

---

### Task 5: SQLite run store

**Files:**
- Create: `src/agentharness/store/__init__.py`, `src/agentharness/store/db.py`, `src/agentharness/store/runs.py`
- Create: `tests/test_run_store.py`

**Interfaces:**
- Consumes: `Task`, `RunRecord`, `ScheduleDef`, `TaskStatus`.
- Produces:
  - `connect(db_path: Path) -> sqlite3.Connection` — enables WAL and foreign keys, applies migrations
  - `class RunStore(db_path: Path)` with:
    - `.record_task(task: Task, status: TaskStatus = "pending") -> None` (upsert on `task_id`)
    - `.set_task_status(task_id: str, status: TaskStatus) -> None`
    - `.record_run(run: RunRecord) -> None` (upsert on `run_id`)
    - `.record_handoff(parent_task_id: str, child_task_id: str | None, agent: str, accepted: bool, reason: str | None = None) -> None`
    - `.event(kind: str, *, task_id=None, trace_id=None, run_id=None, agent=None, data: dict | None = None) -> None`
    - `.trace_open_count(trace_id: str) -> int` — tasks whose status is `pending`/`leased`/`running`
    - `.trace_leaf_branches(trace_id: str) -> list[str]` — `run/*` branches of successful runs whose task has no child task
    - `.trace_merged(trace_id: str, merge_ref: str) -> None`
    - `.recent_runs(limit: int = 50) -> list[RunRecord]`
    - `.get_run(run_id: str) -> RunRecord | None`
    - `.trace_runs(trace_id: str) -> list[RunRecord]`
    - `.upsert_schedule(s: ScheduleDef, next_fire_at: datetime) -> None`
    - `.due_schedules(now: datetime) -> list[tuple[ScheduleDef, datetime]]`
    - `.list_schedules() -> list[ScheduleDef]`
    - `.delete_schedule(schedule_id: str) -> None`

**Schema (`db.py`, `SCHEMA_VERSION = 1`):** tables `tasks(task_id PK, trace_id, parent_task_id, agent, repo, intent, payload_json, artifacts_json, idempotency_key, priority, attempt, status, created_at, schedule_id)`, `runs(run_id PK, task_id, trace_id, agent, attempt, status, exit_code, is_error, degraded, started_at, ended_at, duration_ms, claude_session_id, num_turns, total_cost_usd, workspace_path, output_ref, branch, stdout_log, stderr_log)`, `handoffs(id PK, parent_task_id, child_task_id, agent, accepted, reason, created_at)`, `schedules(schedule_id PK, cron, agent, intent, repo, payload_json, enabled, next_fire_at, last_fired_at)`, `traces(trace_id PK, merged_ref, merged_at)`, `events(id PK, ts, kind, task_id, trace_id, run_id, agent, data_json)`. Indexes on `tasks(trace_id)`, `tasks(status)`, `runs(trace_id)`, `events(trace_id)`, `events(ts)`.

- [ ] **Step 1: Write failing tests**: schema applies on a fresh file and re-applies idempotently; `record_task` then `get` via `trace_open_count` = 1; `set_task_status("done")` drops open count to 0; `record_run` roundtrips a `RunRecord` through `get_run` unchanged; `recent_runs` orders newest first and respects `limit`; `trace_leaf_branches` returns only the branch of the childless task in a 3-task linear chain; with a fan-out (one parent, two children) it returns both child branches and not the parent; failed runs are excluded from leaves; `event` rows are readable and ordered; `due_schedules` returns only schedules whose `next_fire_at <= now` and `enabled = 1`.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `db.py` (schema constant + `connect` applying it inside a transaction, `PRAGMA user_version` gate) then `runs.py` (row ⇄ model mapping; JSON columns via `json.dumps`/`loads`; datetimes stored as ISO-8601 UTC strings).
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: SQLite run store for tasks, runs, handoffs, schedules, and events`

---
## Phase 2 — Git plane

### Task 6: Repo lock and git primitives

**Files:**
- Create: `src/agentharness/git/__init__.py`, `src/agentharness/git/lock.py`, `src/agentharness/git/mirror.py`
- Create: `tests/test_git_mirror.py`, `tests/conftest.py` (extend)

**Interfaces:**
- Consumes: `Config`.
- Produces:
  - `repo_lock(home: Path, repo_id: str, timeout: float = 120.0)` — context manager, `fcntl.flock` on `<home>/locks/<repo_id>.lock`, raises `LockTimeout` on expiry
  - `class GitError(RuntimeError)` carrying `.argv`, `.returncode`, `.stderr`
  - `git(*args: str, cwd: Path | None = None, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess[str]`
  - `clone_mirror(url: str, dest: Path) -> None`
  - `init_bare(dest: Path) -> None`
  - `empty_root_commit(bare: Path, branch: str = "main") -> str`
  - `fetch(mirror: Path) -> None`
  - `resolve_ref(mirror: Path, ref: str) -> str`
  - `branch_exists(mirror: Path, branch: str) -> bool`
  - `create_branch(mirror: Path, branch: str, at: str) -> None`
  - `delete_branch(mirror: Path, branch: str) -> None`
  - `list_branches(mirror: Path, pattern: str = "*") -> list[str]`
  - `commit_time(mirror: Path, ref: str) -> datetime`
  - `is_ancestor(mirror: Path, a: str, b: str) -> bool`
- Extend `tests/conftest.py` with `origin_repo(tmp_path) -> Path`: a non-bare repo with one commit on `main` containing `README.md`, usable as a clone URL.

**Note on `git()`:** always pass `-c user.name=... -c user.email=...` from `Config` for commit-producing calls, and set `GIT_TERMINAL_PROMPT=0` in the environment so a credential prompt fails fast instead of hanging a worker.

- [ ] **Step 1: Write failing tests**: `git("rev-parse","--git-dir")` succeeds in a repo; `git` on a bad subcommand raises `GitError` with stderr populated; `check=False` returns non-zero without raising; `clone_mirror` of `origin_repo` produces a bare dir whose `refs/heads/main` resolves; `init_bare` + `empty_root_commit` yields a resolvable `main` with an empty tree; `resolve_ref` on unknown ref raises `GitError`; `branch_exists` true/false; `create_branch` then `list_branches("run/*")` returns it; `delete_branch` removes it; `is_ancestor` true for a commit and its descendant, false reversed; `repo_lock` is re-entrant-safe across processes (second acquire with `timeout=0.1` raises `LockTimeout` while the first is held, tested with a thread).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_git_mirror.py -v` — expect FAIL.
- [ ] **Step 3: Implement** `lock.py` then `mirror.py`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: git mirror primitives and per-repo ref lock`

---

### Task 7: Worktree lifecycle

**Files:**
- Create: `src/agentharness/git/worktree.py`
- Create: `tests/test_git_worktree.py`

**Interfaces:**
- Consumes: `git`, `repo_lock`, `Config`.
- Produces:
  - `add_worktree(mirror: Path, path: Path, branch: str, base_ref: str) -> None` — `git worktree add --no-checkout=false <path> -b <branch> <base_ref>`
  - `commit_all(worktree: Path, message: str, cfg: Config) -> str | None` — stages everything including untracked, commits, returns SHA; returns `None` when the tree is clean
  - `push_branch(worktree: Path, branch: str) -> None` — pushes the branch back into the mirror (`git push origin <branch>` is wrong for a mirror-backed worktree; the branch already lives in the mirror's ref store, so this is a no-op assertion that `resolve_ref(mirror, branch)` matches the worktree HEAD)
  - `remove_worktree(mirror: Path, path: Path, force: bool = True) -> None`
  - `prune_worktrees(mirror: Path) -> None`
  - `write_json(worktree: Path, rel_path: str, data: dict) -> Path` — creates parent dirs, writes indented JSON

**Critical detail:** because worktrees are added off the **bare mirror**, the new branch ref is created directly in the mirror's `refs/heads/`. There is no push step. `commit_all` therefore makes the commit immediately visible to `resolve_ref(mirror, branch)`. A test must assert exactly this.

- [ ] **Step 1: Write failing tests**: `add_worktree` creates the dir with the base commit's files present; the branch exists in the mirror after add; `write_json` creates nested dirs under `.harness/runs/tr/t/`; `commit_all` returns a SHA and `resolve_ref(mirror, branch)` equals it; `commit_all` on a clean tree returns `None`; committed content is retrievable via `git show <sha>:.harness/runs/tr/t/task.json`; a second worktree added off the first's output commit sees the first's files (this is artifact inheritance — the core mechanism); `remove_worktree` deletes the dir but `resolve_ref` on the branch still works; `prune_worktrees` after a manual `rmtree` clears the stale registration.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.** Use `git -C <worktree> add -A` then `commit --allow-empty=false`; detect the clean case with `git status --porcelain` before committing.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: per-run git worktree lifecycle with artifact inheritance`

---

### Task 8: Integration-branch merge

**Files:**
- Create: `src/agentharness/git/merge.py`
- Create: `tests/test_git_merge.py`

**Interfaces:**
- Consumes: `git`, `repo_lock`, `add_worktree`, `remove_worktree`, `RepoDef`, `Config`.
- Produces:
  - `class MergeConflict(RuntimeError)` with `.branch: str` and `.files: list[str]`
  - `merge_leaves(mirror: Path, branches: list[str], repo: RepoDef, cfg: Config) -> str` — ensures `integration_branch` exists (creating it at `base_branch` if absent), adds a temporary worktree on it, merges each branch in order with `--no-ff`, returns the final integration SHA, removes the worktree. On conflict: `git merge --abort`, remove the worktree, raise `MergeConflict` naming the branch and conflicted paths.
  - `gc_run_branches(mirror: Path, older_than_days: int, cfg: Config) -> list[str]` — deletes `run/*` branches whose tip commit is older than the window **and** is an ancestor of `integration_branch`; returns deleted names.

**Safety rule with a test:** `merge_leaves` must never touch `base_branch`. A test asserts `resolve_ref(mirror, "main")` is unchanged across a merge.

- [ ] **Step 1: Write failing tests**: merging one leaf into a fresh integration branch creates it and includes the leaf's file; merging two non-overlapping leaves includes both files; merging two leaves that edit the same line raises `MergeConflict` with the branch name and file listed; after a conflict the temporary worktree directory no longer exists and the integration branch is unchanged; `main` is byte-identical before and after any merge; `gc_run_branches` deletes an old merged branch, keeps a recent one, and keeps an old unmerged one.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: merge leaf run branches into the integration branch`

---

## Phase 3 — Queue

### Task 9: Filesystem queue

**Files:**
- Create: `src/agentharness/queue/__init__.py`, `src/agentharness/queue/base.py`, `src/agentharness/queue/filesystem.py`
- Create: `tests/test_queue.py`

**Interfaces:**
- Consumes: `Task`, `Config`.
- Produces:
  - `class Queue(ABC)` with abstract `enqueue`, `lease`, `ack`, `nack`, `dead_letter`, `depth`, `reclaim_expired`, `list_dead`, `agents_with_work`
  - `class FilesystemQueue(Queue)`, constructed as `FilesystemQueue(root: Path)`:
    - `.enqueue(task: Task) -> bool` — `False` when `idempotency_key` was already seen
    - `.lease(agent: str, visibility_timeout: int) -> Task | None`
    - `.ack(task: Task) -> None`
    - `.nack(task: Task, *, requeue: bool = True, delay_seconds: float = 0.0) -> None`
    - `.dead_letter(task: Task, reason: str) -> None`
    - `.depth(agent: str) -> int`
    - `.reclaim_expired(now: float | None = None) -> list[Task]`
    - `.promote_delayed(now: float | None = None) -> list[Task]`
    - `.list_dead(agent: str) -> list[Task]`
    - `.replay_dead(agent: str, task_id: str) -> bool`
    - `.agents_with_work() -> list[str]`

**On-disk layout** under `<queues_dir>/<agent>/`:
- `pending/<priority:02d>-<created_epoch:013d>-<task_id>.json`
- `delayed/<ready_epoch:013d>-<task_id>.json`
- `processing/<deadline_epoch:013d>-<task_id>.json`
- `dead/<task_id>.json` (with a sibling `<task_id>.reason.txt`)
- `.keys/<sha256(idempotency_key)>` — created with `O_CREAT|O_EXCL` to make dedup atomic

Leasing is `os.rename` from `pending/` to `processing/`; the rename either wins or raises `FileNotFoundError`, which is how two workers racing for one task is resolved. Ordering: sort `pending/` filenames lexicographically, which sorts by priority then creation time.

- [ ] **Step 1: Write failing tests**: enqueue then depth = 1; lease returns the task and depth drops to 0; lease on an empty queue returns `None`; ack removes the processing file; duplicate `idempotency_key` returns `False` and does not increase depth; priority 1 leases before priority 9 enqueued earlier; equal priority leases FIFO by creation time; `nack(requeue=True)` returns it to pending with `attempt` incremented; `nack(requeue=True, delay_seconds=60)` lands it in `delayed/` and it is not leasable until `promote_delayed` runs with a later `now`; `dead_letter` writes the task and the reason and it appears in `list_dead`; `replay_dead` moves it back to pending and returns `True`; `reclaim_expired` returns tasks whose deadline passed and makes them leasable again, while leaving unexpired leases alone; two threads leasing concurrently each get a distinct task and never the same one; `agents_with_work` lists only agents with pending or ready-delayed work.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `base.py` then `filesystem.py`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: durable filesystem queue with leases, delays, and dead-lettering`

---
## Phase 4 — Runner

### Task 10: Executor abstraction

**Files:**
- Create: `src/agentharness/runner/__init__.py`, `src/agentharness/runner/executor.py`
- Create: `tests/test_executor.py`

**Interfaces:**
- Consumes: `AgentDef`, `Config`.
- Produces:
  - `@dataclass ExecRequest`: `prompt: str`, `system_prompt: str | None`, `allowed_tools: list[str]`, `disallowed_tools: list[str]`, `permission_mode: str`, `model: str | None`, `max_turns: int`, `mcp_config: Path | None`, `cwd: Path`, `timeout_seconds: int`
  - `@dataclass ExecResult`: `exit_code: int`, `is_error: bool`, `stdout: str`, `stderr: str`, `cli_json: dict | None`, `session_id: str | None`, `num_turns: int | None`, `total_cost_usd: float | None`, `result_text: str | None`, `duration_ms: int`, `timed_out: bool`
  - `class Executor(ABC)` with `run(self, req: ExecRequest) -> ExecResult`
  - `class LocalExecutor(Executor)`, `__init__(self, binary: str = "claude")`, plus a **pure** `build_argv(self, req: ExecRequest) -> list[str]` that performs no I/O
  - `class FakeExecutor(Executor)`, `__init__(self, script: Callable[[ExecRequest], ExecResult] | None = None)`, recording every request in `.requests: list[ExecRequest]`. The script may write files into `req.cwd` — that is how tests simulate an agent producing `result.json`.
  - `fake_ok(result_payload: dict, *, cost: float = 0.01) -> Callable[...]` — a helper that writes `result_payload` to the request's artifact dir and returns a success `ExecResult`

**argv contract (each clause is a test):** starts with `[binary, "-p", prompt]`; includes `--output-format json`; includes `--permission-mode <mode>`; includes `--max-turns <n>`; includes `--add-dir <cwd>`; includes `--allowedTools` with tools comma-joined, omitted entirely when the list is empty; includes `--disallowedTools` only when non-empty; includes `--model` only when set; includes `--mcp-config` only when set; includes `--append-system-prompt` only when a system prompt is given; **never contains `--resume` or `--continue`**.

- [ ] **Step 1: Write failing tests** for every argv clause above plus: `LocalExecutor.run` on a fake binary (a shell script written to `tmp_path` that echoes a canned CLI JSON envelope) populates `session_id`, `num_turns`, `total_cost_usd`, `result_text` from the JSON; a non-zero exit sets `exit_code` and `is_error`; malformed stdout leaves `cli_json` as `None` without raising; a binary that sleeps past `timeout_seconds` sets `timed_out=True` and is killed (use `timeout_seconds=1`); `FakeExecutor` records requests and its script can write into `cwd`.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.** `LocalExecutor.run` uses `subprocess.run(argv, cwd=req.cwd, capture_output=True, text=True, timeout=req.timeout_seconds)` wrapped in `try/except subprocess.TimeoutExpired`. Parse stdout as JSON; the CLI's envelope keys are `session_id`, `num_turns`, `total_cost_usd`, `is_error`, `result`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: claude -p executor with a fake for zero-cost testing`

---

### Task 11: Prompt composition

**Files:**
- Create: `src/agentharness/runner/prompt.py`
- Create: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `Task`, `AgentDef`.
- Produces:
  - `PROTOCOL_PREAMBLE: str` — a module-level template
  - `compose_prompt(task: Task, agent: AgentDef) -> str`

The composed prompt must contain, verbatim and testably: the agent name; the path `.harness/runs/<trace>/<task>/task.json`; the path `.harness/runs/<trace>/<task>/result.json`; the intent; the literal list of allowed handoff targets; and, when `can_handoff_to` is empty, the sentence `You may not hand off to any agent; this is a terminal step.`

- [ ] **Step 1: Write failing tests**: prompt names the agent; contains both artifact paths built from the task's ids; contains the intent; lists each allowed target; states the terminal-step sentence when the allow-list is empty and does not state it otherwise; does not contain the word `resume`; is under 2000 characters (the prompt must stay tiny — state lives in files).
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: protocol preamble and prompt composition`

---

### Task 12: Result parsing

**Files:**
- Create: `src/agentharness/runner/result.py`
- Create: `tests/test_result_parsing.py`

**Interfaces:**
- Consumes: `Result`, `ExecResult`, `Task`.
- Produces:
  - `@dataclass ParsedResult`: `result: Result`, `degraded: bool`, `reason: str | None`
  - `parse_result(worktree: Path, task: Task, exec_result: ExecResult) -> ParsedResult`

**Rules (each a test):** a valid `result.json` in the task's artifact dir parses with `degraded=False`; a missing file falls back to a `Result(status="ok", summary=exec_result.result_text)` with `degraded=True` and a reason; invalid JSON falls back the same way with a different reason; a `result.json` that fails schema validation falls back with `degraded=True`; when `exec_result.is_error` is true the fallback status is `failed`; a `result.json` present *and* `is_error` true still parses the file but the run is failed by the caller, not here; handoffs survive the round trip.

- [ ] **Step 1: Write failing tests** per the rules above.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: result.json parsing with degraded fallback`

---

### Task 13: Run lifecycle

**Files:**
- Create: `src/agentharness/runner/runner.py`
- Create: `tests/test_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 1–12.
- Produces:
  - `@dataclass RunOutcome`: `run: RunRecord`, `result: Result | None`, `error: str | None`
  - `class Runner`, `__init__(self, cfg: Config, agents: AgentRegistry, repos: RepoRegistry, store: RunStore, executor: Executor)`
  - `.execute(self, task: Task) -> RunOutcome` — the full lifecycle, synchronous (the dispatcher calls it via `asyncio.to_thread`)

**Lifecycle, in order:**
1. Resolve agent and repo (`repo=None` → scratch).
2. Determine `base_ref`: `task.artifacts.base_ref` if set, else `resolve_ref(mirror, repo.base_branch)`.
3. Under `repo_lock`: `add_worktree` at `<worktrees_dir>/<trace_id>/<task_id>` on branch `run/<task_id>`.
4. `write_json(worktree, f"{task.artifact_dir}/task.json", task.model_dump(mode="json"))`.
5. `compose_prompt`, build `ExecRequest`, call `executor.run`.
6. Write `logs/stdout.log`, `logs/stderr.log`, `logs/cli.json` into the artifact dir.
7. `parse_result`.
8. Write `result.json` into the artifact dir when it was absent (so the commit always contains one).
9. Under `repo_lock`: `commit_all` → `output_ref`; then `remove_worktree`.
10. Build and persist the `RunRecord`; emit `run.started` / `run.finished` events.

**Failure handling inside `execute`:** any exception is caught, the run is recorded with `status="failed"` and the message in `error`, and the worktree is removed in a `finally`. A timeout produces `status="timeout"`. The method never raises.

- [ ] **Step 1: Write failing tests** using `FakeExecutor` against a real scratch repo: a successful run returns `status="ok"` with a non-null `output_ref`; the worktree directory is gone afterwards but `run/<task_id>` resolves in the mirror; `task.json` is retrievable from the commit and equals the input task; `result.json` is in the commit; `logs/stdout.log` is in the commit; a run whose fake writes no `result.json` is marked `degraded=True` and still commits; a fake returning `is_error=True` yields `status="failed"`; a fake returning `timed_out=True` yields `status="timeout"`; a second task carrying the first run's `output_ref` as `base_ref` sees the first run's outputs in its worktree; an exception raised by the executor is caught and recorded rather than propagated, and leaves no worktree behind; the `RunRecord` carries `total_cost_usd` and `num_turns` from the `ExecResult`; two `run.*` events land in the store.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: run lifecycle wiring worktrees, claude -p, and the run store`

---

## Phase 5 — Dispatcher

### Task 14: Routing and retry policy

**Files:**
- Create: `src/agentharness/dispatch/__init__.py`, `src/agentharness/dispatch/routing.py`, `src/agentharness/dispatch/retry.py`
- Create: `tests/test_routing.py`, `tests/test_retry.py`

**Interfaces:**
- Produces (`routing.py`):
  - `@dataclass RoutedHandoff`: `task: Task | None`, `handoff: Handoff`, `accepted: bool`, `reason: str | None`
  - `route_handoffs(parent: Task, agent: AgentDef, result: Result, output_ref: str, registry: AgentRegistry) -> list[RoutedHandoff]`
- Produces (`retry.py`):
  - `backoff_seconds(attempt: int, policy: RetryPolicy, base: float = 30.0, cap: float = 3600.0) -> float`
  - `should_retry(attempt: int, policy: RetryPolicy) -> bool`

**Routing rules (each a test):** an allowed target produces a `Task` with a fresh `task_id`, the parent's `trace_id`, `parent_task_id` set, `repo` inherited from the parent, `base_ref` set to `output_ref`, `attempt=1`, and `idempotency_key` deterministic as `f"{parent.task_id}:{index}:{handoff.agent}:{handoff.intent}"`; a target absent from `can_handoff_to` is rejected with a reason naming both agents and yields `task=None`; a target that is not a registered agent is rejected with a distinct reason; an empty handoff list yields an empty result; the same parent and result routed twice produce identical idempotency keys (so a crash-retry deduplicates); handoff `payload` and `artifacts.inputs` are carried onto the child task.

**Retry rules:** `exponential` gives 30, 60, 120 for attempts 1, 2, 3 and is capped at `cap`; `linear` gives 30, 60, 90; `fixed` gives 30 always; `should_retry` is true while `attempt < max_attempts` and false at or beyond it.

- [ ] **Step 1: Write failing tests** per the rules above.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: guard-railed handoff routing and retry backoff`

---

### Task 15: Concurrency and the rate-limit gate

**Files:**
- Create: `src/agentharness/dispatch/limits.py`
- Create: `tests/test_limits.py`

**Interfaces:**
- Consumes: `Config`, `ExecResult`.
- Produces:
  - `class RateLimitGate`, `__init__(self, patterns: list[str], initial_backoff: float, max_backoff: float)`
    - `.detect(exec_result: ExecResult) -> bool` — case-insensitive substring match over `result_text` and `stderr`, only when `is_error` is true
    - `.trip() -> None` — sets paused and doubles the backoff (bounded by `max_backoff`)
    - `.clear() -> None` — clears paused and resets backoff to `initial_backoff`
    - `.paused: bool` property
    - `async .wait_until_clear(sleep=asyncio.sleep) -> None` — returns immediately when not paused; otherwise sleeps the current backoff and auto-clears, so dispatch resumes without operator action
  - `class ConcurrencyLimiter`, `__init__(self, global_limit: int)`
    - `.slot(agent: str, agent_limit: int)` — async context manager acquiring the agent semaphore then the global one, always in that order to avoid deadlock
    - `.active(agent: str) -> int`, `.active_total() -> int`

- [ ] **Step 1: Write failing tests**: `detect` matches `rate limit` and `429` in `result_text` and in `stderr`, case-insensitively; `detect` is false when `is_error` is false even if the text matches; `trip` sets paused and backoff doubles across three trips, capping at `max_backoff`; `clear` resets both; `wait_until_clear` returns immediately when not paused; when paused it awaits the injected sleep once, is then unpaused, and the recorded sleep equals the current backoff; `ConcurrencyLimiter` allows exactly `global_limit` simultaneous holders across agents; per-agent limit is enforced below the global one; releasing a slot lets a waiter through; `active`/`active_total` track correctly.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: global concurrency limiter and auto-resuming rate-limit gate`

---
### Task 16: Dispatcher and trace completion

**Files:**
- Create: `src/agentharness/dispatch/dispatcher.py`
- Create: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: everything from Tasks 1–15.
- Produces:
  - `class Dispatcher`, `__init__(self, cfg: Config, agents: AgentRegistry, repos: RepoRegistry, queue: Queue, store: RunStore, runner: Runner, gate: RateLimitGate | None = None)`
    - `async .run_forever(self) -> None` — the poll loop; exits cleanly on `.stop()`
    - `.stop(self) -> None`
    - `async .tick(self) -> int` — one poll pass; returns how many tasks were dispatched. **Tests drive `tick`, never `run_forever`.**
    - `async .handle_task(self, task: Task) -> None` — lease-to-completion for one task
    - `.submit(self, agent: str, intent: str, *, repo: str | None = None, payload: dict | None = None, schedule_id: str | None = None) -> Task` — mints a root task with a fresh `trace_id` and enqueues it

**`tick` sequence:** reclaim expired leases → promote ready delayed tasks → `await gate.wait_until_clear()` → for each agent with work, while a slot is free, lease and spawn `handle_task` as a task on the loop.

**`handle_task` sequence:** mark task `running`; `await asyncio.to_thread(runner.execute, task)`; then branch:
- **ok / degraded** → `route_handoffs`, enqueue accepted children (recording rejected ones as `handoffs` rows with `accepted=0`), `ack`, mark task `done`, then check trace completion.
- **needs_input** → `ack`, mark task `blocked`, no handoffs, then check trace completion.
- **failed / timeout** → if `gate.detect(...)` then `gate.trip()` and `nack(requeue=True, delay_seconds=0)` **without** incrementing the attempt (a rate limit is not the task's fault); else if `should_retry` then `nack(requeue=True, delay_seconds=backoff_seconds(...))` and mark `pending`; else `dead_letter` and mark `dead`, then check trace completion.

**Trace completion:** when `store.trace_open_count(trace_id) == 0`, take `store.trace_leaf_branches(trace_id)`; if non-empty, call `merge_leaves` under `repo_lock`, record the merge ref via `store.trace_merged`, and emit a `trace.merged` event. On `MergeConflict`, emit `trace.merge_conflict`, dead-letter nothing (the tasks are already done) but mark the trace with a null merge ref and log the conflicted branches for manual resolution.

- [ ] **Step 1: Write failing tests**, all with `FakeExecutor` and a real scratch repo:
  - a single submitted task is leased, executed, acked, and marked `done` by one `tick` plus awaiting the spawned work
  - a fake emitting one allowed handoff enqueues exactly one child task on the target agent's queue with `base_ref` equal to the parent's `output_ref`
  - a fake emitting a handoff to a **disallowed** agent enqueues nothing and records a `handoffs` row with `accepted=0`
  - a fan-out of two handoffs enqueues two children, and both run on a subsequent tick
  - a three-agent linear chain (`a→b→c`) completes with three runs and the final trace merges into the integration branch, which then contains all three agents' outputs
  - `main` is unchanged after the trace merges
  - two leaves that conflict produce a `trace.merge_conflict` event and leave the integration branch unchanged
  - a failing fake retries with the configured backoff and eventually dead-letters at `max_attempts`, with the task visible via `list_dead`
  - a rate-limited failure trips the gate, requeues the task without incrementing `attempt`, and dispatches nothing on the next tick while paused
  - a `needs_input` result marks the task `blocked` and enqueues no handoffs
  - the global concurrency cap is respected: with `max_concurrency=1` and two queued tasks, only one executor call is in flight at a time (assert via a fake that records concurrent entry counts)
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS. **This is the first end-to-end milestone: a task graph executes and merges without a single network call.**
- [ ] **Step 5: Commit** `feat: dispatcher with routing, retries, rate limiting, and trace merge`

---

## Phase 6 — Scheduler, CLI, service

### Task 17: Durable cron scheduler

**Files:**
- Create: `src/agentharness/scheduler/__init__.py`, `src/agentharness/scheduler/scheduler.py`
- Create: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `ScheduleDef`, `RunStore`, `Dispatcher`, `croniter`.
- Produces:
  - `next_fire(cron: str, after: datetime) -> datetime`
  - `class Scheduler`, `__init__(self, store: RunStore, dispatcher: Dispatcher)`
    - `.add(schedule: ScheduleDef) -> None` — validates the cron expression, computes `next_fire_at`, upserts
    - `.remove(schedule_id: str) -> None`
    - `.list() -> list[ScheduleDef]`
    - `.tick(now: datetime | None = None) -> list[Task]` — fires every due schedule, submitting a root task stamped with `schedule_id`, then advances `next_fire_at`
    - `async .run_forever(self, interval: float = 30.0) -> None`

- [ ] **Step 1: Write failing tests**: `next_fire` for `0 7 * * *` after 06:00 is 07:00 the same day and after 08:00 is 07:00 the next day; `add` with an invalid cron raises `ValueError`; `add` persists and survives a fresh `Scheduler` over the same store; `tick` before the fire time submits nothing; `tick` at or after it submits exactly one task carrying `schedule_id`, `agent`, `intent`, and `payload`; `tick` immediately again submits nothing (because `next_fire_at` advanced); a disabled schedule never fires; a schedule whose `next_fire_at` is long past fires once, not once per missed interval.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: durable cron scheduler backed by the run store`

---

### Task 18: CLI

**Files:**
- Create: `src/agentharness/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Produces a Typer app `app` with these commands, all reading `AGENTHARNESS_HOME`:
  - `init` — create the home layout, the scratch repo, and a commented `config.yaml`
  - `serve [--no-web] [--port 8787]` — dispatcher + scheduler + dashboard in one asyncio process
  - `submit AGENT INTENT [--repo R] [--payload JSON] [--priority N]`
  - `agents list|show NAME|validate`
  - `repos add REPO_ID URL [--integration-branch B] [--base-branch B]`, `repos list`, `repos sync REPO_ID`
  - `queue list`, `queue peek AGENT`, `queue dead AGENT`, `queue replay AGENT TASK_ID`
  - `runs list [--limit N]`, `runs show RUN_ID`
  - `trace show TRACE_ID`
  - `schedule list|add|remove`
  - `gc [--days N] [--dry-run]`

Every command exits non-zero with a readable message on failure — never a traceback.

- [ ] **Step 1: Write failing tests** with Typer's `CliRunner`: `init` creates the home layout and scratch repo and is idempotent; `agents validate` exits 0 on a good dir and non-zero with the offending agent named on a bad one; `submit` for an unknown agent exits non-zero without enqueueing; `submit` for a known agent enqueues exactly one task and prints its `task_id` and `trace_id`; `queue list` shows the depth; `runs list` on an empty store prints a header and exits 0; `repos add` registers and `repos list` shows it; `gc --dry-run` deletes nothing and lists candidates.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: agentharness CLI`

---

### Task 19: launchd service

**Files:**
- Create: `deploy/com.agentharness.plist.template`, `deploy/install.sh`, `README.md`
- Create: `tests/test_deploy_template.py`

**Interfaces:** `install.sh` substitutes `__HOME__`, `__PYTHON__`, and `__HARNESS_HOME__` into the template, writes it to `~/Library/LaunchAgents/com.agentharness.plist`, and `launchctl bootstrap`s it. It refuses to run if `agentharness init` has not been run.

- [ ] **Step 1: Write a failing test** asserting the template contains no unsubstituted `__`-delimited placeholders after `install.sh --print` renders it, that `KeepAlive` is true, and that stdout/stderr are routed into `<harness home>/logs/`.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** the template, the script, and a README covering install, first run, and the `launchctl kickstart -k gui/$(id -u)/com.agentharness` restart line.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: launchd service definition and installer`

---

## Phase 7 — Observability

### Task 20: Structured logging and metrics

**Files:**
- Create: `src/agentharness/obs/__init__.py`, `src/agentharness/obs/logging.py`, `src/agentharness/obs/metrics.py`
- Modify: `src/agentharness/dispatch/dispatcher.py`, `src/agentharness/runner/runner.py` — replace ad-hoc store events with `log_event`
- Create: `tests/test_metrics.py`

**Interfaces:**
- Produces (`logging.py`): `configure_logging(home: Path, level: str = "INFO") -> None` writing JSON lines to `<home>/logs/harness.jsonl` and to stderr; `log_event(store: RunStore, kind: str, **fields) -> None` which both persists the event row and emits the log line, so the two never drift.
- Produces (`metrics.py`): `@dataclass Snapshot` with `queue_depths: dict[str,int]`, `dead_depths: dict[str,int]`, `active: dict[str,int]`, `runs_24h: int`, `failures_24h: int`, `cost_24h: float`, `paused: bool`; `snapshot(cfg, queue, store, limiter, gate) -> Snapshot`; `cost_by_agent(store, since: datetime) -> dict[str,float]`; `cost_by_trace(store, trace_id: str) -> float`; `latency_percentiles(store, since: datetime) -> dict[str,float]` returning `p50`/`p95`.

- [ ] **Step 1: Write failing tests**: `log_event` writes both a store row and a JSON line containing `kind`, `trace_id`, and a timestamp; `cost_by_agent` sums `total_cost_usd` per agent and ignores runs outside the window and runs with a null cost; `cost_by_trace` sums a whole trace; `latency_percentiles` on a known set returns the expected p50/p95; `snapshot` reports queue and dead depths per agent, the paused flag, and 24-hour run and failure counts.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement**, then update the dispatcher and runner to call `log_event`.
- [ ] **Step 4: Run** the full suite: `.venv/bin/pytest -v` — expect PASS.
- [ ] **Step 5: Commit** `feat: structured lifecycle logging and cost/latency metrics`

---

### Task 21: Read-only dashboard

**Files:**
- Create: `src/agentharness/web/__init__.py`, `src/agentharness/web/app.py`, `src/agentharness/web/templates/{base,index,runs,run,trace}.html`
- Create: `tests/test_web.py`

**Interfaces:**
- Produces: `create_app(cfg: Config, queue: Queue, store: RunStore, limiter, gate) -> FastAPI` with routes `GET /` (queue depths, paused banner, 24h cost, recent runs), `GET /runs`, `GET /runs/{run_id}` (record plus stdout/stderr from the commit), `GET /traces/{trace_id}` (the run tree with durations and costs), and `GET /api/snapshot` returning the `Snapshot` as JSON. **Read-only: no route mutates anything.**

- [ ] **Step 1: Write failing tests** with `TestClient`: `/` returns 200 and contains each agent's queue depth; `/api/snapshot` returns the snapshot fields; `/runs` lists a recorded run's id; `/runs/{id}` shows the agent, status, and cost; `/runs/unknown` returns 404; `/traces/{id}` lists every run in the trace in order; the paused banner appears only when the gate is tripped; **no route accepts POST/PUT/DELETE** (assert 405 on `POST /`).
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat: read-only dashboard for queues, runs, and traces`

---

## Phase 8 — Proof workload

### Task 22: The dev pipeline agent set

**Files:**
- Create: `examples/agents/{planner,implementer,reviewer}.yaml` and their `.system.md` files
- Create: `tests/test_dev_pipeline.py`
- Modify: `CLAUDE.md` — replace the scaffold placeholder with real orientation
- Modify: `docs/superpowers/specs/2026-07-19-agent-harness-design.md` — record the croniter deviation

**Agent set:** `planner` (Read/Write/Glob/Grep/Bash, `can_handoff_to: [implementer]`) → `implementer` (Read/Write/Edit/Glob/Grep/Bash, `can_handoff_to: [reviewer]`) → `reviewer` (Read/Glob/Grep/Bash, `can_handoff_to: []`, terminal).

- [ ] **Step 1: Write the failing acceptance test** — with `FakeExecutor` scripted per agent, submit one task to `planner` against a real throwaway repo and assert: three runs execute in order; each agent's worktree inherited the previous one's committed files; the trace merges into `harness/integration`; the integration branch contains the planner's plan, the implementer's code change, and the reviewer's report; `main` is untouched; `.harness/runs/<trace>/` holds three task/result pairs; the trace's total cost is the sum of the three runs.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Write the agent YAML and system prompts** so the test passes.
- [ ] **Step 4: Run the full suite** `.venv/bin/pytest -v` — expect PASS.
- [ ] **Step 5: Add the live smoke test** `tests/test_live_smoke.py`, marked `@pytest.mark.live`: one real `claude -p` run of a trivial agent (`Read`/`Write` only, `max_turns: 3`) that writes `result.json` into a scratch-repo worktree. Verify manually with `.venv/bin/pytest -m live -v`, then document in the README that it consumes subscription usage.
- [ ] **Step 6: Rewrite `CLAUDE.md`** with the real orientation: what the project is, the venv path, how to run tests, the module map, and the CLI-only / statelessness invariants.
- [ ] **Step 7: Commit** `feat: dev pipeline agent set and end-to-end acceptance test`

---

## Self-review notes

- **Spec coverage.** Every spec section maps to a task: §3 layout → 1, 4; §4 modules → all; §5 data model → 2, 5; §6 execution → 10–13; §7 merge → 8, 16; §8 failure handling → 9, 14, 15, 16; §9 observability → 20, 21; §10 testing → 10 (`FakeExecutor`) and every task's tests; §11 CLI → 18; §12 phases → the phase headings.
- **Deliberate spec deviations**, both recorded in Task 22 Step 1's doc edit: croniter instead of APScheduler; `push_branch` is a no-op assertion rather than a real push, because worktrees are added directly off the bare mirror so branch refs already live there.
- **Naming consistency check.** `output_ref` (never `out_ref`), `base_ref`, `artifact_dir`, `can_handoff_to`, `trace_open_count`, `trace_leaf_branches`, `merge_leaves`, `route_handoffs`, `backoff_seconds`, `should_retry`, `wait_until_clear` — these exact names appear identically in every task that references them.
