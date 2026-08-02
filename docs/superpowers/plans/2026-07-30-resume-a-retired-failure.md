# Resuming a Retired Failure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator tell a healer-retired failure apart from a real completion on the board, and resume it at the step it died at without redoing the steps that passed.

**Architecture:** Three pure derivations over `Task.history` in `models.py` answer "how did this fail" and "is this task's current terminal position a failure". `TaskControl` gains a `resume` verb that rewinds `(status, lastOutcome)` to the hop *before* the failed step and re-inboxes the task, so the existing dispatcher routes it forward into that step again — no queue is written directly, no new status, no change to `route()`. The board reads the same derivation through a jinja filter to stop rendering a retired failure as a green `done` card.

**Tech Stack:** Python 3.11, FastAPI + Jinja2 + htmx, pytest. No new dependencies.

## Global Constraints

- **Project language is English — always.** Code, comments, docstrings, string literals, tests, docs, commit messages.
- **Commit straight into `main`.** No branches, no PRs, don't ask (harness_v2's own CLAUDE.md).
- **Conventional commits are load-bearing.** `feat:` bumps minor, `fix:`/`perf:` patch, `docs:`/`test:`/`chore:`/`refactor:` release nothing. Every commit message below is already correctly prefixed — do not change the prefixes.
- **Python is 3.11**, run via `.venv/bin/pytest -q` from the repo root.
- **`models.py` imports nothing from the `harness` package.** It is the base layer; keep it that way.
- **`dispatcher.py` / `consumer.py` must not import `ports/control.py`** — guarded by `tests/test_architecture.py`.
- **No test may sleep in real time.** Use `FakeClock`.
- **Invariant #3 must hold:** only the dispatcher places a task into a step queue. `resume` sets state and re-inboxes; it never writes to a step queue.
- **Do not reintroduce the `healed` column, queue or status.** ADR-0024 retired them.

Spec: `docs/superpowers/specs/2026-07-30-resume-a-retired-failure-design.md`

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/harness/models.py` | `HEAL_ACTOR`, `FailureTrace`, `failure_trace`, `is_retired_failure`, `resumable_failure` — the pure derivations | 1 |
| `src/harness/drivers/failed_tasks_check.py` | import `HEAL_ACTOR` instead of its own literal; `_diagnostic_request` names the real step | 2 |
| `src/harness/ports/control.py` | the `resume` abstract verb | 3 |
| `src/harness/task_control.py` | `TaskControlService.resume` — claim, rewind, re-inbox, emit | 3 |
| `tests/fakes.py` | `FakeTaskControl.resume` — the ABC's 2nd implementation | 3 |
| `src/harness/api/app.py` | `_NullTaskControl.resume` — the ABC's 3rd implementation | 3 |
| `src/harness/api/routes.py` | `_retired_failure` jinja filter; `retired` in the task fragment context; `POST /tasks/{id}/resume` | 4 |
| `src/harness/api/templates/_columns.html` | `is-retired` accent, `retired` badge, `data-search` term | 5 |
| `src/harness/api/templates/_task.html` | `retired` Info row + Resume button | 5 |
| `src/harness/api/static/app.css` | `--retired-bg`/`--retired-fg` (light + dark), `.card.is-retired`, `.badge.retired` | 5 |
| `docs/adr/0025-a-retired-failure-is-resumable.md` | the decision record | 6 |
| `CLAUDE.md` | invariants #23, #24, #30 | 6 |

Tasks 1→3 are strictly ordered (each consumes the previous). Task 4 depends on 3, task 5 on 4. Task 6 is documentation and can be done last.

---

### Task 1: The three derivations in `models.py`

Everything else reads these. They are pure functions over `Task.history` — no I/O, no clock.

**Files:**
- Modify: `src/harness/models.py` (add after `append_history`, at the end of the file)
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `HEAL_ACTOR: str` — the literal `"failed-tasks"`
  - `FailureTrace` — frozen dataclass with fields `failed_step: str`, `reason: str | None`, `resume_status: str | None`, `resume_outcome: str | None`
  - `failure_trace(task: Task) -> FailureTrace | None`
  - `is_retired_failure(task: Task) -> bool`
  - `resumable_failure(task: Task) -> FailureTrace | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
from harness.models import (
    END,
    FAILED,
    HEAL_ACTOR,
    HistoryEntry,
    Task,
    failure_trace,
    is_retired_failure,
    resumable_failure,
)


def _timed_out_at_development(status=END, extra=()):
    """A task that passed plan/design/architecture and timed out in development.

    `status=END` plus the trailing `failed-tasks` entry is the retired-failure
    shape; `status=FAILED` without it is the shape a declined failure has.
    """
    history = (
        HistoryEntry(
            at="2026-07-28T19:15:29Z", actor="dispatcher", from_step=None, to_step="plan"
        ),
        HistoryEntry(
            at="2026-07-28T20:02:05Z",
            actor="consumer:plan",
            from_step="plan",
            to_step=None,
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-28T20:02:05Z",
            actor="dispatcher",
            from_step="plan",
            to_step="design",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-28T20:31:29Z",
            actor="dispatcher",
            from_step="design",
            to_step="architecture",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-28T20:31:29Z",
            actor="dispatcher",
            from_step="architecture",
            to_step="development",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-29T00:05:45Z",
            actor="consumer:development",
            from_step="development",
            to_step=FAILED,
            reason="behavior raised an exception: claude timed out after 1800.0s",
        ),
    ) + tuple(extra)
    return Task(
        id="tsk_1",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=status,
        last_outcome="done",
        history=history,
    )


RETIRED_STAMP = HistoryEntry(
    at="2026-07-29T00:06:06Z",
    actor=HEAL_ACTOR,
    from_step=FAILED,
    to_step=END,
    summary="queued for healing",
)


