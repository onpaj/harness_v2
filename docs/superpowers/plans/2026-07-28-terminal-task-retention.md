# Terminal-Task Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `done`/`failed`/`healed` growing without bound by archiving every terminal task once it has been settled longer than a retention window.

**Architecture:** A fourth reconciler, `RetentionReconciler`, alongside `MergeReconciler` and `IssueReconciler`. It walks the three terminal queues, and for each task settled longer ago than the window runs the archive body those two already share — claim, append history, `status=ARCHIVED`, `transfer` into `archived/`, emit `"archived"`. `ProjectionSink` already maps that event to `BoardProjection.archive`, so the board and the SSE stream need no changes at all.

**Tech Stack:** Python ≥3.11, asyncio, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-28-terminal-task-retention-design.md`

## Global Constraints

- Python `>=3.11` (`pyproject.toml`); `datetime.fromisoformat` is parsed with the codebase's existing idiom `datetime.fromisoformat(text.replace("Z", "+00:00"))`.
- Tests run with `.venv/bin/pytest -q` from the repo root.
- `RetentionReconciler` is **orchestration**: it may import `harness.models`, `harness.ids` and `harness.ports.*` only — never `harness.drivers.*`. Enforced by `tests/test_architecture.py`.
- Every module under `src/harness/` must have its bare stem named somewhere in `CLAUDE.md`, or `tests/test_claude_md_module_map.py::test_every_source_module_is_named_in_claude_md` fails.
- Every environment variable `src/harness` reads as configuration must be listed in `tests/conftest.py::_HARNESS_ENVIRONMENT`, or the suite stops being hermetic.
- Default retention window: **2 days**. Env var: `HARNESS_RETENTION_DAYS`.
- Work on branch `spec/terminal-task-retention` (already created, spec already committed there). Four files were already dirty in the tree from other work — `src/harness/api/routes.py`, `src/harness/api/static/app.css`, `src/harness/api/templates/_columns.html`, `tests/test_api_html.py`. **Never `git add` those**; every commit below names its files explicitly.

---

### Task 1: The `RetentionReconciler` core

**Files:**
- Create: `src/harness/retention_reconciler.py`
- Create: `tests/test_retention_reconciler.py`
- Modify: `CLAUDE.md` (two lines: the orchestration layer row, and the module map)

**Interfaces:**
- Consumes: `harness.models.{ARCHIVED, HistoryEntry, Task, append_history}`, `harness.ids.new_lock_id`, `harness.ports.queue.TaskQueue`, `harness.ports.events.EventSink`, `harness.ports.clock.Clock` — all already exist.
- Produces:
  - `DEFAULT_RETENTION_DAYS: int = 2`
  - `RetentionReconciler(*, queues: list[TaskQueue], archived: TaskQueue, days: int, events: EventSink, clock: Clock)`
  - `RetentionReconciler.tick() -> bool` — True if anything was archived
  - `ACTOR = "retention"` (the `HistoryEntry.actor` value)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retention_reconciler.py`:

