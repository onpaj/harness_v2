import subprocess

import pytest

from harness.drivers.git_workspace import GitError, GitWorkspace
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
    # `-b main` pins the initial branch regardless of the machine's
    # `init.defaultBranch` (older git / CI defaults to `master`), so the tests
    # that push and merge `main` don't depend on ambient git config.
    _git(["init", "-b", "main"], path)
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


def _make_bare_remote(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "--bare"], path)


def test_push_publishes_the_task_branch_to_origin(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    remote = tmp_path / "remote.git"
    _make_bare_remote(remote)
    _git(["remote", "add", "origin", str(remote)], repo)
    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root=tmp_path / "wt")

    handle = workspace.attach(_make_task())
    handle.write("app.py", "print('hi')\n")
    handle.commit("work")
    handle.push()

    branches = _git(["branch", "--list"], remote)
    assert "harness/tsk_1" in branches


def test_push_twice_is_a_noop(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    remote = tmp_path / "remote.git"
    _make_bare_remote(remote)
    _git(["remote", "add", "origin", str(remote)], repo)
    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root=tmp_path / "wt")

    handle = workspace.attach(_make_task())
    handle.write("app.py", "print('hi')\n")
    handle.commit("work")
    handle.push()
    handle.push()  # must not raise

    assert "harness/tsk_1" in _git(["branch", "--list"], remote)


def test_push_without_a_remote_raises(tmp_path):
    workspace = _workspace(tmp_path)  # no origin configured

    handle = workspace.attach(_make_task())
    handle.write("app.py", "x\n")
    handle.commit("work")

    with pytest.raises(GitError):
        handle.push()


def _workspace_with_remote(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    remote = tmp_path / "remote.git"
    _make_bare_remote(remote)
    _git(["remote", "add", "origin", str(remote)], repo)
    registry = MemoryRepositoryRegistry({"app": repo})
    return GitWorkspace(registry, worktrees_root=tmp_path / "wt")


def test_attach_with_branch_override_force_checks_out_already_checked_out_branch(tmp_path):
    """A resolver task's branch override targets an existing PR branch — one
    that, by construction, is already checked out in the *original* task's own
    worktree (nothing ever removes a worktree). Without `--force` git refuses
    a second checkout of the same branch."""
    workspace = _workspace_with_remote(tmp_path)
    original = workspace.attach(_make_task("tsk_original"))
    original.write("feature.txt", "hi\n")
    original.commit("[development] work")
    original.push()

    resolver_task = Task(
        id="tsk_resolver",
        workflow_template="resolver",
        created="2026-07-20T10:00:00Z",
        repository="app",
        data={"branch": "harness/tsk_original"},
    )

    handle = workspace.attach(resolver_task)

    assert handle.path == tmp_path / "wt" / "tsk_resolver"
    assert handle.branch == "harness/tsk_original"
    assert (handle.path / "feature.txt").read_text(encoding="utf-8") == "hi\n"
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], handle.path).strip()
    assert branch == "harness/tsk_original"


def test_attach_with_branch_override_creates_from_origin_when_no_local_copy(tmp_path):
    """When the base repo never locally saw the branch (fresh registry entry),
    it is created from `origin/<branch>` instead of reused."""
    repo = tmp_path / "repo"
    _make_repo(repo)
    remote = tmp_path / "remote.git"
    _make_bare_remote(remote)
    _git(["remote", "add", "origin", str(remote)], repo)
    _git(["push", "origin", "main"], repo)

    # A branch that exists only on the remote (as if pushed from elsewhere,
    # e.g. a different registry checkout), never checked out locally here.
    other_clone = tmp_path / "other_clone"
    _git(["clone", str(remote), str(other_clone)], tmp_path)
    _git(["checkout", "-b", "harness/tsk_elsewhere"], other_clone)
    (other_clone / "feature.txt").write_text("from elsewhere\n", encoding="utf-8")
    _git(["add", "-A"], other_clone)
    _git(["commit", "-m", "work"], other_clone)
    _git(["push", "origin", "harness/tsk_elsewhere"], other_clone)

    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root=tmp_path / "wt")
    resolver_task = Task(
        id="tsk_resolver",
        workflow_template="resolver",
        created="2026-07-20T10:00:00Z",
        repository="app",
        data={"branch": "harness/tsk_elsewhere"},
    )

    handle = workspace.attach(resolver_task)

    assert handle.branch == "harness/tsk_elsewhere"
    assert (handle.path / "feature.txt").read_text(encoding="utf-8") == "from elsewhere\n"


