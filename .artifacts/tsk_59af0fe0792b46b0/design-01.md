# Design — Delete action on the task detail view, guarded by confirmation

Builds on `plan-01.md`, which already settled the two open questions this
design assumes as given: **hard delete** (not an archival queue), and
**htmx's native `hx-confirm`** (not a bespoke modal) as the confirmation
mechanism. This document works out the concrete shapes: the UI, the
component boundaries/interfaces, and the data schemas that cross them.

## UX/UI

### Wireframe — task detail dialog (`#detail`)

The existing dialog (`_task.html`) gains one more button next to the
conditional "Restart". Unlike Restart, Delete is unconditional — a task can
be deleted from any status.

```
┌──────────────────────────────────────────────────────┐
│  tsk_9                                                │
│  [Delete]  [Restart]◄── only rendered if status=failed│
│  ────────────────────────────────────────────────────│
│  repository   acme                                    │
│  worktree     /work/worktrees/tsk_9                    │
│  workflow     default                                 │
│  status       failed                                  │
│  lastOutcome  request_changes                          │
│  lockId       —                                        │
│  created      2026-07-19T10:00:00Z                     │
│  data         { "title": "..." }                       │
│                                                        │
│  history                                              │
│  ┌──────┬────────┬──────┬──────┬─────────┬─────────┐  │
│  │ time │ actor  │ from │  to  │ outcome │ reason  │  │
│  └──────┴────────┴──────┴──────┴─────────┴─────────┘  │
│                                                        │
│  artifacts / live output ...                           │
└──────────────────────────────────────────────────────┘
```

Clicking **Delete** triggers the browser's native confirmation:

```
┌───────────────────────────────────────────┐
│  this page says                            │
│  Delete task tsk_9? This cannot be undone.  │
│                                             │
│                          [Cancel]  [OK]     │
└───────────────────────────────────────────┘
```

### Component hierarchy

```
board.html
 └─ #board (SSE: sse-connect=/api/events, hx-trigger=sse:board → GET /fragment/board)
 │   └─ _columns.html (card per task, click → GET /fragment/task/{id} → #detail)
 └─ #detail  <dialog>
     └─ _task.html
         ├─ [Delete]  hx-post=/tasks/{id}/delete  hx-confirm="..."  hx-target=#detail
         ├─ [Restart] hx-post=/tasks/{id}/restart (unchanged, status==failed only)
         ├─ detail table, history table, artifacts table (unchanged)
         └─ live-output SSE panel (unchanged)
```

No new template files and no new top-level element — Delete is a second
button inside the fragment that already exists, following the exact pattern
Restart established (`hx-post` + `hx-target=#detail` + `hx-swap=innerHTML`),
plus `hx-confirm` for the guard.

### Interaction sequence

```
Operator                 Browser/htmx              Server                    Board (SSE)
   │ click card              │                         │                         │
   │─────────────────────────▶ GET /fragment/task/{id} │                         │
   │                         │────────────────────────▶│                         │
   │                         │◀──── _task.html (with Delete button) ─────────────│
   │  <dialog> shown         │                         │                         │
   │ click Delete            │                         │                         │
   │─────────────────────────▶ window.confirm(...)     │                         │
   │                         │                         │                         │
   ├─ Cancel ────────────────▶ (no request issued)      │                         │
   │  dialog & task unchanged│                         │                         │
   │                         │                         │                         │
   ├─ OK ────────────────────▶ POST /tasks/{id}/delete  │                         │
   │                         │────────────────────────▶│ control.delete(id)      │
   │                         │                         │  → True: emit "deleted" │──▶ revision++
   │                         │◀──── 200, empty body ───│                         │
   │  #detail swapped empty  │                         │                         │
   │  → afterSwap: close()   │                         │                         │
   │                         │                         │                         │  GET /fragment/board
   │                         │                         │                         │◀── (card for id gone)
```

If `control.delete` returns `False` (task not found / already gone / claimed
mid-flight), the endpoint answers `404`; htmx leaves the dialog's current
content in place (its default behavior on a non-2xx response is not to
swap), so the operator sees the detail view unchanged rather than a blank
dialog — the same shape restart already has for its 404 case.

### `board.html` change

The `afterSwap` listener currently always calls `showModal()` when `#detail`
is the swap target. It gains one branch: if the swapped-in content is empty
(the delete response), close the dialog instead of (re-)opening it.

```js
document.body.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail.target.id !== 'detail') { return; }
  var detail = document.getElementById('detail');
  if (detail.innerHTML.trim() === '') {
    if (detail.open) { detail.close(); }   // triggers the existing 'close' handler, which clears innerHTML
  } else {
    detail.showModal();
  }
});
```

The pre-existing `close` listener (clears `innerHTML`, lets the SSE
extension tear down the live-output `EventSource`) fires either way — on a
manual close (click-outside / Esc) or this programmatic one — so no new
teardown path is needed.

