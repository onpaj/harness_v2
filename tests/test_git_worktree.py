import json
import shutil
from pathlib import Path

import pytest

from agentharness.config import Config
from agentharness.git.mirror import (
    branch_exists,
    clone_mirror,
    git,
    resolve_ref,
)
from agentharness.git.worktree import (
    add_worktree,
    commit_all,
    prune_worktrees,
    remove_worktree,
    write_json,
)


@pytest.fixture()
def mirror(tmp_path, origin_repo):
    dest = tmp_path / "mirror.git"
    clone_mirror(str(origin_repo), dest)
    return dest


@pytest.fixture()
def cfg(tmp_path):
    c = Config(home=tmp_path / "home")
    c.ensure_dirs()
    return c


def _worktree_paths(mirror):
    out = git("worktree", "list", "--porcelain", cwd=mirror).stdout
    return [
        str(Path(line.split(" ", 1)[1]).resolve())
        for line in out.splitlines()
        if line.startswith("worktree ")
    ]


def _p(path):
    return str(Path(path).resolve())


# --- add_worktree -----------------------------------------------------------


def test_add_worktree_creates_dir_with_base_files(tmp_path, mirror):
    wt = tmp_path / "wt1"
    add_worktree(mirror, wt, "run/one", "main")
    assert wt.is_dir()
    assert (wt / "README.md").read_text() == "# origin\n"


def test_add_worktree_creates_branch_in_mirror(tmp_path, mirror):
    base = resolve_ref(mirror, "main")
    wt = tmp_path / "wt2"
    add_worktree(mirror, wt, "run/two", "main")
    assert branch_exists(mirror, "run/two")
    assert resolve_ref(mirror, "run/two") == base


def test_add_worktree_accepts_sha_base_ref(tmp_path, mirror):
    base = resolve_ref(mirror, "main")
    wt = tmp_path / "wt3"
    add_worktree(mirror, wt, "run/three", base)
    assert resolve_ref(mirror, "run/three") == base


def test_add_worktree_on_existing_branch_checks_it_out(tmp_path, mirror, cfg):
    wt_a = tmp_path / "a"
    add_worktree(mirror, wt_a, "run/shared", "main")
    write_json(wt_a, "x.json", {"v": 1})
    sha = commit_all(wt_a, "first", cfg)
    remove_worktree(mirror, wt_a)

    wt_b = tmp_path / "b"
    add_worktree(mirror, wt_b, "run/shared", "main")
    assert (wt_b / "x.json").exists()
    head = git("rev-parse", "HEAD", cwd=wt_b).stdout.strip()
    assert head == sha


# --- write_json -------------------------------------------------------------


def test_write_json_creates_nested_dirs(tmp_path, mirror):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/j", "main")
    p = write_json(wt, ".harness/runs/tr/t/task.json", {"a": 1, "b": ["x"]})
    assert p == wt / ".harness/runs/tr/t/task.json"
    assert p.exists()
    assert json.loads(p.read_text()) == {"a": 1, "b": ["x"]}
    # Indented, human-readable output.
    assert "\n  " in p.read_text()


# --- commit_all -------------------------------------------------------------


def test_commit_all_returns_sha_visible_in_mirror(tmp_path, mirror, cfg):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/c1", "main")
    write_json(wt, ".harness/runs/tr/t/task.json", {"hello": "world"})
    sha = commit_all(wt, "run output", cfg)
    assert sha is not None
    assert len(sha) == 40
    # No push step: the mirror sees the commit immediately.
    assert resolve_ref(mirror, "run/c1") == sha


def test_commit_all_clean_tree_returns_none(tmp_path, mirror, cfg):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/c2", "main")
    before = resolve_ref(mirror, "run/c2")
    assert commit_all(wt, "nothing", cfg) is None
    assert resolve_ref(mirror, "run/c2") == before


def test_commit_all_second_call_after_commit_returns_none(tmp_path, mirror, cfg):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/c3", "main")
    (wt / "a.txt").write_text("a\n")
    assert commit_all(wt, "one", cfg) is not None
    assert commit_all(wt, "two", cfg) is None


def test_commit_all_stages_untracked_and_modified_and_deleted(tmp_path, mirror, cfg):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/c4", "main")
    (wt / "new.txt").write_text("new\n")
    (wt / "README.md").unlink()
    sha = commit_all(wt, "churn", cfg)
    listing = git("ls-tree", "--name-only", "-r", sha, cwd=mirror).stdout.split()
    assert "new.txt" in listing
    assert "README.md" not in listing