def test_failure_trace_reads_the_failed_step_reason_and_rewind_pair():
    trace = failure_trace(_timed_out_at_development(extra=(RETIRED_STAMP,)))

    assert trace is not None
    assert trace.failed_step == "development"
    assert trace.reason == "behavior raised an exception: claude timed out after 1800.0s"
    assert trace.resume_status == "architecture"
    assert trace.resume_outcome == "done"


def test_failure_trace_is_none_when_the_task_never_failed():
    task = Task(
        id="tsk_2",
        created="2026-07-28T19:14:54Z",
        status=END,
        last_outcome="done",
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step="land",
                to_step=END,
                outcome="done",
            ),
        ),
    )

    assert failure_trace(task) is None


def test_failure_trace_rewind_pair_is_none_when_the_start_step_failed():
    """A first-step failure has no prior hop — the dispatcher entry reads
    None -> plan, so there is nothing to rewind to and route() falls back to
    the workflow's start."""
    task = Task(
        id="tsk_3",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=END,
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step=None,
                to_step="plan",
            ),
            HistoryEntry(
                at="2026-07-28T19:45:29Z",
                actor="consumer:plan",
                from_step="plan",
                to_step=FAILED,
                reason="behavior raised an exception: boom",
            ),
            RETIRED_STAMP,
        ),
    )

    trace = failure_trace(task)

    assert trace is not None
    assert trace.failed_step == "plan"
    assert trace.resume_status is None
    assert trace.resume_outcome is None


def test_failure_trace_is_none_for_a_failure_with_no_step_to_return_to():
    """A dispatcher failure of a task that was never in a step (status None):
    there is no step to resume into, so this is a restart case, not a resume."""
    task = Task(
        id="tsk_4",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step=None,
                to_step=FAILED,
                reason="workflow-less task has no usable step",
            ),
        ),
    )

    assert failure_trace(task) is None


def test_is_retired_failure_recognises_the_healer_stamp():
    assert is_retired_failure(_timed_out_at_development(extra=(RETIRED_STAMP,))) is True


def test_is_retired_failure_rejects_an_ordinary_completion():
    task = Task(
        id="tsk_5",
        created="2026-07-28T19:14:54Z",
        status=END,
        last_outcome="done",
        history=(
            HistoryEntry(
                at="2026-07-28T19:15:29Z",
                actor="dispatcher",
                from_step="land",
                to_step=END,
                outcome="done",
            ),
        ),
    )

    assert is_retired_failure(task) is False


def test_is_retired_failure_rejects_a_task_still_sitting_in_failed():
    assert is_retired_failure(_timed_out_at_development(status=FAILED)) is False


def test_resumable_failure_admits_a_retired_failure():
    trace = resumable_failure(_timed_out_at_development(extra=(RETIRED_STAMP,)))

    assert trace is not None
    assert trace.failed_step == "development"


def test_resumable_failure_admits_a_declined_failure_still_in_failed():
    trace = resumable_failure(_timed_out_at_development(status=FAILED))

    assert trace is not None
    assert trace.failed_step == "development"


def test_resumable_failure_rejects_a_completion_that_failed_earlier_in_its_life():
    """Failed at development, was restarted, then ran through to `end`. Its
    current terminal position was NOT reached by failing, so resuming it would
    re-run a step of an already-finished task."""
    task = _timed_out_at_development(
        extra=(
            HistoryEntry(
                at="2026-07-29T01:00:00Z",
                actor="operator",
                from_step=FAILED,
                to_step=None,
                reason="restarted by operator",
            ),
            HistoryEntry(
                at="2026-07-29T02:00:00Z",
                actor="dispatcher",
                from_step="land",
                to_step=END,
                outcome="done",
            ),
        )
    )

    assert resumable_failure(task) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_models.py -q -k "failure_trace or retired or resumable"`
Expected: FAIL — `ImportError: cannot import name 'HEAL_ACTOR' from 'harness.models'`

- [ ] **Step 3: Implement the derivations**

Append to the end of `src/harness/models.py`:

```python
HEAL_ACTOR = "failed-tasks"
"""The `failed-tasks` Check's actor name in a task's history. Defined here, in
the base layer, because it has two readers: the driver that writes the stamp
(`drivers/failed_tasks_check.py`) and the derivations below that read it back —
the same one-source-two-readers shape as `github_issues.MARKER_PREFIX`."""


@dataclass(frozen=True)
class FailureTrace:
    """How a task's most recent failure happened, and where to rewind to so the
    dispatcher routes it back into the step that failed.

    `resume_status`/`resume_outcome` are the `(status, lastOutcome)` pair the
    task held just before it was dispatched into `failed_step` — feeding that
    pair back through `route()` yields `MoveTo(failed_step)` again, which is how
    a resume avoids naming a queue itself (invariant #3). Both are None when
    `failed_step` was the workflow's start: `route()` already sends a
    status-less task to `workflow.start`.
    """

    failed_step: str
    reason: str | None
    resume_status: str | None
    resume_outcome: str | None


def failure_trace(task: Task) -> FailureTrace | None:
    """The task's most recent failure, or None if it records none usable.

    None in two cases: the history holds no `-> failed` entry at all, and the
    failing entry's `from_step` is None — a dispatcher failure of a task that
    was never in a step, where there is no step to return to (that is a
    `restart`, not a `resume`).
    """
    failing = next(
        (entry for entry in reversed(task.history) if entry.to_step == FAILED), None
    )
    if failing is None or failing.from_step is None:
        return None

    step = failing.from_step
    entered = next(
        (entry for entry in reversed(task.history) if entry.to_step == step), None
    )
    return FailureTrace(
        failed_step=step,
        reason=failing.reason,
        resume_status=entered.from_step if entered else None,
        resume_outcome=entered.outcome if entered else None,
    )


