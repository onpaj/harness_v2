# Phase 1 — orchestration loop

Status: approved
Date: 2026-07-19

## Goal

A working program that polls a source directory of tasks and routes them, according
to a workflow, into individual output queues, where consumers pick them up and hand
them back. A task flows through the entire workflow from `start` to `end`.

This is a POC. Most of the moving parts get swapped out in later phases — JSON files
for a datastore, stdout for OTel, directories for storage queues, the dummy behavior
for a real agent. **Architectural requirement: only ever the driver may be swapped,
never its surroundings.**

### Out of scope for phase 1

- Git. The harness versions nothing and uses no worktrees. `repository` and `worktree`
  are opaque metadata that merely ride along.
- Real agent calls. `ConsumerBehavior` is a dummy.
- Persistent storage, HTTP API, dashboard, scheduler, retry policies,
  rate limiting.
- Multiple processes. Phase 1 runs in a single process.

## Core thesis (ARD1)

The unit of work is the **task**. A task carries its own metadata and travels between
queues. Modularity means that every moving part sits behind a port and is replaceable
by swapping the driver.

## Data models

### Task

```json
{
  "id": "tsk_2026071910000000",
  "repository": "app-backend",
  "worktree": null,
  "workflowTemplate": "default",
  "status": null,
  "lastOutcome": null,
  "lockId": null,
  "created": "2026-07-19T10:00:00Z",
  "history": [],
  "data": {}
}
```

| Field | Meaning |
|---|---|
| `id` | Task identity. Stable for its entire life. |
| `repository` | Opaque. The harness does not read it. |
| `worktree` | Opaque. The harness does not read it. |
| `workflowTemplate` | Workflow name. `WorkflowRepository` loads the definition by it. |
| `status` | Current step. `null` for a new task. Changed **only by the dispatcher**. |
| `lastOutcome` | Result of the last behavior run. Written **only by the consumer**. |
| `lockId` | Identity of the lease holder, `null` when no one holds the task. |
| `created` | ISO 8601 UTC. |
| `history` | Audit log, see below. |
| `data` | Opaque payload. The harness does not read it. |

The `workflow` field (an inline workflow definition in the task) is omitted for the MVP.
Only `workflowTemplate` is used.

### HistoryEntry

`history` is an audit log, not a list of visited phases. An entry is appended on
**every** status change, including error ones.

```json
{
  "at": "2026-07-19T10:00:05Z",
  "actor": "dispatcher",
  "from": "design",
  "to": "architecture",
  "outcome": "done"
}
```

- `actor` — `dispatcher` or `consumer:<step>`.
- `from` / `to` — steps; `null` at the start, `"end"` / `"failed"` at the end.
- `outcome` — the outcome the decision was based on; for a consumer, the outcome
  the behavior returned. On an error `to` carries the value `"failed"` and a
  `reason` field is added with the reason text.

The cost is a higher volume of records. The benefit is that a task's whole story is
visible, including why it failed.

### Workflow

```json
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan",         "on": "done",            "to": "design"},
    {"from": "design",       "on": "done",            "to": "architecture"},
    {"from": "architecture", "on": "done",            "to": "development"},
    {"from": "development",  "on": "done",            "to": "review"},
    {"from": "review",       "on": "done",            "to": "end"},
    {"from": "review",       "on": "request_changes", "to": "development"}
  ]
}
```

A workflow is a small state machine. Each transition is a triple `(from, on, to)`, so
backward edges are explicit and **need not be symmetric** — `review` goes back to
`development`, but `architecture` could go back straight to `plan`, say.

- `start` — the step a task with `status == null` goes into.
- `end` — the reserved name of the terminal node. `to: "end"` means a move into `done/`.
  The `end` node has no outgoing edges and no queue of its own.
- Retrying the same step is expressed as `to == from`; no special construct is needed.

`end` is a reserved node **deliberately**, instead of a rule that "a state with no
outgoing edges is the end". A typo in a step name would, under the implicit rule,
silently look like success; with a reserved `end`, the task drops into `failed/`,
where you'll see it.

### Outcome

