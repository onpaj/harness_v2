"""Opt-in smoke with the **real** `claude -p`.

Unlike the entire rest of the suite, this test calls the real `claude` CLI — it's
non-deterministic, expensive, and requires auth. That's why it runs **only** when
`HARNESS_SMOKE_CLAUDE` is set; otherwise it's skipped and `pytest -q` never runs it.

It verifies the thin subprocess shell of `ClaudeCliRunner` that the fake runners in
the other tests have to bypass: a real agent in a tmp git repo gets a trivial task,
finishes, the worker commits its work, and the task makes it to `done/` with a PR.

Run:  HARNESS_SMOKE_CLAUDE=1 .venv/bin/pytest tests/test_smoke_claude.py -q
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from harness.app import HarnessLayout, build
from harness.cli import main
from harness.drivers.claude_cli import ClaudeCliRunner
from harness.drivers.fake_forge import FakeForge
from harness.drivers.fs_agents import FilesystemAgentCatalog
from harness.drivers.fs_repos import FilesystemRepositoryRegistry
from harness.drivers.git_workspace import GitWorkspace
from harness.drivers.system_clock import SystemClock
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.models import Task

# Minimal workflow: one agent step + landing. Fewer real `claude` calls,
# a faster and more robust smoke than the whole default chain.
MINIMAL_WORKFLOW = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
    ],
}

# The real `claude` runs for minutes; the opt-in test is allowed to wait for it.
BUDGET_SECONDS = 600.0
POLL_SECONDS = 1.0


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


@pytest.mark.skipif(
    not os.environ.get("HARNESS_SMOKE_CLAUDE"),
    reason="opt-in: real claude (set HARNESS_SMOKE_CLAUDE)",
)
async def test_trivial_task_lands_with_real_claude(tmp_path):
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    _make_repo(repo)
    layout = HarnessLayout(root)

    # `harness init` creates the tree, the default agents (`agents/plan.json`, …), and
    # an empty `repos.json`.
    assert main(["init", "--root", str(root)]) == 0

    # A minimal workflow instead of the default chain, and map the repo name to a path.
    (layout.workflows / "default.json").write_text(
        json.dumps(MINIMAL_WORKFLOW, ensure_ascii=False), encoding="utf-8"
    )
    layout.repos.write_text(
        json.dumps({"app": str(repo)}, ensure_ascii=False), encoding="utf-8"
    )

    # A trivial task with the repo name.
    assert (
        main(
            [
                "submit",
                "--root",
                str(root),
                "--repo",
                "app",
                "--data",
                json.dumps({"title": "write one sentence about the project into NOTES.md"}),
            ]
        )
        == 0
    )
    task_id = next(iter(layout.tasks.glob("*.json")))
    task_id = json.loads(task_id.read_text())["id"]

    registry = FilesystemRepositoryRegistry(layout.repos)
    catalog = FilesystemAgentCatalog(layout.agents)
    harness = build(
        root,
        "default",
        clock=SystemClock(),
        workspace=GitWorkspace(registry, layout.worktrees),
        catalog=catalog,
        runner=ClaudeCliRunner(),
        artifact_view=WorktreeArtifactView(layout.worktrees),
        forge=FakeForge(root / "forge"),
        agent_timeout=300.0,
        delay=0.0,
    )

    stop = asyncio.Event()
    loop = asyncio.create_task(harness.run(poll_interval=POLL_SECONDS, stop=stop))
    done_path = root / "done" / f"{task_id}.json"
    failed_dir = root / "failed"
    waited = 0.0
    while waited < BUDGET_SECONDS:
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        if done_path.exists() or any(failed_dir.glob("*.json")):
            break
    stop.set()
    await asyncio.wait_for(loop, timeout=30.0)

    # The task must not end up in failed/ and must make it to done/ at the terminal.
    assert not any(failed_dir.glob("*.json")), "task fell into failed/"
    assert done_path.exists(), "task did not make it to done/"
    finished = Task.from_dict(json.loads(done_path.read_text()))
    assert finished.status == "end"

    # The worker committed the agent's work on the task branch and landing opened a PR.
    worktree = layout.worktrees / task_id
    log = subprocess.run(
        ["git", "-C", str(worktree), "log", "--oneline"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert log.count("\n") >= 2, "missing per-step commit above init"

    prs = json.loads((root / "forge" / "prs.json").read_text())
    assert len(prs) == 1
    assert prs[0]["branch"] == f"harness/{task_id}"
