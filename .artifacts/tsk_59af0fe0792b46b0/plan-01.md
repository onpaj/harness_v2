# Plan — Delete action on the task detail view, guarded by confirmation

## Summary

Add a "Delete" action to the task detail dialog, next to the existing
"Restart" one. Activating it asks the operator to confirm (via htmx's native
`hx-confirm`, consistent with this codebase's no-JS-framework, server-rendered
approach); on confirmation the task is hard-removed from disk and drops off
the board immediately. The action is exposed through the same `TaskControl`
write-side port that `restart` already uses, so `api/` stays free of any
driver import.

## Context

The board only ever *moves* a task (`todo → step → done/failed`, or
`failed → todo` via restart). There is no way to make a task disappear
entirely. Obsolete, duplicate, or mistaken tasks pile up forever with no
cleanup path other than manually editing the harness's data directory.
Because this is the first genuinely destructive write the UI performs, it
needs an explicit confirmation step and a design decision about whether
"delete" means hard removal or an archival move — this plan chooses hard
removal, since that is what "delete from harness" means to an operator, and
records the reasoning so the PR can call it out per the task's request.

## Functional requirements

**FR-1 — `TaskControl.delete(task_id)` port method.**
Extend the `TaskControl` port (`ports/control.py`) with `delete(task_id: str)
-> bool`, mirroring `restart`'s shape: `True` when a task by that id was
found and removed, `False` when it wasn't found anywhere (unknown id, or lost
a race to another actor).
- Acceptance: `TaskControl` is an ABC with two abstract methods after this
  change (`restart`, `delete`); every existing implementer (`TaskControlService`,
  `_NullTaskControl` in `api/app.py`, `FakeTaskControl` in `tests/fakes.py`)
  implements both or the module fails to import.

**FR-2 — `TaskControlService.delete` searches every queue, not just `failed/`.**
Unlike `restart` (which only ever looks in `failed/`), a task the operator
wants gone can be sitting anywhere: `todo` (inbox), any step queue, `done`, or
`failed`. `TaskControlService` gains constructor deps for `step_queues` and
`done` (it already has `inbox`/`failed`), searches `[inbox, *step_queues.values(),
done, failed]` in that order, and removes the task from the first queue where
an *unclaimed* copy is found.
- Acceptance: a task unit-tested as present in `todo`, a step queue, `done`,
  or `failed` is each, independently, deleted by `delete(task_id)`.
- Acceptance: a task currently claimed (mid-flight, in a queue's
  `.processing/`) is **not** found by `list()` and so is not deleted — see
  Open Questions for why this is the deliberate scope for this change.
- Acceptance: an unknown id, or losing the claim race to another actor
  (dispatcher/consumer), returns `False` and leaves all queues untouched.