```python
"""RetentionReconciler — retires terminal tasks that settled long enough ago."""

from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.models import ARCHIVED, HistoryEntry, Task
from harness.retention_reconciler import DEFAULT_RETENTION_DAYS, RetentionReconciler

NOW = "2026-07-28T12:00:00Z"


def _task(task_id="tsk_1", *, settled=None, created="2026-07-01T10:00:00Z", status="done"):
    """A terminal task. `settled` becomes the last history entry's `at`."""
    history = ()
    if settled is not None:
        history = (
            HistoryEntry(
                at=settled, actor="consumer", from_step="plan", to_step=None, outcome="done"
            ),
        )
    return Task(
        id=task_id,
        workflow_template=None,
        created=created,
        status=status,
        history=history,
    )


def _build(*queues, days=2):
    archived = MemoryTaskQueue("archived")
    events = MemoryEventSink()
    reconciler = RetentionReconciler(
        queues=list(queues),
        archived=archived,
        days=days,
        events=events,
        clock=FakeClock(NOW),
    )
    return reconciler, archived, events


def test_task_settled_longer_ago_than_the_window_is_archived():
    done = MemoryTaskQueue("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-25T12:00:00Z"))  # 3 days

    assert reconciler.tick() is True
    assert done.list() == []
    assert [t.id for t in archived.list()] == ["tsk_1"]
    assert "archived" in events.names()


def test_task_settled_inside_the_window_stays_on_the_board():
    done = MemoryTaskQueue("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-27T12:00:00Z"))  # 1 day

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []
    assert "archived" not in events.names()


def test_age_is_measured_from_the_last_history_entry_not_creation():
    """Created a month ago, settled today: it must stay."""
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done)
    done.put(_task(created="2026-06-25T10:00:00Z", settled="2026-07-28T09:00:00Z"))

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []


def test_empty_history_falls_back_to_created():
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done)
    done.put(_task(created="2026-07-01T10:00:00Z"))  # no history, long past

    assert reconciler.tick() is True
    assert [t.id for t in archived.list()] == ["tsk_1"]


def test_unparseable_timestamp_leaves_the_task_alone():
    """Malformed data must not silently vanish off the board."""
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done)
    done.put(_task(created="not-a-timestamp"))

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []


def test_it_sweeps_every_queue_it_was_given():
    done = MemoryTaskQueue("done")
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    reconciler, archived, _ = _build(done, failed, healed)
    done.put(_task("tsk_d", settled="2026-07-20T12:00:00Z", status="done"))
    failed.put(_task("tsk_f", settled="2026-07-20T12:00:00Z", status="failed"))
    healed.put(_task("tsk_h", settled="2026-07-20T12:00:00Z", status="healed"))

    assert reconciler.tick() is True
    assert sorted(t.id for t in archived.list()) == ["tsk_d", "tsk_f", "tsk_h"]


def test_the_archived_task_carries_archived_status_and_an_audit_entry():
    done = MemoryTaskQueue("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-20T12:00:00Z"))

    reconciler.tick()

    task = archived.list()[0]
    assert task.status == ARCHIVED
    assert task.lock_id is None
    entry = task.history[-1]
    assert entry.actor == "retention"
    assert entry.at == NOW
    assert entry.from_step == "done"
    assert "retention" in (entry.reason or "")

    name, fields = next(pair for pair in events.events if pair[0] == "archived")
    assert fields["task_id"] == "tsk_1"
    assert fields["queue"] == "archived"
    assert fields["task"]["status"] == ARCHIVED


def test_a_lost_claim_race_is_skipped_not_fatal():
    class LosesEveryClaim(MemoryTaskQueue):
        def claim(self, task, lock_id):
            return None

    done = LosesEveryClaim("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-20T12:00:00Z"))

    assert reconciler.tick() is False
    assert archived.list() == []
    assert "archived" not in events.names()


def test_days_zero_archives_every_terminal_task_on_the_next_sweep():
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done, days=0)
    done.put(_task(settled=NOW))

    assert reconciler.tick() is True
    assert [t.id for t in archived.list()] == ["tsk_1"]


def test_the_default_window_is_two_days():
    assert DEFAULT_RETENTION_DAYS == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/pytest tests/test_retention_reconciler.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'harness.retention_reconciler'`.

- [ ] **Step 3: Write the implementation**

Create `src/harness/retention_reconciler.py`:

