"""End-to-end proof that the workflow — not a frozen persona snapshot — owns
the outcome vocabulary (ADR-0018, Package C in
`docs/superpowers/plans/2026-07-23-workflow-defined-outcomes.md`).

A small `fork-demo` workflow lets a `plan` step emit either `"ui"` or
`"backend"`; the router follows the matching edge with no new mechanism at
all. A `"backend"` verdict skips `designer` entirely; a `"ui"` verdict visits
it. This exercises the exact path `test_phase3_e2e.py` doesn't: `DummyBehavior`
never reads `AgentSpec.allowed_outcomes` or a workflow, so it can't prove the
live-derivation Package C added. Here, `ClaudeCliBehavior` is driven by a real
`MemoryAgentCatalog` + a scriptable fake `AgentRunner` + a real git worktree,
mirroring `test_phase3_e2e.py`'s wiring shape (duplicated rather than imported —
that file's helpers are module-private by convention).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from harness.app import Harness, HarnessLayout, build
from harness.drivers.memory import (
    FakeClock,
    MemoryAgentCatalog,
    MemoryForge,
    MemoryRepositoryRegistry,
)
from harness.drivers.git_workspace import GitWorkspace
from harness.models import DONE, Task
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec

DEFINITION = {
    "name": "fork-demo",
    "start": "plan",
    "descriptions": {
        "designer": "produce UI mockups; only reached for user-facing features",
    },
    "transitions": [
        {
            "from": "plan",
            "on": "ui",
            "to": "designer",
            "hint": "the feature has a user-facing surface",
        },
        {
            "from": "plan",
            "on": "backend",
            "to": "development",
            "hint": "purely server-side, no UI",
        },
        {"from": "designer", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "end"},
    ],
}

MAX_STEPS = 1000

# `.artifacts/<task_id>/<step>-<NN>.md`, the way `compose_prompt` inserts it
# into the prompt — same regex as `test_phase3_e2e.py`'s `EchoRunner`.
_RELPATH = re.compile(
    r"\.artifacts/(?P<task>[^/\s]+)/(?P<step>[^/\s]+)-(?P<nn>\d+)\.md"
)


class ScriptedRunner(AgentRunner):
    """Fake agent: writes the artifact from the prompt, returns a scripted verdict.

    The verdict for a given step is scriptable per call (`script[step]` is a
    list consumed in order, one entry per invocation of that step; the last
    entry repeats once exhausted) so a test can make `plan` emit `"ui"` on one
    run and `"backend"` on another, while every other step just returns
    `DONE`. Every call is recorded so a test can assert both call counts and
    the exact prompt/spec a step was invoked with.
    """

    def __init__(self, script: dict[str, list[str]] | None = None) -> None:
        self._script = script or {}
        self._script_index: dict[str, int] = {}
        self.calls: list[dict[str, Any]] = []

    async def run(
        self, *, prompt: str, spec: AgentSpec, cwd: Path, timeout: float, on_output=None
    ) -> AgentRun:
        self.calls.append(
            {"prompt": prompt, "spec": spec, "cwd": cwd, "timeout": timeout}
        )

        match = _RELPATH.search(prompt)
        assert match is not None, "prompt does not contain an artifact path"
        relpath = match.group(0)
        target = Path(cwd) / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {spec.name} attempt\n", encoding="utf-8")

        outcomes = self._script.get(spec.name)
        if outcomes:
            index = self._script_index.get(spec.name, 0)
            outcome = outcomes[min(index, len(outcomes) - 1)]
            self._script_index[spec.name] = index + 1
        else:
            outcome = DONE

        assert outcome in spec.allowed_outcomes, (
            f"scripted outcome {outcome!r} for step {spec.name!r} is not in "
            f"the live allowed set {spec.allowed_outcomes!r}"
        )
        return AgentRun(outcome, summary=f"{spec.name}: ok")

    def count(self, step: str) -> int:
        return sum(1 for call in self.calls if call["spec"].name == step)

    def prompt_for(self, step: str, *, call: int = 0) -> str:
        prompts = [c["prompt"] for c in self.calls if c["spec"].name == step]
        return prompts[call]

    def spec_for(self, step: str, *, call: int = 0) -> AgentSpec:
        specs = [c["spec"] for c in self.calls if c["spec"].name == step]
        return specs[call]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("# app\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")

    # No `land` step in `fork-demo` (it ends at "development" -> "end"), so
    # nothing here ever pushes or opens a PR — but the fixture mirrors
    # `test_phase3_e2e.py`'s remote setup anyway, in case a step is added
    # later that lands.
    remote = path.parent / (path.name + "-remote.git")
    _git(remote.parent, "init", "--bare", "-q", str(remote))
    _git(path, "remote", "add", "origin", str(remote))
    _git(path, "push", "-q", "origin", "HEAD:main")


def _catalog() -> MemoryAgentCatalog:
    # The workflow-derived set wins at run time (Package C) whenever a
    # workflow resolves, so these `allowed_outcomes` barely matter here — they
    # are the fallback a workflow-less path would use, kept a valid (DONE,)
    # so the spec itself is well-formed.
    def spec(step: str) -> AgentSpec:
        return AgentSpec(
            name=step,
            prompt=f"Persona for the {step} step.",
            allowed_outcomes=(DONE,),
        )

    return MemoryAgentCatalog(
        {
            "plan": spec("plan"),
            "designer": spec("designer"),
            "development": spec("development"),
        }
    )


def seed(tmp_path: Path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "fork-demo.json").write_text(json.dumps(DEFINITION))


def submit(tmp_path: Path, task: Task) -> None:
    (tmp_path / "tasks" / f"{task.id}.json").write_text(json.dumps(task.to_dict()))


async def drive_until_quiet(harness: Harness) -> int:
    for step in range(MAX_STEPS):
        acted = harness.dispatcher.tick()
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return step
    raise AssertionError("loop did not settle")


async def _run_fork(tmp_path: Path, task_id: str, plan_outcome: str):
    seed(tmp_path)

    repo = tmp_path / "repo"
    _make_repo(repo)
    worktrees_root = tmp_path / "wt"

    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root)
    catalog = _catalog()
    runner = ScriptedRunner({"plan": [plan_outcome]})
    forge = MemoryForge()

    harness = build(
        tmp_path,
        "fork-demo",
        clock=FakeClock(),
        forge=forge,
        workspace=workspace,
        catalog=catalog,
        runner=runner,
        delay=0.0,
    )

    task = Task(
        id=task_id,
        workflow_template="fork-demo",
        created="2026-07-23T10:00:00Z",
        repository="app",
        worktree=None,
        data={"title": "a fork-demo task"},
    )
    submit(tmp_path, task)
    await drive_until_quiet(harness)

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / f"{task_id}.json").read_text())
    )
    return finished, runner


async def test_backend_outcome_skips_designer(tmp_path):
    finished, runner = await _run_fork(tmp_path, "tsk_fork_backend", "backend")

    assert finished.status == "end"
    routed = [e.to_step for e in finished.history if e.actor == "dispatcher"]
    assert routed == ["plan", "development", "end"]
    assert "designer" not in routed

    assert runner.count("designer") == 0
    assert runner.count("plan") == 1
    assert runner.count("development") == 1

    # The live-derived set — not the catalog's fallback `(DONE,)` — reached
    # both the prompt and the runner's enforcement at the `plan` step.
    plan_spec = runner.spec_for("plan")
    assert plan_spec.allowed_outcomes == ("ui", "backend")


async def test_ui_outcome_visits_designer(tmp_path):
    finished, runner = await _run_fork(tmp_path, "tsk_fork_ui", "ui")

    assert finished.status == "end"
    routed = [e.to_step for e in finished.history if e.actor == "dispatcher"]
    assert routed == ["plan", "designer", "development", "end"]

    assert runner.count("designer") == 1
    assert runner.count("plan") == 1
    assert runner.count("development") == 1

    plan_spec = runner.spec_for("plan")
    assert plan_spec.allowed_outcomes == ("ui", "backend")

    # The workflow's step description reached the designer's prompt.
    designer_prompt = runner.prompt_for("designer")
    assert (
        "produce UI mockups; only reached for user-facing features"
        in designer_prompt
    )

    # The transition hints reached the plan step's own prompt.
    plan_prompt = runner.prompt_for("plan")
    assert "the feature has a user-facing surface" in plan_prompt
    assert "purely server-side, no UI" in plan_prompt