**FR-3 — `TaskQueue.discard` — hard removal, added to the port.**
`TaskQueue` (`ports/queue.py`) gains `discard(task: Task) -> None`: given a
task already claimed (i.e. currently sitting in that queue's `.processing/`),
permanently removes it — the terminal counterpart to `transfer`, which moves
a claimed task somewhere else. Implemented in both drivers:
- `FilesystemTaskQueue.discard`: deletes `<processing>/<id>.json` outright
  (`missing_ok=True`, matching the idempotent tone of `recover()`/`_quarantine_file`).
- `MemoryTaskQueue.discard`: pops the task out of `self._claimed`.
- Acceptance: after `queue.claim(task, lock_id)` then `queue.discard(claimed)`,
  the task is in neither the ready set nor `.processing/`; a subsequent
  `queue.recover()` does not resurrect it.

**FR-4 — Deletion is reflected in the board projection.**
`TaskControlService.delete` emits a `"deleted"` event carrying `task_id`
(no `task`/`queue` fields — there is no destination column, so this is
deliberately not shaped like a movement event per invariant #7, which governs
task *movement*). `ProjectionSink` (`drivers/projection_events.py`) special-cases
this event name and calls a new `BoardProjection.remove(task_id)`, which drops
the task from both `self._tasks` and `self._columns` and bumps the revision
(reusing the existing SSE fan-out).
- Acceptance: after a successful delete, `BoardView.get(task_id)` returns
  `None` and the task is absent from every column of `BoardView.snapshot()`.
- Acceptance: connected SSE clients receive a new revision so the board
  redraws without the deleted card.

**FR-5 — HTML endpoint: `POST /tasks/{task_id}/delete`.**
Add a route to `api/routes.py`'s HTML router (same shape as
`POST /tasks/{task_id}/restart`): calls `control.delete(task_id)`; on `True`
returns an empty `HTMLResponse` (200) that clears the `#detail` dialog's
content (there is nothing left to render — the task is gone); on `False`
raises `HTTPException(404)`. The read-only JSON API (`/api/tasks/{id}`)
is untouched — `test_no_endpoint_mutates` (DELETE on `/api/tasks/{id}` → 405)
continues to hold; deletion is only ever a POST on the HTML surface, exactly
like restart.
- Acceptance: mirrors the four existing restart tests in `test_api_html.py`
  (button present/absent, success path returns 200 and empties/refreshes the
  dialog, `False` from control → 404, default `_NullTaskControl` → 404).

**FR-6 — Delete button + confirmation dialog in the task detail template.**
`_task.html` gets a "Delete" button next to (not replacing) the conditional
"Restart" one, shown for every task regardless of status:
```html
<button hx-post="/tasks/{{ task.id }}/delete"
        hx-target="#detail" hx-swap="innerHTML"
        hx-confirm="Delete task {{ task.id }}? This cannot be undone.">Delete</button>
```
`hx-confirm` blocks the request behind the browser's native `confirm()` —
cancelling it means htmx never issues the POST, so the task is untouched.
This keeps the dialog server-rendered and dependency-free, consistent with
the rest of the board (no client-side framework, no bespoke modal component).
A small addition to `board.html`'s existing `htmx:afterSwap` listener closes
`#detail` (instead of calling `showModal()`) when the swapped-in fragment is
empty, so a successful delete closes the dialog rather than showing a blank
modal; the board itself updates from the SSE revision bump (FR-4).
- Acceptance: confirming deletes the task and closes the dialog; cancelling
  the browser prompt leaves the task, the dialog content, and all queues
  unchanged (no request is ever sent — verified indirectly by asserting
  `control.deleted` stays empty in a test that never confirms, i.e. never
  hits the endpoint).

## Non-functional requirements

- **Safety over convenience:** the whole feature is opt-in-per-click and
  requires a positive confirmation; there is no bulk/undo path (explicitly
  out of scope), so a mis-click has a guard but a confirmed click is final.
- **No new races beyond what `claim()`/`transfer()` already handle:** reusing
  the atomic `claim()` rename before `discard()` means deletion can never
  double-fire or clash with the dispatcher/a consumer claiming the same task
  first — losing that race surfaces as an honest `False`/404, not a crash.
- **No architecture-invariant regressions:** `api/` still imports only
  `BoardView`/`ArtifactView`/`StageOutputView`/`TaskControl`/`Clock` — no
  driver, no queue. `dispatcher.py`/`consumer.py` are untouched.
  `tests/test_architecture.py` must keep passing unmodified.

## Data model

No changes to `Task`, `Workflow`, or history shape. The only new pieces:
- A `"deleted"` event: `{task_id: str}` — distinct in shape from every other
  emitted event (`{task, queue, ...}`), which is exactly why `ProjectionSink`
  must special-case it by name rather than by the presence of `task`/`queue`.
- `BoardProjection.remove(task_id)` — a new mutator alongside `apply`, dropping
  a task from the in-memory read model rather than re-placing it in a column.

## Interfaces

