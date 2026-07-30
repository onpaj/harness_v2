# Resuming a retired failure — design

*2026-07-30*

## The request

> Why are there tasks in done in development workflow but no PRs?

and, once the cause was known:

> But in that case I need to see that info about such tasks…and distinguish
> them. And also they are missing retry button

## What is actually there

Six tasks sat in the `development` tab's `done` column with no pull request.
None of them completed. All six died at the same place:

```
consumer:development | behavior raised an exception: claude timed out after 1800.0s
```

`plan`, `design` and `architecture` had each succeeded and written an artifact.
The implementation step then exhausted the 1800s per-agent timeout, so `verify`,
`review` and `land` never ran — and `land` is the step that opens the PR. The
healer diagnosed it correctly, filed `#141`, deduped the other five into it, and
`#145` raised the default to 5400s. The *timeout* is fixed. What remains are the
two things the operator could not do about the six casualties.

**They are indistinguishable from real completions.** ADR-0024 retired a claimed
failure into `done/` with `status = END` on purpose, and named its own cost:

> `done` holds two kinds of ending. Distinguishing them is a history read
> (`actor == "failed-tasks"`), not a column read.

Nothing performs that history read. Worse, the card actively asserts the wrong
thing: the accent chain in `_columns.html` is

```jinja
{% set accent = 'is-failed' if task.status == 'failed'
   else ... 'is-done' if task.last_outcome == 'done' and col.kind == 'terminal' ... %}
```

A retired failure has `status == 'end'` and `last_outcome == 'done'` — left over
from the `architecture` step that passed before the timeout — so it renders as a
**green card with a `done` badge**. The comment three lines above that block
says green must mean "the *task* is finished" and calls the alternative "the same
lie the bare `done` badge did". These six cards tell exactly that lie.

**They cannot be retried.** Two independent reasons:

- The button is gated on `{% if task.status == "failed" %}` (`_task.html`), and
  a retired failure has `status == "end"`.
- `TaskControlService.restart()` searches only `self._failed`, so even a
  hand-POSTed restart returns `False`.

And a third, latent: the failing step is **not stored anywhere**. `consumer._fail`
overwrites `status` with `"failed"`, and `Task.step` is null for these tasks.
The only record that `development` was the step that died is the history entry
`consumer:development, development → failed`. That same information loss is
already producing a visible defect elsewhere: `failed_tasks_check._diagnosis()`
renders `f"failed at step {task.status!r}"`, so every heal request on disk reads
**failed at step 'failed'**.

Two facts make recovery cheap rather than expensive. All six worktrees still
exist, with `plan-01.md`, `design-01.md` and `architecture-01.md` intact —
invariant #30 never removes a worktree. And `artifacts_layout.next_attempt()`
counts existing `<step>-NN.md` files, so a re-run writes a new artifact instead
of clobbering the old one.

## What it is

Two changes that share one derivation.

1. **`TaskControl` gains a second verb, `resume`** — put the task back one hop
   before the step it died at, and let the dispatcher route it forward into that
   step again, keeping the worktree and every artifact.
2. **The board names a retired failure** — on the card, not only in the detail
   panel, so the operator does not have to open 35 cards to find 6.

No new column, no fifth status, no change to `route()`, the dispatcher or the
consumer. This implements ADR-0024's "distinguishing them is a history read"
clause rather than reversing the decision.

## Rules

### Detection is exactly the record ADR-0024 nominated

A task is a retired failure when `status == END` **and** its last history entry
is `actor == "failed-tasks"`, `from_step == FAILED`, `to_step == END`. That is
the stamp `FailedTasksCheck._settle()` writes and nothing else writes.

The actor literal moves to `models.py` and `drivers/failed_tasks_check.py`
imports it from there, so it has one source and two readers — the shape
`MARKER_PREFIX` already uses, with the dependency pointing core → driver rather
than the reverse.

### Resume is a rewind, never a placement

Invariants #3, #23 and #35 all say the same thing: nothing but the dispatcher
puts a task into a step queue. `route()` decides purely from
`(status, last_outcome)`. So `resume` does not write into the `development`
queue. It sets the pair that makes the dispatcher choose `development` on its
own:

```
history: dispatcher, architecture → development, outcome=done
         consumer:development, development → failed
         failed-tasks, failed → end

resume:  status = "architecture", last_outcome = "done"  →  inbox
         dispatcher: route() → MoveTo("development")
```

The dispatcher then stamps its own `dispatched architecture → development`
entry, exactly as it did the first time. `route()`, `dispatcher.py` and
`consumer.py` are untouched.

When the failed step was the workflow's *start* step there is no prior hop — the
dispatcher entry reads `None → plan`. The rewind pair is then `(None, None)`,
which `route()` already resolves to `MoveTo(workflow.start)`. Resume degrades
gracefully into exactly what `restart` does, with no special case in the code.