```python
"""RetentionReconciler: retires terminal tasks that settled long enough ago.

`done`, `failed` and `healed` are queues nobody consumes, so a task that
reaches one stays on the board for the lifetime of the root. Recurring
Processes settle several tasks a day, and the board grows without bound —
most visibly in the `No workflow` tab, where step-targeted Processes land.

This is the rule that says *when* a settled task should go. "Go" is the exact
`archived/` disposition `PrWatcher`, `MergeReconciler` and `IssueReconciler`
already share: off every board column, still gettable by id, file intact.
Nothing is deleted.

Age is measured from when the task **settled** — the last history entry, the
one recording the move into the terminal column — not from `created`. A task
that ran for three weeks and finished this morning must stay visible; keying
off `created` would archive it the moment it completed.

Step queues are never passed in. A task sitting in a step queue is backlog,
not garbage, however long it has sat there.

Knows only ports, models and `ids` — never a driver, like the reconcilers it
mirrors.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from harness.ids import new_lock_id
from harness.models import ARCHIVED, HistoryEntry, Task, append_history
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue

ACTOR = "retention"

DEFAULT_RETENTION_DAYS = 2
"""Days a settled task stays on the board. Two days holds a root producing a
handful of settled tasks a day at ~10 visible: yesterday's runs are still there
in the morning, and everything older is one `archived/` lookup away."""


def _moment(text: str) -> datetime | None:
    """Parse an ISO-8601 harness timestamp, or None if it will not parse.

    `.replace("Z", "+00:00")` is the codebase's existing idiom (see
    `ports/triggers.py`, `drivers/scheduled_trigger.py`). A naive result is
    pinned to UTC so it can never raise on comparison with an aware one —
    every harness timestamp comes from `Clock.now()` and carries the `Z`, but
    a hand-edited task file must not be able to crash the sweep.
    """
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def settled_at(task: Task) -> str:
    """When the task reached its terminal column: the last history entry's
    timestamp, falling back to `created` when the history is empty."""
    return task.history[-1].at if task.history else task.created


class RetentionReconciler:
    def __init__(
        self,
        *,
        queues: list[TaskQueue],
        archived: TaskQueue,
        days: int,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._queues = queues
        self._archived = archived
        self._days = days
        self._events = events
        self._clock = clock

    def tick(self) -> bool:
        """Sweep every terminal queue. True if anything was archived."""
        now = _moment(self._clock.now())
        if now is None:  # unreachable with a real Clock; not worth crashing over
            return False
        cutoff = now - timedelta(days=self._days)

        archived_any = False
        for queue in self._queues:
            for task in queue.list():
                moment = _moment(settled_at(task))
                if moment is None or moment > cutoff:
                    # Unparseable: leave it be, loudly visible on the board.
                    # Inside the window: not yet ours.
                    continue
                if self._archive(queue, task):
                    archived_any = True
        return archived_any

    def _archive(self, queue: TaskQueue, task: Task) -> bool:
        claimed = queue.claim(task, new_lock_id())
        if claimed is None:
            return False  # lost a race (a concurrent housekeeping loop)
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=claimed.status,
            to_step=None,
            reason=f"retention: settled more than {self._days}d ago",
        )
        resolved = append_history(replace(claimed, status=ARCHIVED, lock_id=None), entry)
        queue.transfer(resolved, self._archived)
        self._events.emit(
            "archived", task_id=task.id, queue="archived", task=resolved.to_dict()
        )
        return True
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/pytest tests/test_retention_reconciler.py -q
```

Expected: `10 passed`.

- [ ] **Step 5: Name the module in `CLAUDE.md`**

Two edits. First, add `retention_reconciler` to the orchestration layer row (currently line 159). Find:

```
| Orchestration | `dispatcher`, `consumer`, `source_poller`, `task_control`, `pr_watcher`, `merge_reconciler`, `issue_reconciler` — know only ports (and, for `pr_watcher`/`merge_reconciler`/`issue_reconciler`, the base `ids` module — not `workspace`/`forge`/`artifacts`/`agent`/`repos`/`drivers`) |
```

Replace with:

```
| Orchestration | `dispatcher`, `consumer`, `source_poller`, `task_control`, `pr_watcher`, `merge_reconciler`, `issue_reconciler`, `retention_reconciler` — know only ports (and, for `pr_watcher`/`merge_reconciler`/`issue_reconciler`/`retention_reconciler`, the base `ids` module — not `workspace`/`forge`/`artifacts`/`agent`/`repos`/`drivers`) |
```

Second, add a module-map bullet immediately after the `issue_reconciler.py` bullet (currently line 262). Find:

```
- `issue_reconciler.py` — `IssueReconciler`: the core that sweeps every live queue and archives a task whose source issue was closed or deleted out from under it (knows only ports/models/ids, mirrors `pr_watcher.py`)
```

Add on the following line:

```
- `retention_reconciler.py` — `RetentionReconciler`: the core that sweeps the terminal queues and archives a task settled longer ago than the retention window, so `done`/`failed`/`healed` stop growing without bound (knows only ports/models/ids, mirrors `issue_reconciler.py`)
```

- [ ] **Step 6: Run the guard tests**

```bash
.venv/bin/pytest tests/test_architecture.py tests/test_claude_md_module_map.py -q
```

