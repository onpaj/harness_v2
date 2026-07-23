# ADR-0013: IssueReconciler retires tasks whose source issue was closed

Status: Accepted

## Context

A task on the board exists because of something in the outside world — for a
GitHub-sourced task, an open issue (`task.data.source`, ADR-0010). That reason
can vanish out from under a live task: a human closes the issue, or the task's
PR merges and GitHub auto-closes the issue behind it. When it does, the task
keeps showing on the dashboard even though there is nothing left to do — the
operator sees tasks that are "actually closed and even merged".

`MergeReconciler` (ADR nowhere — invariant 32) and `PrWatcher` already retire a
`done` task once its *PR* resolves, but that only covers the `done/` column and
only when a `data.pr` reference exists. An issue can close while its task is
still in `todo`, mid-workflow, or in `failed/` — and it can close without the
task ever having landed a PR at all. Nothing swept those.

The natural place to notice is the same periodic GitHub housekeeping the merge
reconciler already does: while we poll GitHub, also check the issues behind the
tasks we already hold.

## Decision

A new read-only source-side port `ports/issue_state.py` — `IssueChecker.is_open(
task) -> bool | None` — mirrors `MergeChecker` exactly: `True` while the issue is
open, `False` once it is closed or deleted, `None` when the task carries no issue
this checker resolves (a `harness submit` task, or a foreign `kind`), and it
*raises* on a transient failure so the caller retries rather than archiving a
merely-unreachable task. `TaskSource`'s three verbs stay fixed (invariant 18):
this is a fourth, separate capability, not a fourth verb — the same split
`MergeChecker` keeps from `Forge`.

A new core loop `issue_reconciler.py` — `IssueReconciler` — is `PrWatcher`'s
structural sibling: it knows only ports/models/ids, sweeps every live queue it is
given (`inbox`, the step queues, `done`, `failed`) all-per-tick, and for each
task whose `is_open` comes back `False` it claims the task out of its queue and
`transfer`s it to `archived/`. "Removed from the dashboard" is therefore the
exact `archived/` mechanism `PrWatcher`/`MergeReconciler` already use (invariant
24): off every rendered column, still gettable by id, crash-safe via the queue's
own `claim`/`recover`. No new board concept is introduced.

`drivers/github_issue_checker.py` — `GithubIssueChecker` — reads `repo`/`issue`
off `task.data.source` at check time (one checker serves every repo the token can
reach, unlike the per-repo `GithubTaskSource`) and maps a 404 to "gone"
(`get_issue_state` returns `None`), which `is_open` treats as not-open. Wiring
lives in `app.py`/`cli.py`; the loop runs on the existing `reconcile_interval`
and is gated on `GITHUB_TOKEN`, exactly like the `MergeReconciler`. Invariant 34
guards that `dispatcher.py`/`consumer.py` never import the port.

## Consequences

- A task whose GitHub issue is closed or deleted drops off the board on the next
  reconcile sweep, from whatever column it was in — the operator's stale-task
  complaint is addressed without a manual cleanup command.
- The reconciler and `MergeReconciler` both claim from `done/`. That is safe: a
  claim is an atomic rename, so the loser of the race simply skips the task, and
  the two signals (PR merged, issue closed) are complementary, not conflicting.
- Archiving a queued-but-not-yet-worked task means the harness stops working an
  issue that was closed — deliberate: if the reason for the work is gone, the
  work should stop, not run to a PR nobody asked for.
- A `harness submit` task (no `data.source`) and any non-GitHub source are
  `None` from the checker and never touched, so the cleanup is invisible to
  workflows that don't come from GitHub — the same "foreign task passes through
  untouched" property the outward projection already has (ADR-0010).
- No token, no checker, no loop: an offline or `--forge fake` run pays nothing
  for this path and behaves exactly as before.
