# Board UI — an overview on top of the harness

Status: approved
Date: 2026-07-19
Follows on from: `2026-07-19-orchestration-phase1-design.md`

## Goal

A web application that shows what is happening in the harness right now. Columns
are workflow steps, cards are tasks in their current status, and clicking a card
shows the task's metadata and history. The board refreshes itself.

It is built in parallel with the phase 1 implementation and is **part of the
harness package** — not a standalone project.

### Architectural requirement

The same as in phase 1, and it drives this design: **the UI must know nothing
about the drivers the harness runs on.** It must not know that tasks are JSON
files, that queues are directories, or that one day they will live in a database.
It sees a port and nothing else.

### Out of scope

- Any action. The board is a read-only observation deck, not a control panel. No
  retry, requeue, manual card move, or task creation.
- Authentication, multiple users, view persistence.
- Multiple workflows. Phase 1 has only `default`; the board hardcodes it. A
  switcher arrives once more workflows do.

## Core thesis

The source of truth for the UI is the **event stream**, not the store. The board
is a projection over that stream, kept in memory, and the API reads it through
the `BoardView` port.

Consequence: the UI reaches its data by no path other than the one the harness
already publishes. It has no storage reads of its own, and therefore no path
through which driver knowledge could leak.

## Topology

```
dispatcher/consumer ──emit──> EventSink (CompositeEventSink)
                                 ├──> StdoutEvents
                                 └──> ProjectionSink ──> BoardProjection
                                                            ▲        │
                              TaskQueue.list() ─hydration──┘        │
                                                                     ▼
                                                      BoardView (port) <── FastAPI
```

Everything runs in **one process** and one asyncio loop — the dispatcher, the
consumers, and uvicorn. The projection is therefore a plain in-memory object; no
transport, no extra serialization.

Cost: the UI goes down with the harness and scales with it. For a POC that is the
right trade; splitting it into its own service is a matter of swapping the
`BoardView` driver and adding a transport under `ProjectionSink`, not rewriting
the API.

## Additions to phase 1

| Addition | Kind | Responsibility |
|---|---|---|
| `BoardView` | **port** | `snapshot()`, `get(id)`, `subscribe()` |
| `BoardProjection` | read model | holds board state, applies events |
| `CompositeEventSink` | `EventSink` driver | fans an event out to multiple sinks |
| `ProjectionSink` | `EventSink` driver | delivers an event to `BoardProjection` |
| `api/` | application | FastAPI + templates |

The dispatcher and consumer **do not change**. They do not know that event
listeners have multiplied.

## Change to the event contract

An event must carry the **whole task snapshot**, not just the `task_id`.

Without it the projection cannot display a task that came into being after the
process started: hydration doesn't know about it, and the event says nothing but
its identity. With the full snapshot the projection is self-sufficient and never
reaches back into a queue.

```json
{
  "name": "task.routed",
  "at": "2026-07-19T10:00:05Z",
  "actor": "dispatcher",
  "queue": "design",
  "task": { "id": "tsk_…", "status": "design", "…": "…" }
}
```

`queue` is the target queue — a step name, `done`, or `failed`. See Column
placement.

The cost is a bulkier line on stdout. Formatting stays the driver's business —
`StdoutEvents` may abbreviate the snapshot, `ProjectionSink` needs it whole.

### Visibility of new tasks

A task freshly dropped into `tasks/` is visible on the board only once the
dispatcher first handles it, i.e. with a delay of at most one poll interval.

That is why the board **has no Inbox column** — it would be practically always
empty and its contents would be arbitrary, depending on when the page loaded.

## BoardProjection

### Hydration at startup

The in-memory projection is empty after a restart, but tasks sitting in queues
generate no event until they move. The board would then lie about the state of
the system.

At startup, therefore, the projection reads **all queues once through
`TaskQueue.list()`** — `queues/*`, `done/`, and `failed/` — and builds its
initial state from them. Only then does it start applying events.

It reads through the port, not the filesystem; the driver-ignorance requirement
holds.

Cost: the projection has two sources, the snapshot and the stream. This is
deliberate — the alternative was a persistent event log with replay, which means
a format, rotation, and replay time already in phase 1.

Tasks in `<queue>/.processing/` are included in hydration too; otherwise a
restart would make exactly the tasks that were being worked on disappear.

### State and revision

The projection holds a `task_id -> Task` map and a monotonically increasing
`revision`, which bumps on every applied change. `revision` goes out in the SSE
event and lets the client skip a needless redraw.

### Column placement

A column is **not** derived from `status` — `done/` and `failed/` are not
workflow steps, and a task in them carries the last `status` it had. The
projection therefore places by source:

- **During hydration** by the queue the task was read from (`queues/<step>`,
  `done/`, `failed/`).
- **At runtime** by the decision the event carries: `MoveTo(step)` → column
  `step`, `Finished` → `Done`, `Failed` → `Failed`.

The event thus carries the target queue alongside the task snapshot. Without it
the projection would have to guess the terminal state from `status` — precisely
the ambiguity for which phase 1 has the reserved `end` node.

`lockId != null` means the task is being worked on right now — a badge on the
card.

## The BoardView port

```python
class BoardView(Protocol):
    def snapshot(self) -> Board: ...
    def get(self, task_id: str) -> Task | None: ...
    def subscribe(self) -> AsyncIterator[int]: ...   # yields revision
```