Expected: all pass. If `test_every_source_module_is_named_in_claude_md` fails with `CLAUDE.md doesn't mention: ['retention_reconciler.py']`, Step 5's second edit did not land.

- [ ] **Step 7: Commit**

```bash
git add src/harness/retention_reconciler.py tests/test_retention_reconciler.py CLAUDE.md
git commit -m "feat: add RetentionReconciler, archiving terminal tasks by age"
```

---

### Task 2: Wire it into `app.py`

**Files:**
- Modify: `src/harness/app.py` (import; `Harness.__init__` param + attribute; `_retention_loop`; `run()` loop list; `build()` param + construction + `Harness(...)` argument)
- Create: `tests/test_retention_wiring.py`

**Interfaces:**
- Consumes: `RetentionReconciler`, `DEFAULT_RETENTION_DAYS` from Task 1.
- Produces:
  - `app.build(..., retention_days: int = DEFAULT_RETENTION_DAYS)` — new keyword-only parameter
  - `Harness.retention_reconciler: RetentionReconciler | None` — always a real instance when built via `build()`
  - `Harness._retention_loop(reconcile_interval, stop)` — hosted in `run()` at the existing 300s reconcile cadence

- [ ] **Step 1: Write the failing test**

Create `tests/test_retention_wiring.py`:

```python
"""`build()` always wires a RetentionReconciler over the terminal queues."""

from harness.app import build
from harness.drivers.memory import FakeClock, MemoryEventSink
from harness.models import ARCHIVED, HistoryEntry, Task
from harness.retention_reconciler import DEFAULT_RETENTION_DAYS

NOW = "2026-07-28T12:00:00Z"


def _settled(task_id, *, at, status):
    return Task(
        id=task_id,
        workflow_template=None,
        created="2026-07-01T10:00:00Z",
        status=status,
        history=(
            HistoryEntry(at=at, actor="consumer", from_step="plan", to_step=None),
        ),
    )


def test_build_always_wires_a_retention_reconciler(tmp_path):
    harness = build(tmp_path, "default", events=MemoryEventSink())
    assert harness.retention_reconciler is not None


def test_the_sweep_archives_old_terminal_tasks_from_all_three_queues(tmp_path):
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
        retention_days=2,
    )
    harness._done.put(_settled("tsk_d", at="2026-07-20T12:00:00Z", status="done"))
    harness._failed.put(_settled("tsk_f", at="2026-07-20T12:00:00Z", status="failed"))
    harness._healed.put(_settled("tsk_h", at="2026-07-20T12:00:00Z", status="healed"))

    assert harness.retention_reconciler.tick() is True

    assert harness._done.list() == []
    assert harness._failed.list() == []
    assert harness._healed.list() == []
    assert sorted(t.id for t in harness.archived.list()) == ["tsk_d", "tsk_f", "tsk_h"]
    assert all(t.status == ARCHIVED for t in harness.archived.list())


def test_the_sweep_never_touches_a_step_queue(tmp_path):
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
        retention_days=2,
    )
    step = next(iter(harness._step_queues))
    harness._step_queues[step].put(
        _settled("tsk_backlog", at="2026-06-01T12:00:00Z", status=step)
    )

    assert harness.retention_reconciler.tick() is False
    assert [t.id for t in harness._step_queues[step].list()] == ["tsk_backlog"]


def test_the_default_window_comes_from_the_reconciler_module(tmp_path):
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
    )
    harness._done.put(_settled("tsk_d", at="2026-07-27T12:00:00Z", status="done"))

    # 1 day old, inside the DEFAULT_RETENTION_DAYS=2 window
    assert DEFAULT_RETENTION_DAYS == 2
    assert harness.retention_reconciler.tick() is False
    assert [t.id for t in harness._done.list()] == ["tsk_d"]


def test_the_sweep_takes_the_task_off_the_board_but_leaves_it_gettable(tmp_path):
    """The whole reason for reusing `archived/`: `build()` composes the caller's
    sink with `ProjectionSink`, so the emitted `archived` event drives
    `BoardProjection.archive` with no extra wiring — off every column, still
    resolvable by id."""
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
        retention_days=2,
    )
    harness.projection.hydrate(
        inbox=harness._inbox,
        step_queues=harness._step_queues,
        done=harness._done,
        failed=harness._failed,
        archived=harness.archived,
        healed=harness._healed,
    )
    harness._done.put(_settled("tsk_old", at="2026-07-20T12:00:00Z", status="done"))
    harness.projection.apply("done", harness._done.list()[0])

    def on_board() -> list[str]:
        return [
            task.id
            for tab in harness.projection.snapshot().workflows
            for column in tab.columns
            for task in column.tasks
        ]

    assert "tsk_old" in on_board()

    harness.retention_reconciler.tick()

    assert "tsk_old" not in on_board()
    assert harness.projection.get("tsk_old") is not None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_retention_wiring.py -q
```

