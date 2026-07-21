# ADR-0011: TaskControl as the write-side counterpart of BoardView

Status: Accepted

## Context

`BoardView` gives the operator board a read-only window onto every task; there
was, until this decision, no way for a human looking at that board to *act* on
a task — most pressingly, to retry one that ended up in `failed/` without
resubmitting it by hand and losing its history. Any such action needs the same
UI-must-not-know-the-driver discipline that reads already have (ADR-0005): the
board should be able to ask for a restart without knowing that a "task" is a
JSON file or that "restarting" means an atomic claim plus a queue transfer.

## Decision

`ports/control.py`'s `TaskControl` is the write-side port mirroring the
read-side `BoardView`. Its first (and, so far, only) verb is `restart(task_id)
-> bool`. `task_control.py`'s `TaskControlService` is the core implementation:
it looks the task up in `failed/`, claims it (the same atomic rename as any
other claim, ADR-0003 — losing the race just returns `False`), clears its
`status`/`last_outcome`/`lock_id`, appends a history entry, and transfers it
back to the inbox. Restart is explicitly a **reset, not a routing decision**: it
does not decide where the task goes next — clearing `status` to `None` puts it
back in exactly the shape a fresh `harness submit` produces, and the dispatcher
makes the actual routing call afterward, same as it would for any other task
(invariant #3 still holds; `TaskControlService` never writes a step name).

`dispatcher.py`/`consumer.py` don't import `ports.control` at all (invariant
#23, guarded by `tests/test_architecture.py::test_orchestration_does_not_
import_control`) — only `task_control.py` itself, `api/`, and the wiring in
`app.py` ever reach for it.

## Consequences

- A restarted task's history shows the restart as an explicit operator action
  (`actor="operator"`, `from_step=FAILED`, `reason="restarted by operator"`),
  not as if the original failure never happened — the audit trail stays
  intact.
- Because restart re-inboxes rather than re-routes, the dispatcher's routing
  logic never needs a special case for "a task coming back from a restart" —
  it looks exactly like any other task sitting in the inbox with `status=None`.
- A future second write-side verb (retry-in-place, cancel, reassign) has an
  obvious home: a new method on `TaskControl`, implemented in
  `TaskControlService`, wired the same way — not a new ad hoc endpoint in
  `api/routes.py` that reaches around the port.
