# Architecture assessment — delete action on the task detail view

Reviewed against the actual tree (`src/harness/...`), not just the plan/design
prose. Verdict: **the plan (`plan-01.md`) and design (`design-01.md`) are
architecturally sound and match the existing code exactly** — every file,
signature and pattern they cite exists as described. This document confirms
the fit against the invariants, resolves the one gap they leave open, and
gives the implementer a concrete, ordered build sequence.

## Alignment with existing patterns

Verified line-by-line against the current tree:

- `TaskControl` (`ports/control.py:13`) is a one-method ABC today (`restart`);
  extending it to two methods is the same shape `BoardView`/`ArtifactView`
  already have (multiple read verbs on one port). No new port needed.
- `TaskControlService.restart` (`task_control.py:38`) is the template:
  find → `queue.claim(found, new_lock_id())` → act → `events.emit(...)`. The
  planned `delete` reuses this exact skeleton — same race handling
  (`claimed is None` → lost race → `False`), same actor model.
- `TaskQueue` (`ports/queue.py`) already has the `claim`/`transfer`/`recover`
  triad; `discard` is the missing terminal verb, symmetric with `transfer`
  (moves a claimed task elsewhere) instead of a new concept.
- `FilesystemTaskQueue`/`MemoryTaskQueue` (`drivers/fs_queue.py`,
  `drivers/memory.py`) both operate on `.processing/`/`self._claimed` once a
  task is claimed — `discard` is a one-line addition in each, no new
  failure mode beyond what `transfer` already has.
- `BoardProjection` (`projection.py:50`) has `apply`/`hydrate`/`snapshot`/`get`
  but no removal path — `remove` is a genuinely new capability, not a
  rename of something existing.
- `ProjectionSink.emit` (`drivers/projection_events.py:16`) currently
  requires both `task` and `queue` fields to do anything; a `"deleted"`
  event that carries neither is correctly handled as a name-based special
  case, not a shape it can infer.
- `api/routes.py` restart route (`:141`) and `api/app.py`'s `_NullTaskControl`
  (`:44`) are the direct templates for the new route and its null
  implementation.
- `app.py::build` (`:249-347`) already has `step_queues` and `done` as
  locals in scope at the point `TaskControlService` is constructed (`:327`)
  — extending that constructor call needs no new plumbing, just passing
  what's already built.

No invariant in `CLAUDE.md` is threatened by this shape. In particular:
invariant #23 (`TaskControl` mirrors `BoardView`, touched only by
`TaskControlService`/`api/`/wiring) is exactly what `test_architecture.py`
already enforces (`test_orchestration_does_not_import_control`,
`test_api_does_not_import_drivers`, `test_only_app_and_cli_wire_drivers`) —
none of those tests need to change, and the new code must keep passing them
unmodified.

## Proposed architecture

No new port, no new module. Four existing seams extended, one new read-model
mutator:

```
ports/queue.py        TaskQueue.discard(task) -> None            [new abstract method]
ports/control.py      TaskControl.delete(task_id) -> bool         [new abstract method]
task_control.py        TaskControlService.delete(...)              [new method + 2 ctor deps]
projection.py          BoardProjection.remove(task_id) -> None    [new method]
drivers/projection_events.py  ProjectionSink.emit                [special-case "deleted"]
api/routes.py           POST /tasks/{id}/delete                   [new route]
api/app.py               _NullTaskControl.delete                  [new method]
app.py                   TaskControlService(...) call site        [+ step_queues, done]
templates/_task.html     Delete button + hx-confirm                [new markup]
templates/board.html     afterSwap: close-on-empty branch          [new branch]
```

### Key decision 1 — hard delete, not an archival queue