Expected: FAIL — `AttributeError: 'Harness' object has no attribute 'retention_reconciler'`, and `TypeError: build() got an unexpected keyword argument 'retention_days'`.

If `snapshot().workflows` or `column.tasks` turns out to be named differently, read `src/harness/ports/board.py` (`Board`, `BoardTab`, `BoardColumn`) and use the real attribute names — the assertion is what matters, not the traversal.

- [ ] **Step 3: Add the import**

In `src/harness/app.py`, next to the sibling reconciler imports (around line 62–64):

```python
from harness.pr_watcher import PrWatcher
```

Add after it:

```python
from harness.retention_reconciler import DEFAULT_RETENTION_DAYS, RetentionReconciler
```

- [ ] **Step 4: Add the constructor parameter and attribute**

In `Harness.__init__`'s signature, after `issue_reconciler: IssueReconciler | None = None,`:

```python
        retention_reconciler: RetentionReconciler | None = None,
```

And in the body, after `self.issue_reconciler = issue_reconciler`:

```python
        self.retention_reconciler = retention_reconciler
```

- [ ] **Step 5: Host the loop in `run()`**

In `Harness.run`, in the `loops = [...]` list, after the `issue_reconciler` block:

```python
            *(
                [self._issue_reconcile_loop(reconcile_interval, stop)]
                if self.issue_reconciler is not None
                else []
            ),
```

add:

```python
            *(
                [self._retention_loop(reconcile_interval, stop)]
                if self.retention_reconciler is not None
                else []
            ),
```

Then add the loop method immediately after `_issue_reconcile_loop` (which ends around line 450):

```python
    async def _retention_loop(
        self, reconcile_interval: float, stop: asyncio.Event
    ) -> None:
        # Shares the reconcilers' cadence. This is the cheapest sweep of the
        # three — three local `list()` calls over directories holding tens of
        # files, no remote API — and the slowest-moving: whether a task settled
        # two days ago does not change between ticks.
        assert self.retention_reconciler is not None
        while not stop.is_set():
            if not self.retention_reconciler.tick():
                await asyncio.sleep(reconcile_interval)
            else:
                await asyncio.sleep(0)
```

- [ ] **Step 6: Add the `build()` parameter and construction**

In `build()`'s signature, after `dropped_workflows: set[str] | None = None,`:

```python
    retention_days: int = DEFAULT_RETENTION_DAYS,
```

Then, immediately after the `issue_reconciler` block (which ends with its closing `)` around line 624), insert:

```python
    # Terminal queues are consumed by nobody, so without this a settled task
    # stays on the board for the lifetime of the root. Unlike the merge and
    # issue reconcilers this is *always* built: it needs no external service,
    # only the local queues and the clock.
    retention_reconciler = RetentionReconciler(
        queues=[done, failed, healed_queue],
        archived=archived,
        days=retention_days,
        events=events,
        clock=clock,
    )
```

Finally, in the `return Harness(` call, after `issue_reconciler=issue_reconciler,`:

```python
        retention_reconciler=retention_reconciler,
```

- [ ] **Step 7: Run the test to verify it passes**

```bash
.venv/bin/pytest tests/test_retention_wiring.py -q
```

Expected: `5 passed`.

- [ ] **Step 8: Run the full suite**

```bash
.venv/bin/pytest -q
```

Expected: all pass. `tests/test_app.py` and `tests/test_board_e2e.py` build harnesses with the default window and no old tasks, so nothing should move.