def is_retired_failure(task: Task) -> bool:
    """True when a `done` task got there via the healer, not via the workflow.

    ADR-0024 retires a claimed failure into `done/` with `status = END` and
    records the healer's involvement only in history — this reads exactly that
    record: the last entry being the `failed-tasks` actor's `failed -> end`
    move. Anchored on the *last* entry, so a task that failed earlier and later
    completed normally is not one of these.
    """
    if task.status != END or not task.history:
        return False
    last = task.history[-1]
    return (
        last.actor == HEAL_ACTOR
        and last.from_step == FAILED
        and last.to_step == END
    )


def resumable_failure(task: Task) -> FailureTrace | None:
    """The rewind target for a task whose *current* terminal position was
    reached by failing — a healer-retired `done` task, or one still sitting in
    `failed/`. None for everything else, so an ordinary completion (including
    one that failed once and was restarted) can never be resumed.
    """
    if not (is_retired_failure(task) or task.status == FAILED):
        return None
    return failure_trace(task)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models.py -q`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Run the full suite — `models.py` is imported everywhere**

Run: `.venv/bin/pytest -q`
Expected: PASS. If `tests/test_architecture_model.py` fails, read its assertion: it guards `models.py`'s import surface. These additions import nothing new, so a failure there means a genuine mistake — fix the code, not the test.

- [ ] **Step 6: Commit**

```bash
git add src/harness/models.py tests/test_models.py
git commit -m "feat: derive a task's failure trace and retired-failure status from history"
```

---

### Task 2: The healer names the real step

Same information loss, already producing a visible defect: every heal request on disk says `failed at step 'failed'` because `_diagnostic_request` renders `task.status`, which is `"failed"` by then.

**Files:**
- Modify: `src/harness/drivers/failed_tasks_check.py` (`ACTOR` at line 58; `_diagnostic_request` at ~line 236)
- Test: `tests/test_failed_tasks_check.py`

**Interfaces:**
- Consumes: `harness.models.HEAL_ACTOR`, `harness.models.failure_trace` (Task 1).
- Produces: nothing new. `ACTOR` keeps its name and value so every existing reference and test still resolves.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_failed_tasks_check.py`:

```python
def test_diagnostic_request_names_the_step_that_failed_not_the_word_failed():
    from harness.drivers.failed_tasks_check import _diagnostic_request
    from harness.models import FAILED, HistoryEntry, Task

    task = Task(
        id="tsk_1",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
        history=(
            HistoryEntry(
                at="2026-07-28T20:31:29Z",
                actor="dispatcher",
                from_step="architecture",
                to_step="development",
                outcome="done",
            ),
            HistoryEntry(
                at="2026-07-29T00:05:45Z",
                actor="consumer:development",
                from_step="development",
                to_step=FAILED,
                reason="behavior raised an exception: claude timed out after 1800.0s",
            ),
        ),
    )

    request = _diagnostic_request(task)

    assert "'development'" in request
    assert "'failed'" not in request


def test_diagnostic_request_falls_back_to_status_when_there_is_no_trace():
    from harness.drivers.failed_tasks_check import _diagnostic_request
    from harness.models import FAILED, Task

    task = Task(
        id="tsk_2",
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=FAILED,
    )

    assert "'failed'" in _diagnostic_request(task)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_failed_tasks_check.py -q -k diagnostic_request`
Expected: FAIL — the first test fails on `assert "'development'" in request`, because the request currently reads `failed at step 'failed'`.

- [ ] **Step 3: Implement**

In `src/harness/drivers/failed_tasks_check.py`, change the import line (currently `from harness.models import END, FAILED, HistoryEntry, Task, append_history`) to also bring in the two new names:

```python
from harness.models import (
    END,
    FAILED,
    HEAL_ACTOR,
    HistoryEntry,
    Task,
    append_history,
    failure_trace,
)
```

Replace the `ACTOR = "failed-tasks"` assignment at line 58 with an alias, so the literal has one home:

```python
ACTOR = HEAL_ACTOR
"""This check's actor name in a task's history. The literal lives in `models`
(`HEAL_ACTOR`) because the derivations that read the stamp back live there too;
this alias keeps every existing reference in this module unchanged."""
```

Replace `_diagnostic_request`:

```python
def _diagnostic_request(task: Task) -> str:
    """A short, synthesized diagnostic line — deliberately not the original
    task's own request (see `original_request`, carried separately).

    The failing step comes from history, not from `task.status`: by the time a
    failure is claimed its status is the word `failed`, so reading it named the
    step `'failed'` in every request this ever produced.
    """
    trace = failure_trace(task)
    step = trace.failed_step if trace is not None else task.status
    return (
        f"Diagnose why task {task.id} failed at step {step!r} "
        f"(workflow {task.workflow_template!r})."
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_failed_tasks_check.py -q`
Expected: PASS, whole file.

- [ ] **Step 5: Commit**

```bash
git add src/harness/drivers/failed_tasks_check.py tests/test_failed_tasks_check.py
git commit -m "fix: name the step that actually failed in the healer's diagnostic request"
```

---

### Task 3: `resume` on the port and the service

The rewind. Sets `(status, lastOutcome)` and re-inboxes; the dispatcher does the placing.

