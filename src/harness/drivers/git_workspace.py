"""Git worktree as a working directory.

`attach` derives the repo root from the name (`task.repository`) via
`RepositoryRegistry` and places the worktree under `<worktrees_root>/<task_id>`.
If it does not exist, it creates it via `git worktree add` on the task branch
`harness/<task_id>` from the repo's HEAD; if it exists (reattach after a crash /
backward edge), it **resets** it back to HEAD and cleans up untracked files
(`reset --hard` + `clean -fd`, without `-x`), so the next attempt starts from a
clean slate. The handle can write a file and commit. Commit stages everything
and returns None instead of an empty commit when the working directory is empty.

`task.data["branch"]`, when set (resolver tasks), overrides the task branch:
`attach` checks out that *existing* branch into a second worktree instead of
creating a fresh one from HEAD — `--force`d, because the branch is by
construction already checked out in the original task's own, never-cleaned-up
worktree; that worktree is permanently inert once its task is terminal, so
sharing the branch ref between the two is safe. This reuses the local branch
ref as-is (not `-B` from `origin/<branch>`: git refuses to force-reset a
branch checked out elsewhere no matter how many times you pass `--force` —
that guard is separate from, and not overridden by, the "already checked out"
guard `--force` does lift), falling back to creating it fresh from
`origin/<branch>` only the one time no local copy exists yet. Reusing the
local branch this way would leave the new worktree stale whenever the branch
last advanced server-side (`GithubConflictsCheck`'s `GithubClient.update_branch`
call, which never touches any local ref) rather than through a harness-driven
commit+push here — so once the reused worktree exists, it is immediately hard-reset to
`origin/<branch>`'s actual tip before the caller does anything else with it.
A similar reconciliation runs on **reattach** of an override task (the worktree
already exists from a prior attempt), but there it is ancestry-aware rather
than an unconditional reset: local `HEAD` behind or equal to `origin/<branch>`
still resets to origin (a resolver retry, or a server-side advance with
nothing local to lose); local `HEAD` ahead of `origin/<branch>` — the
`resolve` step's un-pushed merge commit, waiting for `land` to push it — is
left untouched; if the two have diverged, `attach` raises rather than
silently discarding either side (see `GitError`, invariant 31). A non-override
reattach still unconditionally resets to local `HEAD` — nobody else moves a
`harness/<task.id>` branch.

Every `git` invocation that may create a commit (`commit`, and the up-front
identity check `merge` makes) carries the harness's own identity in the
environment, so the driver works on a machine with no git identity configured.

A task with no `repository` at all (`task.repository is None` — e.g. a `heal`
task, ADR-0018) has no registered repo to derive a worktree from; `attach`
checks this first and hands off to `_attach_repo_less`, which git-initializes a
standalone scratch repo at the same `<worktrees_root>/<task_id>` path instead,
with the same reset-on-reattach idempotence. Implicit contract: a repo-less
task's workflow must end before any step that pushes — `push()` has no
`origin` to push to there.

Calls the system `git` via subprocess — no new production dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness.models import Task
from harness.ports.repos import RepositoryRegistry
from harness.ports.workspace import Workspace, WorkspaceHandle

_IDENTITY = {
    "GIT_AUTHOR_NAME": "harness",
    "GIT_AUTHOR_EMAIL": "harness@local",
    "GIT_COMMITTER_NAME": "harness",
    "GIT_COMMITTER_EMAIL": "harness@local",
}


class GitError(RuntimeError):
    """The system `git` failed. Carries the command and its stderr."""


def _git(args: list[str], *, cwd: Path | None = None, env_extra: dict[str, str] | None = None) -> str:
    env = None
    if env_extra is not None:
        import os

        env = {**os.environ, **env_extra}
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise GitError(
            f"git {' '.join(args)} failed (exit {error.returncode}): {error.stderr.strip()}"
        ) from error
    return result.stdout


def _branch_exists_locally(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _is_ancestor(repo: Path, maybe_ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", maybe_ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _reconcile_override_reattach(base: Path, worktree: Path, branch: str) -> None:
    """Reattach an override (resolver) worktree, preserving any un-pushed local
    commit instead of blindly resetting to origin.

    Three-way ancestry check via `git merge-base --is-ancestor`:
      - local HEAD is an ancestor of (or equal to) origin/<branch>: nothing
        local to lose — reset to origin (today's behavior, e.g. a resolver
        retry with no commit yet, or update_branch's server-side advance).
      - origin/<branch> is an ancestor of local HEAD: a real, un-pushed local
        commit is sitting ahead (the resolve -> land hand-off) — keep it,
        untouched.
      - neither is an ancestor of the other: both sides advanced
        independently since the worktree was last reconciled — raise rather
        than silently pick a side.
    """
    _git(["-C", str(base), "fetch", "origin", branch])
    local_head = _git(["-C", str(worktree), "rev-parse", "HEAD"]).strip()
    origin_head = _git(["-C", str(base), "rev-parse", f"origin/{branch}"]).strip()

    if local_head == origin_head or _is_ancestor(worktree, local_head, origin_head):
        _git(["-C", str(worktree), "reset", "--hard", f"origin/{branch}"])
    elif _is_ancestor(worktree, origin_head, local_head):
        pass  # local is ahead — nothing to reconcile, HEAD stays as-is
    else:
        raise GitError(
            f"branch {branch!r} diverged on reattach: local HEAD {local_head} and "
            f"origin/{branch} {origin_head} share no ancestry — refusing to guess "
            "which side to keep"
        )
    _git(["-C", str(worktree), "clean", "-fd"])


class GitWorkspaceHandle(WorkspaceHandle):
    """An attached git worktree."""

    def __init__(self, path: Path, branch: str) -> None:
        self._path = Path(path)
        self._branch = branch

    @property
    def path(self) -> Path:
        return self._path

    @property
    def branch(self) -> str:
        return self._branch

    def write(self, relpath: str, content: str) -> None:
        target = self._path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str | None:
        _git(["-C", str(self._path), "add", "-A"])
        status = _git(["-C", str(self._path), "status", "--porcelain"])
        if status.strip() == "":
            return None
        _git(
            ["-C", str(self._path), "commit", "-m", message],
            env_extra=_IDENTITY,
        )
        return _git(["-C", str(self._path), "rev-parse", "HEAD"]).strip()

    def push(self) -> None:
        # Plain push, no force: the task branch only ever moves forward.
        # Reset-on-reattach (`reset --hard` + `clean -fd`) discards uncommitted
        # working-tree state on a re-run, it never rewinds the branch pointer.
        # A rejected push therefore means something else touched the branch —
        # that must surface as a failure, not be forced over.
        _git(
            [
                "-C",
                str(self._path),
                "push",
                "-u",
                "origin",
                self._branch,
            ]
        )

    def merge(self, base: str) -> bool:
        _git(["-C", str(self._path), "fetch", "origin", base])
        # `git merge` validates the committer identity up front — even with
        # `--no-commit`, which only defers the *commit*, not the identity check
        # git makes before touching the working tree. On a machine with no git
        # identity configured this fails `exit 128` ("Committer identity
        # unknown") before any merge happens, which would otherwise land in the
        # `raise GitError` branch below and fail the task. So the harness's own
        # identity travels with the merge exactly as it does with `commit()`.
        import os

        env = {**os.environ, **_IDENTITY}
        result = subprocess.run(
            ["git", "-C", str(self._path), "merge", "--no-commit", "--no-ff", f"origin/{base}"],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode == 0:
            return False
        if "CONFLICT" in result.stdout or "Automatic merge failed" in result.stdout:
            # Conflict markers are now in the working tree — `commit()`'s
            # existing add-all + commit sequence produces a two-parent merge
            # commit unmodified once the caller has resolved them (MERGE_HEAD
            # is present, no `--continue`/special flag needed).
            return True
        raise GitError(f"git merge origin/{base} failed: {result.stderr.strip()}")

    def abort_merge(self) -> None:
        # `git merge --abort` restores the pre-merge HEAD, index and working
        # tree (clears MERGE_HEAD and the conflict markers). Landing calls this
        # only when merge() reported a conflict, so a merge is always in
        # progress here — there is nothing to abort otherwise.
        _git(["-C", str(self._path), "merge", "--abort"])


class GitWorkspace(Workspace):
    """Creates and reuses git worktrees under a shared root.

    Derives the repo root from the **name** (`task.repository`) via the registry
    — the task carries a logical name, not a path. The worktree lives at
    `<worktrees_root>/<task_id>`. If it does not exist, it creates it via
    `git worktree add`; on reattach a dirty worktree is reset to HEAD (so both a
    backward edge and a restart after a crash start from a clean slate).
    """

    def __init__(
        self,
        registry: RepositoryRegistry,
        worktrees_root: Path,
    ) -> None:
        self._registry = registry
        self._worktrees_root = Path(worktrees_root)

    def attach(self, task: Task) -> GitWorkspaceHandle:
        # A repo-less task (`task.repository is None` — e.g. a `heal` task,
        # ADR-0018) has no registered repo to derive a worktree from at all;
        # `resolve` must never see `None`, so this is checked first, before
        # `override`/`branch` are even computed.
        if task.repository is None:
            return self._attach_repo_less(task)

        # `task.data["branch"]` (resolver tasks) checks out an *existing* branch
        # instead of creating a fresh `harness/<task.id>` from HEAD — the
        # resolver fixes the same PR's branch, it doesn't open a new one.
        override = task.data.get("branch")
        branch = override or f"harness/{task.id}"

        base = self._registry.resolve(task.repository)
        worktree = self._worktrees_root / task.id
        if not worktree.exists():
            worktree.parent.mkdir(parents=True, exist_ok=True)
            if override:
                # No worktree is ever removed (nothing under src/harness ever
                # calls `git worktree remove`), so a harness-authored branch is
                # always still checked out in the *original* task's own
                # worktree. `--force` is git's own escape hatch for "branch
                # already checked out elsewhere" — safe here because that
                # original worktree is permanently inert once its task reaches
                # a terminal state; nothing ever writes to it again.
                #
                # `-B` (force-create-or-reset) is NOT an option here even with
                # `--force`: git refuses to reset a branch that is checked out
                # in another worktree, unconditionally — `--force` overrides
                # only the "already checked out" checkout guard, not the
                # branch-reset guard. So: reuse the existing local branch as-is
                # when it's already there (the common case — the original task
                # created it), only falling back to creating it from
                # `origin/<branch>` the one time it isn't (e.g. a fresh clone
                # of the registry that never locally saw this branch before).
                _git(["-C", str(base), "fetch", "origin", branch])
                if _branch_exists_locally(base, branch):
                    _git(
                        ["-C", str(base), "worktree", "add", "--force", str(worktree), branch]
                    )
                    # The shared local ref can be behind `origin/<branch>`:
                    # GithubConflictsCheck's update_branch call advances
                    # the branch server-side via the GitHub API, touching no
                    # local git state at all. Reconcile the *new* worktree with
                    # origin's actual tip before anything (merge/agent/commit)
                    # runs against it. Safe: this worktree was just created (no
                    # local-only work in it yet), and unlike the porcelain
                    # branch-reset commands run from `base`, this reset targets
                    # the branch as checked out *in this worktree* — the
                    # "checked out elsewhere" guard doesn't apply to it.
                    _git(["-C", str(worktree), "reset", "--hard", f"origin/{branch}"])
                else:
                    _git(
                        [
                            "-C", str(base), "worktree", "add",
                            str(worktree), "-b", branch, f"origin/{branch}",
                        ]
                    )
            else:
                _git(
                    ["-C", str(base), "worktree", "add", str(worktree), "-b", branch]
                )
        elif override:
            # Reset-on-reattach for a resolver task's shared branch. Unlike a
            # `harness/<task.id>` branch — which only ever advances through this
            # worktree — a resolver's overridden branch can have moved forward
            # *server-side* (`GithubConflictsCheck`'s `update_branch` call) between
            # this task's attempts, touching no local ref. Resetting to local
            # `HEAD` here would leave the worktree stale and the resolver's
            # eventual push rejected as non-fast-forward, so reconcile with
            # `origin/<branch>`'s actual tip — the same reconciliation the
            # create path does (invariant 31), extended to the reattach path.
            # The reset targets the branch as checked out *in this worktree*, so
            # the "checked out elsewhere" guard (invariant 30) doesn't apply.
            #
            # But a plain reset-to-origin is only safe when there's nothing
            # local to lose. The `resolve` step commits its merge resolution
            # *locally* (pushing isn't its job — see `ports/workspace.py`);
            # `land` then reattaches this same worktree before pushing. If we
            # reset unconditionally here, that un-pushed commit is discarded
            # before `land` ever gets a chance to push it (#86). So this is a
            # three-way ancestry check, not a blind reset: behind/equal ->
            # reset (unchanged), ahead -> keep local HEAD untouched, diverged
            # -> raise rather than silently discard either side.
            _reconcile_override_reattach(base, worktree, branch)
        else:
            # Reset-on-reattach: both a backward edge and a restart after a
            # crash must start from a clean slate. `reset --hard` discards
            # uncommitted changes, `clean -fd` removes untracked files and
            # directories. Without `-x` — ignored files (e.g. `.artifacts/` if
            # they were gitignored) stay.
            _git(["-C", str(worktree), "reset", "--hard", "HEAD"])
            _git(["-C", str(worktree), "clean", "-fd"])
        return GitWorkspaceHandle(worktree, branch)

    def _attach_repo_less(self, task: Task) -> GitWorkspaceHandle:
        """A standalone scratch repo for a task with no registered repository
        (`task.repository is None` — e.g. `heal`, ADR-0018): the persona
        reasons over a failure report, not a diff, so there is no base repo to
        derive a worktree from. Mirrors the ordinary create/reattach shape
        (`attach`, above) against its own root commit instead of a shared
        repo's HEAD.

        An empty repo has no HEAD commit yet — reset-on-reattach needs one to
        reset *to* — so the create path commits an empty root commit right
        away, making this idempotent on its own: a second `attach()` call
        (crash-and-retry before anything was ever written) finds a repo that
        already satisfies "has a HEAD", same as the registered-repo path
        always did.

        Implicit contract: `push()` is never called for a repo-less task — its
        workflow must end before any step that pushes (there is no `origin`
        here to push to).
        """
        branch = f"harness/{task.id}"
        worktree = self._worktrees_root / task.id
        if not worktree.exists():
            worktree.mkdir(parents=True, exist_ok=True)
            _git(["init", "-q", "--initial-branch", branch, str(worktree)])
            _git(
                ["-C", str(worktree), "commit", "--allow-empty", "-q", "-m", "root"],
                env_extra=_IDENTITY,
            )
        else:
            # Same reset-on-reattach primitive as the ordinary create path,
            # just against this repo's own root commit instead of a shared
            # repo's HEAD.
            _git(["-C", str(worktree), "reset", "--hard", "HEAD"])
            _git(["-C", str(worktree), "clean", "-fd"])
        return GitWorkspaceHandle(worktree, branch)