A closed enumeration: `done` | `request_changes`. Any other value from a behavior is
an error and sends the task to `failed/`.

## Topology — everything is a queue

```
<root>/
  workflows/
    default.json
  tasks/                 # dispatcher inbox
    .processing/
  queues/
    plan/
      .processing/
    design/
      .processing/
    architecture/
      .processing/
    development/
      .processing/
    review/
      .processing/
  done/
  failed/
```

`tasks/`, `queues/*`, `done/`, and `failed/` are instances of the **same** port
`TaskQueue`. "Done" and "failed" are simply queues that no one consumes — there is no
special code for terminal states.

The queues under `queues/` are created from the workflow: one for each step that
appears as a `from` or `to` in the transitions, except `end`.

## Flow

```
tasks/ ──dispatcher──> queues/<step>/ ──consumer──> tasks/ ──dispatcher──> …
                                                                    │
                                                              done/ or failed/
```

1. The **dispatcher** picks one task from `tasks/` via `EnqueueStrategy` (or none).
2. Claims it (`claim`).
3. Loads the workflow by `workflowTemplate`.
4. Calls the pure function `route(task, workflow)`.
5. Per the decision, overwrites `status`, appends to `history`, and moves the task to
   the target queue / `done/` / `failed/`.
6. The **consumer** over the `<step>` queue claims the task, hands it to
   `ConsumerBehavior`, gets an outcome, writes `lastOutcome` and `history`, and returns
   the task to `tasks/`.

### Division of decision-making

Each link knows only its own piece and must not reach next door:

```
ConsumerBehavior  →  what happened   (done | request_changes)
Consumer          →  just delivers   (no decision)
Dispatcher        →  where it goes   (lookup in the workflow)
```

- **`ConsumerBehavior` is the only place an outcome is born.** How it arrives at one
  is its internal business — today a sleep, tomorrow an agent reading a diff.
- **The consumer is a thin wrapper.** It has **no** code branch that depends on the
  outcome value; it just writes it. If an `if outcome == ...` shows up in the consumer,
  responsibility has leaked across the boundary.
- **The consumer never changes `status`.** Only the dispatcher can do that.
- **The dispatcher never inspects `data`.** It decides solely from `(status, lastOutcome)`.

## Router

```python
def route(task: Task, workflow: Workflow) -> Decision
```

A pure function. No I/O, no filesystem, no time. `Decision` is one of:

| Decision | When | Consequence |
|---|---|---|
| `MoveTo(step)` | an edge was found | the task goes to `queues/<step>/` |
| `Finished` | the edge target is `end` | the task goes to `done/` |
| `Failed(reason)` | no edge matches | the task goes to `failed/` |

Rules:

- `status is None` → `MoveTo(workflow.start)`.
- Otherwise, look up `(status, lastOutcome)`. If none is found, `Failed`.
- `lastOutcome is None` for a task that already has a status is an inconsistency →
  `Failed`.

The entire routing logic — including backward edges and error paths — is thereby
testable without a single file on disk.

## Ports and drivers

| Port | Responsibility | Driver in phase 1 | Swapped for |
|---|---|---|---|
| `TaskQueue` | `list()`, `claim(task)`, `put(task)`, `recover()` | a directory of JSON files | storage queue |
| `EnqueueStrategy` | `select(tasks) -> Task \| None` | FIFO by `created` | priority, fair-share |
| `WorkflowRepository` | `get(name) -> Workflow` | `workflows/<name>.json` | DB, API |
| `ConsumerBehavior` | `run(task) -> Outcome` | sleep 5 s → `done` | a real agent |
| `EventSink` | `emit(event)` | lines on stdout | OTel |
| `Clock` | `now()`, `sleep(s)` | real time | — (exists for tests) |

Every port also gets an **in-memory driver for tests**. The whole loop then runs
without a disk and without five-second waits; tests must not touch real time.

### Notes on the ports

- `EnqueueStrategy` receives an already-loaded list of tasks and returns the chosen one.
  Selection is thereby decoupled from how the queue is read.
- `EventSink` receives a structured event (name, task id, fields), not a formatted
  string. Formatting is the driver's job — otherwise the OTel driver would be parsing
  text.