**Files:**
- Modify: `src/harness/ports/control.py`
- Modify: `src/harness/task_control.py`
- Modify: `src/harness/api/app.py` (`_NullTaskControl`, ~line 57 — the ABC's third implementation)
- Modify: `tests/fakes.py` (`FakeTaskControl`, ~line 45 — the ABC's second implementation)
- Test: `tests/test_task_control.py`, `tests/test_router.py`

**Interfaces:**
- Consumes: `harness.models.resumable_failure`, `harness.models.END` (Task 1).
- Produces: `TaskControl.resume(task_id: str) -> bool`, implemented by `TaskControlService.resume`. Emits an event named `"resumed"` with kwargs `task_id`, `queue=TODO_COLUMN`, `task=<dict>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_task_control.py`. Note the existing `build()` helper in that file returns `(service, inbox, failed, events)` and takes a `done=` queue — these tests pass their own `MemoryTaskQueue` for `done` so they can seed it.

```python
from harness.models import END, HistoryEntry
from harness.ports.board import TODO_COLUMN


def make_retired_failure(task_id="tsk_r", status=END, with_stamp=True):
    """A task that timed out at `development` after architecture passed."""
    history = [
        HistoryEntry(
            at="2026-07-28T20:31:29Z",
            actor="dispatcher",
            from_step="architecture",
            to_step="development",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-29T00:05:45Z",
            actor="consumer:development",
            from_step="development",
            to_step=FAILED,
            reason="behavior raised an exception: claude timed out after 1800.0s",
        ),
    ]
    if with_stamp:
        history.append(
            HistoryEntry(
                at="2026-07-29T00:06:06Z",
                actor="failed-tasks",
                from_step=FAILED,
                to_step=END,
                summary="queued for healing",
            )
        )
    return Task(
        id=task_id,
        workflow_template="development",
        created="2026-07-28T19:14:54Z",
        status=status,
        last_outcome="done",
        history=tuple(history),
    )


def test_resume_rewinds_a_retired_failure_to_the_hop_before_the_failed_step():
    done = MemoryTaskQueue("done")
    done.put(make_retired_failure())
    service, inbox, _, _ = build(done=done)

    assert service.resume("tsk_r") is True

    assert done.list() == []
    moved = inbox.list()
    assert len(moved) == 1
    # The pair route() turns back into MoveTo("development").
    assert moved[0].status == "architecture"
    assert moved[0].last_outcome == "done"
    assert moved[0].lock_id is None


def test_resume_appends_an_operator_entry_naming_the_step():
    done = MemoryTaskQueue("done")
    done.put(make_retired_failure())
    service, inbox, _, _ = build(done=done)

    service.resume("tsk_r")

    entry = inbox.list()[0].history[-1]
    assert entry.actor == "operator"
    assert entry.to_step is None
    assert entry.reason == "resumed at 'development' by operator"


def test_resume_emits_a_resumed_event_for_the_todo_column():
    done = MemoryTaskQueue("done")
    done.put(make_retired_failure())
    service, _, _, events = build(done=done)

    service.resume("tsk_r")

    names = [name for name, _ in events.events]
    assert "resumed" in names
    payload = next(payload for name, payload in events.events if name == "resumed")
    assert payload["task_id"] == "tsk_r"
    assert payload["queue"] == TODO_COLUMN


def test_resume_also_works_on_a_declined_failure_still_in_failed():
    service, inbox, failed, _ = build(
        [make_retired_failure(task_id="tsk_d", status=FAILED, with_stamp=False)]
    )

    assert service.resume("tsk_d") is True

    assert failed.list() == []
    assert inbox.list()[0].status == "architecture"


def test_resume_refuses_an_ordinary_completion():
    done = MemoryTaskQueue("done")
    done.put(
        Task(
            id="tsk_ok",
            workflow_template="development",
            created="2026-07-28T19:14:54Z",
            status=END,
            last_outcome="done",
            history=(
                HistoryEntry(
                    at="2026-07-28T21:00:00Z",
                    actor="dispatcher",
                    from_step="land",
                    to_step=END,
                    outcome="done",
                ),
            ),
        )
    )
    service, inbox, _, _ = build(done=done)

    assert service.resume("tsk_ok") is False

    assert len(done.list()) == 1
    assert inbox.list() == []


def test_resume_refuses_an_unknown_id():
    service, _, _, _ = build()

    assert service.resume("tsk_missing") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_task_control.py -q -k resume`
Expected: FAIL — `AttributeError: 'TaskControlService' object has no attribute 'resume'`

- [ ] **Step 3: Add the port verb**

In `src/harness/ports/control.py`, update the module docstring's last sentence and add the method. Replace `Today its only verb is `restart` (a failed task back to the inbox); it is the first step toward changing a task's stage.` with:

```
Its verbs are `restart` (a failed task back to the inbox, from the start) and
`resume` (a terminal failure back into the step it died at). Neither is a
routing decision — both set state and let the dispatcher place the task.
```

Add this method to the `TaskControl` ABC, after `restart`:

```python
    @abstractmethod
    def resume(self, task_id: str) -> bool:
        """Return a task whose terminal position was reached by failing to the
        inbox, rewound to the hop *before* the step it failed at, so the
        dispatcher routes it back into that step with its worktree and prior
        artifacts intact.

        Accepts a healer-retired task in `done/` (ADR-0024) and one still in
        `failed/`. True when a task by that id was found and requeued, False
        otherwise (unknown id, an ordinary completion, a failure with no step
        to return to, or a lost race).
        """
```

**Then update the other two implementations of this ABC in the same step** — a new abstract method makes them un-instantiable, which would take out `create_app`'s default wiring and every test that uses it. There are exactly three implementations; `grep -rn "TaskControl)" src tests` confirms it.

In `src/harness/api/app.py`, add to `_NullTaskControl` (~line 57) and widen its docstring's "restart simply reports nothing was done" to "restart and resume simply report nothing was done":

```python
    def resume(self, task_id: str) -> bool:
        return False
```

In `tests/fakes.py`, extend `FakeTaskControl` (~line 45) so the API tests in Task 4 can drive it:

```python
    def __init__(
        self,
        result: bool = True,
        delete_result: bool = True,
        resume_result: bool = True,
    ) -> None:
        self._result = result
        self._delete_result = delete_result
        self._resume_result = resume_result
        self.restarted: list[str] = []
        self.deleted: list[str] = []
        self.resumed: list[str] = []

    def resume(self, task_id: str) -> bool:
        self.resumed.append(task_id)
        return self._resume_result
```

- [ ] **Step 4: Implement the service method**

In `src/harness/task_control.py`, extend the existing models import (currently `from harness.models import FAILED, HistoryEntry, append_history`) to:

```python
from harness.models import FAILED, HistoryEntry, append_history, resumable_failure
```

Add this method to `TaskControlService`, between `restart` and `delete`:

```python
    def resume(self, task_id: str) -> bool:
        """Rewind a terminal failure to just before the step it died at.

        Deliberately not a placement: it writes the `(status, lastOutcome)` pair
        the task held before it was dispatched into the failing step and returns
        it to the *inbox*, so `route()` re-derives that step and the dispatcher
        moves it (invariant #3). Nothing else is cleared — the worktree and the
        artifacts the earlier steps produced are the whole point.
        """
        for queue in (self._done, self._failed):
            found = next((task for task in queue.list() if task.id == task_id), None)
            if found is None:
                continue

            trace = resumable_failure(found)
            if trace is None:
                return False

            claimed = queue.claim(found, new_lock_id())
            if claimed is None:
                # Lost the race — another actor moved the file first.
                return False

            entry = HistoryEntry(
                at=self._clock.now(),
                actor=ACTOR,
                from_step=claimed.status,
                to_step=None,
                reason=f"resumed at {trace.failed_step!r} by operator",
            )
            reset = append_history(
                replace(
                    claimed,
                    status=trace.resume_status,
                    last_outcome=trace.resume_outcome,
                    lock_id=None,
                ),
                entry,
            )
            queue.transfer(reset, self._inbox)
            self._events.emit(
                "resumed",
                task_id=task_id,
                queue=TODO_COLUMN,
                task=reset.to_dict(),
            )
            return True
        return False
```

`ACTOR` is already `"operator"` at the top of this module, and `TODO_COLUMN`, `replace` and `new_lock_id` are already imported — nothing else to add.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_task_control.py -q`
Expected: PASS, whole file.

- [ ] **Step 6: Prove the rewind actually routes back into the failed step**

This is the load-bearing claim of the whole design — assert the composition, not just the pair. `tests/test_router.py` already has a module-level `WORKFLOW` carrying the `architecture --done--> development` edge and a `task(...)` helper, so this is one assertion. Append to `tests/test_router.py`:

```python
def test_a_resumed_task_routes_back_into_the_step_it_failed_at():
    """The pair TaskControlService.resume writes must make route() choose the
    failed step again — that is what lets resume avoid naming a queue itself
    (invariant #3). Rewinding to ("architecture", "done") must re-derive
    `development`, the step that failed."""
    rewound = task(status="architecture", last_outcome="done")

    assert route(rewound, WORKFLOW) == MoveTo("development")
```

Run: `.venv/bin/pytest tests/test_router.py -q -k resumed`
Expected: PASS immediately — `route()` is unchanged by this task, and that is the point: the test pins that the rewind pair composes with the *existing* router.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. `tests/test_architecture.py` verifies `dispatcher.py`/`consumer.py` do not import `ports/control.py` — untouched here, so a failure means something else went wrong.

- [ ] **Step 8: Commit**

```bash
git add src/harness/ports/control.py src/harness/task_control.py src/harness/api/app.py tests/fakes.py tests/test_task_control.py tests/test_router.py
git commit -m "feat: resume a terminal failure at the step it died at"
```

---

### Task 4: The API surface

Route, filter, fragment context, and the fake the API tests drive.

**Files:**
- Modify: `src/harness/api/routes.py` (filter beside `_outcome_step` ~line 130; `_task_fragment` ~line 578; route after `restart_task` ~line 618)
- Test: `tests/test_api_html.py`
- (`tests/fakes.py` and `src/harness/api/app.py` were already updated in Task 3.)

**Interfaces:**
- Consumes: `TaskControl.resume` (Task 3), `harness.models.is_retired_failure`, `harness.models.resumable_failure` (Task 1).
- Produces:
  - `_retired_failure(task) -> FailureTrace | None`, registered as the jinja filter `retired_failure`
  - `retired` in the `_task.html` render context — a `FailureTrace` or `None`
  - `POST /tasks/{task_id}/resume` → the refreshed task fragment, or 404

- [ ] **Step 1: Confirm the ground is solid before adding routes**

`FakeTaskControl.resume` and `_NullTaskControl.resume` were both added in Task 3 (they had to be — the abstract method makes the ABC un-instantiable without them). Verify that landed rather than re-doing it:

Run: `.venv/bin/pytest tests/test_api_html.py -q`
Expected: PASS, whole file, unchanged. A `TypeError: Can't instantiate abstract class` here means Task 3's stub updates were skipped — go back and apply them before continuing.

- [ ] **Step 2: Write the failing tests**

Three edits to `tests/test_api_html.py`.

**(a)** Add the fixture task next to the other module-level tasks (after `UNATTRIBUTED`, ~line 131):

```python
# A failure the healer retired into `done` (ADR-0024): status `end` and a
# leftover `done` outcome from the step that passed *before* the timeout, which
# is exactly why the accent chain must not read it as a completion.
RETIRED = Task(
    id="tsk_r",
    workflow_template="default",
    created="2026-07-19T10:00:11Z",
    repository="app-backend",
    status="end",
    last_outcome="done",
    history=(
        HistoryEntry(
            at="2026-07-19T10:30:00Z",
            actor="dispatcher",
            from_step="architecture",
            to_step="development",
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-19T11:00:00Z",
            actor="consumer:development",
            from_step="development",
            to_step="failed",
            reason="behavior raised an exception: claude timed out after 1800.0s",
        ),
        HistoryEntry(
            at="2026-07-19T11:00:06Z",
            actor="failed-tasks",
            from_step="failed",
            to_step="end",
            summary="queued for healing",
        ),
    ),
)
```

**(b)** Add it to the `client` fixture's terminal `done` column (~line 172), beside the `UNATTRIBUTED` card the accent tests already use, and make it gettable for the detail fragment:

```python
                    BoardColumn(
                        name="done", tasks=(UNATTRIBUTED, RETIRED), kind=COLUMN_TERMINAL
                    ),
```

```python
    view = FakeBoardView(
        board,
        {"tsk_1": WORKING, "tsk_4": WORKFLOW_LESS, "tsk_9": BROKEN, "tsk_r": RETIRED},
    )
```

**(c)** Append the tests. `/fragment/board` takes no query parameter — it renders the whole snapshot, exactly as the existing accent tests call it:

```python
def test_a_retired_failure_is_not_rendered_as_a_finished_task(client):
    """`tsk_6` and `tsk_r` sit in the same terminal column with the same
    `last_outcome`. Only one of them finished."""
    body = client.get("/fragment/board").text

    tag = _card_open_tag(body, "tsk_r")
    assert "is-retired" in tag
    assert "is-done" not in tag
    assert "is-done" in _card_open_tag(body, "tsk_6")


def test_a_retired_failure_card_names_the_step_it_died_at(client):
    body = client.get("/fragment/board").text

    assert "development</span>retired" in body


def test_a_retired_failure_card_is_findable_by_the_board_filter(client):
    body = client.get("/fragment/board").text

    assert "retired development" in _card_open_tag(body, "tsk_r")


def test_resume_button_shown_only_for_a_resumable_failure(client):
    retired_body = client.get("/fragment/task/tsk_r").text
    assert "/tasks/tsk_r/resume" in retired_body
    assert "Resume at development" in retired_body

    working_body = client.get("/fragment/task/tsk_1").text
    assert "/tasks/tsk_1/resume" not in working_body


def _board_with_retired() -> Board:
    return Board(
        revision=9,
        workflows=(
            BoardTab(
                name="default",
                columns=(
                    BoardColumn(name="todo", tasks=()),
                    BoardColumn(name="development", tasks=(WORKING,)),
                    BoardColumn(name="done", tasks=(RETIRED,), kind=COLUMN_TERMINAL),
                ),
            ),
        ),
    )


def test_resume_invokes_control_and_returns_refreshed_fragment():
    view = FakeBoardView(_board_with_retired(), {"tsk_r": RETIRED})
    control = FakeTaskControl(resume_result=True)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_r/resume")

    assert response.status_code == 200
    assert control.resumed == ["tsk_r"]
    assert "tsk_r" in response.text


def test_resume_returns_404_when_control_reports_nothing():
    view = FakeBoardView(_board_with_retired(), {"tsk_r": RETIRED})
    control = FakeTaskControl(resume_result=False)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_r/resume")

    assert response.status_code == 404
    assert control.resumed == ["tsk_r"]
```

If `Board` rejects `_board_with_retired()` for a missing argument, copy `_board_with_broken()` (~line 492) exactly and swap its columns — it is the same helper shape and carries whatever fields this version of `Board` requires.

Two assertions above depend on the exact badge markup Task 5 writes (`<span class="badge__step">development</span>retired`) and on the `data-search` term (`retired development`). They are written to match Task 5's template verbatim — do not change them; make the template match.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_html.py -q -k "retired or resume"`
Expected: FAIL — 404 on `/tasks/tsk_r/resume` and `is-retired` absent from the card.

- [ ] **Step 4: Implement the filter**

In `src/harness/api/routes.py`, extend the `harness.models` import to include `is_retired_failure` and `resumable_failure`, then add after the `TEMPLATES.env.filters["outcome_step"] = _outcome_step` line (~130):

```python
def _retired_failure(task: Task) -> FailureTrace | None:
    """The failure a `done` task actually ended on, or None for a real
    completion.

    ADR-0024 puts both kinds of ending in `done` and leaves the difference in
    history; without this the accent chain reads a retired failure's leftover
    `last_outcome == "done"` and paints the card green, asserting the one thing
    that is not true of it.
    """
    if not is_retired_failure(task):
        return None
    return failure_trace(task)


TEMPLATES.env.filters["retired_failure"] = _retired_failure
```

Add `FailureTrace` and `failure_trace` to the same `harness.models` import.

- [ ] **Step 5: Implement the fragment context and the route**

In `_task_fragment` (~line 578), pass the trace in — the detail panel needs the button to work for a `failed/` task too, so it uses `resumable_failure`, not the `done`-only filter:

```python
    def _task_fragment(request: Request, task_id: str) -> HTMLResponse:
        found = view.get(task_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} does not exist")
        return TEMPLATES.TemplateResponse(
            request=request,
            name="_task.html",
            context={
                "task": found,
                "artifacts": artifacts.list(task_id),
                "retired": resumable_failure(found),
            },
        )
```

Add the route immediately after `restart_task` (~line 618):

```python
    @router.post("/tasks/{task_id}/resume", response_class=HTMLResponse)
    def resume_task(request: Request, task_id: str) -> HTMLResponse:
        if not control.resume(task_id):
            raise HTTPException(
                status_code=404, detail=f"task {task_id} is not a resumable failure"
            )
        # Same shape as restart: the projection updated synchronously off the
        # emitted event, so the refreshed fragment shows the task in `todo`.
        return _task_fragment(request, task_id)
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_api_html.py -q`
Expected: the `resume` route tests PASS; the three card tests still FAIL (the templates come next). If `test_resume_button_shown_only_for_a_resumable_failure` also still fails, that is expected too.

- [ ] **Step 7: Commit**

```bash
git add src/harness/api/routes.py tests/test_api_html.py
git commit -m "feat: expose a resume route and a retired-failure filter to the board"
```

---

### Task 5: The board stops lying

**Files:**
- Modify: `src/harness/api/templates/_columns.html` (accent block ~line 27, `card__meta` ~line 42)
- Modify: `src/harness/api/templates/_task.html` (header ~line 6, Info list ~line 30)
- Modify: `src/harness/api/static/app.css` (`:root` ~line 36, dark block ~line 65, `.card.is-*` ~line 242, `.badge.*` ~line 268)
- Test: `tests/test_api_html.py` (the failing card tests from Task 4)

**Interfaces:**
- Consumes: the `retired_failure` jinja filter and the `retired` context key (Task 4).
- Produces: nothing other tasks read.

- [ ] **Step 1: Run the card tests to confirm they still fail**

Run: `.venv/bin/pytest tests/test_api_html.py -q -k retired`
Expected: FAIL on `is-retired`.

- [ ] **Step 2: Add the accent and the badge to the card**

In `_columns.html`, bind the trace once and put `is-retired` **ahead of** `is-done` in the chain. Replace the `{% set accent = ... %}` block with:

```jinja
  {% set retired = task | retired_failure %}
  {% set accent = 'is-failed' if task.status == 'failed'
     else 'is-working' if task.lock_id
     else 'is-retired' if retired
     else 'is-done' if task.last_outcome == 'done' and col.kind == 'terminal'
     else 'is-changes' if task.last_outcome == 'request_changes'
     else '' %}
```

Append the marker to `data-search` so the fulltext filter finds these — change the `data-search` attribute to end with `~ ' ' ~ ('retired ' ~ retired.failed_step if retired else '')`:

```jinja
       data-search="{{ ((task.data.title or task.id) ~ ' ' ~ task.id ~ ' ' ~ (task.repository | basename) ~ ' ' ~ (task.worktree | basename) ~ ' ' ~ (task.last_outcome or '') ~ ' ' ~ ('retired ' ~ retired.failed_step if retired else '')) | lower }}"
```

Add the badge as the first entry inside `<div class="card__meta">`, before the `processing` badge:

```jinja
      {% if retired %}
      <span class="badge retired"
            title="{{ retired.reason or 'the self-healer took this failure over' }}">
        <span class="badge__step">{{ retired.failed_step }}</span>retired</span>
      {% endif %}
```

The badge renders as `development · retired` — the `·` comes from the existing `.badge__step::after` rule, the same separator the outcome badge already uses. Task 4's tests assert this markup verbatim (`"development</span>retired"`, and `"retired development"` inside `data-search`), so write the template exactly as above rather than paraphrasing the class names.

The badge deliberately does **not** say "timed out": a timeout is only why *these six* failed. The reason goes in the `title`, the step name on the face.

- [ ] **Step 3: Add the detail row and the Resume button**

In `_task.html`, add the button after the existing Restart block:

```jinja
    {% if retired %}
    <button class="btn small primary" hx-post="/tasks/{{ task.id }}/resume"
            hx-target="#detail"
            hx-swap="innerHTML">Resume at {{ retired.failed_step }}</button>
    {% endif %}
```

Add the Info row after the `lastOutcome` `<li>`:

```jinja
        {% if retired %}
        <li><span class="k">retired</span>
            <span class="v">failed at <code>{{ retired.failed_step }}</code>{% if retired.reason %} — {{ retired.reason }}{% endif %}</span></li>
        {% endif %}
```

- [ ] **Step 4: Add the CSS**

In `app.css`, add to the light `:root` block beside the other pairs (~line 36-39):

```css
  --retired-bg: #f1eef8;  --retired-fg: #6b4fa8;
```

Add to the dark block (~line 65-68):

```css
    --retired-bg: #241a3d;  --retired-fg: #b295e8;
```

Add beside the other card accents (~line 242):

```css
.card.is-retired::before { background: var(--retired-fg); }
```

Add beside the other badges (~line 268):

```css
.badge.retired { background: var(--retired-bg); color: var(--retired-fg); }
```

Purple, deliberately: amber is already `--changes-fg` and red is `--failed-fg`. A retired failure is neither "came back for another pass" nor "still yours to fix", so it must not borrow either colour.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_api_html.py -q`
Expected: PASS, whole file — including `test_resume_button_shown_only_for_a_resumable_failure`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. `tests/test_api_html_mobile.py` renders the same templates — if it fails, read the assertion; a mobile-specific card layout may need the badge too.

- [ ] **Step 7: Commit**

```bash
git add src/harness/api/templates/_columns.html src/harness/api/templates/_task.html src/harness/api/static/app.css tests/test_api_html.py
git commit -m "feat: mark a retired failure on its card and offer to resume it"
```

---

### Task 6: The record

**Files:**
- Create: `docs/adr/0025-a-retired-failure-is-resumable.md`
- Modify: `CLAUDE.md` (invariants 23, 24, 30)
- Test: `tests/test_adr_docs.py` (existing — enforces the shape)

**Interfaces:**
- Consumes: everything above, as implemented.
- Produces: nothing code reads.

- [ ] **Step 1: Read the enforced ADR shape**

Run: `.venv/bin/pytest tests/test_adr_docs.py -q`
Expected: PASS now. The file enforces: filename `NNNN-slug.md` (lowercase, digits and hyphens only), a `# ADR-NNNN: ...` title, a `Status:` line, and the three headings `## Context`, `## Decision`, `## Consequences`. Every one is required.

- [ ] **Step 2: Write the ADR**

Create `docs/adr/0025-a-retired-failure-is-resumable.md`:

```markdown
# ADR-0025: A retired failure is resumable at the step it died at

Status: Accepted

Additive to ADR-0024 per ADR-0000's convention — that decision (a claimed
failure lands in `done`, a declined one stays in `failed`) is unchanged and
still authoritative.

## Context

ADR-0024 retires a claimed failure into `done/` with `status = END` and names
its own cost: "`done` holds two kinds of ending. Distinguishing them is a
history read (`actor == "failed-tasks"`), not a column read."

Nothing performed that read. Six `development` tasks that timed out at their
implementation step sat in `done` looking exactly like completions — worse than
neutral, because the card's accent chain reads the `last_outcome == "done"` left
over from the step that passed *before* the timeout and painted them green, in a
column whose own template comment forbids exactly that lie.

They were also unrecoverable. `Restart` is gated on `status == "failed"` and
`TaskControlService.restart` searches only `failed/`, so neither the button nor
a hand-rolled POST reached them. And a from-scratch restart would have been the
wrong tool anyway: `plan`, `design` and `architecture` had each succeeded and
written an artifact, and invariant #30 means the worktree still held all three.

## Decision

**A terminal failure can be resumed at the step it died at, and the board says
which tasks those are.**

- **`TaskControl` gains `resume`.** It is not a routing decision. It writes the
  `(status, lastOutcome)` pair the task held *before* it was dispatched into the
  failing step and returns it to the **inbox**; `route()` re-derives the failing
  step from that pair and the dispatcher places it, exactly as the first time.
  No queue is written directly (invariant #3), `route()` is unchanged, and a
  failure at the workflow's start step degenerates to `(None, None)` — which is
  what `restart` already produces — with no special case.
- **The failing step comes from history, never from `status`.** By the time a
  failure settles, `status` is the word `failed`; the step's name survives only
  as the `from_step` of the `-> failed` entry. Three pure functions in `models`
  expose it: `failure_trace` (how it failed and where to rewind), and
  `is_retired_failure` / `resumable_failure` — two separate predicates, because
  "is this `done` task a healed failure" and "was this task's current terminal
  position reached by failing" are different questions with different answers
  for a declined failure in `failed/` and for a completion that failed earlier
  in its life.
- **The distinction is a card marker, not a column.** The `healed` column stays
  retired. A retired failure suppresses the `is-done` accent, carries a
  `retired` badge naming its failed step, and is findable by the board's
  fulltext filter.
- **`resume` spans both terminal queues.** A declined failure in `failed/` has
  the same good-steps-already-done problem; `restart` remains the right tool
  when the prior artifacts are themselves suspect.

## Consequences

- **The healer's own diagnosis gets more accurate for free.** It rendered
  `task.status` as the failing step, so every heal request ever filed said
  `failed at step 'failed'`. It now reads the same trace.
- **Invariant #30 loses its justification's absoluteness.** "The original
  worktree is permanently inert once its task reaches a terminal state" is why
  the harness never cleans up worktrees; a resume revives one. The mechanism
  copes — invariant #31's reattach path resets only when local `HEAD` is behind
  or equal to `origin/<branch>` and raises on divergence — but if another task
  force-checked-out the same branch into a second worktree meanwhile, resuming
  the original leaves two live worktrees on one branch. Nothing here changes
  worktree handling; it makes the case reachable.
- **A resumed step writes a fresh artifact.** `next_attempt` counts existing
  `<step>-NN.md` files, so the record of the failed attempt is kept, consistent
  with ADR-0006.
- **The affordance has the retention window's lifetime.** Once terminal tasks
  are archived for age, an archived task is off every board column and its
  Resume button with it.
- **Two kinds of ending still share one column, on purpose.** ADR-0024's trade
  stands: the board answers "is anyone still on the hook for this?", and both
  answer no. What changes is that it no longer answers "did this succeed?"
  wrongly.
```

- [ ] **Step 3: Verify the ADR passes its own gate**

Run: `.venv/bin/pytest tests/test_adr_docs.py -q`
Expected: PASS.

- [ ] **Step 4: Update the invariants**

In `CLAUDE.md`, invariant **23** — replace the sentence starting "`restart` is a reset, not a routing decision:" with:

```
`restart` is a reset and `resume` a rewind — neither is a routing decision.
`restart` clears `status`/`lastOutcome` and re-inboxes a `failed` task; `resume`
writes the `(status, lastOutcome)` pair the task held before it was dispatched
into the step it failed at and re-inboxes it from `done/` or `failed/`. In both
cases the dispatcher decides where next (invariant #3 holds), and neither ever
writes into a step queue.
```

In invariant **24**, after the sentence ending "not in a status or column of its own." add:

```
That history stamp is now read back: `models.is_retired_failure` is what lets
the board mark a retired failure instead of rendering it as a completion, and
`models.resumable_failure` is what lets an operator resume one at the step it
died at (ADR-0025).
```

In invariant **30**, replace "Safe because the original worktree is permanently inert once its task reaches a terminal state." with:

```
Safe because the original worktree is inert for as long as its task stays
terminal — with one operator-driven exception: `TaskControl.resume` (ADR-0025)
returns a terminal failure to the step it died at and so revives its worktree.
Invariant 31's ancestry-aware reattach is what keeps that safe; a branch
force-checked-out into a second worktree meanwhile is the residual risk.
```

- [ ] **Step 5: Run the docs tests**

Run: `.venv/bin/pytest tests/test_adr_docs.py tests/test_claude_md_module_map.py tests/test_architecture.py -q`
Expected: PASS. `test_claude_md_module_map.py` checks the module-map table against the source tree — these edits touch invariants, not the map, so it must stay green.

- [ ] **Step 6: Run the full suite one final time**

Run: `.venv/bin/pytest -q`
Expected: PASS, everything.

- [ ] **Step 7: Commit**

```bash
git add docs/adr/0025-a-retired-failure-is-resumable.md CLAUDE.md
git commit -m "docs: record that a retired failure is resumable (ADR-0025)"
```

---

## Out of scope

Per the spec, deliberately not in this plan:

- **Re-queuing the six existing casualties.** Once `resume` ships, the operator presses the button; nothing auto-revives history.
- **Decomposing the `development` step.** `#141` raised the timeout ceiling to 5400s and deferred decomposition.
- **A bulk "resume all retired" action.** One task at a time, like `restart` and `delete`.
- **Reviving the `healed` column.** ADR-0024 stands.