### Scope: both terminal queues

`resume` searches `done/` **and** `failed/`. A failure the healer *declined*
stays in `failed/` per ADR-0024, and it has the same "don't redo the three good
steps" problem; today its only button is the from-scratch `Restart`. This costs
one extra entry in a tuple and no new machinery — `resumable_failure` already
admits both positions, which is precisely why it is a separate function from the
`done`-only board predicate. `restart` is left exactly as it
is — a from-scratch reset remains the right tool when the artifacts themselves
are suspect.

### The worktree and `data` are left alone

That is the point. The artifacts survive, and the `development` persona's prompt
already instructs it to read the previous steps' artifacts and any prior
`verify-NN.md`. Nothing is cleared but `status`, `last_outcome` and `lock_id`.

## The unit

Two questions, deliberately not one function. "How did this task last fail?" is
a derivation over history. "Is this task's *current* terminal position a
failure?" is a predicate about where it sits now. Conflating them produces two
bugs: a declined failure in `failed/` (`status == FAILED`, never `END`) fails the
`done`-shaped predicate, and a task that failed early, was restarted and then
completed properly passes the bare "has a failure in history" test.

All pure, in `models.py` beside `append_history`:

```python
@dataclass(frozen=True)
class FailureTrace:
    failed_step: str            # "development"
    reason: str | None          # "behavior raised an exception: claude timed out after 1800.0s"
    resume_status: str | None   # "architecture"  (None when failed_step was the workflow's start)
    resume_outcome: str | None  # "done"          (None likewise)

def failure_trace(task: Task) -> FailureTrace | None:
    """How this task's most recent failure happened and where to rewind to.
    None when its history records no failure, or when the failing entry's
    `from_step` is None (a dispatcher failure of a task that was never in a
    step — there is no step to resume into; that is a `restart`)."""

def is_retired_failure(task: Task) -> bool:
    """True when a `done` task got there via the healer rather than the
    workflow: `status == END` and the last history entry is the
    `failed-tasks` actor's `failed → end` stamp."""

def resumable_failure(task: Task) -> FailureTrace | None:
    """The rewind target for a task whose *current* terminal position was
    reached by failing — a healer-retired `done` task, or a task sitting in
    `failed/`. None for everything else, including a task that failed once,
    was restarted, and later completed normally."""
    if not (is_retired_failure(task) or task.status == FAILED):
        return None
    return failure_trace(task)
```

`failure_trace` reads three facts off history:

- `failed_step` — the last entry with `to_step == FAILED`, its `from_step`
- `reason` — that same entry's `reason`
- `resume_status` / `resume_outcome` — the entry with `to_step == failed_step`,
  its `from_step` and `outcome`; `(None, None)` when there is none

Who uses which:

| caller | function | why |
|---|---|---|
| card accent + badge | `is_retired_failure` + `failure_trace` | is it one, and which step |
| detail Info row | same | same |
| `TaskControl.resume` | `resumable_failure` | covers `done` *and* `failed`, rejects a genuine completion |
| `_diagnosis()` | `failure_trace` | only needs `.failed_step` |

## `TaskControlService.resume`

```python
def resume(self, task_id: str) -> bool
```

1. Locate the task across `(done, failed)`; `None` → `False`.
2. `resumable_failure(task)`; `None` → `False`. This is the one guard that
   rejects an ordinary completion, a completion that failed earlier in its life,
   and a dispatcher failure with no step to return to.
3. `queue.claim(found, new_lock_id())`; `None` → `False` (lost race).
4. Append one entry: `actor="operator"`, `from_step=task.status`, `to_step=None`,
   `reason=f"resumed at {failed_step!r} by operator"`.
5. `replace(claimed, status=resume_status, last_outcome=resume_outcome, lock_id=None)`.
6. `queue.transfer(reset, self._inbox)`, then emit `"resumed"` with
   `queue=TODO_COLUMN` — the same event shape `restart` emits, so the projection
   and SSE redraw need no changes.

The port docstring in `ports/control.py` gains the verb; `TaskControl`'s import
surface is unchanged, so `test_architecture.py`'s existing guard still covers it.

## The board

**Card** (`_columns.html`), via a jinja filter registered exactly like
`_outcome_step` — same history-reading shape, same registration line:

- a new `is-retired` branch in the accent chain, placed **before** `is-done`, so
  the green stripe can no longer win. Amber in `app.css`, not green.
- a `retired` badge in `card__meta` reading `timed out at development` — the
  failed step named, the full `reason` in the `title` attribute.
- the marker appended to `data-search`, so the fulltext filter added in `#142`
  finds these by typing "retired".

The existing `architecture done` badge stays. It is truthful — it says which
step reported the outcome — and it is informative here.

