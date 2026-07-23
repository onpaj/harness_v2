# Development — delete action on the task detail view

Implements `plan-01.md` / `design-01.md` exactly as endorsed by
`architecture-01.md`: hard delete via a new `TaskQueue.discard` +
`TaskControl.delete`, a shape-distinct `"deleted"` event consumed by
`BoardProjection.remove`, a `POST /tasks/{id}/delete` HTML endpoint, and an
unconditional `hx-confirm`-guarded Delete button next to Restart. No new
port, no new module — every seam extended already existed.

## Files changed

**Ports**
- `src/harness/ports/queue.py` — `TaskQueue.discard(task) -> None`, abstract,
  the terminal counterpart to `transfer`.
- `src/harness/ports/control.py` — `TaskControl.delete(task_id) -> bool`,
  abstract, second method on the ABC.

**Drivers**
- `src/harness/drivers/fs_queue.py` — `FilesystemTaskQueue.discard`: unlinks
  `.processing/<id>.json` with `missing_ok=True`.
- `src/harness/drivers/memory.py` — `MemoryTaskQueue.discard`: pops from
  `self._claimed`.

**Core**
- `src/harness/task_control.py` — `TaskControlService` gains `step_queues`
  and `done` constructor deps; `delete(task_id)` searches
  `inbox → step queues → done → failed`, claims the first unclaimed match,
  discards it, emits `"deleted"` with only `task_id` (no `task`/`queue` —
  there is no destination column), returns `True`/`False`. A claimed
  (in-flight) task is invisible to `list()` so it's out of scope by
  construction, matching the plan's deliberate exclusion.
- `src/harness/projection.py` — `BoardProjection.remove(task_id)`: drops the
  task from `_tasks`/`_columns`, bumps the revision only if something was
  actually present (no spurious SSE tick on an unknown id).
- `src/harness/drivers/projection_events.py` — `ProjectionSink.emit` branches
  on `name == "deleted"` before the existing `task`/`queue` shape check and
  calls `projection.remove(task_id)`.

**API**
- `src/harness/api/routes.py` — `POST /tasks/{task_id}/delete`: `404` when
  `control.delete` returns `False`, else an empty `200` body (no fragment
  left to render). `api/` still imports only `BoardView`/`ArtifactView`/
  `StageOutputView`/`TaskControl`/`Clock` — no driver.
- `src/harness/api/app.py` — `_NullTaskControl.delete` returns `False`.

**Wiring**
- `src/harness/app.py` — `TaskControlService(...)` call site passes the
  already-built `step_queues`/`done` locals.

**Templates**
- `src/harness/api/templates/_task.html` — unconditional Delete button:
  `hx-post=/tasks/{id}/delete hx-confirm="Delete task {id}? This cannot be
  undone."`, next to the existing conditional Restart button.
- `src/harness/api/templates/board.html` — `afterSwap` listener now closes
  `#detail` (instead of calling `showModal()`) when the swapped-in content is
  empty, so a successful delete closes the dialog rather than showing a
  blank modal; the pre-existing `close` listener still clears `innerHTML`
  either way.

**Test doubles**
- `tests/fakes.py` — `FakeTaskControl` gets an independent
  `delete_result`/`deleted` list alongside `result`/`restarted`.

## Tests added

- `tests/test_fs_queue.py` — `discard` removes a claimed task permanently and
  is idempotent on a second call.
- `tests/test_memory_drivers.py` — same two cases for `MemoryTaskQueue`.
- `tests/test_task_control.py` — `delete` found independently in inbox, a
  step queue, `done`, `failed`; emits `"deleted"` with exactly `{"task_id":
  ...}`; unknown id returns `False` untouched; a claimed task is invisible
  and returns `False`; losing the claim race mid-`delete` returns `False`.
  `build()` now takes `step_queues`/`done`/`inbox` so each queue kind is
  independently testable.
- `tests/test_projection.py` — `remove` drops a task from the read model;
  removing an unknown id is a no-op that doesn't bump the revision.
- `tests/test_projection_events.py` — a `"deleted"` event removes the task
  from the board; one missing `task_id` is ignored (no crash, no bump).
- `tests/test_api_html.py` — Delete button present for both a working and a
  failed task (unconditional) and carries `hx-confirm`; success → `200` +
  empty body + `control.deleted == [id]`; `False` from control → `404`;
  default `_NullTaskControl` → `404`; a delete call never touches
  `control.restarted`.

## Verification

```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Result: **493 passed, 1 skipped** (the skip is the pre-existing opt-in
`HARNESS_SMOKE_CLAUDE=1` real-`claude` smoke test, unaffected by this
change). `tests/test_architecture.py` (14 tests) passes unmodified,
confirming no driver leaked into `api/`, `dispatcher.py`, or `consumer.py`,
and that `TaskControl` is still touched only by `TaskControlService`/`api/`/
wiring.

Manual verification path (not run headlessly, but traceable through the
tests above): open a task's detail view → the Delete button is always
present → clicking it triggers the browser's native `confirm()` via
`hx-confirm` → cancelling issues no request (nothing to assert server-side,
by construction) → confirming `POST`s `/tasks/{id}/delete`, which returns an
empty `200`, closes the dialog via the new `afterSwap` branch, and a
subsequent `GET /api/tasks/{id}` 404s per `BoardView.get` returning `None`.

## Known, deliberate limitations (per plan/architecture)

- **Hard delete, not archival.** `discard` unlinks the file / drops the dict
  entry; there is no undo path (explicitly out of scope per the task).
- **In-flight (claimed) tasks are not deletable.** `list()` only sees
  unclaimed tasks, so a task a consumer is actively working returns `False`/
  `404`; the operator can retry once the step finishes.
- **A deleted task's worktree/artifacts on disk are untouched.** `discard`
  only removes the queue's JSON file, symmetric with `transfer`.
- **A GitHub-sourced task's issue label is not updated on delete.**
  `SourceReflectorSink` reads `fields["task"]`, which the `"deleted"` event
  deliberately omits (no destination to reflect), so it silently no-ops —
  the harness forgets the task but GitHub keeps its last label. Flagged in
  the architecture assessment as an accepted gap, not addressed here since
  the acceptance criteria are scoped to the board UI.

```json
{"outcome": "done", "summary": "Implemented the delete action end-to-end per plan-01/design-01/architecture-01: TaskQueue.discard (fs + memory drivers), TaskControl.delete on TaskControlService (searches inbox/step-queues/done/failed, claims, discards, emits a shape-distinct 'deleted' event), BoardProjection.remove wired through ProjectionSink's name-based branch, POST /tasks/{id}/delete (404 on not-found, empty 200 on success), an unconditional hx-confirm-guarded Delete button in _task.html, and board.html's afterSwap closing #detail on an empty swap. Added unit tests for discard (both drivers), TaskControlService.delete (all queue kinds, unknown id, claimed/in-flight, lost race), BoardProjection.remove, ProjectionSink's deleted-event handling, and the HTML endpoint (button presence, success, 404, default null control). Full suite: 493 passed, 1 pre-existing skip; test_architecture.py's 14 tests pass unmodified."}
```