def test_commit_all_content_retrievable_via_git_show(tmp_path, mirror, cfg):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/c5", "main")
    write_json(wt, ".harness/runs/tr/t/task.json", {"intent": "do-it"})
    sha = commit_all(wt, "artifact", cfg)
    blob = git("show", f"{sha}:.harness/runs/tr/t/task.json", cwd=mirror).stdout
    assert json.loads(blob) == {"intent": "do-it"}


def test_commit_all_uses_config_author_identity(tmp_path, mirror):
    cfg = Config(
        home=tmp_path / "home",
        commit_author_name="Robo Agent",
        commit_author_email="robo@example.test",
    )
    cfg.ensure_dirs()
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/c6", "main")
    (wt / "f.txt").write_text("f\n")
    sha = commit_all(wt, "identity", cfg)
    fmt = git("show", "-s", "--format=%an|%ae|%cn|%ce|%s", sha, cwd=mirror).stdout.strip()
    assert fmt == "Robo Agent|robo@example.test|Robo Agent|robo@example.test|identity"


def test_commit_all_message_is_used(tmp_path, mirror, cfg):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/c7", "main")
    (wt / "f.txt").write_text("f\n")
    sha = commit_all(wt, "a very specific message", cfg)
    subject = git("show", "-s", "--format=%s", sha, cwd=mirror).stdout.strip()
    assert subject == "a very specific message"


# --- artifact inheritance (the core mechanism) ------------------------------


def test_second_worktree_inherits_first_runs_artifacts(tmp_path, mirror, cfg):
    wt1 = tmp_path / "run1"
    add_worktree(mirror, wt1, "run/first", "main")
    write_json(wt1, ".harness/runs/tr/t1/result.json", {"status": "ok"})
    (wt1 / "src.txt").write_text("produced by run 1\n")
    sha1 = commit_all(wt1, "run 1 output", cfg)
    assert sha1 is not None

    wt2 = tmp_path / "run2"
    add_worktree(mirror, wt2, "run/second", sha1)
    assert (wt2 / "src.txt").read_text() == "produced by run 1\n"
    inherited = wt2 / ".harness/runs/tr/t1/result.json"
    assert json.loads(inherited.read_text()) == {"status": "ok"}

    write_json(wt2, ".harness/runs/tr/t2/result.json", {"status": "ok", "n": 2})
    sha2 = commit_all(wt2, "run 2 output", cfg)
    assert sha2 is not None
    assert resolve_ref(mirror, "run/second") == sha2
    files = git("ls-tree", "--name-only", "-r", sha2, cwd=mirror).stdout.split()
    assert ".harness/runs/tr/t1/result.json" in files
    assert ".harness/runs/tr/t2/result.json" in files
    parent = git("rev-parse", f"{sha2}^", cwd=mirror).stdout.strip()
    assert parent == sha1


# --- remove_worktree / prune_worktrees --------------------------------------


def test_remove_worktree_deletes_dir_but_keeps_branch(tmp_path, mirror, cfg):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/rm", "main")
    (wt / "f.txt").write_text("f\n")
    sha = commit_all(wt, "keep me", cfg)

    remove_worktree(mirror, wt)
    assert not wt.exists()
    assert resolve_ref(mirror, "run/rm") == sha
    assert _p(wt) not in _worktree_paths(mirror)


def test_remove_worktree_with_dirty_tree_forces(tmp_path, mirror):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/dirty", "main")
    (wt / "uncommitted.txt").write_text("junk\n")
    (wt / "README.md").write_text("modified\n")
    remove_worktree(mirror, wt, force=True)
    assert not wt.exists()


def test_remove_worktree_missing_path_is_tolerated(tmp_path, mirror):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/gone", "main")
    shutil.rmtree(wt)
    remove_worktree(mirror, wt)
    assert _p(wt) not in _worktree_paths(mirror)


def test_prune_worktrees_clears_stale_registration(tmp_path, mirror):
    wt = tmp_path / "wt"
    add_worktree(mirror, wt, "run/stale", "main")
    assert _p(wt) in _worktree_paths(mirror)
    shutil.rmtree(wt)
    # Still registered until pruned.
    prune_worktrees(mirror)
    assert _p(wt) not in _worktree_paths(mirror)
    # The branch survives pruning.
    assert branch_exists(mirror, "run/stale")


def test_prune_worktrees_keeps_live_worktrees(tmp_path, mirror):
    wt = tmp_path / "live"
    add_worktree(mirror, wt, "run/live", "main")
    prune_worktrees(mirror)
    assert _p(wt) in _worktree_paths(mirror)
    assert wt.is_dir()