def test_attach_with_branch_override_reconciles_stale_local_ref_with_origin(tmp_path):
    """`GithubConflictsCheck`'s `update_branch` call advances a PR branch
    entirely server-side (merges base into head via the GitHub API) — no local
    git operation touches it. Simulate that by advancing `origin/<branch>` through
    a *second* clone that never shares the base repo's local refs, mirroring
    the real `behind` -> `update_branch` -> `dirty` -> resolver sequence.
    `attach`'s branch-override path must reconcile the reused local ref with
    that new tip, not silently reuse the now-stale one — otherwise the
    resolver's eventual push is rejected as non-fast-forward."""
    workspace = _workspace_with_remote(tmp_path)
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"

    original = workspace.attach(_make_task("tsk_original"))
    original.write("feature.txt", "hi\n")
    original.commit("[development] work")
    original.push()

    # Advance origin independently of `repo`'s local refs — simulating
    # GitHub's server-side update-branch merge, which never touches any local
    # git state anywhere in this process.
    other_clone = tmp_path / "other_clone"
    _git(["clone", str(remote), str(other_clone)], tmp_path)
    _git(["checkout", "harness/tsk_original"], other_clone)
    (other_clone / "server_side.txt").write_text("from update_branch\n", encoding="utf-8")
    _git(["add", "-A"], other_clone)
    _git(["commit", "-m", "server-side update-branch merge"], other_clone)
    _git(["push", "origin", "harness/tsk_original"], other_clone)

    resolver_task = Task(
        id="tsk_resolver",
        workflow_template="resolver",
        created="2026-07-20T10:00:00Z",
        repository="app",
        data={"branch": "harness/tsk_original"},
    )

    handle = workspace.attach(resolver_task)

    # The new worktree must reflect origin's actual tip, not the stale local ref.
    assert (handle.path / "server_side.txt").read_text(encoding="utf-8") == "from update_branch\n"
    head = _git(["rev-parse", "HEAD"], handle.path).strip()
    origin_head = _git(["rev-parse", "origin/harness/tsk_original"], repo).strip()
    assert head == origin_head

    # And a subsequent push (after the resolver's own commit) must succeed —
    # not get rejected as non-fast-forward.
    handle.write("resolved.txt", "resolved\n")
    handle.commit("[resolve] merge conflict resolution")
    handle.push()


def test_attach_reattach_with_override_reconciles_with_origin_after_server_side_advance(tmp_path):
    """A resolver task that fails once and is re-run reattaches an existing
    worktree. If its overridden branch advanced server-side (`update_branch`)
    between attempts, the reattach must reconcile with `origin/<branch>` — not
    reset to the stale local `HEAD` — or the resolver's eventual push is
    rejected as non-fast-forward. Mirrors invariant 31 on the reattach path."""
    workspace = _workspace_with_remote(tmp_path)
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"

    original = workspace.attach(_make_task("tsk_original"))
    original.write("feature.txt", "hi\n")
    original.commit("[development] work")
    original.push()

    resolver_task = Task(
        id="tsk_resolver",
        workflow_template="resolver",
        created="2026-07-20T10:00:00Z",
        repository="app",
        data={"branch": "harness/tsk_original"},
    )

    # First attach creates the resolver's worktree (reconciled with origin).
    workspace.attach(resolver_task)

    # Now the branch advances server-side, independently of any local ref —
    # as `GithubConflictsCheck`'s `update_branch` call does via the GitHub API.
    other_clone = tmp_path / "other_clone"
    _git(["clone", str(remote), str(other_clone)], tmp_path)
    _git(["checkout", "harness/tsk_original"], other_clone)
    (other_clone / "server_side.txt").write_text("from update_branch\n", encoding="utf-8")
    _git(["add", "-A"], other_clone)
    _git(["commit", "-m", "server-side update-branch merge"], other_clone)
    _git(["push", "origin", "harness/tsk_original"], other_clone)

    # Re-run: the worktree already exists, so this exercises the reattach path.
    handle = workspace.attach(resolver_task)

    assert (handle.path / "server_side.txt").read_text(encoding="utf-8") == "from update_branch\n"
    head = _git(["rev-parse", "HEAD"], handle.path).strip()
    origin_head = _git(["rev-parse", "origin/harness/tsk_original"], repo).strip()
    assert head == origin_head

    # And a subsequent commit + push must stay fast-forward.
    handle.write("resolved.txt", "resolved\n")
    handle.commit("[resolve] merge conflict resolution")
    handle.push()


