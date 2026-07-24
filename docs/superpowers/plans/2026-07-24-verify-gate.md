# Verify Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic `verify` step between `development` and `review` that runs a per-repo test command in the worktree and routes `request_changes` back to development on failure — no LLM involved.

**Architecture:** `verify` is a workflow step bound to a new finisher kind `"verify"` (the ADR-0016/0018 registry — replace-style, like `open-pr`), so it needs no agent persona and no new wiring seam. `VerifyBehavior` (a `ConsumerBehavior` in `behaviors/`) attaches the worktree, runs the repo's `verify` command through a new `CommandRunner` port, writes the output as an attempt-indexed artifact, commits it (invariant 9), and maps exit code → outcome. The command comes from `repos.json`, whose entries gain an optional object form.

**Tech Stack:** Python 3.11, asyncio subprocess, pytest with in-memory fakes.

**Spec:** `docs/superpowers/specs/2026-07-24-dev-flow-hardening-design.md` (increment 1).

## Global Constraints

- Project language is English — all code, comments, tests, commits.
- Conventional commits; commit straight into `main` (repo convention); the release workflow keys off `feat:`/`fix:`.
- Test command: `.venv/bin/pytest -q` from the repo root. Unit tests use in-memory drivers + `FakeClock` — no disk, no real sleeping. (The subprocess-driver test in Task 2 is a deliberate real-subprocess exception, kept sub-second, like `test_smoke_git.py` is for git.)
- `dispatcher.py`/`consumer.py` must not import the new port (`ports/command.py`) — add the guard to `tests/test_architecture.py` in the same style as the existing `ports.merge` guard.
- Exit code → outcome mapping: `0` → `done`, non-zero → `request_changes`. A crash/timeout raises → the consumer fails the task into `failed/`. A red suite is `request_changes`, never `failed`.

---

### Task 1: `verify_command` on the repository registry

`repos.json` entries gain an optional object form; the registry port learns to answer "what is this repo's verify command?".

**Files:**
- Modify: `src/harness/ports/repos.py`
- Modify: `src/harness/drivers/fs_repos.py`
- Modify: `src/harness/drivers/memory.py` (class `MemoryRepositoryRegistry`, around line 478)
- Test: `tests/test_repos.py` (extend)