## Component design

### 1. `ports/queue.py` — `TaskQueue.discard`

New abstract method, the terminal counterpart to `transfer` (which moves a
*claimed* task to another queue; `discard` removes it instead of moving it):

```python
@abstractmethod
def discard(self, task: Task) -> None:
    """Permanently remove a claimed task. The task must currently be held
    (i.e. returned by a prior claim()) — this is not a search by id."""
```

Two implementers:

- **`FilesystemTaskQueue.discard`** (`drivers/fs_queue.py`): unlinks
  `<processing>/<task.id>.json` with `missing_ok=True`. No new failure mode —
  the file already lives at a known path once claimed, same as `transfer`'s
  first branch reads/writes it.
- **`MemoryTaskQueue.discard`** (`drivers/memory.py`): `self._claimed.pop(task.id, None)`.

`discard` never touches the ready set (`_ready` / the queue root) — by the
time it's called the task has already been claimed out of it, mirroring how
`transfer` only ever acts on `.processing/`.

### 2. `ports/control.py` — `TaskControl.delete`

```python
@abstractmethod
def delete(self, task_id: str) -> bool:
    """Permanently remove a task, wherever it currently sits (unclaimed).
    True when a task by that id was found and removed, False otherwise
    (unknown id, already deleted, or currently claimed/in-flight)."""
```

`TaskControl` becomes a two-method ABC; every implementer — production
(`TaskControlService`), the API's fallback (`_NullTaskControl`), and the
test double (`FakeTaskControl`) — must supply both or fail to import/instantiate.

### 3. `task_control.py` — `TaskControlService`

Constructor grows two dependencies, mirroring what `app.py` already builds
for the dispatcher:

```python
def __init__(
    self,
    *,
    inbox: TaskQueue,
    step_queues: dict[str, TaskQueue],
    done: TaskQueue,
    failed: TaskQueue,
    events: EventSink,
    clock: Clock,
) -> None:
```

`delete`:

```python
def delete(self, task_id: str) -> bool:
    for queue in (self._inbox, *self._step_queues.values(), self._done, self._failed):
        found = next((t for t in queue.list() if t.id == task_id), None)
        if found is None:
            continue
        claimed = queue.claim(found, new_lock_id())
        if claimed is None:
            return False          # lost the race — another actor moved it first
        queue.discard(claimed)
        self._events.emit("deleted", task_id=task_id)
        return True
    return False                  # not found in any queue (unknown id, or currently claimed)
```

Search order (`inbox → step queues → done → failed`) doesn't matter for
correctness — a task id is unique and lives in exactly one queue's ready set
at a time — it's chosen simply to check the common cases (a stuck `failed`
task) no slower than any other. `list()` only sees unclaimed tasks, so a
task mid-flight in some queue's `.processing/` is invisible here and
`delete` returns `False` for it — consistent with FR-2/the plan's decision
to keep in-flight deletion out of scope. No `HistoryEntry` is appended
(there is no destination task object left to carry it — deletion has no
"to" step), unlike `restart`.

### 4. `models.py` event contract — the `"deleted"` event

Distinct in shape from every event `ProjectionSink` currently understands: no
`task`/`queue` fields, because there's no destination column. This is the
reason `ProjectionSink` must switch on event **name**, not field presence.

### 5. `projection.py` — `BoardProjection.remove`

```python
def remove(self, task_id: str) -> None:
    """Drop a task from the read model entirely — the counterpart to
    apply(), which places a task into a column. No-op if unknown."""
    if self._tasks.pop(task_id, None) is not None:
        self._columns.pop(task_id, None)
        self._bump()
```

