# ADR-0009: Landing proposes, never touches main

Status: Accepted

## Context

The last step of a workflow needs to turn a task's committed work into
something a human can review and decide to merge. The harness could, in
principle, merge the branch itself once a workflow reaches `end` — but that
would mean an unattended process making the final call on what lands in a
shared branch, with no human review in between. The project's stance is that a
task is a transaction: the work happens in an isolated worktree, and only a
human's merge makes it real.

## Decision

Landing (`behaviors/landing.py`'s `LandingBehavior`) is a step like any other —
it can fail into `failed/` like any other step, and `end` stays a clean
terminal that nothing writes to except a successful landing (invariant #12).
It pushes the task's branch (`WorkspaceHandle.push()`) and calls `ports/
forge.py`'s `Forge.open_pull_request(...)` — it never merges, never pushes to
`main`, never deletes a branch. `drivers/github_forge.py`'s `GithubForge` is
the real implementation: it raises `ForgeError` on a missing `GITHUB_TOKEN`, a
non-GitHub origin, or any API failure, and the consumer turns that into
`failed/` — so a task that reports success always means a pull request that
genuinely exists (before this behavior, `land` could report success while only
writing to a local `prs.json`, which this replaced deliberately).

Opening a PR is idempotent: `GithubForge` matches an existing PR by
`head=owner:branch` and returns it instead of creating a duplicate, so a re-run
after a crash cannot open a second PR for the same branch.

## Consequences

- The harness never has write access to the concept of "merged" — the merge
  strategy (squash, rebase, review requirements) is entirely a human's call,
  enforced outside the harness by the repository's own branch protection.
- Landing needs a pushable remote (`origin`) to do its job — a registered repo
  with no remote cannot land, which is why the git e2e/smoke fixtures create a
  bare sibling repo and register it as `origin` rather than trying to land
  without one.
- A failed PR is not silently swallowed: the task lands in `failed/` with the
  forge's own explanation of what GitHub said (`_explain()` reads the API
  response body, not just the HTTP status line), so an operator sees the real
  reason rather than a generic error.