**Interfaces:**
- Produces: `RepositoryRegistry.verify_command(name: str) -> str | None` — `None` for a string-form entry, an unknown name, or a missing/broken config (lenient like `names()`, never raises).
- Produces: `repos.json` value schema: `"name": "/path"` (unchanged) **or** `"name": {"path": "/path", "verify": "cmd"}` (`verify` optional inside the object form).
- Produces: `MemoryRepositoryRegistry(repos, verify=None)` — optional `verify: dict[str, str]` keyword.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repos.py` (follow its existing fixture style for writing the config file; shown here with a plain `tmp_path` write):

```python
def test_object_form_entry_resolves_path(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": {"path": "/srv/app", "verify": "pytest -q"}}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.resolve("app") == Path("/srv/app")
    assert registry.names() == ["app"]


def test_verify_command_object_form(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": {"path": "/srv/app", "verify": "pytest -q"}}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.verify_command("app") == "pytest -q"


def test_verify_command_string_form_is_none(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": "/srv/app"}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.verify_command("app") is None


def test_verify_command_unknown_or_missing_is_none(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": "/srv/app"}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.verify_command("ghost") is None
    assert FilesystemRepositoryRegistry(tmp_path / "absent.json").verify_command("app") is None


def test_object_form_without_path_is_not_found(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": {"verify": "pytest -q"}}))
    registry = FilesystemRepositoryRegistry(config)
    with pytest.raises(RepositoryNotFound):
        registry.resolve("app")


def test_memory_registry_verify_command(tmp_path):
    registry = MemoryRepositoryRegistry({"app": tmp_path}, verify={"app": "make test"})
    assert registry.verify_command("app") == "make test"
    assert registry.verify_command("other") is None
    assert MemoryRepositoryRegistry({"app": tmp_path}).verify_command("app") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_repos.py`
Expected: FAIL — `verify_command` does not exist; object-form entry raises in `resolve`.

- [ ] **Step 3: Implement**

`src/harness/ports/repos.py` — add a **concrete** default to the ABC (existing fakes and drivers stay valid without changes):

```python
    def verify_command(self, name: str) -> str | None:
        """The repo's verify command (test suite), or None when it has none.

        Lenient like `names()`: an unknown name or an unreadable registry is
        None, never an exception — a repo without a verify command is a normal,
        supported state (the verify step no-ops on it).
        """
        return None
```

`src/harness/drivers/fs_repos.py` — support the object form. Extract a `_load()` helper and route both value shapes through `_entry(name)`:

```python
class FilesystemRepositoryRegistry(RepositoryRegistry):
    def __init__(self, config: Path) -> None:
        self._config = Path(config)

    def _load(self) -> dict:
        try:
            raw = json.loads(self._config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RepositoryNotFound(
                f"repos config does not exist ({self._config})"
            ) from None
        except json.JSONDecodeError as error:
            raise RepositoryNotFound(
                f"repos config has broken JSON ({self._config}): {error}"
            ) from None
        if not isinstance(raw, dict):
            raise RepositoryNotFound(
                f"repos config has an invalid shape: expected object, "
                f"got {type(raw).__name__}"
            )
        return raw

    def resolve(self, name: str) -> Path:
        raw = self._load()
        try:
            entry = raw[name]
        except KeyError:
            raise RepositoryNotFound(
                f"repo {name!r} is not in the registry ({self._config})"
            ) from None
        # String form: the value is the path. Object form: {"path": ..., "verify": ...}.
        if isinstance(entry, dict):
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                raise RepositoryNotFound(
                    f"repo {name!r} has an object entry without a 'path' "
                    f"({self._config})"
                )
            return Path(path).expanduser()
        return Path(entry).expanduser()

    def verify_command(self, name: str) -> str | None:
        try:
            raw = self._load()
        except RepositoryNotFound:
            return None
        entry = raw.get(name)
        if isinstance(entry, dict):
            verify = entry.get("verify")
            if isinstance(verify, str) and verify.strip():
                return verify
        return None

    def names(self) -> list[str]:
        try:
            raw = json.loads(self._config.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(raw, dict):
            return []
        return list(raw)
```

`src/harness/drivers/memory.py` — extend `MemoryRepositoryRegistry`:

```python
class MemoryRepositoryRegistry(RepositoryRegistry):
    """Repository registry over a name → path dict."""

    def __init__(
        self, repos: dict[str, Path], *, verify: dict[str, str] | None = None
    ) -> None:
        self._repos = repos
        self._verify = verify or {}

    def resolve(self, name: str) -> Path:
        try:
            return self._repos[name]
        except KeyError:
            raise RepositoryNotFound(f"repo {name!r} is not in the registry") from None

    def verify_command(self, name: str) -> str | None:
        return self._verify.get(name)

    def names(self) -> list[str]:
        return list(self._repos)
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `.venv/bin/pytest -q tests/test_repos.py` → PASS, then `.venv/bin/pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add src/harness/ports/repos.py src/harness/drivers/fs_repos.py src/harness/drivers/memory.py tests/test_repos.py
git commit -m "feat(repos): optional object-form entries with a verify command"
```

---

### Task 2: `CommandRunner` port + subprocess driver + memory fake

**Files:**
- Create: `src/harness/ports/command.py`
- Create: `src/harness/drivers/subprocess_command.py`
- Modify: `src/harness/drivers/memory.py` (append `MemoryCommandRunner`)
- Test: `tests/test_command_runner.py` (new)
- Modify: `tests/test_architecture.py` (guard: dispatcher/consumer don't import `ports.command` — copy the existing `ports.merge` guard's shape)

**Interfaces:**
- Produces: `CommandResult(exit_code: int, output: str)` — frozen dataclass, `output` is merged stdout+stderr.
- Produces: `CommandTimeout(Exception)`.
- Produces: `CommandRunner.run(command: str, *, cwd: Path, timeout: float) -> CommandResult` (async; raises `CommandTimeout` after killing the process).
- Produces: `MemoryCommandRunner(results: list[CommandResult] | None = None)` — pops scripted results in order (repeats the last one when exhausted; defaults to a single `CommandResult(0, "ok")`), records `calls: list[dict]` with `command`/`cwd`/`timeout`.

- [ ] **Step 1: Write the failing tests**

`tests/test_command_runner.py`:

```python
"""The subprocess CommandRunner, driven by real (sub-second) commands.

Deliberate real-subprocess exception to the in-memory rule — the same posture
as test_smoke_git.py for git: this is the only live coverage of the driver.
"""

from __future__ import annotations

import pytest

from harness.drivers.memory import MemoryCommandRunner
from harness.drivers.subprocess_command import SubprocessCommandRunner
from harness.ports.command import CommandResult, CommandTimeout


@pytest.mark.asyncio
async def test_zero_exit_and_output(tmp_path):
    runner = SubprocessCommandRunner()
    result = await runner.run("echo hello", cwd=tmp_path, timeout=10.0)
    assert result.exit_code == 0
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_nonzero_exit_with_merged_stderr(tmp_path):
    runner = SubprocessCommandRunner()
    result = await runner.run("echo oops >&2; exit 3", cwd=tmp_path, timeout=10.0)
    assert result.exit_code == 3
    assert "oops" in result.output


@pytest.mark.asyncio
async def test_runs_in_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    runner = SubprocessCommandRunner()
    result = await runner.run("ls", cwd=tmp_path, timeout=10.0)
    assert "marker.txt" in result.output


@pytest.mark.asyncio
async def test_timeout_kills_and_raises(tmp_path):
    runner = SubprocessCommandRunner()
    with pytest.raises(CommandTimeout):
        await runner.run("sleep 5", cwd=tmp_path, timeout=0.2)


@pytest.mark.asyncio
async def test_memory_runner_scripts_results(tmp_path):
    runner = MemoryCommandRunner(
        results=[CommandResult(1, "boom"), CommandResult(0, "fine")]
    )
    first = await runner.run("make test", cwd=tmp_path, timeout=5.0)
    second = await runner.run("make test", cwd=tmp_path, timeout=5.0)
    third = await runner.run("make test", cwd=tmp_path, timeout=5.0)
    assert (first.exit_code, second.exit_code, third.exit_code) == (1, 0, 0)
    assert [call["command"] for call in runner.calls] == ["make test"] * 3
    assert runner.calls[0]["cwd"] == tmp_path
```

(If the suite does not already use `pytest-asyncio` markers, follow whatever async-test convention the existing tests use — check how `tests/test_phase3_e2e.py` drives async code and mirror it.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/test_command_runner.py`
Expected: FAIL — `harness.ports.command` does not exist.

- [ ] **Step 3: Implement**

`src/harness/ports/command.py`:

```python
"""The CommandRunner port — a shell command run to completion in a directory.

The verify step's counterpart of `AgentRunner`: the behavior decides *what* to
run and how to map the result; only the driver knows subprocesses. Timeouts
raise (the process is killed) — a behavior must treat a timeout as a step
failure, never as a red suite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    """What the command did: exit code plus merged stdout+stderr."""

    exit_code: int
    output: str


class CommandTimeout(Exception):
    """The command exceeded its time budget and was killed."""


class CommandRunner(ABC):
    @abstractmethod
    async def run(self, command: str, *, cwd: Path, timeout: float) -> CommandResult:
        """Run to completion in `cwd`. Raises CommandTimeout past `timeout`."""
```

`src/harness/drivers/subprocess_command.py`:

```python
"""Real CommandRunner over asyncio's subprocess shell."""

from __future__ import annotations

import asyncio
from pathlib import Path

from harness.ports.command import CommandResult, CommandRunner, CommandTimeout


class SubprocessCommandRunner(CommandRunner):
    async def run(self, command: str, *, cwd: Path, timeout: float) -> CommandResult:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise CommandTimeout(
                f"command exceeded {timeout:.0f}s: {command}"
            ) from None
        return CommandResult(
            exit_code=process.returncode or 0,
            output=stdout.decode("utf-8", errors="replace"),
        )
```

(Note: on Python 3.11 `asyncio.wait_for` raises `TimeoutError`; catch `asyncio.TimeoutError` too if the linter prefers — they are the same class since 3.11.)

Append to `src/harness/drivers/memory.py`:

```python
class MemoryCommandRunner(CommandRunner):
    """Scripted CommandRunner: pops preset results, repeats the last one."""

    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self._results = list(results) if results else [CommandResult(0, "ok")]
        self.calls: list[dict] = []

    async def run(self, command: str, *, cwd: Path, timeout: float) -> CommandResult:
        self.calls.append({"command": command, "cwd": cwd, "timeout": timeout})
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]
```

(Add the `CommandResult`/`CommandRunner` import at the top of `memory.py` alongside the other port imports.)

`tests/test_architecture.py`: add `ports.command` to the guard that asserts `dispatcher.py`/`consumer.py` import neither drivers nor the listed orchestration-invisible ports (find the existing check for `ports.merge` / `ports.issue_state` and extend its list — same shape, no new test function needed if it's table-driven).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest -q tests/test_command_runner.py tests/test_architecture.py` → PASS, then full suite → green.

- [ ] **Step 5: Commit**

```bash
git add src/harness/ports/command.py src/harness/drivers/subprocess_command.py src/harness/drivers/memory.py tests/test_command_runner.py tests/test_architecture.py
git commit -m "feat: CommandRunner port with subprocess driver and memory fake"
```

---

### Task 3: `VerifyBehavior`

**Files:**
- Create: `src/harness/behaviors/verify.py`
- Test: `tests/test_verify_behavior.py` (new)

**Interfaces:**
- Consumes: `RepositoryRegistry.verify_command` (Task 1), `CommandRunner`/`CommandResult`/`CommandTimeout` (Task 2), `Workspace`/`WorkspaceHandle` (`path`, `write`, `commit`), `next_attempt` from `artifacts_layout`, `DONE`/`REQUEST_CHANGES`/`BehaviorResult` from `models`.
- Produces: `VerifyBehavior(workspace, registry, runner, timeout=900.0)` — a `ConsumerBehavior`; step name is read from `task.status` (like `ClaudeCliBehavior`).

- [ ] **Step 1: Write the failing tests**

`tests/test_verify_behavior.py`:

```python
"""VerifyBehavior: exit code → outcome, output → artifact, worker commits."""

from __future__ import annotations

import pytest

from harness.behaviors.verify import VerifyBehavior
from harness.drivers.memory import (
    MemoryCommandRunner,
    MemoryRepositoryRegistry,
    MemoryWorkspace,
)
from harness.models import DONE, REQUEST_CHANGES, Task
from harness.ports.command import CommandResult, CommandTimeout


def make_task(repository: str | None = "app") -> Task:
    return Task.from_dict(
        {
            "id": "tsk_verify_test",
            "repository": repository,
            "status": "verify",
            "created": "2026-07-24T00:00:00Z",
        }
    )


def make_behavior(tmp_path, runner=None, verify={"app": "make test"}):
    workspace = MemoryWorkspace()
    registry = MemoryRepositoryRegistry({"app": tmp_path}, verify=verify)
    runner = runner or MemoryCommandRunner()
    behavior = VerifyBehavior(workspace=workspace, registry=registry, runner=runner)
    return behavior, workspace, runner


@pytest.mark.asyncio
async def test_green_run_is_done_with_artifact_and_commit(tmp_path):
    runner = MemoryCommandRunner([CommandResult(0, "473 passed\n")])
    behavior, workspace, runner = make_behavior(tmp_path, runner=runner)
    result = await behavior.run(make_task())

    assert result.outcome == DONE
    assert "verify passed" in result.summary
    handle = workspace.handles["tsk_verify_test"]
    # The runner ran the configured command in the worktree.
    assert runner.calls[0]["command"] == "make test"
    assert runner.calls[0]["cwd"] == handle.path
    # The output landed as the step's attempt-indexed artifact, and the
    # worker committed (invariant 9).
    relpaths = [relpath for relpath, _ in handle.writes]
    assert relpaths == [".artifacts/tsk_verify_test/verify-01.md"]
    assert "473 passed" in handle.writes[0][1]
    assert len(handle.commits) == 1


@pytest.mark.asyncio
async def test_red_run_is_request_changes_with_output_tail(tmp_path):
    runner = MemoryCommandRunner([CommandResult(1, "x" * 5000 + "\nFAILED tail")])
    behavior, workspace, runner = make_behavior(tmp_path, runner=runner)
    result = await behavior.run(make_task())

    assert result.outcome == REQUEST_CHANGES
    assert "FAILED tail" in result.summary          # the tail survives
    assert "x" * 5000 not in result.summary          # the head is trimmed
    assert len(workspace.handles["tsk_verify_test"].commits) == 1


@pytest.mark.asyncio
async def test_no_verify_command_is_a_done_noop(tmp_path):
    behavior, workspace, runner = make_behavior(tmp_path, verify={})
    result = await behavior.run(make_task())

    assert result.outcome == DONE
    assert "no verify command" in result.summary
    assert runner.calls == []                        # nothing ran
    assert workspace.handles == {}                   # not even an attach


@pytest.mark.asyncio
async def test_repository_less_task_is_a_done_noop(tmp_path):
    behavior, workspace, runner = make_behavior(tmp_path)
    result = await behavior.run(make_task(repository=None))

    assert result.outcome == DONE
    assert runner.calls == []


@pytest.mark.asyncio
async def test_timeout_bubbles_to_fail_the_task(tmp_path):
    class TimeoutRunner(MemoryCommandRunner):
        async def run(self, command, *, cwd, timeout):
            raise CommandTimeout("too slow")

    behavior, _, _ = make_behavior(tmp_path, runner=TimeoutRunner())
    with pytest.raises(CommandTimeout):
        await behavior.run(make_task())
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/test_verify_behavior.py`
Expected: FAIL — `harness.behaviors.verify` does not exist.

- [ ] **Step 3: Implement**

`src/harness/behaviors/verify.py`:

```python
"""VerifyBehavior — the deterministic test gate between development and review.

"Landing is a step, not magic" (ADR-0009) applied to verification: the step is
a normal `ConsumerBehavior`, but no LLM is involved — the repo's `verify`
command (from `repos.json`) runs in the worktree and the exit code IS the
verdict. Extends invariant 9's philosophy: test results are established by a
driver, not claimed by an agent.

Outcome mapping: exit 0 → done; non-zero → request_changes (the workflow's
back edge gives development another round, with the failure tail in the
summary and the full output in an attempt-indexed artifact). A timeout or
runner crash raises — the consumer fails the task into `failed/`; a red suite
is request_changes, never failed.

A task with no repository, or a repo with no `verify` command configured, is
a `done` no-op — the gate is opt-in per repo (spec: increment 1).
"""

from __future__ import annotations

from harness.artifacts_layout import next_attempt
from harness.models import DONE, REQUEST_CHANGES, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.command import CommandRunner
from harness.ports.repos import RepositoryRegistry
from harness.ports.workspace import Workspace

# How much of the command output the summary keeps (the history line / next
# development round's context). The full output is always in the artifact.
TAIL_CHARS = 1500


class VerifyBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        workspace: Workspace,
        registry: RepositoryRegistry | None,
        runner: CommandRunner,
        timeout: float = 900.0,
    ) -> None:
        self._workspace = workspace
        self._registry = registry
        self._runner = runner
        self._timeout = timeout

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or "verify"

        command = None
        if task.repository and self._registry is not None:
            command = self._registry.verify_command(task.repository)
        if not command:
            return BehaviorResult(
                DONE, "verify skipped: no verify command configured for this repo"
            )

        handle = self._workspace.attach(task)
        attempt, relpath = next_attempt(handle.path, task.id, step)

        result = await self._runner.run(
            command, cwd=handle.path, timeout=self._timeout
        )

        artifact = (
            f"# verify — attempt {attempt:02d}\n\n"
            f"Command: `{command}`\n\n"
            f"Exit code: {result.exit_code}\n\n"
            "```text\n"
            f"{result.output}\n"
            "```\n"
        )
        handle.write(relpath, artifact)
        handle.commit(f"[verify] attempt {attempt:02d}: exit {result.exit_code}")

        if result.exit_code == 0:
            return BehaviorResult(DONE, f"verify passed: `{command}` (exit 0)")
        tail = result.output[-TAIL_CHARS:].strip()
        return BehaviorResult(
            REQUEST_CHANGES,
            f"verify failed (`{command}` exit {result.exit_code}):\n{tail}",
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest -q tests/test_verify_behavior.py` → PASS, then full suite → green.

- [ ] **Step 5: Commit**

```bash
git add src/harness/behaviors/verify.py tests/test_verify_behavior.py
git commit -m "feat: VerifyBehavior — deterministic verify step over CommandRunner"
```

---

### Task 4: Wiring — `verify` finisher kind, cli injection, default workflow, persona line

**Files:**
- Modify: `src/harness/app.py` (`build()` signature ~line 333, finisher registry ~line 524)
- Modify: `src/harness/cli.py` (`DEFAULT_DEFINITION` ~line 103, the `build(...)` call ~line 1743, `_DEVELOPMENT_PERSONA` ~line 307)
- Test: `tests/test_verify_wiring.py` (new)

**Interfaces:**
- Consumes: `VerifyBehavior` (Task 3), `MemoryCommandRunner`/`SubprocessCommandRunner` (Task 2).
- Produces: `build(..., command_runner: CommandRunner | None = None)` — defaults to `MemoryCommandRunner()` (in-memory default-driver convention); `cli._run` injects `SubprocessCommandRunner()`.
- Produces: finisher kind `"verify"` in the default registry — replace-style (ignores `inner`, never triggers a catalog lookup, so a verify step needs no agent JSON; `_write_default_agents` already skips finisher-bound steps).
- Produces: the default development workflow routes `development → verify → review` with `verify --request_changes--> development` and binds `finishers: {"verify": "verify"}`.

- [ ] **Step 1: Write the failing test**

`tests/test_verify_wiring.py`:

```python
"""build() serves a workflow whose verify step is finisher-bound to VerifyBehavior."""

from __future__ import annotations

import json

from harness.app import HarnessLayout, build
from harness.behaviors.verify import VerifyBehavior
from harness.drivers.memory import MemoryCommandRunner, MemoryRepositoryRegistry

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "verify"},
        {"from": "verify", "on": "done", "to": "land"},
        {"from": "verify", "on": "request_changes", "to": "development"},
        {"from": "land", "on": "done", "to": "end"},
    ],
    "finishers": {"verify": "verify"},
}


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


def test_verify_step_gets_verify_behavior(tmp_path):
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        command_runner=MemoryCommandRunner(),
        repository_registry=MemoryRepositoryRegistry({}),
    )
    by_step = {consumer.step: consumer for consumer in harness.consumers}
    assert isinstance(by_step["verify"].behavior, VerifyBehavior)
    # And the other steps keep their non-verify behavior.
    assert not isinstance(by_step["plan"].behavior, VerifyBehavior)
```

(If `Consumer` exposes no public `behavior` attribute, assert through whatever the consumer does expose — check `src/harness/consumer.py`; a read-only property addition is acceptable if none exists, mirroring its existing `step` property.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/test_verify_wiring.py`
Expected: FAIL — `build()` has no `command_runner` parameter / unknown finisher kind `"verify"`.

- [ ] **Step 3: Implement**

`src/harness/app.py`:
1. Imports: add `from harness.behaviors.verify import VerifyBehavior`, `from harness.ports.command import CommandRunner`, and `MemoryCommandRunner` to the existing `drivers.memory` import.
2. `build()` signature: add `command_runner: CommandRunner | None = None,` after `repository_registry`.
3. Just above the finisher-registry block (~line 500, next to the `landing = LandingBehavior(...)` construction):

```python
    command_runner = command_runner or MemoryCommandRunner()
    verify = VerifyBehavior(
        workspace=workspace,
        registry=repository_registry,
        runner=command_runner,
    )
```

4. Extend the default registry (replace-style entry, exactly like `open-pr`):

```python
    finisher_registry: dict[
        str, Callable[[str, dict, Callable[[], ConsumerBehavior]], ConsumerBehavior]
    ] = {
        "open-pr": lambda step, config, inner: landing,
        "verify": lambda step, config, inner: verify,
    }
```

`src/harness/cli.py`:
1. `DEFAULT_DEFINITION` becomes:

```python
DEFAULT_DEFINITION = {
    "name": "development",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "architecture"},
        {"from": "architecture", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "verify"},
        {"from": "verify", "on": "done", "to": "review"},
        {"from": "verify", "on": "request_changes", "to": "development"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
    "finishers": {"verify": "verify"},
}
```

2. The `build(...)` call in `_run` (~line 1743, the one already passing `repository_registry=registry`): add `command_runner=SubprocessCommandRunner(),` and import `SubprocessCommandRunner` from `harness.drivers.subprocess_command` at the top.
3. `_DEVELOPMENT_PERSONA` (~line 307): append one sentence to the revision-round paragraph (locate the wording about reading a previous review among the artifacts):

```
"A revision round may also be triggered by a failed verify run — a "
"verify-NN.md artifact with the test command's output. Read it and fix "
"the failures it shows.\n"
```

4. Check `RESOLVER_DEFINITION` (~line 120): per the spec, the resolver workflow is unchanged in this increment — do not touch it.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest -q tests/test_verify_wiring.py` → PASS, then the full suite → green. Pay attention to any test asserting the exact `DEFAULT_DEFINITION` shape or `harness init` output (`steps:` line now includes `verify`) — update those assertions to the new workflow, they are expected casualties, not regressions.

- [ ] **Step 5: Commit**

```bash
git add src/harness/app.py src/harness/cli.py tests/test_verify_wiring.py
git commit -m "feat: wire the verify gate — finisher kind, cli injection, default workflow"
```

---

### Task 5: In-memory end-to-end — the verify loop

Proves the full loop: development → verify (red) → development → verify (green) → review → land → end, on in-memory drivers.

**Files:**
- Test: `tests/test_verify_e2e.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 1–4; the `drive_until_quiet` pattern from `tests/test_phase3_e2e.py` (dispatcher tick + consumer ticks until no one acts).

- [ ] **Step 1: Write the test**

`tests/test_verify_e2e.py`:

```python
"""E2E: a red verify run loops the task back to development, a green one
lets it through to land. In-memory drivers, DummyBehavior for agent steps,
scripted MemoryCommandRunner for the verify step."""

from __future__ import annotations

import json

import pytest

from harness.app import HarnessLayout, build
from harness.drivers.memory import MemoryCommandRunner, MemoryRepositoryRegistry
from harness.models import Task
from harness.ports.command import CommandResult

DEFINITION = {
    "name": "default",
    "start": "development",
    "transitions": [
        {"from": "development", "on": "done", "to": "verify"},
        {"from": "verify", "on": "done", "to": "land"},
        {"from": "verify", "on": "request_changes", "to": "development"},
        {"from": "land", "on": "done", "to": "end"},
    ],
    "finishers": {"verify": "verify"},
}

MAX_STEPS = 200


def seed(tmp_path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


def submit(tmp_path, task: Task) -> None:
    (tmp_path / "tasks" / f"{task.id}.json").write_text(json.dumps(task.to_dict()))


async def drive_until_quiet(harness) -> None:
    for _ in range(MAX_STEPS):
        acted = harness.dispatcher.tick()
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return
    raise AssertionError("loop did not settle")


@pytest.mark.asyncio
async def test_red_verify_loops_back_then_green_lands(tmp_path):
    seed(tmp_path)
    runner = MemoryCommandRunner(
        results=[CommandResult(1, "1 failed"), CommandResult(0, "all passed")]
    )
    harness = build(
        tmp_path,
        "default",
        delay=0.0,
        command_runner=runner,
        repository_registry=MemoryRepositoryRegistry(
            {"app": tmp_path}, verify={"app": "make test"}
        ),
    )
    task = Task.from_dict(
        {
            "id": "tsk_verify_e2e",
            "workflowTemplate": "default",
            "repository": "app",
            "created": "2026-07-24T00:00:00Z",
            "data": {"title": "verify e2e"},
        }
    )
    submit(tmp_path, task)
    harness.hydrate()
    await drive_until_quiet(harness)

    done = {t.id: t for t in harness.done.list()}
    assert "tsk_verify_e2e" in done, "task did not reach done/"
    finished = done["tsk_verify_e2e"]

    # verify ran twice: red then green.
    assert [call["command"] for call in runner.calls] == ["make test", "make test"]

    # History shows the loop: development consumed twice, verify twice with
    # request_changes then done.
    verify_outcomes = [
        entry.outcome
        for entry in finished.history
        if entry.actor == "consumer:verify"
    ]
    assert verify_outcomes == ["request_changes", "done"]
    development_runs = [
        entry
        for entry in finished.history
        if entry.actor == "consumer:development"
    ]
    assert len(development_runs) == 2
```

Adapt the mechanics to what `tests/test_phase3_e2e.py` actually does around hydration and queue access (`harness.hydrate()` vs something the constructor does; `harness.done.list()` vs a differently-named attribute; `delay=0.0` vs whatever keeps `DummyBehavior` from sleeping) — the assertions are the contract, the plumbing must match the existing e2e file. `DummyBehavior` returns `done` for the agent steps, which is exactly what this flow needs.

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest -q tests/test_verify_e2e.py`
Expected: PASS. If the loop doesn't settle or the task lands in `failed/`, debug via the task's history — every hop is recorded.

- [ ] **Step 3: Full suite**

Run: `.venv/bin/pytest -q`
Expected: everything green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_verify_e2e.py
git commit -m "test: e2e for the verify gate's request_changes loop"
```

---

### Post-merge operator steps (not code — do after the release is installed)

1. **Update the operator's workflow file** `~/harness-root/workflows/development.json`: insert the `verify` step edges and the `"finishers": {"verify": "verify"}` binding exactly as in Task 4's `DEFAULT_DEFINITION` (the installed file is operator config; `harness init` only writes it for fresh roots).
2. **Configure verify commands** in `~/harness-root/repos.json` — convert each entry to the object form where wanted, e.g. `"harness_v2": {"path": "/Users/rem/harness_v2-run", "verify": ".venv/bin/pytest -q"}`. Repos left in string form skip the gate (done no-op).
3. Restart the service (`harness update` / launchd kickstart) and watch one task flow through the new `verify` column on the board.

### Self-review notes

- Spec coverage: config (`repos.json` object form) → Task 1; `CommandRunner` port/driver → Task 2; behavior incl. artifact, tail summary, timeout→failed, no-op paths → Task 3; wiring + default workflow + persona line → Task 4; loop proof → Task 5; "no agent JSON for verify" → finisher-bound step, `_write_default_agents` skips it (verified in cli.py:536-546).
- The spec's `verify-NN/output.txt` artifact naming is adjusted to the repo's actual flat convention `verify-NN.md` (`artifacts_layout.py` is the single source of truth; a directory-shaped artifact would be invisible to `WorktreeArtifactView`).
- The spec's "15 min default" timeout: `VerifyBehavior` defaults to 900s; a per-repo/per-call override is YAGNI until someone needs it.
