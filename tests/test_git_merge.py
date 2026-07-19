from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentharness.config import Config
from agentharness.git.merge import MergeConflict, gc_run_branches, merge_leaves
from agentharness.git.mirror import (
    branch_exists,
    clone_mirror,
    create_branch,
    git,
    resolve_ref,
)
from agentharness.git.worktree import add_worktree, commit_all, remove_worktree
from agentharness.models import RepoDef


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


@pytest.fixture()
def repo():
    return RepoDef(
        repo_id="demo",
        url="file:///dev/null",
        integration_branch="harness/integration",
        base_branch="main",
    )


def _leaf(tmp_path, mirror, cfg, branch, files, base="main"):
    """Create `branch` off `base` containing `files` (name -> content)."""
    wt = tmp_path / f"wt-{branch.replace('/', '-')}"
    add_worktree(mirror, wt, branch, base)
    for name, content in files.items():
        p = wt / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    sha = commit_all(wt, f"work on {branch}", cfg)
    remove_worktree(mirror, wt)
    return sha


def _backdate(mirror, branch, days):
    """Rewrite `branch`'s tip commit so its committer date is `days` old."""
    sha = resolve_ref(mirror, branch)
    tree = git("rev-parse", f"{sha}^{{tree}}", cwd=mirror).stdout.strip()
    parents = git("rev-list", "--parents", "-n", "1", sha, cwd=mirror).stdout.split()[1:]
    when = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S+0000"
    )
    args = ["commit-tree", tree]
    for p in parents:
        args += ["-p", p]
    args += ["-m", "backdated"]
    new = git(
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@localhost",
        *args,
        cwd=mirror,
        env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
    ).stdout.strip()
    create_branch(mirror, branch, new)
    return new


def _files_at(mirror, ref):
    return git("ls-tree", "--name-only", "-r", ref, cwd=mirror).stdout.split()


def _worktree_paths(mirror):
    out = git("worktree", "list", "--porcelain", cwd=mirror).stdout
    return [
        str(Path(line.split(" ", 1)[1]).resolve())
        for line in out.splitlines()
        if line.startswith("worktree ")
    ]


# --- merge_leaves -----------------------------------------------------------