- `DummyBehavior` returns `done` **deterministically**, with a configurable exception:
  for one chosen step it returns `request_changes` on the first pass and `done` on the
  second. Without that the backward edge would never be exercised; without determinism
  the loop could spin forever.

## Concurrency, leases, and crashes

One process, asyncio: one dispatcher task and one consumer task per workflow step, all
in the same event loop. Polling with a configurable interval.

**`claim()` is an atomic `rename` into `<queue>/.processing/`.** A single operation
handles three things at once:

- **lease** — the file vanishes from the queue, no one else picks it up; `lockId` is
  written into the task,
- **idempotency** — the move either happened or it didn't; there is no intermediate
  state,
- **provenance** — because each queue has its own `.processing/`, after a crash it's
  visible where the task came from, without storing that anywhere.

If a process loses the race for a file (`rename` fails because it's no longer there),
that's not an error — it just takes the next one.

**Recovery at startup:** each queue returns the contents of its `.processing/` back to
itself and clears `lockId`. Because the work in phase 1 is idempotent, that suffices.

A lease TTL is not implemented in phase 1 — a single process that crashed recovers at
startup. `lockId` is in the model anyway, because phase 2 will need it.

## Error states

Everything below sends the task to `failed/`, appends to `history` with a `reason`, and
emits an event. **One bad task must not stop the loop.**

| Situation | Detection |
|---|---|
| Unknown `workflowTemplate` | `WorkflowRepository.get` finds nothing |
| `status` with no matching edge | the router returns `Failed` |
| Invalid outcome from the behavior | validation against the enumeration |
| Broken or unreadable JSON | on load from the queue |
| Exception from `ConsumerBehavior` | caught in the consumer |

Broken JSON is a special case: the task can't be deserialized, so it can't be given any
history. The file is moved into `failed/` as is, and the reason goes only into the event.

## Code structure

```
src/harness/
  models.py            # Task, HistoryEntry, Workflow, Transition, Outcome, Decision
  router.py            # pure route()
  dispatcher.py
  consumer.py
  ports/
    queue.py
    workflows.py
    strategy.py
    behavior.py
    events.py
    clock.py
  drivers/
    fs_queue.py
    fs_workflows.py
    fifo_strategy.py
    dummy_behavior.py
    stdout_events.py
    memory.py          # in-memory drivers for tests
  app.py               # wiring + asyncio runtime
  cli.py
```

Dependencies flow strictly downward. `models.py` imports nothing from the package.
`ports/` does not import `drivers/`. `dispatcher.py` and `consumer.py` know only the
ports, never a concrete driver — all wiring lives in `app.py`.

The package is named `harness`.

## Testing strategy

| Layer | How |
|---|---|
| `router.py` | table-driven unit tests — forward edges, backward, `start`, `end`, missing edge, inconsistent state |
| Dispatcher, consumer | in-memory drivers, fake clock, no disk |
| Filesystem drivers | tmp directory; `claim` atomicity and recovery from `.processing/` separately |
| End-to-end | the whole flow `start → end` on in-memory drivers, including one `request_changes` |
| Smoke | one run on a real filesystem with a shortened interval |

A test that verifies `dispatcher.py` and `consumer.py` import nothing from `drivers/`
guards the main architectural invariant.

## Done criteria

Phase 1 is done when:

1. `harness init` sets up the directory tree per the workflow.
2. Dropping a single task JSON into `tasks/` leads to the task reaching `done/`, having
   passed through all five steps and one backward edge.
3. From the events on stdout it's readable what happened to the task at each step.
4. The `history` of the arrived task faithfully describes that course.
5. A task with an unknown `workflowTemplate` ends up in `failed/` and the loop keeps
   running.
6. Killing the process mid-run and restarting leads to the task completing.

## Stack

Python 3.11 (`/Users/rem/.local/bin/python3.11`), `venv` + `pip install -e ".[dev]"`.
There is no `uv` on the machine. Tests with `pytest`.

The `CLAUDE.md` in the repo describes the dead architecture of the previous attempt and
will be rewritten as part of phase 1.