**Detail** (`_task.html`):

- an Info row: `retired` → "by the healer at step `development` — claude timed
  out after 1800.0s"
- a header button beside `Restart`:
  `{% if retired %}<button hx-post="/tasks/{{ task.id }}/resume">Resume at {{ retired.failed_step }}</button>{% endif %}`

**Route** — `POST /tasks/{task_id}/resume`, mirroring `restart_task`: `404` with
`detail=f"task {task_id} is not a resumable failure"` when `resume()` returns
`False`, else the re-rendered fragment.

## Two truth fixes in the same pass

- `LIFECYCLE_DESCRIPTIONS[DONE_COLUMN]` reads "Reached `end` — the workflow ran
  to completion." That is false for half the column's contents now. It must name
  both endings.
- `_diagnosis()` becomes `f"failed at step {trace.failed_step!r}"` off
  `failure_trace(task)`, falling back to the current `task.status` rendering when
  the trace is `None`, so the healer's own prompt stops saying **failed at step
  'failed'**.

## Testing

`test_task_control.py`
- resume of a retired failure rewinds to `("architecture", "done")` and lands in
  the inbox
- resume of an ordinary completion → `False`, task not moved
- resume where the failed step was the start step → `(None, None)`
- resume of a declined failure in `failed/` → rewound, not reset
- resume of a task that failed early, was restarted, and then completed → `False`
- lost race (claim returns `None`) → `False`

`test_router.py` — one composition test: the rewound pair feeds `route()` and
yields `MoveTo("development")`. Guards the whole mechanism against a workflow
graph change.

`test_models.py` — the three functions independently, since the split between
them is where the bugs live:
- `failure_trace` on a retired failure → all four fields; on a never-failed task
  → `None`; on a dispatcher failure whose `from_step` is `None` → `None`
- `is_retired_failure` → `True` for the healer stamp; `False` for a dispatcher
  `→ end`; `False` for a task in `failed/`
- `resumable_failure` → non-`None` for a retired failure *and* for a
  `failed/` task; `None` for a completion that carries an earlier `FAILED` entry
  from a `request_changes` loop or a prior restart

`test_routes.py` — `POST /resume` 200 on a retired failure, 404 on a plain done
task; the fragment renders the button only for a retired failure.

Card rendering — a retired failure's card carries `is-retired` and **not**
`is-done`.

## Invariants and ADR

Text edits to `CLAUDE.md`:

- **#23** — `restart` is no longer `TaskControl`'s only verb. Add `resume`,
  noting that it is also not a routing decision: it sets `(status, lastOutcome)`
  to a pair the dispatcher resolves, so invariant #3 still holds.
- **#24** — record that a retired failure is resumable, and that the history
  stamp ADR-0024 made the distinguisher is now read by the board and by
  `TaskControl`.
- **#30** — its justification for never cleaning up worktrees is "the original
  worktree is permanently inert once its task reaches a terminal state". Resume
  makes that false. The mechanism copes: invariant #31's reattach path is
  ancestry-aware, hard-resetting only when local `HEAD` is behind or equal to
  `origin/<branch>` and raising on divergence. But the sentence needs its
  exception, and the residual risk is real — if another task force-checked-out
  the same branch into a second worktree in the meantime, resuming the original
  leaves two live worktrees on one branch. This design changes no worktree
  handling; it only makes the case reachable.

**ADR-0025: a retired failure is resumable at the step it died at.** Short, and
additive to ADR-0024 per ADR-0000's convention — it records the rewind
mechanism, why it is not a routing decision, and the invariant #30 exception.

## Interaction with the terminal-task-retention design

`docs/superpowers/specs/2026-07-28-terminal-task-retention-design.md` proposes
archiving terminal tasks after 2 days. `archived/` is off every board column, so
once that ships a retired failure becomes unresumable through the UI after two
days — the card it needs is gone. The two designs are compatible but the window
matters: whichever ships second should note that the resume affordance has the
retention window's lifetime. Not a blocker for either.

## Deliberately out of scope

- **Re-queuing the six existing casualties.** Resume is a button, not a
  migration. Once it exists the operator presses it six times; nothing
  auto-revives history.
- **Decomposing the `development` step** so large refactors fit a timeout. `#141`
  raised the ceiling to 5400s and explicitly deferred decomposition; this design
  does not reopen it.
- **A bulk "resume all retired" action.** One task at a time, like `restart` and
  `delete`.
- **Reviving the `healed` column.** ADR-0024's decision stands.

## Expected effect

The six casualties render amber, badged `timed out at development`, findable by
typing "retired" in the board filter. Each has a `Resume at development` button
that re-enters the implementation step against its existing worktree and its
three good artifacts — now with a 5400s budget — and, on success, carries on
into `verify`, `review` and `land`, which is where the missing pull requests
come from.
