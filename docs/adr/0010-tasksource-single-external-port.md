# ADR-0010: TaskSource as the single external-world port

Status: Accepted

## Context

Tasks no longer only arrive by hand through `harness submit` — they can flow in
from the outside world (a GitHub issue labeled for pickup) and their state needs
to be projected back outward as they progress (a label change on that issue) or
finish (success/failure, with a PR link). Left unconstrained, this could mean
GitHub-specific knowledge — issue numbers, label names, the GitHub API — leaking
into the dispatcher or consumer, exactly the kind of surrounding-vs-driver
boundary ADR-0001 exists to prevent.

## Decision

The entire outside world of task control sits behind one port, `ports/
source.py`'s `TaskSource`, with exactly three verbs: `poll()` (bring in new
tasks), `report_progress(task, progress)` (project in-progress state outward),
and `finish(task, result)` (project terminal state outward). `source_poller.py`'s
`SourcePoller` is the core-side consumer of `poll()` — a second producer of the
inbox queue alongside `harness submit` — and it knows only ports, never GitHub
(`tests/test_architecture.py::test_source_poller_imports_only_ports_and_
models`). `drivers/source_reflector.py`'s `SourceReflectorSink` is the
event-stream-to-outward-projection bridge, mapping harness events
(`dispatched`/`finished`/`failed`) to `Progress`/`FinishResult` without any
GitHub knowledge either — the adapter (`drivers/github_source.py`'s
`GithubTaskSource`) is the only place that turns those into a label change.

A task's origin travels with the task itself, in `task.data.source` (`{kind,
repo, issue, url}`, invariant #19) — neither the router nor the dispatcher ever
reads it; only the reflector and the adapter do, to route projection and to
build the PR body's `Closes #n` line. `dispatcher.py`/`consumer.py` don't import
`ports.source` at all (invariant #20, guarded by
`test_orchestration_does_not_import_source_port`).

Deduplication is keyed by `Task.dedup_key`, persisted with the task rather than
kept only in the adapter's transient memory — `GithubTaskSource.poll()` swaps
the issue's label before returning the task (the GitHub twin of `fs_queue`'s
atomic rename-claim, ADR-0003) and additionally keeps an in-process `_claimed`
ledger, because `list_issues` reads with read-after-write lag and a fast poll
could otherwise see the same issue twice before its label swap is visible.
`SourcePoller` layers a second, restart-surviving guard on top: it seeds a
`_seen` set of dedup keys from every task already on disk at startup, so label
drift or a lagging read can never turn one GitHub issue into two tasks across a
restart.

## Consequences

- A foreign task (a different `kind`, or no `source` at all — e.g. one created
  by `harness submit`) is silently ignored by the GitHub adapter's `_mine()`
  guard, so manually submitted tasks never trigger a spurious label call.
- A source failure (GitHub down, rate-limited) cannot stop the orchestration
  loop: `SourcePoller.tick()` catches the exception from `poll()` and the tick
  just returns `False` — the next tick tries again.
- Adding a second source kind (a drop-folder, Jira) is a new driver behind the
  same `TaskSource` port plus a new entry in whatever composes the sources list
  in `app.py`/`cli.py` — no change to `SourcePoller`, `SourceReflectorSink`, or
  the routing core.
