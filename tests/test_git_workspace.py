import subprocess

from harness.drivers.git_workspace import GitWorkspace
from harness.drivers.memory import MemoryRepositoryRegistry
from harness.models import Task


def _git(args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init"], path)
    _git(["config", "user.name", "seed"], path)
    _git(["config", "user.email", "seed@local"], path)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-m", "initial"], path)


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    registry = MemoryRepositoryRegistry({"app": repo})
    return GitWorkspace(registry, worktrees_root=tmp_path / "wt")


def _make_task(task_id="tsk_1"):
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
    )


def test_attach_creates_worktree_on_task_branch(tmp_path):
    workspace = _workspace(tmp_path)

    handle = workspace.attach(_make_task())

    assert handle.path.is_dir()
    assert handle.path == tmp_path / "wt" / "tsk_1"
    assert handle.branch == "harness/tsk_1"
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], handle.path).strip()
    assert branch == "harness/tsk_1"


def test_write_and_commit_returns_sha_and_logs_message(tmp_path):
    workspace = _workspace(tmp_path)
    handle = workspace.attach(_make_task())

    handle.write("feature.txt", "hi\n")
    sha = handle.commit("[design] work")

    assert sha is not None
    assert len(sha) == 40
    log = _git(["log", "--oneline"], handle.path)
    assert "[design] work" in log
    assert (handle.path / "feature.txt").read_text(encoding="utf-8") == "hi\n"


def test_commit_without_changes_returns_none(tmp_path):
    workspace = _workspace(tmp_path)
    handle = workspace.attach(_make_task())

    handle.write("feature.txt", "hi\n")
    first = handle.commit("[design] work")
    second = handle.commit("[design] nothing new")

    assert first is not None
    assert second is None


def test_reattach_reuses_existing_worktree(tmp_path):
    workspace = _workspace(tmp_path)
    task = _make_task()

    first = workspace.attach(task)
    first.write("feature.txt", "hi\n")
    first.commit("[design] work")

    second = workspace.attach(task)

    assert second.path == first.path
    assert second.branch == "harness/tsk_1"
    assert (second.path / "feature.txt").read_text(encoding="utf-8") == "hi\n"


def test_reattach_resets_dirty_worktree(tmp_path):
    workspace = _workspace(tmp_path)
    task = _make_task()

    first = workspace.attach(task)
    # Untracked file added outside a commit — the second attach must delete it.
    (first.path / "scratch.txt").write_text("work in progress\n", encoding="utf-8")
    assert (first.path / "scratch.txt").exists()

    second = workspace.attach(task)

    assert not (second.path / "scratch.txt").exists()
