# ADR-0008: RepositoryRegistry — name to path indirection

Status: Accepted

## Context

A task needs to say which repository it works on, but the actual path to that
repository's clone (`/Users/you/code/my-app`, or wherever a given machine keeps
it) is specific to the machine running the harness, not to the task itself. If
a task stored an absolute path, the same task definition would break the moment
it moved to a different machine or a different checkout location, and the path
would leak machine-specific layout into something meant to be portable
(submitted via `harness submit`, or ingested from a GitHub issue).

## Decision

`task.repository` is a name (invariant #15), never a path. `ports/repos.py`'s
`RepositoryRegistry.resolve(name) -> Path` is the only thing that turns that
name into a location on disk, backed by `drivers/fs_repos.py` reading
`repos.json` — a machine-specific, uncommitted config file that `harness init`
seeds empty. The harness itself derives the task's actual worktree path as
`<worktrees_root>/<task_id>`, independent of where the registered clone lives;
`RepositoryRegistry` is only consulted to find the *source* clone a fresh
worktree branches from. Reattaching a worktree that has drifted dirty resets it
to `HEAD` (reset-on-reattach) rather than trying to reconcile arbitrary local
changes.

## Consequences

- The same task (or the same GitHub-issue-derived task) can be replayed on a
  different machine simply by giving that machine its own `repos.json` entry
  for the same name — nothing about the task itself needs to change.
- `RepositoryRegistry` is unknown to `dispatcher.py`/`consumer.py` (invariant
  #17, guarded by `test_architecture.py`) — only the behavior and the wiring
  in `app.py`/`cli.py` ever resolve a name to a path.
- A repository with a typo'd or missing name in `repos.json` fails at the one
  point that actually needs the path (attaching the workspace, or opening a PR
  through `GithubForge`, which also falls back to the registry) — not silently,
  and not by leaking a path onto the task where it could go stale.
