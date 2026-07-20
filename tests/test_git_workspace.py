import subprocess

from harness.drivers.git_workspace import GitWorkspace
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


def _make_task(tmp_path, task_id="tsk_1"):
    repo = tmp_path / "repo"
    _make_repo(repo)
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository=str(repo),
        worktree=str(tmp_path / "wt"),
    )


def test_attach_creates_worktree_on_task_branch(tmp_path):
    task = _make_task(tmp_path)
    workspace = GitWorkspace()

    handle = workspace.attach(task)

    assert handle.path.is_dir()
    assert handle.branch == "harness/tsk_1"
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], handle.path).strip()
    assert branch == "harness/tsk_1"


def test_write_and_commit_returns_sha_and_logs_message(tmp_path):
    task = _make_task(tmp_path)
    workspace = GitWorkspace()
    handle = workspace.attach(task)

    handle.write("feature.txt", "ahoj\n")
    sha = handle.commit("[design] práce")

    assert sha is not None
    assert len(sha) == 40
    log = _git(["log", "--oneline"], handle.path)
    assert "[design] práce" in log
    assert (handle.path / "feature.txt").read_text(encoding="utf-8") == "ahoj\n"


def test_commit_without_changes_returns_none(tmp_path):
    task = _make_task(tmp_path)
    workspace = GitWorkspace()
    handle = workspace.attach(task)

    handle.write("feature.txt", "ahoj\n")
    first = handle.commit("[design] práce")
    second = handle.commit("[design] nic nového")

    assert first is not None
    assert second is None


def test_reattach_reuses_existing_worktree(tmp_path):
    task = _make_task(tmp_path)
    workspace = GitWorkspace()

    first = workspace.attach(task)
    first.write("feature.txt", "ahoj\n")
    first.commit("[design] práce")

    second = workspace.attach(task)

    assert second.path == first.path
    assert second.branch == "harness/tsk_1"
    assert (second.path / "feature.txt").read_text(encoding="utf-8") == "ahoj\n"
