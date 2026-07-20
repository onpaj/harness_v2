"""End-to-end fáze 3 na fake agentovi + reálném git worktree.

Task se **jménem repa** (`repository="app"`) proteče celým workflow fáze 3
`plan→design→architecture→development→review→land→end`. `review` jednou vrátí
`request_changes` (zpětná hrana na `development`), takže `development` i `review`
běží dvakrát a jejich artefakty dostanou attempt 01 i 02.

Práci kroku pohání `EchoRunner` — malý `AgentRunner`, který z promptu vyparsuje
cestu artefaktu a fyzicky ji zapíše do worktree (aby `next_attempt` příště
napočítal další číslo a artefakt se dal commitnout). `FakeAgentRunner` z
`memory.py` má statický `writes` per jméno agenta, takže by neuměl psát pokaždé
jiné jméno souboru; proto vlastní runner.

Reálný `claude` NEBĚŽÍ; git ano — worktree je skutečný, aby se ověřilo, že
artefakty jsou versované (`git ls-files` je ukáže).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from harness.app import Harness, HarnessLayout, build
from harness.drivers.memory import (
    MemoryAgentCatalog,
    MemoryForge,
    MemoryRepositoryRegistry,
)
from harness.drivers.git_workspace import GitWorkspace
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.drivers.memory import FakeClock
from harness.models import Outcome, Task
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "architecture"},
        {"from": "architecture", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}

TASK_ID = "tsk_p3_e2e"
MAX_STEPS = 1000

# `.artifacts/<task_id>/<step>-<NN>.md`, jak ji `compose_prompt` vkládá do promptu.
_RELPATH = re.compile(
    r"\.artifacts/(?P<task>[^/\s]+)/(?P<step>[^/\s]+)-(?P<nn>\d+)\.md"
)


class EchoRunner(AgentRunner):
    """Fake agent: zapíše artefakt na cestu z promptu a vrátí verdikt kroku.

    - Z promptu vyparsuje `.artifacts/<id>/<step>-NN.md` a fyzicky ji zapíše do
      `cwd` (vytvoří rodiče), takže artefakt se objeví ve worktree a
      `next_attempt` příště napočítá další číslo.
    - `review` vrací `REQUEST_CHANGES` při PRVNÍM průchodu daného tasku a `DONE`
      podruhé (jako `DummyBehavior.request_changes_once`); ostatní kroky vždy
      `DONE`. Outcome vždy leží v `spec.allowed_outcomes`.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._review_seen: set[str] = set()

    async def run(
        self, *, prompt: str, spec: AgentSpec, cwd: Path, timeout: float
    ) -> AgentRun:
        self.calls.append(
            {"prompt": prompt, "spec": spec, "cwd": cwd, "timeout": timeout}
        )

        match = _RELPATH.search(prompt)
        assert match is not None, "prompt neobsahuje cestu artefaktu"
        relpath = match.group(0)
        task_id = match.group("task")
        target = Path(cwd) / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {spec.name} attempt\n", encoding="utf-8")

        outcome = Outcome.DONE
        if spec.name == "review" and task_id not in self._review_seen:
            self._review_seen.add(task_id)
            outcome = Outcome.REQUEST_CHANGES

        assert outcome in spec.allowed_outcomes
        return AgentRun(outcome, summary=f"{spec.name}: ok")

    def count(self, step: str) -> int:
        return sum(1 for call in self.calls if call["spec"].name == step)


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


def _catalog() -> MemoryAgentCatalog:
    def spec(step: str, *outcomes: Outcome) -> AgentSpec:
        return AgentSpec(
            name=step,
            prompt=f"Persona kroku {step}.",
            allowed_outcomes=outcomes or (Outcome.DONE,),
        )

    return MemoryAgentCatalog(
        {
            "plan": spec("plan"),
            "design": spec("design"),
            "architecture": spec("architecture"),
            "development": spec("development"),
            "review": spec("review", Outcome.DONE, Outcome.REQUEST_CHANGES),
        }
    )


def seed(tmp_path: Path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


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
    raise AssertionError("smyčka se nezklidnila")


async def build_and_run(tmp_path: Path):
    seed(tmp_path)

    repo = tmp_path / "repo"
    _make_repo(repo)
    worktrees_root = tmp_path / "wt"

    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root)
    catalog = _catalog()
    runner = EchoRunner()
    artifact_view = WorktreeArtifactView(worktrees_root)
    forge = MemoryForge()

    harness = build(
        tmp_path,
        "default",
        clock=FakeClock(),
        forge=forge,
        workspace=workspace,
        catalog=catalog,
        runner=runner,
        artifact_view=artifact_view,
        delay=0.0,
    )

    task = Task(
        id=TASK_ID,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
        worktree=None,
        data={"title": "přidat rate limiting"},
    )
    submit(tmp_path, task)
    await drive_until_quiet(harness)

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / f"{TASK_ID}.json").read_text())
    )
    worktree = worktrees_root / TASK_ID
    return finished, runner, artifact_view, forge, worktree


async def test_task_reaches_done_through_land(tmp_path):
    finished, *_ = await build_and_run(tmp_path)

    assert finished.status == "end"
    assert (tmp_path / "done" / f"{TASK_ID}.json").exists()
    routed = [e.to_step for e in finished.history if e.actor == "dispatcher"]
    assert routed[-2:] == ["land", "end"]


async def test_runner_called_per_step_with_loop(tmp_path):
    _, runner, *_ = await build_and_run(tmp_path)

    # Každý agentní krok proběhl; development a review dvakrát kvůli smyčce.
    assert runner.count("plan") == 1
    assert runner.count("design") == 1
    assert runner.count("architecture") == 1
    assert runner.count("development") == 2
    assert runner.count("review") == 2
    # `land` má LandingBehavior, ne agenta — runner ho nevidí.
    assert runner.count("land") == 0

    # Agent běžel ve worktree tasku a s odpovídajícím spec.
    worktree = tmp_path / "wt" / TASK_ID
    assert all(call["cwd"] == worktree for call in runner.calls)
    assert all(call["spec"].name != "land" for call in runner.calls)


async def test_second_attempt_artifacts_exist_and_are_committed(tmp_path):
    _, _, artifact_view, _, worktree = await build_and_run(tmp_path)

    names = {ref.name for ref in artifact_view.list(TASK_ID)}
    assert "development-01.md" in names
    assert "development-02.md" in names
    assert "review-01.md" in names
    assert "review-02.md" in names

    # Fyzicky ve worktree a commitnuté — git je vidí jako tracked soubory.
    tracked = subprocess.run(
        ["git", "-C", str(worktree), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for name in ("development-01", "development-02", "review-01", "review-02"):
        assert f".artifacts/{TASK_ID}/{name}.md" in tracked


async def test_history_carries_summaries(tmp_path):
    finished, *_ = await build_and_run(tmp_path)

    consumer_entries = [
        e for e in finished.history if e.actor.startswith("consumer:")
    ]
    assert consumer_entries
    assert all(e.summary for e in consumer_entries)


async def test_landing_opens_exactly_one_pull_request(tmp_path):
    _, _, _, forge, _ = await build_and_run(tmp_path)

    assert len(forge.opened) == 1
    assert forge.opened[0].branch == f"harness/{TASK_ID}"