- [ ] **Step 9: Commit**

```bash
git add src/harness/app.py tests/test_retention_wiring.py
git commit -m "feat: wire the retention sweep into the reconcile loop"
```

---

### Task 3: `HARNESS_RETENTION_DAYS`

**Files:**
- Modify: `src/harness/cli.py` (new `_retention_days()` helper; pass `retention_days=` at the `build(` call in `_run`)
- Modify: `tests/conftest.py` (add the variable to `_HARNESS_ENVIRONMENT`)
- Modify: `tests/test_cli.py` (new tests)

**Interfaces:**
- Consumes: `DEFAULT_RETENTION_DAYS` from Task 1; `build(..., retention_days=...)` from Task 2.
- Produces: `cli._retention_days() -> int`.

**Ordering note:** `tests/test_hermetic_environment.py` derives the variable list from the source and checks it **both ways** — a config variable the package reads but conftest doesn't list fails, *and so does a conftest entry the package doesn't read*. So between Step 1 and Step 4 that test is red. This is expected; do not "fix" it by reverting Step 1. Only Step 7 (after the source reads the variable) runs the full suite.

- [ ] **Step 1: Make the suite hermetic against the new variable**

In `tests/conftest.py`, the `_HARNESS_ENVIRONMENT` tuple is alphabetically ordered. Change:

```python
    "GITHUB_TOKEN",
    "HARNESS_HOME",
    "JIRA_API_TOKEN",
```

to:

```python
    "GITHUB_TOKEN",
    "HARNESS_HOME",
    "HARNESS_RETENTION_DAYS",
    "JIRA_API_TOKEN",
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_retention_days_defaults_when_unset():
    from harness.cli import _retention_days
    from harness.retention_reconciler import DEFAULT_RETENTION_DAYS

    assert _retention_days() == DEFAULT_RETENTION_DAYS


def test_retention_days_reads_the_environment(monkeypatch):
    from harness.cli import _retention_days

    monkeypatch.setenv("HARNESS_RETENTION_DAYS", "5")
    assert _retention_days() == 5


def test_retention_days_accepts_zero(monkeypatch):
    """0 is a real setting — archive every terminal task on the next sweep."""
    from harness.cli import _retention_days

    monkeypatch.setenv("HARNESS_RETENTION_DAYS", "0")
    assert _retention_days() == 0


def test_unparseable_retention_days_warns_and_falls_back(monkeypatch, capsys):
    from harness.cli import _retention_days
    from harness.retention_reconciler import DEFAULT_RETENTION_DAYS

    monkeypatch.setenv("HARNESS_RETENTION_DAYS", "a fortnight")
    assert _retention_days() == DEFAULT_RETENTION_DAYS
    assert "HARNESS_RETENTION_DAYS" in capsys.readouterr().err


def test_negative_retention_days_warns_and_falls_back(monkeypatch, capsys):
    from harness.cli import _retention_days
    from harness.retention_reconciler import DEFAULT_RETENTION_DAYS

    monkeypatch.setenv("HARNESS_RETENTION_DAYS", "-1")
    assert _retention_days() == DEFAULT_RETENTION_DAYS
    assert "HARNESS_RETENTION_DAYS" in capsys.readouterr().err
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
.venv/bin/pytest tests/test_cli.py -k retention -q
```

Expected: FAIL — `ImportError: cannot import name '_retention_days' from 'harness.cli'`.

- [ ] **Step 4: Write the helper**

In `src/harness/cli.py`, add near the other configuration helpers (anywhere above `_run`; `os` and `sys` are already imported):

```python
def _retention_days() -> int:
    """The terminal-task retention window, from `HARNESS_RETENTION_DAYS`.

    A tuning knob, not a secret — so a bad value is a non-fatal startup
    warning and the default, never an exit. The harness refusing to start
    over a typo in a housekeeping number would be a worse failure than the
    window being wrong for one run.

    `0` is deliberately valid: it archives every terminal task on the next
    sweep, which is the "clear the board now" setting.
    """
    raw = os.environ.get("HARNESS_RETENTION_DAYS")
    if raw is None:
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw)
    except ValueError:
        print(
            f"warning: HARNESS_RETENTION_DAYS={raw!r} is not an integer; "
            f"using {DEFAULT_RETENTION_DAYS}",
            file=sys.stderr,
        )
        return DEFAULT_RETENTION_DAYS
    if days < 0:
        print(
            f"warning: HARNESS_RETENTION_DAYS={raw!r} is negative; "
            f"using {DEFAULT_RETENTION_DAYS}",
            file=sys.stderr,
        )
        return DEFAULT_RETENTION_DAYS
    return days
```