- `TaskControl.delete(task_id: str) -> bool` (port, `ports/control.py`)
- `TaskQueue.discard(task: Task) -> None` (port, `ports/queue.py`)
- `POST /tasks/{task_id}/delete` → `200` + empty body (deleted) | `404` (not found)
- UI: "Delete" button in `_task.html`'s detail fragment, `hx-confirm`-guarded

## Dependencies and scope

**Depends on:** the existing `restart`/`TaskControl` plumbing (`task_control.py`,
`api/routes.py`, `api/app.py`, `tests/fakes.py`) as the direct template to extend;
the queue port/drivers (`ports/queue.py`, `drivers/fs_queue.py`, `drivers/memory.py`);
the projection/event-sink pair (`projection.py`, `drivers/projection_events.py`);
wiring in `app.py` (`TaskControlService` construction needs `step_queues`/`done` added).

**In scope:** single-task hard delete from any non-claimed queue, confirmation
UI, port + driver + wiring + tests.

**Out of scope (explicit):**
- Bulk delete.
- Undo / soft-delete / archival queue (see Open Questions — hard delete was
  chosen deliberately over an `archived/` queue).
- Deleting a task that is currently claimed/in-flight (being worked on by a
  consumer or the dispatcher) — see Open Questions.
- Any change to the JSON API's read-only surface.

## Rough plan

1. **Port:** add `TaskQueue.discard` to `ports/queue.py`; implement in
   `FilesystemTaskQueue` and `MemoryTaskQueue`.
2. **Port:** add `TaskControl.delete` to `ports/control.py`.
3. **Core:** extend `TaskControlService` to take `step_queues`/`done`, implement
   `delete` (search → claim → discard → emit `"deleted"`), update its
   construction site in `app.py`.
4. **Projection:** add `BoardProjection.remove`; special-case the `"deleted"`
   event in `ProjectionSink`.
5. **API:** add `POST /tasks/{task_id}/delete` to the HTML router; update
   `_NullTaskControl` and `tests/fakes.py`'s `FakeTaskControl` with `delete`.
6. **UI:** add the Delete button + `hx-confirm` to `_task.html`; adjust
   `board.html`'s `afterSwap` listener to close-on-empty.
7. **Tests:** unit tests for `TaskQueue.discard` (both drivers), `TaskControlService.delete`
   (found in each queue kind, not-found, claimed/lost-race), `BoardProjection.remove`,
   `ProjectionSink` handling of `"deleted"`, and the HTML endpoint (mirroring the
   six existing restart tests). Run `.venv/bin/pytest -q` and confirm
   `tests/test_architecture.py` still passes untouched.

## Open questions (defaults chosen)

- **Hard delete vs. archival queue** — chosen: hard delete (file removal via
  `discard`), because "delete from harness" is an explicit, operator-driven
  destructive action, distinct from the automatic `done`/`failed` terminal
  queues. An `archived/` queue would be a reasonable alternative for a future
  "soft delete/undo" feature, but nothing in the task's acceptance criteria
  asks for recovery, and keeping it out avoids a queue that silently grows
  forever with no view onto it.
- **Deleting a claimed/in-flight task** — chosen: not supported in this pass.
  `list()` only sees unclaimed tasks, so `delete` naturally can't find (and
  therefore can't race) a task a consumer is actively working on; the
  operator gets a clear `False`/404 and can retry once the step finishes.
  Reaching into `.processing/` to rip out a task while a subprocess may still
  be running against its worktree is a materially bigger and riskier change
  (killing an in-flight `claude -p`, or corrupting the artifact commit
  mid-write) and isn't implied by the task's acceptance criteria.
- **Confirmation mechanism** — chosen: htmx's native `hx-confirm` (a browser
  `confirm()`), not a custom modal, because the codebase has zero client-side
  JS beyond htmx/SSE glue and the existing `<dialog>` is already used for a
  different purpose (task detail). This is the smallest change that
  satisfies "an explicit confirmation dialog before it runs."