**Chosen: hard delete** (`discard` unlinks the file / drops the dict entry).
Endorsed. An `archived/` queue is the same false economy `done`/`failed`
already avoid turning into: a queue nobody reads is not meaningfully
different from deletion, except it silently grows forever and needs its own
GC story later. Nothing in the acceptance criteria asks for undo, and adding
a queue "just in case" is exactly the kind of speculative surface this
codebase's conventions (see `CLAUDE.md`: no abstraction beyond what's asked)
argue against. If undo is wanted later, it is a new, deliberate feature
(soft-delete flag, or an explicit archive queue with its own view) — not a
default baked into this change.

### Key decision 2 — `TaskControlService.delete` searches every queue, `restart` still doesn't

`restart` only ever looks in `failed/`, because a restart is only ever
meaningful for a failed task. `delete` is meaningful from any state, so it
must search `inbox`, every step queue, `done`, and `failed`. This is a
genuine asymmetry between the two verbs on the same port, not an
inconsistency — `TaskControl` is allowed to have methods with different
reach, the same way `BoardView.get` and `BoardView.snapshot` differ in
scope. `TaskControlService`'s constructor grows from 4 to 6 dependencies to
support this; that's an acceptable, bounded cost (all six are already
built locals in `app.py::build`, per the wiring point cited above).

### Key decision 3 — a `"deleted"` event with only `task_id`, not `task`+`queue`