Guarding the bump behind "was it actually present" keeps a stray/duplicate
`"deleted"` event from ticking the SSE revision (and therefore every
client's board refetch) for nothing.

### 6. `drivers/projection_events.py` — `ProjectionSink.emit`

```python
def emit(self, name: str, **fields: Any) -> None:
    if name == "deleted":
        task_id = fields.get("task_id")
        if isinstance(task_id, str):
            self._projection.remove(task_id)
        return
    raw = fields.get("task")
    ...  # unchanged movement-event path
```

### 7. `api/routes.py` — `POST /tasks/{task_id}/delete`

Added to `build_html_router`, same file, right after `restart_task`:

```python
@router.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
def delete_task(task_id: str) -> HTMLResponse:
    if not control.delete(task_id):
        raise HTTPException(status_code=404, detail=f"task {task_id} does not exist")
    return HTMLResponse("")
```

No `Request` parameter and no template render needed — unlike `restart`,
there is no task left to re-fragment; an empty 200 body is the "gone"
signal the `board.html` `afterSwap` branch above keys off. The JSON API
(`api/tasks/{id}`) is untouched — deletion, like restart, is HTML-surface
only; `test_no_endpoint_mutates` keeps asserting `DELETE /api/tasks/{id}` →
405.

### 8. `api/app.py` — `_NullTaskControl.delete`

```python
def delete(self, task_id: str) -> bool:
    return False
```

### 9. `app.py` wiring — `TaskControlService` construction

```python
control = TaskControlService(
    inbox=inbox, step_queues=step_queues, done=done, failed=failed,
    events=events, clock=clock,
)
```

`step_queues` and `done` already exist as locals in `build()` at this point
(built above for the dispatcher/consumers) — no new construction, just
passing what's already in scope. Single wiring site (`app.py::build`); `cli.py`
only consumes `harness.control`, unchanged.

### 10. `tests/fakes.py` — `FakeTaskControl`

Extended with an independent `delete_result`/`deleted` so restart and delete
behavior can be configured and asserted separately in API tests:

```python
class FakeTaskControl(TaskControl):
    def __init__(self, result: bool = True, delete_result: bool = True) -> None:
        self._result = result
        self._delete_result = delete_result
        self.restarted: list[str] = []
        self.deleted: list[str] = []

    def restart(self, task_id: str) -> bool:
        self.restarted.append(task_id)
        return self._result

    def delete(self, task_id: str) -> bool:
        self.deleted.append(task_id)
        return self._delete_result
```

### 11. `api/templates/_task.html`

```html
<h2>{{ task.id }}</h2>
<button hx-post="/tasks/{{ task.id }}/delete"
        hx-target="#detail" hx-swap="innerHTML"
        hx-confirm="Delete task {{ task.id }}? This cannot be undone.">Delete</button>
{% if task.status == "failed" %}
<button hx-post="/tasks/{{ task.id }}/restart"
        hx-target="#detail"
        hx-swap="innerHTML">Restart</button>
{% endif %}
```

## Data schemas

### Event: `"deleted"`

Emitted by `TaskControlService.delete` exactly once per successful deletion,
via the same `EventSink` chain (`CompositeEventSink` → `StdoutEventSink` /
`ProjectionSink` / `SourceReflectorSink`, in that order):

```json
{"name": "deleted", "task_id": "tsk_9"}
```

Contrast with every existing movement event, which always carries the full
task and its destination column:

```json
{"name": "restarted", "task_id": "tsk_9", "queue": "todo", "task": {"...": "..."}}
```

`SourceReflectorSink` (routes by `task.data.source.kind`, reading `fields["task"]`)
sees no `task` field on `"deleted"` and simply falls through its existing
"not a dict" guard — no code change needed there, and a task born from a
`TaskSource` (e.g. a GitHub issue) is not reflected outward on delete. That
is an explicit scope boundary of this design (not raised in the plan): the
GitHub issue behind a deleted task is left exactly as it was (label
unchanged), since `TaskSource.finish`/`report_progress` have no "deleted"
verb and adding one is out of scope for this task.

### HTTP: `POST /tasks/{task_id}/delete`

| | |
|---|---|
| Request body | none |
| `200` | empty body — task deleted; client closes `#detail` |
| `404` | `{"detail": "task {id} does not exist"}` — unknown id, already deleted, or currently claimed |

No new JSON API endpoint and no change to the existing `GET /api/tasks/{id}`
response shape — after a successful delete that endpoint simply starts
404-ing for that id (`BoardView.get` returns `None`), which is the
acceptance criterion verbatim.

### On-disk shape

No new file format. `discard` deletes
`<queue-root>/.processing/<task_id>.json` outright; nothing is written in
its place (contrast with `transfer`, which always leaves the task readable
somewhere).

## Edge cases

| Scenario | Behavior |
|---|---|
| Delete an id not present in any queue | `delete()` returns `False` → `404`; no event, no projection change |
| Delete a task currently claimed (dispatcher/consumer mid-tick) | invisible to `list()` in every queue → `False` → `404`; operator can retry once the step finishes |
| Two delete clicks in quick succession (double-submit) | Second call's `claim()` on an already-discarded task returns `None` (file already gone) → `False` → `404`; first call's `True` already fired the event |
| Delete races a `restart` on the same task | Whichever wins `claim()` proceeds; the loser's `claim()` returns `None` → its operation reports `False`/`404`. No double-processing, no crash |
| Cancel the browser confirm | htmx never issues the request — no server-side effect at all; verified indirectly by never hitting the endpoint in that test path |

```json
{"outcome": "done", "summary": "Designed the delete action: TaskQueue.discard (hard-remove a claimed task) and TaskControl.delete (search all queues, claim, discard, emit a shape-distinct 'deleted' event with only task_id) as the write-side port extensions; BoardProjection.remove/ProjectionSink special-casing the event by name; POST /tasks/{id}/delete returning an empty 200 or 404; and an unconditional Delete button in _task.html guarded by hx-confirm, with board.html's afterSwap closing #detail on an empty swap instead of reopening it."}
```

