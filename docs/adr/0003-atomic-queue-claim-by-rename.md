# ADR-0003: Atomic queue claim() by rename

Status: Accepted

## Context

A queue implemented as a directory of JSON files needs a way to hand a task to
exactly one worker at a time, and to recover cleanly if the process crashes
mid-step — without a database, a lock server, or any coordination beyond the
filesystem itself. The two failure modes to rule out are two workers claiming
the same task, and a claimed task silently vanishing if the process dies before
finishing it.

## Decision

`FilesystemTaskQueue.claim()` (`drivers/fs_queue.py`) is a single `os.replace`
of the task's JSON file from `<queue>/` into `<queue>/.processing/`. A rename
within the same filesystem is atomic: either it succeeds and the caller now
uniquely owns the file, or it raises `FileNotFoundError`/`IsADirectoryError`
because someone else already moved it — in which case `claim()` returns `None`
and the caller simply takes the next task. There is no separate lock file, no
lease timestamp, no heartbeat: the rename itself *is* the lease.

Recovery follows directly from this: each queue owns its own `.processing/`,
so after a crash, `Harness.run()`'s `recover()` step only needs to look inside
every queue's `.processing/` and move what it finds back to the queue root —
it never needs to record, anywhere, which queue a claimed task came from.

## Consequences

- Losing the race for `claim()` is not an error condition to handle specially
  — it is the normal, expected outcome of two workers reaching for the same
  task, and the codebase treats it as such (see the "Gotchas" note in
  `CLAUDE.md`).
- `recover()` must run before `hydrate()` in `Harness.run()` (invariant #6) —
  reversing the order would let the projection hydrate from a queue that still
  has tasks stuck in `.processing/` from a previous crash, silently losing them
  from the board until the next restart.
- The approach only works because each queue directory is exclusively owned by
  one `FilesystemTaskQueue` instance; a queue backend that spans multiple
  processes without a shared filesystem would need a different claim mechanism
  entirely (a real lease with a timeout), which is exactly the kind of change
  that would stay isolated to a new driver behind the same `TaskQueue` port
  (ADR-0001).
