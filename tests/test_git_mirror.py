import subprocess
import threading
from datetime import datetime, timezone

import pytest

from agentharness.git.lock import LockTimeout, repo_lock
from agentharness.git.mirror import (
    GitError,
    branch_exists,
    clone_mirror,
    commit_time,
    create_branch,
    delete_branch,
    empty_root_commit,
    fetch,
    git,
    init_bare,
    is_ancestor,
    list_branches,
    resolve_ref,
)


@pytest.fixture()
def mirror(tmp_path, origin_repo):
    dest = tmp_path / "mirror.git"
    clone_mirror(str(origin_repo), dest)
    return dest


def _commit_in_origin(origin_repo, name, content="x\n"):
    (origin_repo / name).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=origin_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"add {name}"], cwd=origin_repo, check=True
    )
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=origin_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


# --- git() ------------------------------------------------------------------


def test_git_rev_parse_succeeds_in_repo(origin_repo):
    cp = git("rev-parse", "--git-dir", cwd=origin_repo)
    assert cp.returncode == 0
    assert cp.stdout.strip()


def test_git_bad_subcommand_raises_giterror(origin_repo):
    with pytest.raises(GitError) as ei:
        git("definitely-not-a-command", cwd=origin_repo)
    err = ei.value
    assert err.returncode != 0
    assert err.stderr
    assert "definitely-not-a-command" in err.argv


def test_git_check_false_returns_nonzero(origin_repo):
    cp = git("definitely-not-a-command", cwd=origin_repo, check=False)
    assert cp.returncode != 0


def test_git_disables_terminal_prompt(origin_repo, monkeypatch):
    # A shell alias lets the child report the environment it actually saw.
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    cp = git(
        "-c",
        "alias.envcheck=!echo $GIT_TERMINAL_PROMPT",
        "envcheck",
        cwd=origin_repo,
    )
    assert cp.stdout.strip() == "0"


def test_git_env_argument_is_passed_through(origin_repo):
    cp = git(
        "-c",
        "alias.envcheck=!echo $HARNESS_PROBE",
        "envcheck",
        cwd=origin_repo,
        env={"HARNESS_PROBE": "hello"},
    )
    assert cp.stdout.strip() == "hello"


# --- clone_mirror / init_bare / empty_root_commit ---------------------------


def test_clone_mirror_produces_bare_repo_with_main(tmp_path, origin_repo):
    dest = tmp_path / "m.git"
    clone_mirror(str(origin_repo), dest)
    assert dest.is_dir()
    assert not (dest / ".git").exists()
    assert git("rev-parse", "--is-bare-repository", cwd=dest).stdout.strip() == "true"
    sha = resolve_ref(dest, "refs/heads/main")
    assert len(sha) == 40


def test_init_bare_and_empty_root_commit(tmp_path):
    dest = tmp_path / "scratch.git"
    init_bare(dest)
    assert git("rev-parse", "--is-bare-repository", cwd=dest).stdout.strip() == "true"
    sha = empty_root_commit(dest, "main")
    assert len(sha) == 40
    assert resolve_ref(dest, "main") == sha
    tree = git("rev-parse", f"{sha}^{{tree}}", cwd=dest).stdout.strip()
    # The empty tree has a well-known SHA.
    assert tree == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    assert git("ls-tree", tree, cwd=dest).stdout.strip() == ""


def test_empty_root_commit_custom_branch(tmp_path):
    dest = tmp_path / "s2.git"
    init_bare(dest)
    sha = empty_root_commit(dest, branch="trunk")
    assert resolve_ref(dest, "trunk") == sha
    assert branch_exists(dest, "trunk")


# --- resolve_ref / branch_exists -------------------------------------------


def test_resolve_ref_unknown_raises(mirror):
    with pytest.raises(GitError):
        resolve_ref(mirror, "refs/heads/nope")


def test_branch_exists_true_and_false(mirror):
    assert branch_exists(mirror, "main") is True
    assert branch_exists(mirror, "nope") is False


# --- branches ---------------------------------------------------------------


def test_create_list_and_delete_branch(mirror):
    base = resolve_ref(mirror, "main")
    create_branch(mirror, "run/abc", base)
    assert branch_exists(mirror, "run/abc")
    assert resolve_ref(mirror, "run/abc") == base
    assert list_branches(mirror, "run/*") == ["run/abc"]
    assert "main" in list_branches(mirror)
    assert "main" not in list_branches(mirror, "run/*")

    delete_branch(mirror, "run/abc")
    assert branch_exists(mirror, "run/abc") is False
    assert list_branches(mirror, "run/*") == []


def test_list_branches_sorted(mirror):
    base = resolve_ref(mirror, "main")
    create_branch(mirror, "run/b", base)
    create_branch(mirror, "run/a", base)
    assert list_branches(mirror, "run/*") == ["run/a", "run/b"]


# --- fetch ------------------------------------------------------------------


def test_fetch_picks_up_new_origin_commit(mirror, origin_repo):
    before = resolve_ref(mirror, "main")
    new_sha = _commit_in_origin(origin_repo, "a.txt")
    assert resolve_ref(mirror, "main") == before
    fetch(mirror)
    assert resolve_ref(mirror, "main") == new_sha


# --- commit_time ------------------------------------------------------------


def test_commit_time_returns_aware_datetime(mirror):
    ts = commit_time(mirror, "main")
    assert isinstance(ts, datetime)
    assert ts.tzinfo is not None
    now = datetime.now(timezone.utc)
    assert abs((now - ts).total_seconds()) < 600


# --- is_ancestor ------------------------------------------------------------


def test_is_ancestor_true_and_false(mirror, origin_repo):
    old = resolve_ref(mirror, "main")
    _commit_in_origin(origin_repo, "b.txt")
    fetch(mirror)
    new = resolve_ref(mirror, "main")
    assert old != new
    assert is_ancestor(mirror, old, new) is True
    assert is_ancestor(mirror, new, old) is False


# --- repo_lock --------------------------------------------------------------


def test_repo_lock_creates_lock_dir_and_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with repo_lock(home, "myrepo", timeout=5):
        assert (home / "locks" / "myrepo.lock").exists()


def test_repo_lock_sequential_acquisitions_ok(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with repo_lock(home, "r", timeout=5):
        pass
    with repo_lock(home, "r", timeout=5):
        pass


def test_repo_lock_contention_raises_timeout(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    held = threading.Event()
    release = threading.Event()
    errors = []

    def holder():
        try:
            with repo_lock(home, "contended", timeout=5):
                held.set()
                release.wait(10)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
            held.set()

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(5)
        assert not errors
        with pytest.raises(LockTimeout):
            with repo_lock(home, "contended", timeout=0.1):
                pass
    finally:
        release.set()
        t.join(10)

    # Once released, the lock is acquirable again.
    with repo_lock(home, "contended", timeout=5):
        pass


def test_repo_lock_different_repos_do_not_block(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with repo_lock(home, "one", timeout=5):
        with repo_lock(home, "two", timeout=0.1):
            pass