This is deliberate and correctly reasoned in the design: invariant #7 says a
*movement* event carries `task`+`queue` because the projection needs a
destination to place the task into. Deletion has no destination — forcing a
fake `queue` value onto the event to satisfy a shape check would be worse
than a legitimate second, smaller event shape. `ProjectionSink` already
does a presence check (`isinstance(raw, dict)` / `isinstance(column, str)`)
before touching a `task`/`queue` movement event; adding a **name**-based
branch ahead of that check for `"deleted"` is a small, explicit special
case, not a design smell — the alternative (inferring "this must be a
deletion" from the *absence* of fields) would be far more fragile.

### Key decision 4 — in-flight (claimed) tasks are not deletable in this pass

`TaskQueue.list()` only ever sees unclaimed tasks (every driver confirmed:
`fs_queue.py:59-65`, `memory.py:41-42`), so a task a consumer is currently
running (`claude -p` mid-flight, or the dispatcher mid-route) is invisible
to `delete`'s search and the operator gets a clean `False` → `404`.
Endorsed as the right scope for this task. Reaching into `.processing/` to
kill an in-flight run is a categorically different, riskier feature
(terminating a subprocess, an uncommitted worktree, a partially-written
artifact) that the acceptance criteria don't ask for. The failure mode is
graceful, not silent: the operator sees "not found," not a crash, and can
retry once the step finishes or the task lands in `failed/`.

### Gap not addressed by plan/design — reflection to an external `TaskSource`

One thing neither prior document calls out: a task whose `data.source.kind`
is `"github"` (invariant #19) currently has its GitHub issue label kept in
sync by `SourceReflectorSink` on `"dispatched"`/`"finished"`/`"failed"`
events (`drivers/source_reflector.py:31-42`). Deleting such a task emits
`"deleted"`, which `SourceReflectorSink.emit` silently ignores (no `task`
key → early return) — verified, this **does not crash**, it's a clean no-op.
But the practical effect is that the GitHub issue is left exactly as it was
labeled at the moment of deletion (e.g. still "in progress") — the harness
now has no record of the task, but GitHub does not learn it was removed.

**Recommendation: accept this gap for this task, and say so explicitly in
the PR.** Closing it properly means deciding what a deleted GitHub-sourced
task *should* look like on GitHub (close the issue? re-label to something
like `harness:removed`? leave it and let the operator handle it manually?)
— that's a product decision, not an architectural one, and isn't implied by
the acceptance criteria (which are scoped to "the board UI"). Wiring
`TaskControlService` to `sources: list[TaskSource]` to call `finish()` on
delete is a small mechanical change *if* that product decision is made
later; forcing it now would be scope creep against invariant discipline
(don't design for hypothetical requirements). Flag it in the PR description
as a known, deliberate limitation.

## Implementation guidance

Build bottom-up; each layer is independently testable before the next one
depends on it. This mirrors the plan's "Rough plan" section — the ordering
below is the dependency-safe sequence:

1. **`ports/queue.py`**: add `discard(self, task: Task) -> None` as an
   `@abstractmethod`. This alone breaks every concrete `TaskQueue` until
   step 2 is done — expected, keep the two commits together or the test
   suite won't import.
2. **Drivers**: `FilesystemTaskQueue.discard` — unlink
   `self._processing / f"{task.id}.json"` with `missing_ok=True` (same
   idempotent posture as `_quarantine_file`/`recover`). `MemoryTaskQueue.discard`
   — `self._claimed.pop(task.id, None)`. Unit-test both directly: claim then
   discard, assert absent from both the ready set and `.processing/`, and
   that a subsequent `recover()` doesn't resurrect it.
3. **`ports/control.py`**: add `delete(self, task_id: str) -> bool` as an
   `@abstractmethod`. Breaks `TaskControlService`, `_NullTaskControl`,
   `tests/fakes.py::FakeTaskControl` until implemented — again, keep in the
   same change.
4. **`task_control.py`**: extend `TaskControlService.__init__` with
   `step_queues: dict[str, TaskQueue]` and `done: TaskQueue`; implement
   `delete` per the design's search-claim-discard-emit loop. Test against
   `MemoryTaskQueue` instances the same way `test_task_control.py` already
   tests `restart` — one test per queue kind the task can be found in
   (inbox, a step queue, done, failed), one for unknown id, one for lost
   race (pre-claim the task in the test before calling `delete`, expect
   `False`).
5. **`projection.py`**: add `BoardProjection.remove(task_id)` — pop from
   both `_tasks` and `_columns`, bump revision only if something was
   actually removed (avoids a spurious SSE tick on a stray event).
6. **`drivers/projection_events.py`**: branch on `name == "deleted"` before
   the existing `task`/`queue` shape check, call `self._projection.remove(...)`.
7. **`api/app.py`**: `_NullTaskControl.delete` returns `False`.
8. **`api/routes.py`**: `POST /tasks/{task_id}/delete` in
   `build_html_router` — `control.delete(task_id)` → `404` on `False`,
   else `HTMLResponse("")` (200, empty body — there is no fragment left to
   render, unlike restart which re-renders `_task.html`).
9. **`app.py`**: pass `step_queues=step_queues, done=done` into the existing
   `TaskControlService(...)` call at `:327`.
10. **`tests/fakes.py`**: extend `FakeTaskControl` with an independent
    `delete_result`/`deleted` list, as the design specifies — restart and
    delete must be configurable/assertable independently in API tests
    (mirrors how `test_api_html.py` already asserts `control.restarted`).
11. **Templates**: `_task.html` gets the unconditional Delete button with
    `hx-confirm`; `board.html`'s `afterSwap` listener gets the
    close-on-empty branch. These have no unit-testable logic beyond string
    presence in the rendered fragment (`"/tasks/tsk_9/delete" in body`,
    matching the existing restart-button test's shape) — the confirm/cancel
    browser behavior itself is out of reach of the FastAPI `TestClient` and
    is not worth a browser test for this change.
12. **API tests** (`test_api_html.py`): mirror the four existing restart
    tests — button always present (unconditional, unlike restart),
    success → 200 + empty body + `control.deleted == [id]`, `False` from
    control → 404, default `_NullTaskControl` → 404.

Run `.venv/bin/pytest -q` after each of steps 2, 4, 6, 9 and again at the
end; confirm `tests/test_architecture.py` passes untouched throughout —
it's the mechanical proof that none of this leaked a driver into `api/`,
`dispatcher.py`, or `consumer.py`.

## Data flow (delete path)

```
operator clicks Delete → confirms
  → POST /tasks/{id}/delete
    → control.delete(id)                                   [TaskControlService]
        for queue in (inbox, *step_queues.values(), done, failed):
            found = next(t in queue.list() if t.id == id, None)
            if found: claim → discard → events.emit("deleted", task_id=id) → return True
        return False
    → 404 if False
    → 200 + "" if True
        (side effect, synchronous within emit): ProjectionSink → BoardProjection.remove(id)
        → revision bump → SSE "board" event → other clients' #board refetches
  → htmx swaps #detail with "" → afterSwap sees empty → dialog.close()
```

Note the same synchronous-projection-update property `restart` already
relies on (`api/routes.py:147-148`'s comment) — no polling, no eventual
consistency window between the POST response and the board reflecting the
deletion.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Constructor signature change to `TaskControlService` breaks any other call site | Grepped: `app.py:327` is the only construction site outside tests. Single point of change. |
| `ProjectionSink` special-casing by event name could silently swallow a future event whose author forgot to add `task`/`queue` | Existing behavior already silently no-ops on a malformed movement event (`isinstance` guards); this adds one more named case rather than changing that posture. Not a regression. |
| Losing the claim race mid-`delete` (dispatcher/consumer claims the task between `list()` and `claim()`) | Already handled identically to `restart`: `claim()` returning `None` is treated as "not found," not an error — no crash, no partial state. |
| A task deleted from a step queue leaves an orphaned worktree/branch on disk (phase 3: `.artifacts/<id>/` and the git worktree under `<worktrees_root>/<id>` are not touched by `discard`) | Acceptable for this pass — deletion here means "remove from the harness's queues/board," not "reclaim disk." `discard` operates only on the queue's JSON file, symmetric with `transfer`, which also never touches the worktree. Worth a one-line callout in the PR description so it isn't mistaken for a full teardown; a follow-up (`Workspace` cleanup on delete) is out of scope here per the task's own scope note. |
| External `TaskSource` (GitHub) not reflected on delete | Documented above as an accepted, deliberate gap — call it out in the PR. |
| Un-swallowed exception inside `ProjectionSink.remove` during `CompositeEventSink.emit` fan-out | Already covered structurally: `CompositeEventSink.emit` (`drivers/composite_events.py`) catches and logs per-sink, so a bug in the new `remove` path can't take down `SourceReflectorSink` or the stage-output sink, and vice versa. No new code needed here — just confirm the new branch lives inside `ProjectionSink.emit`, not bypassing the composite. |

## Prerequisites before implementation begins

None outstanding — every file, method and construction site this design
depends on exists in the tree today exactly as described (verified against
`ports/control.py`, `ports/queue.py`, `task_control.py`, `drivers/fs_queue.py`,
`drivers/memory.py`, `projection.py`, `drivers/projection_events.py`,
`api/routes.py`, `api/app.py`, `app.py`, `tests/fakes.py`,
`tests/test_api_html.py`, `tests/test_task_control.py`, and
`tests/test_architecture.py`). No spike, no upstream decision, no missing
dependency blocks starting step 1 of the implementation guidance above.

```json
{"outcome": "done", "summary": "Architecture assessment written to .artifacts/tsk_59af0fe0792b46b0/architecture-01.md: confirms plan/design match the real codebase (verified against ports/control.py, ports/queue.py, task_control.py, fs_queue.py, memory.py, projection.py, projection_events.py, api/routes.py, api/app.py, app.py wiring, tests/fakes.py); endorses hard delete via new TaskQueue.discard + TaskControl.delete, TaskControlService searching all queues, a name-based 'deleted' event distinct from movement events, and excluding in-flight/claimed tasks from this pass. Flags one gap the prior docs missed — deleting a GitHub-sourced task leaves the source issue's label stale since SourceReflectorSink silently no-ops on the shape-distinct 'deleted' event — and recommends accepting it as a documented limitation rather than expanding scope. Gives a 12-step dependency-ordered build sequence and a risk table."}
```
