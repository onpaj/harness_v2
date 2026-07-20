"""Smoke fáze 2 na skutečném gitu.

Jako `test_smoke.py` poluje reálným krátkým `asyncio.sleep` — je to jediné
místo (vedle fázeI smoke), které ověřuje git/fs/forge drivery naživo, end-to-end.
Neuklízet do in-memory podoby; tím by zmizelo jediné pokrytí reálného worktree.
"""

import asyncio
import json
import subprocess

from harness.app import build
from harness.cli import main
from harness.drivers.fake_forge import FakeForge
from harness.drivers.fs_artifacts import FilesystemArtifactStore
from harness.drivers.git_workspace import GitWorkspace
from harness.models import Task

RUNNER_TIMEOUT = 5.0


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("# projekt\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


async def test_task_lands_as_pull_request_on_real_git(tmp_path):
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    _make_repo(repo)

    main(["init", "--root", str(root)])
    main(
        [
            "submit",
            "--root",
            str(root),
            "--repo",
            str(repo),
            "--worktree",
            str(worktree),
        ]
    )
    task_id = None
    for path in (root / "tasks").glob("*.json"):
        task_id = json.loads(path.read_text())["id"]
    assert task_id is not None

    harness = build(
        root,
        "default",
        workspace=GitWorkspace(),
        artifacts=FilesystemArtifactStore(root / "artifacts"),
        forge=FakeForge(root / "forge"),
        delay=0.0,
        request_changes_once_at="review",
    )
    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(600):
        await asyncio.sleep(0.01)
        if (root / "done" / f"{task_id}.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    finished = Task.from_dict(
        json.loads((root / "done" / f"{task_id}.json").read_text())
    )
    assert finished.status == "end"

    # worktree existuje a nese per-fázové commity na task branchi
    assert worktree.is_dir()
    branch = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == f"harness/{task_id}"
    log = subprocess.run(
        ["git", "-C", str(worktree), "log", "--oneline"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "[plan]" in log
    assert "[land] artefakty tasku" in log

    # artefakty na disku, attempt-indexed (review běžel dvakrát kvůli smyčce)
    assert (root / "artifacts" / task_id / "review" / "0").is_dir()
    assert (root / "artifacts" / task_id / "review" / "1").is_dir()

    # PR zaznamenán ve forge
    prs = json.loads((root / "forge" / "prs.json").read_text())
    assert len(prs) == 1
    assert prs[0]["branch"] == f"harness/{task_id}"