def test_attach_reattach_with_override_preserves_unpushed_local_commit(tmp_path):
    """The resolve -> land hand-off: `resolve` attaches, merges, and commits
    *locally* (no push — that isn't its job). `land` then re-attaches the same
    override worktree before pushing. The reattach must not discard that
    un-pushed commit by resetting to the still-stale `origin/<branch>` —
    otherwise `land`'s push is a no-op and the PR head never advances (#86)."""
    workspace = _workspace_with_remote(tmp_path)
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"

    original = workspace.attach(_make_task("tsk_original"))
    original.write("feature.txt", "hi\n")
    original.commit("[development] work")
    original.push()

    resolver_task = Task(
        id="tsk_resolver",
        workflow_template="resolver",
        created="2026-07-20T10:00:00Z",
        repository="app",
        data={"branch": "harness/tsk_original"},
    )

    # `resolve` step: attach, then commit locally — no push.
    resolve_handle = workspace.attach(resolver_task)
    resolve_handle.write("resolved.txt", "resolved\n")
    merge_sha = resolve_handle.commit("[resolve] merge conflict resolution")
    assert merge_sha is not None

    # `land` step: reattach the same override worktree.
    land_handle = workspace.attach(resolver_task)

    head = _git(["rev-parse", "HEAD"], land_handle.path).strip()
    assert head == merge_sha  # the un-pushed merge commit must survive reattach
    origin_head_before = _git(["rev-parse", "origin/harness/tsk_original"], repo).strip()
    assert origin_head_before != merge_sha  # nothing pushed yet

    land_handle.push()

    origin_head_after = _git(["rev-parse", "origin/harness/tsk_original"], repo).strip()
    assert origin_head_after == merge_sha


def test_attach_reattach_with_override_raises_on_genuine_divergence(tmp_path):
    """If both the local worktree (an un-pushed commit from `resolve`) and
    `origin/<branch>` (a server-side `update_branch` merge) advance
    independently between attach calls, neither history is a superset of the
    other. `attach` must not silently pick a side — it raises, leaving both
    the local HEAD and `origin/<branch>` untouched."""
    workspace = _workspace_with_remote(tmp_path)
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"

    original = workspace.attach(_make_task("tsk_original"))
    original.write("feature.txt", "hi\n")
    original.commit("[development] work")
    original.push()

    resolver_task = Task(
        id="tsk_resolver",
        workflow_template="resolver",
        created="2026-07-20T10:00:00Z",
        repository="app",
        data={"branch": "harness/tsk_original"},
    )

    resolve_handle = workspace.attach(resolver_task)
    resolve_handle.write("resolved.txt", "resolved\n")
    local_sha = resolve_handle.commit("[resolve] merge conflict resolution")

    # origin/<branch> advances independently, server-side, via a second clone
    # that never shares this local commit.
    other_clone = tmp_path / "other_clone"
    _git(["clone", str(remote), str(other_clone)], tmp_path)
    _git(["checkout", "harness/tsk_original"], other_clone)
    (other_clone / "server_side.txt").write_text("from update_branch\n", encoding="utf-8")
    _git(["add", "-A"], other_clone)
    _git(["commit", "-m", "server-side update-branch merge"], other_clone)
    _git(["push", "origin", "harness/tsk_original"], other_clone)
    # Read the bare remote directly (not `repo`'s remote-tracking ref, which
    # `attach`'s own fetch is expected to update) — this is the canonical
    # value that only a `push` could move, and `attach` never pushes.
    remote_head_before = _git(["rev-parse", "refs/heads/harness/tsk_original"], remote).strip()

    with pytest.raises(GitError):
        workspace.attach(resolver_task)

    # Neither side moved as a result of the failed reattach.
    head_after = _git(["rev-parse", "HEAD"], resolve_handle.path).strip()
    assert head_after == local_sha
    remote_head_after = _git(["rev-parse", "refs/heads/harness/tsk_original"], remote).strip()
    assert remote_head_after == remote_head_before


