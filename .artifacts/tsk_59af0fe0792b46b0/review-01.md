# Review — delete action on the task detail view

Reviewed the diff (`git diff HEAD~4`) against `plan-01.md`, `design-01.md`,
and `architecture-01.md`, and re-ran the suite.

## Conformance to spec/plan/design

Every functional requirement (FR-1 through FR-6) is implemented exactly as
specified, with no deviation:

- `TaskQueue.discard` added to the port and both drivers (`fs_queue.py`
  unlinks `.processing/<id>.json` with `missing_ok=True`; `memory.py` pops
  from `self._claimed`).
- `TaskControl.delete(task_id) -> bool` added to the ABC; implemented by
  `TaskControlService`, `_NullTaskControl`, and `FakeTaskControl`.
- `TaskControlService.delete` searches `inbox → step queues → done → failed`,
  claims, discards, emits a shape-distinct `"deleted"` event carrying only
  `task_id` — matches the design's code sample verbatim.
- `BoardProjection.remove` drops the task from `_tasks`/`_columns` and only
  bumps the revision when something was actually present.
- `ProjectionSink.emit` branches on `name == "deleted"` ahead of the existing
  `task`/`queue` shape check.
- `POST /tasks/{task_id}/delete` returns 404 on `False`, empty 200 on `True`.
- `_task.html` has an unconditional `hx-confirm`-guarded Delete button next
  to the conditional Restart button; `board.html`'s `afterSwap` closes
  `#detail` on an empty swap instead of reopening it.
- `app.py` wiring passes the already-built `step_queues`/`done` locals into
  the single `TaskControlService(...)` construction site (confirmed via grep
  — no other call site exists outside tests).

## Acceptance criteria — verified against the actual code, not just the docs

- **Delete action + confirmation dialog**: present (`_task.html`), guarded by
  native `hx-confirm`, exactly as required.
- **Confirm deletes / cancel leaves untouched**: htmx never issues a request
  on cancel, so there is no server-side effect to test directly; this is
  correctly treated as a browser-only guarantee (same limitation restart's
  own tests already accept) and is not a gap introduced by this change.
- **Write-side port, no driver import in `api/`**: confirmed — `api/routes.py`
  and `api/app.py` only reference `TaskControl`; `tests/test_architecture.py`
  (14 tests, unmodified) still passes, including the tests that specifically
  assert `api/` imports no driver and only `TaskControlService`/`api/`/wiring
  touch `TaskControl`.
- **Deletion removes the task from board and disk; `GET /api/tasks/{id}`
  404s afterward**: traced end-to-end —
  `TaskControlService.delete` → `queue.discard` (file unlink / dict pop) →
  `"deleted"` event → `ProjectionSink` → `BoardProjection.remove` →
  `BoardView.get` returns `None` → `/api/tasks/{task_id}` (routes.py:79-84)
  raises 404. Confirmed by `test_projection.py`, `test_projection_events.py`,
  and the fs/memory `discard` tests (file/dict entry gone, `recover()` finds
  nothing to resurrect).
- **Idempotent/safe against a task already moved or deleted**: `claim()`
  returning `None` (lost race) and an id absent from every queue both
  produce a clean `False` → 404, no exception. Covered by
  `test_delete_loses_claim_race_returns_false`,
  `test_delete_claimed_task_is_invisible_and_returns_false`,
  `test_discard_of_already_removed_task_is_a_no_op` (double-discard doesn't
  raise).
- **Tests for port + driver + confirmation flow**: present across
  `test_fs_queue.py`, `test_memory_drivers.py`, `test_task_control.py`,
  `test_projection.py`, `test_projection_events.py`, and `test_api_html.py`
  (button presence + `hx-confirm` attribute, success path, 404 path, default
  null-control path, and that a delete call never touches
  `control.restarted`).

## Correctness

No logic errors found. Race handling mirrors `restart`'s already-reviewed
pattern (claim-or-fail, no partial state). `CompositeEventSink` already
isolates sink failures, so a bug in `ProjectionSink`'s new branch can't take
down `SourceReflectorSink` or vice versa — no new code needed there, and none
was added, consistent with the architecture doc's risk table.

Ran the full suite and the architecture guard directly:

```
.venv/bin/pytest -q            → 493 passed, 1 skipped (pre-existing opt-in smoke)
.venv/bin/pytest -q tests/test_architecture.py -v   → 14 passed
```

## Scope and disclosed limitations

The three deliberate limitations (hard delete with no undo, in-flight/claimed
tasks excluded, worktree/artifacts on disk untouched, GitHub source label not
reflected) are all explicitly out of scope per the task's own acceptance
criteria and are documented in `development-01.md`. None of them contradicts
an acceptance criterion — the task scoped this to "the task detail view" /
"the harness['s queues/board]", not disk reclamation or external-system sync.

## Verdict

No functional requirement is unmet, no architecture invariant is violated,
no required test is missing, and no correctness bug was found. This is a
clean, tightly-scoped implementation that matches its own plan/design almost
line-for-line.

```json
{"outcome": "done", "summary": "Verified the delete-action implementation against plan-01/design-01/architecture-01 by re-reading every changed file and diffing HEAD~4: TaskQueue.discard, TaskControl.delete/TaskControlService, BoardProjection.remove via a name-based 'deleted' event, POST /tasks/{id}/delete, and the hx-confirm Delete button all match the design exactly, with a single TaskControlService construction site and no driver import into api/. Ran the full suite (493 passed, 1 pre-existing skip) and test_architecture.py (14 passed) directly rather than trusting the prior artifact's numbers. Traced the acceptance criteria end-to-end in the code: 404 after delete via BoardView.get, idempotent double-delete/lost-race handling, and test coverage across drivers, core, projection, and the HTML API. No functional gaps, invariant violations, or correctness bugs found."}
```