def test_merge_one_leaf_creates_integration_branch(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    assert not branch_exists(mirror, repo.integration_branch)

    sha = merge_leaves(mirror, ["run/a"], repo, cfg)

    assert branch_exists(mirror, repo.integration_branch)
    assert resolve_ref(mirror, repo.integration_branch) == sha
    assert "a.txt" in _files_at(mirror, sha)


def test_merge_two_non_overlapping_leaves(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    _leaf(tmp_path, mirror, cfg, "run/b", {"b.txt": "b\n"})

    sha = merge_leaves(mirror, ["run/a", "run/b"], repo, cfg)

    files = _files_at(mirror, sha)
    assert "a.txt" in files
    assert "b.txt" in files


def test_merge_uses_no_ff(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    sha = merge_leaves(mirror, ["run/a"], repo, cfg)
    parents = git("rev-list", "--parents", "-n", "1", sha, cwd=mirror).stdout.split()
    # A merge commit: itself plus two parents.
    assert len(parents) == 3


def test_merge_into_existing_integration_branch(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    first = merge_leaves(mirror, ["run/a"], repo, cfg)
    _leaf(tmp_path, mirror, cfg, "run/b", {"b.txt": "b\n"})
    second = merge_leaves(mirror, ["run/b"], repo, cfg)

    assert second != first
    files = _files_at(mirror, second)
    assert "a.txt" in files and "b.txt" in files


def test_merge_no_branches_just_ensures_integration_exists(mirror, cfg, repo):
    base = resolve_ref(mirror, "main")
    sha = merge_leaves(mirror, [], repo, cfg)
    assert sha == base
    assert resolve_ref(mirror, repo.integration_branch) == base


def test_merge_removes_temp_worktree(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    merge_leaves(mirror, ["run/a"], repo, cfg)
    # Only the bare mirror itself remains registered.
    assert _worktree_paths(mirror) == [str(Path(mirror).resolve())]
    assert list(cfg.worktrees_dir.iterdir()) == []


# --- safety: base_branch must never move ------------------------------------


def test_merge_never_modifies_base_branch(tmp_path, mirror, cfg, repo):
    main_before = resolve_ref(mirror, "main")
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    _leaf(tmp_path, mirror, cfg, "run/b", {"b.txt": "b\n"})

    merge_leaves(mirror, ["run/a", "run/b"], repo, cfg)

    assert resolve_ref(mirror, "main") == main_before


def test_merge_conflict_does_not_modify_base_branch(tmp_path, mirror, cfg, repo):
    main_before = resolve_ref(mirror, "main")
    _leaf(tmp_path, mirror, cfg, "run/a", {"README.md": "from a\n"})
    _leaf(tmp_path, mirror, cfg, "run/b", {"README.md": "from b\n"})

    with pytest.raises(MergeConflict):
        merge_leaves(mirror, ["run/a", "run/b"], repo, cfg)

    assert resolve_ref(mirror, "main") == main_before


# --- conflicts --------------------------------------------------------------


def test_merge_conflict_raises_with_branch_and_files(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"README.md": "from a\n"})
    _leaf(tmp_path, mirror, cfg, "run/b", {"README.md": "from b\n"})

    with pytest.raises(MergeConflict) as ei:
        merge_leaves(mirror, ["run/a", "run/b"], repo, cfg)

    err = ei.value
    assert err.branch == "run/b"
    assert err.files == ["README.md"]
    assert "run/b" in str(err)
    assert "README.md" in str(err)


def test_merge_conflict_cleans_up_worktree(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"README.md": "from a\n"})
    _leaf(tmp_path, mirror, cfg, "run/b", {"README.md": "from b\n"})

    with pytest.raises(MergeConflict):
        merge_leaves(mirror, ["run/a", "run/b"], repo, cfg)

    assert _worktree_paths(mirror) == [str(Path(mirror).resolve())]
    assert list(cfg.worktrees_dir.iterdir()) == []


def test_merge_conflict_leaves_integration_at_last_good_merge(
    tmp_path, mirror, cfg, repo
):
    _leaf(tmp_path, mirror, cfg, "run/a", {"README.md": "from a\n"})
    after_a = merge_leaves(mirror, ["run/a"], repo, cfg)

    _leaf(tmp_path, mirror, cfg, "run/b", {"README.md": "from b\n"})
    with pytest.raises(MergeConflict):
        merge_leaves(mirror, ["run/b"], repo, cfg)

    assert resolve_ref(mirror, repo.integration_branch) == after_a


# --- gc_run_branches --------------------------------------------------------


def test_gc_deletes_old_merged_branch(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/old", {"old.txt": "o\n"})
    _backdate(mirror, "run/old", days=60)
    merge_leaves(mirror, ["run/old"], repo, cfg)

    deleted = gc_run_branches(mirror, older_than_days=30, cfg=cfg)

    assert deleted == ["run/old"]
    assert not branch_exists(mirror, "run/old")


def test_gc_keeps_recent_merged_branch(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/recent", {"r.txt": "r\n"})
    merge_leaves(mirror, ["run/recent"], repo, cfg)

    deleted = gc_run_branches(mirror, older_than_days=30, cfg=cfg)

    assert deleted == []
    assert branch_exists(mirror, "run/recent")


def test_gc_keeps_old_unmerged_branch(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/keep", {"k.txt": "k\n"})
    _leaf(tmp_path, mirror, cfg, "run/unmerged", {"u.txt": "u\n"})
    _backdate(mirror, "run/unmerged", days=90)
    merge_leaves(mirror, ["run/keep"], repo, cfg)

    deleted = gc_run_branches(mirror, older_than_days=30, cfg=cfg)

    assert deleted == []
    assert branch_exists(mirror, "run/unmerged")


def test_gc_ignores_non_run_branches(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    merge_leaves(mirror, ["run/a"], repo, cfg)
    _backdate(mirror, "main", days=365)

    gc_run_branches(mirror, older_than_days=30, cfg=cfg)

    assert branch_exists(mirror, "main")
    assert branch_exists(mirror, repo.integration_branch)


def test_gc_without_integration_branch_is_a_noop(tmp_path, mirror, cfg):
    _leaf(tmp_path, mirror, cfg, "run/a", {"a.txt": "a\n"})
    _backdate(mirror, "run/a", days=99)

    assert gc_run_branches(mirror, older_than_days=30, cfg=cfg) == []
    assert branch_exists(mirror, "run/a")


def test_gc_deletes_multiple_and_returns_sorted_names(tmp_path, mirror, cfg, repo):
    _leaf(tmp_path, mirror, cfg, "run/x", {"x.txt": "x\n"})
    _leaf(tmp_path, mirror, cfg, "run/y", {"y.txt": "y\n"})
    _backdate(mirror, "run/x", days=40)
    _backdate(mirror, "run/y", days=50)
    merge_leaves(mirror, ["run/x", "run/y"], repo, cfg)

    deleted = gc_run_branches(mirror, older_than_days=30, cfg=cfg)

    assert deleted == ["run/x", "run/y"]
    assert not branch_exists(mirror, "run/x")
    assert not branch_exists(mirror, "run/y")