Add the import alongside the other `harness.*` imports at the top of `cli.py`:

```python
from harness.retention_reconciler import DEFAULT_RETENTION_DAYS
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/pytest tests/test_cli.py -k retention -q
```

Expected: `5 passed`.

- [ ] **Step 6: Pass it through at the call site**

In `cli._run`, in the `harness = build(` call (around line 2402), after `dropped_workflows=dropped_workflows,`:

```python
            retention_days=_retention_days(),
```

- [ ] **Step 7: Run the full suite**

```bash
.venv/bin/pytest -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/harness/cli.py tests/conftest.py tests/test_cli.py
git commit -m "feat: configure the retention window with HARNESS_RETENTION_DAYS"
```

---

### Task 4: Document it

**Files:**
- Modify: `CLAUDE.md` (the operator-facing environment section)

**Interfaces:**
- Consumes: everything above. Produces: nothing code-facing.

- [ ] **Step 1: Update the hermetic-environment paragraph**

`CLAUDE.md` around line 119 lists the config variables by name. Find:

```
`src/harness` reads as config (`GITHUB_TOKEN`, `HARNESS_HOME`, `SLACK_WEBHOOK_URL`,
`JIRA_*`) before each test; a test that wants one sets it itself with
```

Replace with:

```
`src/harness` reads as config (`GITHUB_TOKEN`, `HARNESS_HOME`, `SLACK_WEBHOOK_URL`,
`HARNESS_RETENTION_DAYS`, `JIRA_*`) before each test; a test that wants one sets it itself with
```

- [ ] **Step 2: Document the knob itself**

Add a short operator-facing paragraph in the same part of `CLAUDE.md` that covers runtime behaviour (near the `merge_reconciler`/`issue_reconciler` prose, or wherever the service's housekeeping sweeps are described):

```markdown
**Terminal-task retention.** `done`/`failed`/`healed` are queues nobody consumes,
so without a sweep a settled task stays on the board forever — most visibly in the
`No workflow` tab, where step-targeted Processes land their completed runs.
`RetentionReconciler` archives a task once it has been settled longer than
`HARNESS_RETENTION_DAYS` (default `2`), measuring from the last history entry, not
from `created`. `0` archives every terminal task on the next sweep; a bad value
warns to stderr and falls back to the default rather than failing the run. Step
queues are never swept — a task sitting in a step queue is backlog, not garbage.
Archived tasks keep their file in `archived/` and stay resolvable by id.
```

- [ ] **Step 3: Run the docs guards**

```bash
.venv/bin/pytest tests/test_claude_md_module_map.py tests/test_adr_docs.py tests/test_docs_site.py -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document HARNESS_RETENTION_DAYS"
```

---

## Verification before calling it done

- [ ] `.venv/bin/pytest -q` — full suite green, output pasted into the completion report, not summarised.
- [ ] `git status --short` shows only the four pre-existing dirty files from other work: `src/harness/api/routes.py`, `src/harness/api/static/app.css`, `src/harness/api/templates/_columns.html`, `tests/test_api_html.py`.
- [ ] `git log --oneline spec/terminal-task-retention` shows the spec commit plus four implementation commits.

## Rollout note — do not skip

`~/harness-root` is the live root and the running service self-upgrades within 30 minutes of a release landing on `main` (the launchd `com.harness` service, `~/harness-app`). The first sweep after upgrade will archive every terminal task settled more than 2 days ago — expect roughly 8–10 of the 15 in `No workflow` to move into `~/harness-root/archived/` on the first tick.

That is the intended effect and it is reversible (the files are intact in `archived/`), but take a snapshot before pushing:

```bash
cp -R ~/harness-root/done ~/harness-root/failed ~/harness-root/healed /tmp/harness-terminal-backup-2026-07-28
```
