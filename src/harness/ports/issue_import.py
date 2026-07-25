"""The `IssueImport` port: the write side of manually queueing a GitHub issue
as a task, from the Ahanas board (`api/`) rather than the automatic
`github-issues` ingestion action.

Unlike `TaskControl`'s pure-core `TaskControlService`, `IssueImport`'s only
implementation is inherently GitHub-specific — there is no driver-agnostic
core to extract. It also needs `Harness.build()`'s own live queues (to put the
fresh task and to scan for a duplicate), which don't exist until `build()`
runs. So it follows `FailedTasksCheck`'s shape (ADR-0018), not `TaskControl`'s
or the admin ports' (invariant #33): `build()` constructs the concrete
`IssueImport` from an `IssueImportFactory` that `cli.py` supplies, closing
over the GitHub-specific pieces (`GithubClient`, `RepositoryRegistry`) that
are `cli.py`'s to build, never `app.py`'s.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class IssueImportResult:
    """The outcome of importing one ref, echoed back to the operator."""

    ref: str
    ok: bool
    task_id: str | None = None
    already_queued: bool = False
    error: str | None = None


class IssueImport(ABC):
    """Turn one operator-supplied GitHub issue ref into one outcome.

    A pure `str -> IssueImportResult` contract — no batching, no GitHub/queue
    knowledge leaks through the port. `add()` never raises: every failure mode
    (bad ref syntax, unregistered repo, issue not found, network/auth error)
    comes back as `ok=False` with a human-readable `error`.
    """

    @abstractmethod
    def add(self, ref: str) -> IssueImportResult: ...


class NullIssueImport(IssueImport):
    """The "not configured" fallback — shared by `build()`'s own default (no
    `GITHUB_TOKEN`) and `api/app.py::create_app`'s default parameter (callers,
    chiefly tests, that construct the app without a full harness). One class,
    not two, so the "not configured" message never drifts between the two call
    sites. A concrete, dependency-free port implementation next to its ABC —
    the same shape `Trigger(TaskSource)` already has in `ports/source.py`.
    """

    def add(self, ref: str) -> IssueImportResult:
        return IssueImportResult(
            ref=ref,
            ok=False,
            error="GitHub is not configured on this harness (no GITHUB_TOKEN)",
        )


IssueImportFactory = Callable[..., IssueImport]
"""Built by `cli.py`, invoked once by `app.build()` once its live queues exist
— the same "factory closes over external dependencies, `build()` supplies the
harness's own live state" shape as `extra_checks`/`finishers`. Invoked as:

    factory(inbox=inbox, step_queues=step_queues, done=done, failed=failed,
            healed=healed, archived=archived, events=events, clock=clock)

with `inbox`/`step_queues`/`done`/`failed`/`healed`/`archived` each a
`TaskQueue`, `events` an `EventSink` and `clock` a `Clock` — spelled out here
only as documentation; the factory itself is an opaque `Callable` to every
caller so this port stays driver-free (mirrors `CheckFactory` in
`ports/triggers.py`, which is likewise just a `Callable` alias).
"""