def test_reattach_without_override_still_resets_to_local_head(tmp_path):
    """A non-override reattach is unchanged: it resets to local `HEAD`, keeping
    the committed work of the same task (nobody else advances that branch)."""
    workspace = _workspace(tmp_path)
    task = _make_task()

    first = workspace.attach(task)
    first.write("feature.txt", "hi\n")
    first.commit("[design] work")
    (first.path / "scratch.txt").write_text("wip\n", encoding="utf-8")

    second = workspace.attach(task)

    assert (second.path / "feature.txt").read_text(encoding="utf-8") == "hi\n"
    assert not (second.path / "scratch.txt").exists()


def test_attach_without_override_is_unchanged(tmp_path):
    """The absent-key path (every non-resolver task) is byte-for-byte unchanged."""
    workspace = _workspace(tmp_path)

    handle = workspace.attach(_make_task())

    assert handle.branch == "harness/tsk_1"


def test_merge_clean_stages_result_and_returns_false(tmp_path):
    workspace = _workspace_with_remote(tmp_path)
    repo = tmp_path / "repo"
    _git(["push", "origin", "main"], repo)

    handle = workspace.attach(_make_task())
    handle.write("feature.txt", "hi\n")
    handle.commit("[development] work")

    conflicted = handle.merge("main")

    assert conflicted is False
    log = _git(["log", "--oneline"], handle.path)
    assert "[development] work" in log


def test_merge_without_git_identity_configured_still_succeeds(tmp_path, monkeypatch):
    """`git merge` validates the committer identity up front — even with
    `--no-commit`, which only defers the commit itself. On a machine with no
    identity configured the merge fails `exit 128` ("Committer identity
    unknown") before merging anything, which would land in the `raise GitError`
    branch and fail the task. The driver must supply its own identity to the
    merge exactly as it does to `commit()`."""
    # Neutralize any ambient global/system git identity for this process so the
    # only identity available to the merge is the one the driver injects.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.name", "seed"], repo)
    _git(["config", "user.email", "seed@local"], repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "initial"], repo)
    remote = tmp_path / "remote.git"
    _make_bare_remote(remote)
    _git(["remote", "add", "origin", str(remote)], repo)
    _git(["push", "origin", "main"], repo)

    registry = MemoryRepositoryRegistry({"app": repo})
    workspace = GitWorkspace(registry, worktrees_root=tmp_path / "wt")

    handle = workspace.attach(_make_task())
    handle.write("feature.txt", "hi\n")
    handle.commit("[development] work")

    # main advances with a non-conflicting change, pushed to origin.
    (repo / "CHANGES.md").write_text("more\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "more"], repo)
    _git(["push", "origin", "main"], repo)

    # Now the machine has no git identity at all.
    _git(["config", "--unset", "user.name"], repo)
    _git(["config", "--unset", "user.email"], repo)

    # Must not raise "Committer identity unknown".
    conflicted = handle.merge("main")

    assert conflicted is False


def test_merge_conflict_leaves_markers_and_returns_true(tmp_path):
    workspace = _workspace_with_remote(tmp_path)
    repo = tmp_path / "repo"

    # A PR branch that diverges from main on the same file.
    branch_handle = workspace.attach(_make_task("tsk_branch"))
    branch_handle.write("README.md", "branch change\n")
    branch_handle.commit("[development] branch change")

    # main moves forward with a conflicting edit to the same file, pushed to origin.
    (repo / "README.md").write_text("main change\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "main change"], repo)
    _git(["push", "origin", "main"], repo)

    conflicted = branch_handle.merge("main")

    assert conflicted is True
    content = (branch_handle.path / "README.md").read_text(encoding="utf-8")
    assert "<<<<<<<" in content
    assert ">>>>>>>" in content