This is all the API sees. `BoardProjection` is today's driver. Once the read
model lives in a database in phase 2, the driver is swapped; the API is not
touched.

`subscribe()` is part of the port on purpose — if the API handled it itself, it
would have to know the projection's internals.

Implementation of `subscribe()`: an `asyncio.Queue` per connected client,
**bounded**, dropping the overflow when full. For an event that carries no data
and only says "look again," dropping is safe — another notification will come and
the fragment is always the whole truth.

## API

Every endpoint is read-only.

| Endpoint | Returns |
|---|---|
| `GET /` | the board's HTML page |
| `GET /fragment/board` | HTML fragment with the columns — target of the htmx swap |
| `GET /fragment/task/{id}` | HTML fragment of the detail for the modal |
| `GET /api/events` | SSE stream |
| `GET /api/board` | JSON snapshot of the board |
| `GET /api/tasks/{id}` | JSON detail of the task |

htmx does not use the JSON variants. They exist as a testing surface and a
possible second client. Both branches read from the same `BoardView.snapshot()`,
so they cannot drift apart.

An unknown `task_id` returns 404 in both branches.

## Live refresh

Over SSE the server sends a **bare notification**, not data or a diff:

```
event: board
data: {"revision": 42}
```

The browser reacts with `hx-get="/fragment/board"` and redraws the columns.

That drops a whole category of problems: a reconnect need not reconstruct missed
events (another notification will come anyway and the fragment is always the whole
truth), a slow client cannot inflate a buffer, and message ordering does not
matter.

Two safeguards:

- **Coalescing** — at most one notification every 250 ms. Five consumers could
  otherwise hammer away with redraws.
- **Revision** — the client remembers the last one it rendered and skips the swap
  when it hasn't changed.

## UI

### Columns

The steps of the `default` workflow in reachability order from `start`; back
edges are ignored when determining order, otherwise the order would be undefined.
`Done` and `Failed` on the right.

Cards in a column are ordered by `created` ascending — i.e. in the order the FIFO
`EnqueueStrategy` takes them. The column then reads as the queue it actually is.

### Card

`id`, `repository`, time in status, a `lastOutcome` badge (`request_changes`
visually distinguished), and a **"processing"** badge when `lockId != null`.

That last badge is the only signal that distinguishes a task waiting in the queue
from a task whose behavior is running right now.

### Detail

Clicking a card opens a modal: all of the task's metadata and its `history` as a
timeline — `at`, `actor`, `from → to`, `outcome`, and `reason` on error.

The history is the main value here. From it you can see why a task failed and how
many times it bounced back from `review`.

### Stack

Jinja2 templates in FastAPI, htmx with the SSE extension. No build step, no
`node_modules`, the whole UI is part of the Python package.

Cost: richer interaction will later hit a ceiling. For a board with five columns
and a modal, that ceiling is far off.

## Code structure

```
src/harness/
  ports/
    board.py               # BoardView
  projection.py            # BoardProjection
  drivers/
    composite_events.py    # CompositeEventSink
    projection_events.py   # ProjectionSink
  api/
    app.py                 # factory(view: BoardView) -> ASGI app
    routes.py
    templates/
      board.html
      _columns.html
      _task.html
    static/
  app.py                   # wiring: composite sink, hydration, uvicorn in the same loop
```

Dependencies:

- `projection.py` imports `models.py` and `ports/`. Never `drivers/`.
- `api/` imports `ports/board.py` and `models.py`. Nothing more.
- The factory takes `BoardView` as a parameter. That is why the API is testable
  with a fake view and why it cannot know about drivers even by accident.
- All wiring stays in `app.py`.

## Error states

| Situation | Behavior |
|---|---|
| A task reached by neither hydration nor an event | not on the board; appears with the first event |
| An event with an unknown `task_id` | the projection creates it as a new record |
| An exception in `ProjectionSink` | caught in `CompositeEventSink`; **a sink's failure must not stop the loop or the other sinks** |
| A disconnected SSE client | its queue is dropped; the server does nothing further |
| An overflowed SSE client queue | the overflow is dropped, the connection stays |

## Testing strategy

| Layer | How |
|---|---|
| `BoardProjection` | table-driven: hydration, event → snapshot, back edge, move to `failed`, monotonicity of `revision` |
| `CompositeEventSink` | an exception from one sink does not bring down the others |
| API | `httpx` + a fake `BoardView`; JSON and fragments; 404 on an unknown id |
| SSE | a fake view, manual tick → coalescing and notification delivery |
| End-to-end | the whole loop on in-memory drivers + an HTTP client: a task flows through to `done/` and the board reflects it along the way |
| Invariant | neither `api/` nor `projection.py` imports anything from `drivers/` |

Tests must not touch real time — coalescing is tested via `Clock`, not `sleep`.

## Completion check

1. A running harness serves the board on a local port.
2. After startup the board shows the tasks that were already sitting in queues.
3. A task moving through the workflow advances between columns on the board
   without user intervention.
4. A task whose behavior is running has a "processing" badge.
5. A task with `request_changes` can be seen bouncing back from `review` to
   `development`.
6. The detail of a completed task shows the whole history, including the back
   edge.
7. A task with an unknown `workflowTemplate` appears in the `Failed` column,
   reason included.
8. Restarting the process restores the board to a state matching the queues.
