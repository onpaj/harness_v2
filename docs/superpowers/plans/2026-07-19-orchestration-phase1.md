# Fáze 1 — orchestrační smyčka: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postavit jednoprocesovou orchestrační smyčku, ve které task proteče workflow od `start` do `end` — dispatcher routuje, consumeři vykonávají — a každá pohyblivá část leží za portem, který jde vyměnit záměnou driveru.

**Architecture:** Všechno je `TaskQueue` (inbox, fronty kroků, `done/`, `failed/`). Dispatcher rozhoduje *kam* přes čistou funkci `route()`, `ConsumerBehavior` rozhoduje *co se stalo*, consumer je tenká obálka bez vlastního rozhodování. Jeden proces, asyncio, jeden dispatcher task a jeden consumer task na krok. `claim()` je atomický `rename` do `<queue>/.processing/`, což zároveň řeší lease, idempotenci i původ po pádu.

**Tech Stack:** Python 3.11, stdlib only v runtime (žádné produkční závislosti), `pytest` + `pytest-asyncio` v dev, `argparse` pro CLI.

Spec: `docs/superpowers/specs/2026-07-19-orchestration-phase1-design.md`

## Global Constraints

- Python **3.11**, interpret `/Users/rem/.local/bin/python3.11`. Na stroji **není `uv`** — plain `venv` + `pip install -e ".[dev]"`.
- Balík se jmenuje **`harness`**, žije v `src/harness/`.
- **Runtime nemá žádné produkční závislosti.** `dependencies = []`.
- **Závislosti tečou striktně dolů.** `models.py` neimportuje nic z balíku. `ports/` neimportuje `drivers/`. `dispatcher.py` a `consumer.py` neimportují `drivers/` — veškeré wiring je v `app.py`. Task 11 to hlídá testem.
- **JSON klíče tasku jsou camelCase** (`workflowTemplate`, `lastOutcome`, `lockId`), pythonní atributy snake_case. Převod je v `to_dict` / `from_dict`.
- **Consumer nesmí obsahovat žádnou větev závislou na hodnotě outcome.** Žádné `if outcome ==`.
- **Consumer nikdy nemění `status`.** Mění ho výhradně dispatcher.
- **Dispatcher nikdy nečte `data`, `repository` ani `worktree`.**
- Vyhrazené jméno terminálního uzlu je `"end"`.
- Testy nesmí sahat na skutečný čas ani spát v reálném čase — `Clock` je port právě kvůli tomu.
- **Commitujeme přímo do `main`.** Žádné branche, žádné PR. Tato konvence platí pro repo harnessu samotného.
- Čas je ISO 8601 UTC se sufixem `Z`.

---

### Task 1: Scaffolding a datové modely

**Files:**
- Create: `pyproject.toml`
- Create: `src/harness/__init__.py`
- Create: `src/harness/ids.py`
- Create: `src/harness/models.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nic
- Produces: `Outcome` (StrEnum s `DONE`/`REQUEST_CHANGES`), konstanta `END = "end"`, `HistoryEntry`, `Task`, `Transition`, `Workflow`, `MoveTo`, `Finished`, `Failed`, alias `Decision`, funkce `append_history(task, entry) -> Task`, `new_task_id() -> str`, `new_lock_id() -> str`. Všechny dataclassy jsou `frozen=True`; úpravy přes `dataclasses.replace`.

- [ ] **Step 1: Založ `pyproject.toml`**

```toml
[project]
name = "harness"
version = "0.1.0"
description = "Orchestrační harness pro více agentů — fáze 1"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
harness = "harness.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Vytvoř venv a nainstaluj**

```bash
cd ~/harness_v2
rm -rf .venv
/Users/rem/.local/bin/python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Expected: `Successfully installed harness-0.1.0 ...`

- [ ] **Step 3: Napiš padající test modelů**

Create `tests/test_models.py`:

```python
from harness.models import (
    END,
    Failed,
    Finished,
    HistoryEntry,
    MoveTo,
    Outcome,
    Task,
    Transition,
    Workflow,
    append_history,
)


def test_task_roundtrips_through_camelcase_json():
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="app-backend",
        status="design",
        last_outcome="done",
        lock_id="lck_1",
        data={"request": "add rate limiting"},
    )

    raw = task.to_dict()

    assert raw["workflowTemplate"] == "default"
    assert raw["lastOutcome"] == "done"
    assert raw["lockId"] == "lck_1"
    assert Task.from_dict(raw) == task


def test_new_task_has_null_status_and_empty_history():
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-19T10:00:00Z")

    assert task.status is None
    assert task.last_outcome is None
    assert task.lock_id is None
    assert task.history == ()
    assert task.data == {}


def test_history_entry_uses_reserved_json_keys():
    entry = HistoryEntry(
        at="2026-07-19T10:00:05Z",
        actor="dispatcher",
        from_step="design",
        to_step="architecture",
        outcome="done",
    )

    raw = entry.to_dict()

    assert raw["from"] == "design"
    assert raw["to"] == "architecture"
    assert HistoryEntry.from_dict(raw) == entry


def test_history_entry_omits_reason_when_absent():
    entry = HistoryEntry(at="t", actor="dispatcher", from_step=None, to_step="plan")

    assert "reason" not in entry.to_dict()


def test_append_history_returns_new_task():
    task = Task(id="tsk_1", workflow_template="default", created="t")
    entry = HistoryEntry(at="t", actor="dispatcher", from_step=None, to_step="plan")

    updated = append_history(task, entry)

    assert updated.history == (entry,)
    assert task.history == ()


def test_workflow_target_finds_transition():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="done", to_step="design"),
            Transition(from_step="review", on="request_changes", to_step="development"),
        ),
    )

    assert workflow.target("plan", "done") == "design"
    assert workflow.target("review", "request_changes") == "development"
    assert workflow.target("plan", "request_changes") is None


def test_workflow_steps_excludes_end():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="done", to_step="review"),
            Transition(from_step="review", on="done", to_step=END),
        ),
    )

    assert workflow.steps() == ("plan", "review")


def test_outcome_values():
    assert Outcome.DONE.value == "done"
    assert Outcome.REQUEST_CHANGES.value == "request_changes"


def test_decisions_carry_their_payload():
    assert MoveTo("design").step == "design"
    assert Failed("nope").reason == "nope"
    assert Finished() == Finished()
```

- [ ] **Step 4: Spusť test, ať vidíš, že padá**

Run: `.venv/bin/pytest tests/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.models'`

- [ ] **Step 5: Napiš `src/harness/__init__.py`**

```python
"""Orchestrační harness pro více agentů."""
```

- [ ] **Step 6: Napiš `src/harness/ids.py`**

```python
"""Generátory identit. Jediné místo, kde vzniká náhoda."""

from __future__ import annotations

import uuid


def new_task_id() -> str:
    return f"tsk_{uuid.uuid4().hex[:16]}"


def new_lock_id() -> str:
    return f"lck_{uuid.uuid4().hex[:16]}"
```

- [ ] **Step 7: Napiš `src/harness/models.py`**

```python
"""Datové modely. Tento modul neimportuje nic z balíku harness."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Union

END = "end"
"""Vyhrazené jméno terminálního uzlu. Nemá frontu ani odchozí hrany."""


class Outcome(str, Enum):
    """Jediné hodnoty, které smí ConsumerBehavior vrátit."""

    DONE = "done"
    REQUEST_CHANGES = "request_changes"


@dataclass(frozen=True)
class HistoryEntry:
    """Jeden řádek audit logu tasku."""

    at: str
    actor: str
    from_step: str | None
    to_step: str | None
    outcome: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "at": self.at,
            "actor": self.actor,
            "from": self.from_step,
            "to": self.to_step,
        }
        if self.outcome is not None:
            raw["outcome"] = self.outcome
        if self.reason is not None:
            raw["reason"] = self.reason
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HistoryEntry:
        return cls(
            at=raw["at"],
            actor=raw["actor"],
            from_step=raw.get("from"),
            to_step=raw.get("to"),
            outcome=raw.get("outcome"),
            reason=raw.get("reason"),
        )


@dataclass(frozen=True)
class Task:
    """Jednotka práce. Putuje mezi frontami, nese svá metadata."""

    id: str
    workflow_template: str
    created: str
    repository: str | None = None
    worktree: str | None = None
    status: str | None = None
    last_outcome: str | None = None
    lock_id: str | None = None
    history: tuple[HistoryEntry, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repository": self.repository,
            "worktree": self.worktree,
            "workflowTemplate": self.workflow_template,
            "status": self.status,
            "lastOutcome": self.last_outcome,
            "lockId": self.lock_id,
            "created": self.created,
            "history": [entry.to_dict() for entry in self.history],
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        return cls(
            id=raw["id"],
            workflow_template=raw["workflowTemplate"],
            created=raw["created"],
            repository=raw.get("repository"),
            worktree=raw.get("worktree"),
            status=raw.get("status"),
            last_outcome=raw.get("lastOutcome"),
            lock_id=raw.get("lockId"),
            history=tuple(
                HistoryEntry.from_dict(entry) for entry in raw.get("history", [])
            ),
            data=raw.get("data") or {},
        )


@dataclass(frozen=True)
class Transition:
    """Jedna hrana state machine: z kroku, na outcome, do kroku."""

    from_step: str
    on: str
    to_step: str


@dataclass(frozen=True)
class Workflow:
    name: str
    start: str
    transitions: tuple[Transition, ...]

    def target(self, status: str, outcome: str) -> str | None:
        """Cíl hrany, nebo None když žádná nesedí."""
        for transition in self.transitions:
            if transition.from_step == status and transition.on == outcome:
                return transition.to_step
        return None

    def steps(self) -> tuple[str, ...]:
        """Všechny kroky, které potřebují frontu. END mezi ně nepatří."""
        found: list[str] = []
        for transition in self.transitions:
            for step in (transition.from_step, transition.to_step):
                if step != END and step not in found:
                    found.append(step)
        if self.start != END and self.start not in found:
            found.append(self.start)
        return tuple(found)


@dataclass(frozen=True)
class MoveTo:
    """Task jde do fronty kroku."""

    step: str


@dataclass(frozen=True)
class Finished:
    """Task doputoval na END."""


@dataclass(frozen=True)
class Failed:
    """Task nelze směrovat."""

    reason: str


Decision = Union[MoveTo, Finished, Failed]


def append_history(task: Task, entry: HistoryEntry) -> Task:
    return replace(task, history=task.history + (entry,))
```

- [ ] **Step 8: Spusť testy**

Run: `.venv/bin/pytest tests/test_models.py -q`
Expected: PASS, 9 passed

- [ ] **Step 9: Založ `.gitignore` doplnění a commitni**

Ověř, že `.gitignore` obsahuje `.venv/`, `__pycache__/`, `*.egg-info/`, `.pytest_cache/`. Pokud ano, nech ho být.

```bash
cd ~/harness_v2
git add pyproject.toml src/harness/__init__.py src/harness/ids.py src/harness/models.py tests/__init__.py tests/test_models.py
git commit -m "feat: datové modely tasku, workflow a rozhodnutí routeru"
```

---

### Task 2: Router

**Files:**
- Create: `src/harness/router.py`
- Create: `tests/test_router.py`

**Interfaces:**
- Consumes: `Task`, `Workflow`, `Transition`, `Decision`, `MoveTo`, `Finished`, `Failed`, `END` z `harness.models`
- Produces: `route(task: Task, workflow: Workflow) -> Decision` — čistá funkce, žádné I/O, žádný čas

- [ ] **Step 1: Napiš padající tabulkové testy**

Create `tests/test_router.py`:

```python
import pytest

from harness.models import END, Failed, Finished, MoveTo, Task, Transition, Workflow
from harness.router import route

WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="design"),
        Transition(from_step="design", on="done", to_step="architecture"),
        Transition(from_step="architecture", on="done", to_step="development"),
        Transition(from_step="development", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
        Transition(from_step="review", on="request_changes", to_step="development"),
    ),
)


def task(status=None, last_outcome=None) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status=status,
        last_outcome=last_outcome,
    )


def test_new_task_goes_to_start():
    assert route(task(), WORKFLOW) == MoveTo("plan")


@pytest.mark.parametrize(
    ("status", "outcome", "expected"),
    [
        ("plan", "done", "design"),
        ("design", "done", "architecture"),
        ("architecture", "done", "development"),
        ("development", "done", "review"),
    ],
)
def test_forward_edges(status, outcome, expected):
    assert route(task(status, outcome), WORKFLOW) == MoveTo(expected)


def test_backward_edge():
    assert route(task("review", "request_changes"), WORKFLOW) == MoveTo("development")


def test_end_node_finishes():
    assert route(task("review", "done"), WORKFLOW) == Finished()


def test_missing_edge_fails():
    decision = route(task("plan", "request_changes"), WORKFLOW)

    assert isinstance(decision, Failed)
    assert "plan" in decision.reason
    assert "request_changes" in decision.reason


def test_unknown_status_fails():
    decision = route(task("nonsense", "done"), WORKFLOW)

    assert isinstance(decision, Failed)
    assert "nonsense" in decision.reason


def test_status_without_outcome_is_inconsistent():
    decision = route(task("design", None), WORKFLOW)

    assert isinstance(decision, Failed)
    assert "lastOutcome" in decision.reason


def test_retry_edge_pointing_at_itself():
    workflow = Workflow(
        name="retry",
        start="plan",
        transitions=(Transition(from_step="plan", on="request_changes", to_step="plan"),),
    )

    assert route(task("plan", "request_changes"), workflow) == MoveTo("plan")
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_router.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.router'`

- [ ] **Step 3: Napiš `src/harness/router.py`**

```python
"""Routovací rozhodnutí. Čistá funkce — žádné I/O, žádný čas, žádný stav."""

from __future__ import annotations

from harness.models import END, Decision, Failed, Finished, MoveTo, Task, Workflow


def route(task: Task, workflow: Workflow) -> Decision:
    """Kam má task jít dál.

    Rozhoduje výhradně podle dvojice (status, lastOutcome). Nikdy nečte
    data, repository ani worktree.
    """
    if task.status is None:
        return MoveTo(workflow.start)

    if task.last_outcome is None:
        return Failed(
            f"task má status {task.status!r}, ale žádný lastOutcome"
        )

    target = workflow.target(task.status, task.last_outcome)
    if target is None:
        return Failed(
            f"workflow {workflow.name!r} nemá hranu z {task.status!r} "
            f"na {task.last_outcome!r}"
        )

    if target == END:
        return Finished()

    return MoveTo(target)
```

- [ ] **Step 4: Spusť testy**

Run: `.venv/bin/pytest tests/test_router.py -q`
Expected: PASS, 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/harness/router.py tests/test_router.py
git commit -m "feat: router jako čistá funkce nad workflow state machine"
```

---

### Task 3: Porty a in-memory drivery

**Files:**
- Create: `src/harness/ports/__init__.py`
- Create: `src/harness/ports/queue.py`
- Create: `src/harness/ports/workflows.py`
- Create: `src/harness/ports/strategy.py`
- Create: `src/harness/ports/behavior.py`
- Create: `src/harness/ports/events.py`
- Create: `src/harness/ports/clock.py`
- Create: `src/harness/drivers/__init__.py`
- Create: `src/harness/drivers/memory.py`
- Create: `tests/test_memory_drivers.py`

**Interfaces:**
- Consumes: `Task`, `Workflow`, `Outcome` z `harness.models`
- Produces:
  - `TaskQueue` (ABC): `name: str`, `list() -> list[Task]`, `claim(task, lock_id) -> Task | None`, `put(task) -> None`, `transfer(task, destination: TaskQueue) -> None`, `recover() -> int`
  - `WorkflowRepository` (ABC): `get(name: str) -> Workflow`; výjimka `WorkflowNotFound(Exception)`
  - `EnqueueStrategy` (ABC): `select(tasks: list[Task]) -> Task | None`
  - `ConsumerBehavior` (ABC): `async run(task: Task) -> Outcome`
  - `EventSink` (ABC): `emit(name: str, **fields) -> None`
  - `Clock` (ABC): `now() -> str`, `async sleep(seconds: float) -> None`
  - `MemoryTaskQueue`, `MemoryWorkflowRepository`, `MemoryEventSink` (má `.events: list[tuple[str, dict]]`), `FakeClock` (má `.instant: str`, `.slept: list[float]`), `ScriptedBehavior(outcomes: dict[str, list[Outcome]])`

- [ ] **Step 1: Napiš padající testy in-memory driverů**

Create `tests/test_memory_drivers.py`:

```python
import pytest

from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    MemoryWorkflowRepository,
)
from harness.models import Task, Transition, Workflow
from harness.ports.workflows import WorkflowNotFound


def make_task(task_id="tsk_1") -> Task:
    return Task(id=task_id, workflow_template="default", created="2026-07-19T10:00:00Z")


def test_put_then_list():
    queue = MemoryTaskQueue("tasks")
    task = make_task()

    queue.put(task)

    assert queue.list() == [task]


def test_claim_removes_from_list_and_stamps_lock():
    queue = MemoryTaskQueue("tasks")
    queue.put(make_task())

    claimed = queue.claim(queue.list()[0], "lck_1")

    assert claimed is not None
    assert claimed.lock_id == "lck_1"
    assert queue.list() == []


def test_claim_twice_loses_the_race():
    queue = MemoryTaskQueue("tasks")
    queue.put(make_task())
    task = queue.list()[0]
    queue.claim(task, "lck_1")

    assert queue.claim(task, "lck_2") is None


def test_transfer_moves_between_queues():
    source = MemoryTaskQueue("tasks")
    destination = MemoryTaskQueue("design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(claimed, destination)

    assert source.list() == []
    assert destination.list() == [claimed]


def test_recover_returns_claimed_tasks_and_clears_lock():
    queue = MemoryTaskQueue("tasks")
    queue.put(make_task())
    queue.claim(queue.list()[0], "lck_1")

    recovered = queue.recover()

    assert recovered == 1
    assert queue.list()[0].lock_id is None


def test_workflow_repository_get_and_miss():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step="end"),),
    )
    repository = MemoryWorkflowRepository({"default": workflow})

    assert repository.get("default") == workflow
    with pytest.raises(WorkflowNotFound):
        repository.get("missing")


def test_event_sink_records():
    sink = MemoryEventSink()

    sink.emit("dispatched", task_id="tsk_1", to="design")

    assert sink.events == [("dispatched", {"task_id": "tsk_1", "to": "design"})]


async def test_fake_clock_does_not_really_sleep():
    clock = FakeClock("2026-07-19T10:00:00Z")

    await clock.sleep(5.0)

    assert clock.now() == "2026-07-19T10:00:00Z"
    assert clock.slept == [5.0]
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_memory_drivers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.drivers'`

- [ ] **Step 3: Napiš porty**

Create `src/harness/ports/__init__.py`:

```python
"""Porty. Tento balík neimportuje nic z harness.drivers."""
```

Create `src/harness/ports/queue.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class TaskQueue(ABC):
    """Fronta tasků.

    Inbox, fronty jednotlivých kroků, done i failed jsou instance tohoto
    portu. Terminální stavy jsou prostě fronty, které nikdo nekonzumuje.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def list(self) -> list[Task]:
        """Nezabrané tasky ve frontě."""

    @abstractmethod
    def claim(self, task: Task, lock_id: str) -> Task | None:
        """Zaber task. Vrátí task s vyplněným lockId, nebo None při prohraném závodě."""

    @abstractmethod
    def put(self, task: Task) -> None:
        """Vlož nezabraný task."""

    @abstractmethod
    def transfer(self, task: Task, destination: TaskQueue) -> None:
        """Přesuň zabraný task do jiné fronty.

        Musí být atomické: task nesmí existovat v obou frontách zároveň.
        """

    @abstractmethod
    def recover(self) -> int:
        """Vrať zabrané tasky zpět do fronty a vynuluj lockId. Vrací počet."""
```

Create `src/harness/ports/workflows.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Workflow


class WorkflowNotFound(Exception):
    """Workflow daného jména neexistuje."""


class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow:
        """Načti workflow. Neexistuje-li, vyhoď WorkflowNotFound."""
```

Create `src/harness/ports/strategy.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class EnqueueStrategy(ABC):
    """Vybírá, který task z fronty přijde na řadu."""

    @abstractmethod
    def select(self, tasks: list[Task]) -> Task | None:
        """Vybraný task, nebo None když není z čeho vybírat."""
```

Create `src/harness/ports/behavior.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Outcome, Task


class ConsumerBehavior(ABC):
    """Jediné místo, kde vzniká outcome.

    Jak k rozhodnutí dojde, je vnitřní věc implementace — dnes sleep,
    v dalších fázích skutečný agent. Zvenčí se to neliší.
    """

    @abstractmethod
    async def run(self, task: Task) -> Outcome:
        """Vykonej práci a vrať, co se stalo."""
```

Create `src/harness/ports/events.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EventSink(ABC):
    """Strukturovaný výstup.

    Dostává jméno a pole, nikdy naformátovaný řetězec — formátování je věc
    driveru. Jinak by budoucí OTel driver parsoval text.
    """

    @abstractmethod
    def emit(self, name: str, **fields: Any) -> None:
        """Vyemituj událost."""
```

Create `src/harness/ports/clock.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod


class Clock(ABC):
    """Čas za portem, aby testy nemusely spát."""

    @abstractmethod
    def now(self) -> str:
        """Aktuální čas jako ISO 8601 UTC se sufixem Z."""

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Počkej."""
```

- [ ] **Step 4: Napiš in-memory drivery**

Create `src/harness/drivers/__init__.py`:

```python
"""Konkrétní implementace portů."""
```

Create `src/harness/drivers/memory.py`:

```python
"""In-memory drivery. Používají se v testech — bez disku a bez čekání."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from harness.models import Outcome, Task, Workflow
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository


class MemoryTaskQueue(TaskQueue):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._ready: dict[str, Task] = {}
        self._claimed: dict[str, Task] = {}

    def list(self) -> list[Task]:
        return list(self._ready.values())

    def claim(self, task: Task, lock_id: str) -> Task | None:
        if task.id not in self._ready:
            return None
        del self._ready[task.id]
        claimed = replace(task, lock_id=lock_id)
        self._claimed[task.id] = claimed
        return claimed

    def put(self, task: Task) -> None:
        self._ready[task.id] = task

    def transfer(self, task: Task, destination: TaskQueue) -> None:
        self._claimed.pop(task.id, None)
        destination.put(task)

    def recover(self) -> int:
        count = len(self._claimed)
        for task_id, task in self._claimed.items():
            self._ready[task_id] = replace(task, lock_id=None)
        self._claimed.clear()
        return count


class MemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: dict[str, Workflow]) -> None:
        self._workflows = workflows

    def get(self, name: str) -> Workflow:
        try:
            return self._workflows[name]
        except KeyError:
            raise WorkflowNotFound(f"workflow {name!r} neexistuje") from None


class MemoryEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, **fields: Any) -> None:
        self.events.append((name, fields))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class FakeClock(Clock):
    def __init__(self, instant: str = "2026-07-19T10:00:00Z") -> None:
        self.instant = instant
        self.slept: list[float] = []

    def now(self) -> str:
        return self.instant

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class ScriptedBehavior(ConsumerBehavior):
    """Vrací předepsané outcomes podle kroku, na kterém task stojí.

    Dojdou-li předpisy pro daný krok, vrací DONE.
    """

    def __init__(self, outcomes: dict[str, list[Outcome]] | None = None) -> None:
        self._outcomes = {step: list(values) for step, values in (outcomes or {}).items()}
        self.seen: list[str] = []

    async def run(self, task: Task) -> Outcome:
        step = task.status or ""
        self.seen.append(step)
        pending = self._outcomes.get(step)
        if pending:
            return pending.pop(0)
        return Outcome.DONE
```

- [ ] **Step 5: Spusť testy**

Run: `.venv/bin/pytest tests/test_memory_drivers.py -q`
Expected: PASS, 8 passed

- [ ] **Step 6: Commit**

```bash
git add src/harness/ports src/harness/drivers tests/test_memory_drivers.py
git commit -m "feat: porty a in-memory drivery pro fronty, workflow, eventy a čas"
```

---

### Task 4: Dispatcher

**Files:**
- Create: `src/harness/dispatcher.py`
- Create: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `route` z `harness.router`; porty `TaskQueue`, `WorkflowRepository`, `WorkflowNotFound`, `EnqueueStrategy`, `EventSink`, `Clock`; modely
- Produces: `Dispatcher(inbox, step_queues: dict[str, TaskQueue], done, failed, workflows, strategy, events, clock)` s metodou `tick() -> bool` (True když se něco zpracovalo)

Emitované eventy: `dispatched` (`task_id`, `from`, `to`, `outcome`), `finished` (`task_id`), `failed` (`task_id`, `reason`), `idle` se neemituje.

- [ ] **Step 1: Napiš padající testy dispatchera**

Create `tests/test_dispatcher.py`:

```python
from harness.dispatcher import Dispatcher
from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    MemoryWorkflowRepository,
)
from harness.models import END, Task, Transition, Workflow
from harness.ports.strategy import EnqueueStrategy

WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
        Transition(from_step="review", on="request_changes", to_step="plan"),
    ),
)


class FirstStrategy(EnqueueStrategy):
    def select(self, tasks):
        return tasks[0] if tasks else None


def build(task: Task | None = None):
    inbox = MemoryTaskQueue("tasks")
    step_queues = {step: MemoryTaskQueue(step) for step in WORKFLOW.steps()}
    done = MemoryTaskQueue("done")
    failed = MemoryTaskQueue("failed")
    events = MemoryEventSink()
    dispatcher = Dispatcher(
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        workflows=MemoryWorkflowRepository({"default": WORKFLOW}),
        strategy=FirstStrategy(),
        events=events,
        clock=FakeClock(),
    )
    if task is not None:
        inbox.put(task)
    return dispatcher, inbox, step_queues, done, failed, events


def make_task(status=None, last_outcome=None, template="default") -> Task:
    return Task(
        id="tsk_1",
        workflow_template=template,
        created="2026-07-19T10:00:00Z",
        status=status,
        last_outcome=last_outcome,
        data={"payload": "nedotknutelné"},
    )


def test_tick_on_empty_inbox_does_nothing():
    dispatcher, *_ = build()

    assert dispatcher.tick() is False


def test_new_task_goes_to_start_queue_with_status_set():
    dispatcher, inbox, step_queues, _, _, events = build(make_task())

    assert dispatcher.tick() is True

    assert inbox.list() == []
    routed = step_queues["plan"].list()[0]
    assert routed.status == "plan"
    assert routed.lock_id is None
    assert ("dispatched", {"task_id": "tsk_1", "from": None, "to": "plan", "outcome": None}) in events.events


def test_dispatch_appends_history():
    dispatcher, _, step_queues, _, _, _ = build(make_task())

    dispatcher.tick()

    entry = step_queues["plan"].list()[0].history[-1]
    assert entry.actor == "dispatcher"
    assert entry.from_step is None
    assert entry.to_step == "plan"
    assert entry.at == "2026-07-19T10:00:00Z"


def test_forward_edge_moves_to_next_queue():
    dispatcher, _, step_queues, _, _, _ = build(make_task("plan", "done"))

    dispatcher.tick()

    assert step_queues["review"].list()[0].status == "review"


def test_backward_edge_moves_back():
    dispatcher, _, step_queues, _, _, _ = build(make_task("review", "request_changes"))

    dispatcher.tick()

    assert step_queues["plan"].list()[0].status == "plan"


def test_end_node_lands_in_done():
    dispatcher, _, _, done, _, events = build(make_task("review", "done"))

    dispatcher.tick()

    assert done.list()[0].id == "tsk_1"
    assert "finished" in events.names()


def test_unknown_workflow_template_lands_in_failed():
    dispatcher, _, _, _, failed, events = build(make_task(template="neznamy"))

    assert dispatcher.tick() is True

    task = failed.list()[0]
    assert task.history[-1].to_step == "failed"
    assert "neznamy" in task.history[-1].reason
    assert "failed" in events.names()


def test_missing_edge_lands_in_failed():
    dispatcher, _, _, _, failed, _ = build(make_task("plan", "request_changes"))

    dispatcher.tick()

    assert failed.list()[0].history[-1].to_step == "failed"


def test_step_without_queue_lands_in_failed():
    workflow = Workflow(
        name="default",
        start="chybejici",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )
    inbox = MemoryTaskQueue("tasks")
    failed = MemoryTaskQueue("failed")
    inbox.put(make_task())
    dispatcher = Dispatcher(
        inbox=inbox,
        step_queues={},
        done=MemoryTaskQueue("done"),
        failed=failed,
        workflows=MemoryWorkflowRepository({"default": workflow}),
        strategy=FirstStrategy(),
        events=MemoryEventSink(),
        clock=FakeClock(),
    )

    dispatcher.tick()

    assert "chybejici" in failed.list()[0].history[-1].reason


def test_one_bad_task_does_not_stop_the_loop():
    dispatcher, inbox, step_queues, _, failed, _ = build()
    inbox.put(make_task(template="neznamy"))
    inbox.put(
        Task(id="tsk_2", workflow_template="default", created="2026-07-19T10:00:01Z")
    )

    dispatcher.tick()
    dispatcher.tick()

    assert len(failed.list()) == 1
    assert len(step_queues["plan"].list()) == 1


def test_dispatcher_does_not_touch_payload():
    dispatcher, _, step_queues, _, _, _ = build(make_task())

    dispatcher.tick()

    assert step_queues["plan"].list()[0].data == {"payload": "nedotknutelné"}
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_dispatcher.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.dispatcher'`

- [ ] **Step 3: Napiš `src/harness/dispatcher.py`**

```python
"""Dispatcher: rozhoduje, KAM task jde. Nikdy ne, co se s ním stalo."""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import (
    Failed,
    Finished,
    HistoryEntry,
    MoveTo,
    Task,
    append_history,
)
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.strategy import EnqueueStrategy
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository
from harness.router import route

ACTOR = "dispatcher"


class Dispatcher:
    def __init__(
        self,
        *,
        inbox: TaskQueue,
        step_queues: dict[str, TaskQueue],
        done: TaskQueue,
        failed: TaskQueue,
        workflows: WorkflowRepository,
        strategy: EnqueueStrategy,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._inbox = inbox
        self._step_queues = step_queues
        self._done = done
        self._failed = failed
        self._workflows = workflows
        self._strategy = strategy
        self._events = events
        self._clock = clock

    def tick(self) -> bool:
        """Zpracuj nejvýš jeden task. True, když se něco zpracovalo."""
        selected = self._strategy.select(self._inbox.list())
        if selected is None:
            return False

        task = self._inbox.claim(selected, new_lock_id())
        if task is None:
            return False

        try:
            workflow = self._workflows.get(task.workflow_template)
        except WorkflowNotFound as error:
            self._fail(task, str(error))
            return True

        decision = route(task, workflow)

        if isinstance(decision, Failed):
            self._fail(task, decision.reason)
        elif isinstance(decision, Finished):
            self._finish(task)
        elif isinstance(decision, MoveTo):
            destination = self._step_queues.get(decision.step)
            if destination is None:
                self._fail(task, f"krok {decision.step!r} nemá frontu")
            else:
                self._move(task, decision.step, destination)

        return True

    def _move(self, task: Task, step: str, destination: TaskQueue) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=task.status,
            to_step=step,
            outcome=task.last_outcome,
        )
        routed = append_history(replace(task, status=step, lock_id=None), entry)
        self._inbox.transfer(routed, destination)
        self._events.emit(
            "dispatched",
            task_id=task.id,
            **{"from": task.status, "to": step},
            outcome=task.last_outcome,
        )

    def _finish(self, task: Task) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=task.status,
            to_step="end",
            outcome=task.last_outcome,
        )
        finished = append_history(replace(task, status="end", lock_id=None), entry)
        self._inbox.transfer(finished, self._done)
        self._events.emit("finished", task_id=task.id)

    def _fail(self, task: Task, reason: str) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=task.status,
            to_step="failed",
            outcome=task.last_outcome,
            reason=reason,
        )
        broken = append_history(replace(task, lock_id=None), entry)
        self._inbox.transfer(broken, self._failed)
        self._events.emit("failed", task_id=task.id, reason=reason)
```

- [ ] **Step 4: Spusť testy**

Run: `.venv/bin/pytest tests/test_dispatcher.py -q`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/harness/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: dispatcher směrující tasky podle workflow"
```

---

### Task 5: Consumer

**Files:**
- Create: `src/harness/consumer.py`
- Create: `tests/test_consumer.py`

**Interfaces:**
- Consumes: porty `TaskQueue`, `ConsumerBehavior`, `EnqueueStrategy`, `EventSink`, `Clock`; modely
- Produces: `Consumer(step, queue, inbox, failed, behavior, strategy, events, clock)` s metodou `async tick() -> bool`

Emitované eventy: `consumed` (`task_id`, `step`, `outcome`), `failed` (`task_id`, `reason`).

**Konvence historie consumera:** consumer nesměruje, proto zapisuje `from_step = step` a `to_step = None`. Čte se to jako „běžel na kroku X, výsledek Y".

- [ ] **Step 1: Napiš padající testy consumera**

Create `tests/test_consumer.py`:

```python
import inspect

from harness.consumer import Consumer
from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    ScriptedBehavior,
)
from harness.models import Outcome, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.strategy import EnqueueStrategy


class FirstStrategy(EnqueueStrategy):
    def select(self, tasks):
        return tasks[0] if tasks else None


class ExplodingBehavior(ConsumerBehavior):
    async def run(self, task):
        raise RuntimeError("behavior praskl")


class BogusBehavior(ConsumerBehavior):
    async def run(self, task):
        return "neco jineho"


def build(behavior, task: Task | None = None):
    queue = MemoryTaskQueue("design")
    inbox = MemoryTaskQueue("tasks")
    failed = MemoryTaskQueue("failed")
    events = MemoryEventSink()
    consumer = Consumer(
        step="design",
        queue=queue,
        inbox=inbox,
        failed=failed,
        behavior=behavior,
        strategy=FirstStrategy(),
        events=events,
        clock=FakeClock(),
    )
    if task is not None:
        queue.put(task)
    return consumer, queue, inbox, failed, events


def make_task() -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="design",
    )


async def test_tick_on_empty_queue_does_nothing():
    consumer, *_ = build(ScriptedBehavior())

    assert await consumer.tick() is False


async def test_done_outcome_returns_task_to_inbox():
    consumer, queue, inbox, _, events = build(ScriptedBehavior(), make_task())

    assert await consumer.tick() is True

    assert queue.list() == []
    returned = inbox.list()[0]
    assert returned.last_outcome == "done"
    assert returned.lock_id is None
    assert ("consumed", {"task_id": "tsk_1", "step": "design", "outcome": "done"}) in events.events


async def test_request_changes_outcome_is_written_verbatim():
    behavior = ScriptedBehavior({"design": [Outcome.REQUEST_CHANGES]})
    consumer, _, inbox, _, _ = build(behavior, make_task())

    await consumer.tick()

    assert inbox.list()[0].last_outcome == "request_changes"


async def test_consumer_never_changes_status():
    behavior = ScriptedBehavior({"design": [Outcome.REQUEST_CHANGES]})
    consumer, _, inbox, _, _ = build(behavior, make_task())

    await consumer.tick()

    assert inbox.list()[0].status == "design"


async def test_consumer_appends_history_without_target():
    consumer, _, inbox, _, _ = build(ScriptedBehavior(), make_task())

    await consumer.tick()

    entry = inbox.list()[0].history[-1]
    assert entry.actor == "consumer:design"
    assert entry.from_step == "design"
    assert entry.to_step is None
    assert entry.outcome == "done"


async def test_behavior_exception_lands_in_failed():
    consumer, _, inbox, failed, events = build(ExplodingBehavior(), make_task())

    assert await consumer.tick() is True

    assert inbox.list() == []
    assert "behavior praskl" in failed.list()[0].history[-1].reason
    assert "failed" in events.names()


async def test_invalid_outcome_lands_in_failed():
    consumer, _, _, failed, _ = build(BogusBehavior(), make_task())

    await consumer.tick()

    assert "neco jineho" in failed.list()[0].history[-1].reason


def test_consumer_has_no_branch_on_outcome_value():
    """Rozhodování patří do ConsumerBehavior, ne sem."""
    source = inspect.getsource(Consumer)

    assert "Outcome.DONE" not in source
    assert "Outcome.REQUEST_CHANGES" not in source
    assert "request_changes" not in source
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_consumer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.consumer'`

- [ ] **Step 3: Napiš `src/harness/consumer.py`**

```python
"""Consumer: tenká obálka kolem ConsumerBehavior.

Nemá žádnou větev závislou na hodnotě outcome — jen ho doručí. Objeví-li se
tu `if outcome == ...`, prosákla odpovědnost přes hranici.
"""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import HistoryEntry, Outcome, Task, append_history
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.strategy import EnqueueStrategy


class Consumer:
    def __init__(
        self,
        *,
        step: str,
        queue: TaskQueue,
        inbox: TaskQueue,
        failed: TaskQueue,
        behavior: ConsumerBehavior,
        strategy: EnqueueStrategy,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._step = step
        self._queue = queue
        self._inbox = inbox
        self._failed = failed
        self._behavior = behavior
        self._strategy = strategy
        self._events = events
        self._clock = clock

    @property
    def actor(self) -> str:
        return f"consumer:{self._step}"

    async def tick(self) -> bool:
        """Zpracuj nejvýš jeden task. True, když se něco zpracovalo."""
        selected = self._strategy.select(self._queue.list())
        if selected is None:
            return False

        task = self._queue.claim(selected, new_lock_id())
        if task is None:
            return False

        try:
            outcome = await self._behavior.run(task)
        except Exception as error:  # noqa: BLE001 - jeden vadný task nesmí zastavit smyčku
            self._fail(task, f"behavior vyhodil výjimku: {error}")
            return True

        if not isinstance(outcome, Outcome):
            self._fail(task, f"behavior vrátil neplatný outcome: {outcome!r}")
            return True

        self._deliver(task, outcome)
        return True

    def _deliver(self, task: Task, outcome: Outcome) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=self.actor,
            from_step=self._step,
            to_step=None,
            outcome=outcome.value,
        )
        updated = append_history(
            replace(task, last_outcome=outcome.value, lock_id=None), entry
        )
        self._queue.transfer(updated, self._inbox)
        self._events.emit(
            "consumed", task_id=task.id, step=self._step, outcome=outcome.value
        )

    def _fail(self, task: Task, reason: str) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=self.actor,
            from_step=self._step,
            to_step="failed",
            reason=reason,
        )
        broken = append_history(replace(task, lock_id=None), entry)
        self._queue.transfer(broken, self._failed)
        self._events.emit("failed", task_id=task.id, reason=reason)
```

- [ ] **Step 4: Spusť testy**

Run: `.venv/bin/pytest tests/test_consumer.py -q`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/harness/consumer.py tests/test_consumer.py
git commit -m "feat: consumer jako tenká obálka nad ConsumerBehavior"
```

---

### Task 6: Filesystem driver front

**Files:**
- Create: `src/harness/drivers/fs_queue.py`
- Create: `tests/test_fs_queue.py`

**Interfaces:**
- Consumes: `TaskQueue` port, `Task`, `EventSink`
- Produces: `FilesystemTaskQueue(name, root: Path, events: EventSink, quarantine: TaskQueue | None = None)`. Vytváří `root/` i `root/.processing/`. `transfer` je `os.replace` mezi `.processing/` zdroje a adresářem cíle.

**Klíčové vlastnosti:**
- `claim()` = `os.replace(root/<id>.json, root/.processing/<id>.json)`. Prohraný závod = `FileNotFoundError` → vrací `None`.
- Vlastní `.processing/` u každé fronty znamená, že původ tasku je po pádu implicitní — nikam se neukládá.
- Nečitelný JSON `list()` přeskočí, vyemituje `corrupt` a přesune soubor do `quarantine` tak, jak je. Task nelze deserializovat, takže mu nelze připsat historii — důvod jde jen do eventu.

- [ ] **Step 1: Napiš padající testy**

Create `tests/test_fs_queue.py`:

```python
import json

from harness.drivers.fs_queue import FilesystemTaskQueue
from harness.drivers.memory import MemoryEventSink, MemoryTaskQueue
from harness.models import Task


def make_task(task_id="tsk_1") -> Task:
    return Task(id=task_id, workflow_template="default", created="2026-07-19T10:00:00Z")


def build(tmp_path, name="tasks", quarantine=None):
    events = MemoryEventSink()
    queue = FilesystemTaskQueue(
        name=name, root=tmp_path / name, events=events, quarantine=quarantine
    )
    return queue, events


def test_creates_its_directories(tmp_path):
    queue, _ = build(tmp_path)

    assert (tmp_path / "tasks").is_dir()
    assert (tmp_path / "tasks" / ".processing").is_dir()


def test_put_writes_json_and_list_reads_it(tmp_path):
    queue, _ = build(tmp_path)
    task = make_task()

    queue.put(task)

    raw = json.loads((tmp_path / "tasks" / "tsk_1.json").read_text())
    assert raw["workflowTemplate"] == "default"
    assert queue.list() == [task]


def test_claim_moves_file_into_processing(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())

    claimed = queue.claim(queue.list()[0], "lck_1")

    assert claimed.lock_id == "lck_1"
    assert not (tmp_path / "tasks" / "tsk_1.json").exists()
    assert (tmp_path / "tasks" / ".processing" / "tsk_1.json").exists()
    assert queue.list() == []


def test_claim_of_already_claimed_task_returns_none(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    task = queue.list()[0]
    queue.claim(task, "lck_1")

    assert queue.claim(task, "lck_2") is None


def test_transfer_moves_between_directories(tmp_path):
    source, _ = build(tmp_path, "tasks")
    destination, _ = build(tmp_path, "design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(claimed, destination)

    assert not (tmp_path / "tasks" / ".processing" / "tsk_1.json").exists()
    assert (tmp_path / "design" / "tsk_1.json").exists()
    assert destination.list()[0].id == "tsk_1"


def test_transfer_writes_the_updated_task(tmp_path):
    from dataclasses import replace

    source, _ = build(tmp_path, "tasks")
    destination, _ = build(tmp_path, "design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(replace(claimed, status="design", lock_id=None), destination)

    assert destination.list()[0].status == "design"
    assert destination.list()[0].lock_id is None


def test_transfer_to_foreign_queue_type_still_works(tmp_path):
    source, _ = build(tmp_path, "tasks")
    destination = MemoryTaskQueue("design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(claimed, destination)

    assert destination.list()[0].id == "tsk_1"
    assert not (tmp_path / "tasks" / ".processing" / "tsk_1.json").exists()


def test_recover_returns_claimed_tasks_and_clears_lock(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    queue.claim(queue.list()[0], "lck_1")

    recovered = queue.recover()

    assert recovered == 1
    assert queue.list()[0].lock_id is None
    assert not any((tmp_path / "tasks" / ".processing").iterdir())


def test_corrupt_file_goes_to_quarantine_and_emits(tmp_path):
    quarantine = MemoryTaskQueue("failed")
    queue, events = build(tmp_path, quarantine=quarantine)
    queue.put(make_task())
    (tmp_path / "tasks" / "rozbity.json").write_text("{tohle neni json")

    listed = queue.list()

    assert [task.id for task in listed] == ["tsk_1"]
    assert not (tmp_path / "tasks" / "rozbity.json").exists()
    assert "corrupt" in events.names()


def test_corrupt_file_does_not_stop_listing_without_quarantine(tmp_path):
    queue, events = build(tmp_path)
    queue.put(make_task())
    (tmp_path / "tasks" / "rozbity.json").write_text("{tohle neni json")

    assert [task.id for task in queue.list()] == ["tsk_1"]
    assert "corrupt" in events.names()


def test_list_ignores_non_json_files(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    (tmp_path / "tasks" / "README.txt").write_text("ahoj")

    assert len(queue.list()) == 1
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_fs_queue.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.drivers.fs_queue'`

- [ ] **Step 3: Napiš `src/harness/drivers/fs_queue.py`**

```python
"""Fronta jako adresář s JSON soubory.

claim() je atomický rename do <root>/.processing/. Jedna operace řeší lease,
idempotenci i původ po pádu: protože má .processing/ každá fronta vlastní,
recovery ví, kam task vrátit, aniž by se to kamkoli ukládalo.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

from harness.models import Task
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue

PROCESSING = ".processing"


class FilesystemTaskQueue(TaskQueue):
    def __init__(
        self,
        *,
        name: str,
        root: Path,
        events: EventSink,
        quarantine: TaskQueue | None = None,
    ) -> None:
        super().__init__(name)
        self._root = Path(root)
        self._events = events
        self._quarantine = quarantine
        self._root.mkdir(parents=True, exist_ok=True)
        self._processing.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def _processing(self) -> Path:
        return self._root / PROCESSING

    def list(self) -> list[Task]:
        tasks: list[Task] = []
        for path in sorted(self._root.glob("*.json")):
            task = self._read(path)
            if task is not None:
                tasks.append(task)
        return tasks

    def claim(self, task: Task, lock_id: str) -> Task | None:
        source = self._root / f"{task.id}.json"
        target = self._processing / f"{task.id}.json"
        try:
            os.replace(source, target)
        except (FileNotFoundError, IsADirectoryError):
            return None
        claimed = replace(task, lock_id=lock_id)
        self._write(target, claimed)
        return claimed

    def put(self, task: Task) -> None:
        self._write(self._root / f"{task.id}.json", task)

    def transfer(self, task: Task, destination: TaskQueue) -> None:
        held = self._processing / f"{task.id}.json"
        if isinstance(destination, FilesystemTaskQueue):
            self._write(held, task)
            os.replace(held, destination.root / f"{task.id}.json")
            return
        destination.put(task)
        held.unlink(missing_ok=True)

    def recover(self) -> int:
        count = 0
        for path in sorted(self._processing.glob("*.json")):
            task = self._read(path, quarantine=False)
            if task is None:
                self._quarantine_file(path)
                continue
            self._write(path, replace(task, lock_id=None))
            os.replace(path, self._root / path.name)
            count += 1
        return count

    def _read(self, path: Path, *, quarantine: bool = True) -> Task | None:
        try:
            return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as error:
            self._events.emit("corrupt", queue=self.name, path=str(path), reason=str(error))
            if quarantine:
                self._quarantine_file(path)
            return None

    def _quarantine_file(self, path: Path) -> None:
        """Task se nedá deserializovat, takže mu nelze připsat historii.
        Soubor se přesune tak, jak je; důvod nese jen event."""
        if self._quarantine is None:
            return
        if isinstance(self._quarantine, FilesystemTaskQueue):
            shutil.move(str(path), str(self._quarantine.root / path.name))
        else:
            path.unlink(missing_ok=True)

    def _write(self, path: Path, task: Task) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, path)
```

- [ ] **Step 4: Spusť testy**

Run: `.venv/bin/pytest tests/test_fs_queue.py -q`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/harness/drivers/fs_queue.py tests/test_fs_queue.py
git commit -m "feat: filesystem driver front s atomickým claim a recovery"
```

---

### Task 7: Filesystem workflow repository a FIFO strategie

**Files:**
- Create: `src/harness/drivers/fs_workflows.py`
- Create: `src/harness/drivers/fifo_strategy.py`
- Create: `tests/test_fs_workflows.py`
- Create: `tests/test_fifo_strategy.py`

**Interfaces:**
- Consumes: `WorkflowRepository`, `WorkflowNotFound`, `EnqueueStrategy`, `Workflow`, `Transition`, `Task`
- Produces: `FilesystemWorkflowRepository(root: Path)` s `get(name)`; `FifoStrategy()` s `select(tasks)` vybírající nejnižší `(created, id)`

- [ ] **Step 1: Napiš padající testy**

Create `tests/test_fs_workflows.py`:

```python
import json

import pytest

from harness.drivers.fs_workflows import FilesystemWorkflowRepository
from harness.models import END, Transition
from harness.ports.workflows import WorkflowNotFound

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "plan"},
    ],
}


def test_loads_definition_from_named_file(tmp_path):
    (tmp_path / "default.json").write_text(json.dumps(DEFINITION))
    repository = FilesystemWorkflowRepository(tmp_path)

    workflow = repository.get("default")

    assert workflow.name == "default"
    assert workflow.start == "plan"
    assert workflow.transitions[0] == Transition("plan", "done", "review")
    assert workflow.target("review", "done") == END


def test_missing_file_raises(tmp_path):
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound, match="neznamy"):
        repository.get("neznamy")


def test_malformed_definition_raises(tmp_path):
    (tmp_path / "rozbity.json").write_text("{tohle neni json")
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("rozbity")


def test_definition_without_start_raises(tmp_path):
    (tmp_path / "bez.json").write_text(json.dumps({"name": "bez", "transitions": []}))
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound, match="start"):
        repository.get("bez")


def test_name_with_path_separator_is_rejected(tmp_path):
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("../tajne")
```

Create `tests/test_fifo_strategy.py`:

```python
from harness.drivers.fifo_strategy import FifoStrategy
from harness.models import Task


def make_task(task_id: str, created: str) -> Task:
    return Task(id=task_id, workflow_template="default", created=created)


def test_empty_list_selects_nothing():
    assert FifoStrategy().select([]) is None


def test_selects_oldest_by_created():
    tasks = [
        make_task("tsk_b", "2026-07-19T10:00:05Z"),
        make_task("tsk_a", "2026-07-19T10:00:01Z"),
    ]

    assert FifoStrategy().select(tasks).id == "tsk_a"


def test_ties_broken_by_id_for_determinism():
    tasks = [
        make_task("tsk_b", "2026-07-19T10:00:00Z"),
        make_task("tsk_a", "2026-07-19T10:00:00Z"),
    ]

    assert FifoStrategy().select(tasks).id == "tsk_a"
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_fs_workflows.py tests/test_fifo_strategy.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Napiš `src/harness/drivers/fs_workflows.py`**

```python
"""Workflow jako <root>/<name>.json."""

from __future__ import annotations

import json
from pathlib import Path

from harness.models import Transition, Workflow
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository


class FilesystemWorkflowRepository(WorkflowRepository):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def get(self, name: str) -> Workflow:
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise WorkflowNotFound(f"neplatné jméno workflow: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WorkflowNotFound(f"workflow {name!r} neexistuje ({path})") from None
        except json.JSONDecodeError as error:
            raise WorkflowNotFound(
                f"workflow {name!r} má rozbitou definici: {error}"
            ) from None

        if "start" not in raw:
            raise WorkflowNotFound(f"workflow {name!r} nemá start")

        try:
            transitions = tuple(
                Transition(
                    from_step=item["from"], on=item["on"], to_step=item["to"]
                )
                for item in raw.get("transitions", [])
            )
        except (KeyError, TypeError) as error:
            raise WorkflowNotFound(
                f"workflow {name!r} má neplatný přechod: {error}"
            ) from None

        return Workflow(
            name=raw.get("name", name), start=raw["start"], transitions=transitions
        )
```

- [ ] **Step 4: Napiš `src/harness/drivers/fifo_strategy.py`**

```python
"""FIFO podle created. Shody rozhoduje id, aby byl výběr deterministický."""

from __future__ import annotations

from harness.models import Task
from harness.ports.strategy import EnqueueStrategy


class FifoStrategy(EnqueueStrategy):
    def select(self, tasks: list[Task]) -> Task | None:
        if not tasks:
            return None
        return min(tasks, key=lambda task: (task.created, task.id))
```

- [ ] **Step 5: Spusť testy**

Run: `.venv/bin/pytest tests/test_fs_workflows.py tests/test_fifo_strategy.py -q`
Expected: PASS, 8 passed

- [ ] **Step 6: Commit**

```bash
git add src/harness/drivers/fs_workflows.py src/harness/drivers/fifo_strategy.py tests/test_fs_workflows.py tests/test_fifo_strategy.py
git commit -m "feat: filesystem workflow repository a FIFO enqueue strategie"
```

---

### Task 8: Dummy behavior, stdout sink, systémové hodiny

**Files:**
- Create: `src/harness/drivers/dummy_behavior.py`
- Create: `src/harness/drivers/stdout_events.py`
- Create: `src/harness/drivers/system_clock.py`
- Create: `tests/test_dummy_behavior.py`
- Create: `tests/test_stdout_events.py`

**Interfaces:**
- Consumes: `ConsumerBehavior`, `EventSink`, `Clock`, `Outcome`, `Task`
- Produces:
  - `DummyBehavior(clock: Clock, delay: float = 5.0, request_changes_once_at: str | None = None)` — čeká `delay` a vrací `Outcome.DONE`; pro krok uvedený v `request_changes_once_at` vrátí **při prvním** průchodu `Outcome.REQUEST_CHANGES` a napodruhé `DONE`
  - `StdoutEventSink(stream=sys.stdout)` — jeden řádek na event
  - `SystemClock()` — `now()` z `datetime.now(timezone.utc)`, `sleep()` přes `asyncio.sleep`

**Proč `request_changes_once_at`:** bez něj se zpětná hrana nikdy neproklepne. Bez determinismu (jen jednou, pak `DONE`) by se smyčka točila donekonečna a POC by nikdy nedoběhl.

- [ ] **Step 1: Napiš padající testy**

Create `tests/test_dummy_behavior.py`:

```python
from dataclasses import replace

from harness.drivers.dummy_behavior import DummyBehavior
from harness.drivers.memory import FakeClock
from harness.models import Outcome, Task


def make_task(status: str) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status=status,
    )


async def test_returns_done_and_waits():
    clock = FakeClock()
    behavior = DummyBehavior(clock=clock, delay=5.0)

    outcome = await behavior.run(make_task("design"))

    assert outcome is Outcome.DONE
    assert clock.slept == [5.0]


async def test_configured_step_asks_for_changes_only_once():
    behavior = DummyBehavior(
        clock=FakeClock(), delay=0.0, request_changes_once_at="review"
    )
    task = make_task("review")

    assert await behavior.run(task) is Outcome.REQUEST_CHANGES
    assert await behavior.run(task) is Outcome.DONE
    assert await behavior.run(task) is Outcome.DONE


async def test_other_steps_are_unaffected():
    behavior = DummyBehavior(
        clock=FakeClock(), delay=0.0, request_changes_once_at="review"
    )

    assert await behavior.run(make_task("design")) is Outcome.DONE


async def test_request_changes_is_per_task():
    behavior = DummyBehavior(
        clock=FakeClock(), delay=0.0, request_changes_once_at="review"
    )
    first = make_task("review")
    second = replace(first, id="tsk_2")

    assert await behavior.run(first) is Outcome.REQUEST_CHANGES
    assert await behavior.run(second) is Outcome.REQUEST_CHANGES
    assert await behavior.run(first) is Outcome.DONE
```

Create `tests/test_stdout_events.py`:

```python
import io

from harness.drivers.stdout_events import StdoutEventSink


def test_emits_one_line_per_event():
    stream = io.StringIO()
    sink = StdoutEventSink(stream=stream)

    sink.emit("dispatched", task_id="tsk_1", to="design")

    line = stream.getvalue().strip()
    assert line.startswith("dispatched")
    assert "task_id=tsk_1" in line
    assert "to=design" in line
    assert "\n" not in line


def test_event_without_fields():
    stream = io.StringIO()
    sink = StdoutEventSink(stream=stream)

    sink.emit("idle")

    assert stream.getvalue().strip() == "idle"
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_dummy_behavior.py tests/test_stdout_events.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Napiš `src/harness/drivers/dummy_behavior.py`**

```python
"""Dummy behavior fáze 1: počká a vrátí DONE.

Vrací DONE deterministicky. Volitelně pro jeden krok vrátí při prvním
průchodu REQUEST_CHANGES, aby se zpětná hrana workflow taky proklepla —
ale jen jednou, jinak by se smyčka točila donekonečna.
"""

from __future__ import annotations

from harness.models import Outcome, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock


class DummyBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        clock: Clock,
        delay: float = 5.0,
        request_changes_once_at: str | None = None,
    ) -> None:
        self._clock = clock
        self._delay = delay
        self._step = request_changes_once_at
        self._already_asked: set[str] = set()

    async def run(self, task: Task) -> Outcome:
        await self._clock.sleep(self._delay)

        if self._step is not None and task.status == self._step:
            if task.id not in self._already_asked:
                self._already_asked.add(task.id)
                return Outcome.REQUEST_CHANGES

        return Outcome.DONE
```

- [ ] **Step 4: Napiš `src/harness/drivers/stdout_events.py`**

```python
"""Eventy jako řádky na stdout. V dalších fázích nahradí OTel."""

from __future__ import annotations

import sys
from typing import Any, TextIO

from harness.ports.events import EventSink


class StdoutEventSink(EventSink):
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def emit(self, name: str, **fields: Any) -> None:
        rendered = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{name} {rendered}".rstrip()
        print(line, file=self._stream, flush=True)
```

- [ ] **Step 5: Napiš `src/harness/drivers/system_clock.py`**

```python
"""Skutečný čas."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from harness.ports.clock import Clock


class SystemClock(Clock):
    def now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
```

- [ ] **Step 6: Spusť testy**

Run: `.venv/bin/pytest tests/test_dummy_behavior.py tests/test_stdout_events.py -q`
Expected: PASS, 6 passed

- [ ] **Step 7: Commit**

```bash
git add src/harness/drivers/dummy_behavior.py src/harness/drivers/stdout_events.py src/harness/drivers/system_clock.py tests/test_dummy_behavior.py tests/test_stdout_events.py
git commit -m "feat: dummy behavior, stdout event sink a systémové hodiny"
```

---

### Task 9: Wiring a asyncio runtime

**Files:**
- Create: `src/harness/app.py`
- Create: `tests/test_app.py`

**Interfaces:**
- Consumes: všechno předchozí
- Produces:
  - `HarnessLayout(root: Path)` s vlastnostmi `workflows`, `tasks`, `queues`, `done`, `failed` (všechno `Path`)
  - `build(root: Path, workflow_name: str, *, events=None, clock=None, behavior=None, delay=5.0, request_changes_once_at=None) -> Harness`
  - `Harness` s atributy `dispatcher`, `consumers: list[Consumer]`, `layout`, `workflow`, metodami `recover() -> int` a `async run(poll_interval: float = 0.2, stop: asyncio.Event | None = None) -> None`

`app.py` je **jediné** místo, kde se potkají porty s konkrétními drivery.

- [ ] **Step 1: Napiš padající testy**

Create `tests/test_app.py`:

```python
import asyncio
import json

from harness.app import HarnessLayout, build
from harness.drivers.memory import MemoryEventSink
from harness.models import Task

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "plan"},
    ],
}


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))
    return layout


def test_build_creates_one_queue_per_step(tmp_path):
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert sorted(step for step in harness.workflow.steps()) == ["plan", "review"]
    assert (tmp_path / "queues" / "plan").is_dir()
    assert (tmp_path / "queues" / "review").is_dir()
    assert not (tmp_path / "queues" / "end").exists()
    assert len(harness.consumers) == 2


def test_build_creates_inbox_done_and_failed(tmp_path):
    seed(tmp_path)

    build(tmp_path, "default", events=MemoryEventSink())

    assert (tmp_path / "tasks").is_dir()
    assert (tmp_path / "done").is_dir()
    assert (tmp_path / "failed").is_dir()


async def test_run_drives_a_task_all_the_way_to_done(tmp_path):
    seed(tmp_path)
    events = MemoryEventSink()
    harness = build(
        tmp_path,
        "default",
        events=events,
        delay=0.0,
        request_changes_once_at="review",
    )
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-19T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_1.json").exists():
            break
    stop.set()
    await runner

    assert (tmp_path / "done" / "tsk_1.json").exists()
    finished = Task.from_dict(json.loads((tmp_path / "done" / "tsk_1.json").read_text()))
    visited = [entry.to_step for entry in finished.history if entry.actor == "dispatcher"]
    assert visited == ["plan", "review", "plan", "review", "end"]


def test_recover_returns_stranded_tasks(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink())
    stranded = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        lock_id="lck_1",
    )
    (tmp_path / "tasks" / ".processing" / "tsk_1.json").write_text(
        json.dumps(stranded.to_dict())
    )

    assert harness.recover() == 1
    assert (tmp_path / "tasks" / "tsk_1.json").exists()
    revived = Task.from_dict(json.loads((tmp_path / "tasks" / "tsk_1.json").read_text()))
    assert revived.lock_id is None
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_app.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.app'`

- [ ] **Step 3: Napiš `src/harness/app.py`**

```python
"""Wiring. Jediné místo, kde se porty potkají s konkrétními drivery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from harness.consumer import Consumer
from harness.dispatcher import Dispatcher
from harness.drivers.dummy_behavior import DummyBehavior
from harness.drivers.fifo_strategy import FifoStrategy
from harness.drivers.fs_queue import FilesystemTaskQueue
from harness.drivers.fs_workflows import FilesystemWorkflowRepository
from harness.drivers.stdout_events import StdoutEventSink
from harness.drivers.system_clock import SystemClock
from harness.models import Workflow
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue


@dataclass(frozen=True)
class HarnessLayout:
    root: Path

    @property
    def workflows(self) -> Path:
        return self.root / "workflows"

    @property
    def tasks(self) -> Path:
        return self.root / "tasks"

    @property
    def queues(self) -> Path:
        return self.root / "queues"

    @property
    def done(self) -> Path:
        return self.root / "done"

    @property
    def failed(self) -> Path:
        return self.root / "failed"


class Harness:
    def __init__(
        self,
        *,
        layout: HarnessLayout,
        workflow: Workflow,
        dispatcher: Dispatcher,
        consumers: list[Consumer],
        queues: list[TaskQueue],
        events: EventSink,
        clock: Clock,
    ) -> None:
        self.layout = layout
        self.workflow = workflow
        self.dispatcher = dispatcher
        self.consumers = consumers
        self._queues = queues
        self._events = events
        self._clock = clock

    def recover(self) -> int:
        total = sum(queue.recover() for queue in self._queues)
        if total:
            self._events.emit("recovered", count=total)
        return total

    async def run(
        self, poll_interval: float = 0.2, stop: asyncio.Event | None = None
    ) -> None:
        stop = stop or asyncio.Event()
        self.recover()
        self._events.emit("started", workflow=self.workflow.name)
        await asyncio.gather(
            self._dispatcher_loop(poll_interval, stop),
            *(self._consumer_loop(consumer, poll_interval, stop) for consumer in self.consumers),
        )
        self._events.emit("stopped")

    async def _dispatcher_loop(self, poll_interval: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not self.dispatcher.tick():
                await asyncio.sleep(poll_interval)
            else:
                await asyncio.sleep(0)

    async def _consumer_loop(
        self, consumer: Consumer, poll_interval: float, stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            if not await consumer.tick():
                await asyncio.sleep(poll_interval)
            else:
                await asyncio.sleep(0)


def build(
    root: Path,
    workflow_name: str,
    *,
    events: EventSink | None = None,
    clock: Clock | None = None,
    behavior: ConsumerBehavior | None = None,
    delay: float = 5.0,
    request_changes_once_at: str | None = None,
) -> Harness:
    layout = HarnessLayout(Path(root))
    events = events or StdoutEventSink()
    clock = clock or SystemClock()
    strategy = FifoStrategy()

    workflows = FilesystemWorkflowRepository(layout.workflows)
    workflow = workflows.get(workflow_name)

    failed = FilesystemTaskQueue(name="failed", root=layout.failed, events=events)
    done = FilesystemTaskQueue(name="done", root=layout.done, events=events)
    inbox = FilesystemTaskQueue(
        name="tasks", root=layout.tasks, events=events, quarantine=failed
    )
    step_queues = {
        step: FilesystemTaskQueue(
            name=step, root=layout.queues / step, events=events, quarantine=failed
        )
        for step in workflow.steps()
    }

    behavior = behavior or DummyBehavior(
        clock=clock, delay=delay, request_changes_once_at=request_changes_once_at
    )

    dispatcher = Dispatcher(
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        workflows=workflows,
        strategy=strategy,
        events=events,
        clock=clock,
    )

    consumers = [
        Consumer(
            step=step,
            queue=queue,
            inbox=inbox,
            failed=failed,
            behavior=behavior,
            strategy=strategy,
            events=events,
            clock=clock,
        )
        for step, queue in step_queues.items()
    ]

    return Harness(
        layout=layout,
        workflow=workflow,
        dispatcher=dispatcher,
        consumers=consumers,
        queues=[inbox, *step_queues.values()],
        events=events,
        clock=clock,
    )
```

- [ ] **Step 4: Spusť testy**

Run: `.venv/bin/pytest tests/test_app.py -q`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/harness/app.py tests/test_app.py
git commit -m "feat: wiring a asyncio runtime harnessu"
```

---

### Task 10: CLI

**Files:**
- Create: `src/harness/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build`, `HarnessLayout`, `Task`, `new_task_id`, `SystemClock`
- Produces: `main(argv: list[str] | None = None) -> int` s podpříkazy `init`, `submit`, `run`. Kořen se bere z `--root`, jinak z `HARNESS_HOME`, jinak `~/.harness`.

Podpříkazy:
- `harness init [--root R] [--workflow default]` — založí strom a zapíše výchozí `workflows/default.json`, pokud neexistuje
- `harness submit [--root R] [--workflow default] [--repo NAME] [--data JSON]` — zapíše nový task do `tasks/`, vypíše jeho id
- `harness run [--root R] [--workflow default] [--delay 5.0] [--poll 0.2] [--request-changes-at STEP]` — spustí smyčku

- [ ] **Step 1: Napiš padající testy**

Create `tests/test_cli.py`:

```python
import json

from harness.cli import DEFAULT_WORKFLOW, main
from harness.models import Task


def test_init_creates_layout_and_default_workflow(tmp_path):
    assert main(["init", "--root", str(tmp_path)]) == 0

    definition = json.loads((tmp_path / "workflows" / "default.json").read_text())
    assert definition["start"] == "plan"
    assert {"from": "review", "on": "request_changes", "to": "development"} in definition["transitions"]
    assert (tmp_path / "tasks").is_dir()
    assert (tmp_path / "queues" / "development").is_dir()
    assert (tmp_path / "done").is_dir()
    assert (tmp_path / "failed").is_dir()


def test_init_is_idempotent_and_keeps_edits(tmp_path):
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "default.json").write_text(
        json.dumps({"name": "default", "start": "plan", "transitions": []})
    )

    assert main(["init", "--root", str(tmp_path)]) == 0

    definition = json.loads((tmp_path / "workflows" / "default.json").read_text())
    assert definition["transitions"] == []


def test_submit_writes_a_task(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])

    assert main(
        [
            "submit",
            "--root",
            str(tmp_path),
            "--repo",
            "app-backend",
            "--data",
            '{"request": "rate limiting"}',
        ]
    ) == 0

    task_id = capsys.readouterr().out.strip()
    raw = json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text())
    task = Task.from_dict(raw)
    assert task.repository == "app-backend"
    assert task.workflow_template == DEFAULT_WORKFLOW
    assert task.status is None
    assert task.data == {"request": "rate limiting"}


def test_submit_rejects_invalid_data(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])

    assert main(["submit", "--root", str(tmp_path), "--data", "{rozbite"]) == 2


def test_submit_without_init_fails_cleanly(tmp_path):
    assert main(["submit", "--root", str(tmp_path / "prazdno")]) == 2
```

- [ ] **Step 2: Spusť testy, ať padají**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.cli'`

- [ ] **Step 3: Napiš `src/harness/cli.py`**

```python
"""CLI harnessu."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from harness.app import HarnessLayout, build
from harness.drivers.system_clock import SystemClock
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.workflows import WorkflowNotFound

DEFAULT_WORKFLOW = "default"

DEFAULT_DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "architecture"},
        {"from": "architecture", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}


def _root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    return Path(os.environ.get("HARNESS_HOME", "~/.harness")).expanduser()


def _init(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    layout.workflows.mkdir(parents=True, exist_ok=True)

    definition_path = layout.workflows / f"{args.workflow}.json"
    if not definition_path.exists():
        definition_path.write_text(
            json.dumps(DEFAULT_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    try:
        harness = build(root, args.workflow)
    except WorkflowNotFound as error:
        print(f"chyba: {error}", file=sys.stderr)
        return 2

    print(f"harness připraven v {root}")
    print(f"kroky: {', '.join(harness.workflow.steps())}")
    return 0


def _submit(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"chyba: {root} není inicializovaný, spusť `harness init`", file=sys.stderr)
        return 2

    try:
        data = json.loads(args.data) if args.data else {}
    except json.JSONDecodeError as error:
        print(f"chyba: --data není platný JSON: {error}", file=sys.stderr)
        return 2

    task = Task(
        id=new_task_id(),
        workflow_template=args.workflow,
        created=SystemClock().now(),
        repository=args.repo,
        data=data,
    )
    (layout.tasks / f"{task.id}.json").write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(task.id)
    return 0


def _run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    try:
        harness = build(
            root,
            args.workflow,
            delay=args.delay,
            request_changes_once_at=args.request_changes_at,
        )
    except WorkflowNotFound as error:
        print(f"chyba: {error}", file=sys.stderr)
        return 2

    try:
        asyncio.run(harness.run(poll_interval=args.poll))
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness")
    parser.add_argument("--root", default=None, help="kořen harnessu (jinak HARNESS_HOME)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="založ strom adresářů")
    init.add_argument("--root", default=None)
    init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    init.set_defaults(handler=_init)

    submit = subparsers.add_parser("submit", help="vlož nový task")
    submit.add_argument("--root", default=None)
    submit.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    submit.add_argument("--repo", default=None)
    submit.add_argument("--data", default=None, help="JSON payload")
    submit.set_defaults(handler=_submit)

    run = subparsers.add_parser("run", help="spusť orchestrační smyčku")
    run.add_argument("--root", default=None)
    run.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    run.add_argument("--delay", type=float, default=5.0)
    run.add_argument("--poll", type=float, default=0.2)
    run.add_argument("--request-changes-at", default=None, dest="request_changes_at")
    run.set_defaults(handler=_run)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Spusť testy**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/harness/cli.py tests/test_cli.py
git commit -m "feat: CLI s init, submit a run"
```

---

### Task 11: Architektonický invariant, smoke test a dokumentace

**Files:**
- Create: `tests/test_architecture.py`
- Create: `tests/test_smoke.py`
- Modify: `CLAUDE.md` (celý přepsat)
- Create: `README.md`

**Interfaces:**
- Consumes: všechno předchozí
- Produces: nic pro další tasky — toto je poslední task

- [ ] **Step 1: Napiš test architektonického invariantu**

Create `tests/test_architecture.py`:

```python
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "harness"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_models_imports_nothing_from_the_package():
    assert not {
        module for module in imported_modules(SOURCE / "models.py")
        if module.startswith("harness")
    }


def test_router_only_knows_models():
    imports = {
        module for module in imported_modules(SOURCE / "router.py")
        if module.startswith("harness")
    }
    assert imports == {"harness.models"}


def test_ports_do_not_import_drivers():
    for path in (SOURCE / "ports").glob("*.py"):
        assert not any(
            module.startswith("harness.drivers") for module in imported_modules(path)
        ), f"{path.name} importuje driver"


def test_orchestration_does_not_import_drivers():
    """Dispatcher a consumer znají jen porty. Wiring patří do app.py."""
    for name in ("dispatcher.py", "consumer.py"):
        assert not any(
            module.startswith("harness.drivers")
            for module in imported_modules(SOURCE / name)
        ), f"{name} importuje driver"


def test_only_app_and_cli_wire_drivers():
    wiring = {"app.py", "cli.py"}
    for path in SOURCE.glob("*.py"):
        if path.name in wiring:
            continue
        assert not any(
            module.startswith("harness.drivers") for module in imported_modules(path)
        ), f"{path.name} importuje driver mimo wiring"
```

- [ ] **Step 2: Spusť testy**

Run: `.venv/bin/pytest tests/test_architecture.py -q`
Expected: PASS, 5 passed

- [ ] **Step 3: Napiš smoke test na skutečném filesystemu**

Create `tests/test_smoke.py`:

```python
"""Jeden běh na skutečném filesystemu se zkráceným intervalem."""

import asyncio
import json

from harness.app import build
from harness.cli import main
from harness.models import Task


async def test_task_travels_from_submit_to_done(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    main(["submit", "--root", str(tmp_path), "--repo", "app-backend"])
    task_id = capsys.readouterr().out.strip().splitlines()[-1]

    harness = build(
        tmp_path, "default", delay=0.0, request_changes_once_at="review"
    )
    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(600):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / f"{task_id}.json").exists():
            break
    stop.set()
    await runner

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / f"{task_id}.json").read_text())
    )
    assert finished.status == "end"
    assert finished.repository == "app-backend"

    routed = [entry.to_step for entry in finished.history if entry.actor == "dispatcher"]
    assert routed == [
        "plan",
        "design",
        "architecture",
        "development",
        "review",
        "development",
        "review",
        "end",
    ]
    assert any(entry.outcome == "request_changes" for entry in finished.history)


async def test_unknown_workflow_lands_in_failed_and_loop_survives(tmp_path):
    main(["init", "--root", str(tmp_path)])
    broken = Task(
        id="tsk_broken", workflow_template="neexistuje", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_broken.json").write_text(json.dumps(broken.to_dict()))
    healthy = Task(
        id="tsk_ok", workflow_template="default", created="2026-07-19T10:00:01Z"
    )
    (tmp_path / "tasks" / "tsk_ok.json").write_text(json.dumps(healthy.to_dict()))

    harness = build(tmp_path, "default", delay=0.0)
    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(600):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_ok.json").exists():
            break
    stop.set()
    await runner

    assert (tmp_path / "failed" / "tsk_broken.json").exists()
    assert (tmp_path / "done" / "tsk_ok.json").exists()
```

- [ ] **Step 4: Spusť celou sadu**

Run: `.venv/bin/pytest -q`
Expected: PASS, všechny testy zelené

- [ ] **Step 5: Ověř běh ručně**

```bash
cd ~/harness_v2
rm -rf /tmp/harness-demo
.venv/bin/harness init --root /tmp/harness-demo
.venv/bin/harness submit --root /tmp/harness-demo --repo app-backend --data '{"request":"rate limiting"}'
.venv/bin/harness run --root /tmp/harness-demo --delay 0.5 --request-changes-at review
```

Expected: na stdout `started`, série `dispatched`/`consumed` přes `plan → design → architecture → development → review → development → review`, pak `finished`. Ukonči `Ctrl-C` a ověř:

```bash
ls /tmp/harness-demo/done
python3 -c "import json,sys,glob; print(json.load(open(glob.glob('/tmp/harness-demo/done/*.json')[0]))['history'])"
```

- [ ] **Step 6: Ověř recovery po zabití procesu**

```bash
rm -rf /tmp/harness-crash
.venv/bin/harness init --root /tmp/harness-crash
.venv/bin/harness submit --root /tmp/harness-crash
.venv/bin/harness run --root /tmp/harness-crash --delay 30 &
sleep 3
kill -9 %1
find /tmp/harness-crash -path '*.processing/*.json'
```

Expected: `find` najde uvízlý task v nějakém `.processing/`. Pak:

```bash
.venv/bin/harness run --root /tmp/harness-crash --delay 0.5 --request-changes-at review
```

Expected: na stdout `recovered count=1`, běh pokračuje a task doputuje do `done/`.

- [ ] **Step 7: Přepiš `CLAUDE.md`**

Nahraď **celý** obsah — současný popisuje mrtvou architekturu předchozího pokusu.

```markdown
# harness_v2 — orientace pro Claude

Orchestrační harness pro více agentů. Jednotkou práce je **task**, který putuje
mezi frontami podle **workflow** — malého state machine s explicitními hranami.

Spec fáze 1: `docs/superpowers/specs/2026-07-19-orchestration-phase1-design.md`
Plán fáze 1: `docs/superpowers/plans/2026-07-19-orchestration-phase1.md`

Projekt se staví **po fázích**. Fáze 1 je POC orchestrační smyčky; skutečné
agenty, perzistentní úložiště ani git v ní nejsou.

## Invarianty — nerozbíjet

1. **Vyměnit se smí driver, nikdy jeho okolí.** Každá pohyblivá část leží za
   portem v `ports/`. `dispatcher.py` ani `consumer.py` nesmí importovat nic
   z `drivers/` — wiring patří výhradně do `app.py`. Hlídá to
   `tests/test_architecture.py`.
2. **Rozhodování má tři oddělené role.** `ConsumerBehavior` říká *co se stalo*,
   dispatcher *kam to jde dál*, consumer nerozhoduje nic. V `consumer.py` nesmí
   být větev závislá na hodnotě outcome; test to kontroluje čtením zdrojáku.
3. **Status mění výhradně dispatcher.** `lastOutcome` zapisuje výhradně consumer.
4. **Router je čistá funkce.** `route()` nesmí sáhnout na I/O, čas ani stav.

## Práce tady

```sh
.venv/bin/pytest -q
```

Python je **3.11** (`/Users/rem/.local/bin/python3.11`), na stroji **není `uv`** —
plain `venv` + `pip install -e ".[dev]"`. Runtime nemá žádné produkční závislosti.

Testy běží na in-memory driverech a `FakeClock` — bez disku a bez skutečného
čekání. Nikdy nepiš test, který spí v reálném čase.

## Git konvence

**Commituj přímo do `main`.** V této fázi je to zamýšlený postup — nezakládej
branch, neotevírej PR a neptej se. Platí to pro repo harnessu samotného.

## Mapa modulů

Závislosti tečou striktně dolů, cykly nejsou.

| Vrstva | Moduly |
|---|---|
| Základ | `models` (neimportuje nic z balíku), `ids` |
| Logika | `router` (zná jen `models`) |
| Porty | `ports/{queue,workflows,strategy,behavior,events,clock}` |
| Orchestrace | `dispatcher`, `consumer` — znají jen porty |
| Drivery | `drivers/{fs_queue,fs_workflows,fifo_strategy,dummy_behavior,stdout_events,system_clock,memory}` |
| Okraje | `app` (wiring), `cli` |

## Co je za co zodpovědné

- **`TaskQueue`** — inbox, fronty kroků, `done/` i `failed/` jsou instance téhož
  portu. Terminální stavy jsou prostě fronty, které nikdo nekonzumuje.
- **`claim()`** je atomický `rename` do `<queue>/.processing/`. Jedna operace řeší
  lease, idempotenci i původ po pádu.
- **`END = "end"`** je vyhrazený uzel. Není to „stav bez odchozích hran" —
  překlep by tak tiše vypadal jako úspěch.

## Gotchas

- **`.processing/` má každá fronta vlastní.** Proto se po pádu nemusí nikam
  ukládat, odkud task pochází — recovery ho vrátí do fronty, pod kterou leží.
- **Prohraný závod o `claim()` není chyba.** `os.replace` vyhodí
  `FileNotFoundError`, driver vrátí `None` a smyčka si vezme další task.
- **Rozbitý JSON nemá komu připsat historii.** Soubor se přesune do `failed/`
  tak, jak je, a důvod nese jen event.
- **`DummyBehavior` musí vracet `done` deterministicky.** `request_changes_once_at`
  vrátí `REQUEST_CHANGES` jen při prvním průchodu daného tasku daným krokem;
  jinak by se smyčka točila donekonečna.

## Operátor

Ondrej Pajgrt — „Ondrej" / „Rem". GitHub `onpaj`. Europe/Prague. Kontext stroje
(NanoClaw, podman) je v `~/CLAUDE.md`.

Předchozí pokus o tuto myšlenku leží v historii tohoto repa na commitu `7bc0e6e`;
`main` byl vyprázdněn commitem `b7cab63`, aby se stavělo po fázích od začátku.
```

- [ ] **Step 8: Napiš `README.md`**

```markdown
# harness

Orchestrační harness pro více agentů. Jednotkou práce je **task**; ten putuje
mezi frontami podle **workflow**, což je malý state machine s explicitními
hranami pro každý outcome.

Fáze 1 je POC celé smyčky: task proteče workflow od `start` do `end`, ale práci
zatím zastupuje dummy behavior. Skutečné agenty, perzistentní úložiště a git
přijdou v dalších fázích.

## Instalace

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Rychlý start

```sh
harness init --root /tmp/harness-demo
harness submit --root /tmp/harness-demo --repo app-backend \
    --data '{"request": "add rate limiting"}'
harness run --root /tmp/harness-demo --delay 0.5 --request-changes-at review
```

## Jak práce teče

```
tasks/ ──dispatcher──> queues/<krok>/ ──consumer──> tasks/ ──dispatcher──> …
                                                                    │
                                                              done/ nebo failed/
```

1. Dispatcher vezme task z `tasks/`, načte workflow podle `workflowTemplate`
   a podle dvojice `(status, lastOutcome)` najde cílový krok.
2. Přepíše `status`, připíše řádek do `history` a přesune task do `queues/<krok>/`.
3. Consumer nad tou frontou předá task `ConsumerBehavior`, dostane zpět outcome
   (`done` nebo `request_changes`), zapíše ho a vrátí task do `tasks/`.
4. Až hrana ukáže na `end`, task končí v `done/`. Cokoli nesměrovatelného končí
   v `failed/` s důvodem v historii.

## Workflow

```json
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan", "on": "done", "to": "design"},
    {"from": "review", "on": "done", "to": "end"},
    {"from": "review", "on": "request_changes", "to": "development"}
  ]
}
```

Zpětné hrany jsou explicitní a nemusí být symetrické. Retry téhož kroku se
vyjádří jako `to == from`.

## Architektura

Každá pohyblivá část leží za portem a vymění se záměnou driveru:

| Port | Fáze 1 | Později |
|---|---|---|
| `TaskQueue` | adresář s JSON soubory | storage queue |
| `EnqueueStrategy` | FIFO podle `created` | priority, fair-share |
| `WorkflowRepository` | `workflows/<name>.json` | DB, API |
| `ConsumerBehavior` | sleep → `done` | skutečný agent |
| `EventSink` | řádky na stdout | OTel |

Rozhodování je rozděleno na tři role, které se nepřekrývají: `ConsumerBehavior`
říká *co se stalo*, dispatcher *kam to jde dál*, consumer jen doručuje.
```

- [ ] **Step 9: Spusť celou sadu naposledy**

Run: `.venv/bin/pytest -q`
Expected: PASS, vše zelené

- [ ] **Step 10: Commit**

```bash
git add tests/test_architecture.py tests/test_smoke.py CLAUDE.md README.md
git commit -m "test: architektonické invarianty a smoke test; docs: přepis CLAUDE.md a README"
```

---

## Ověření hotovosti fáze 1

Po Tasku 11 musí platit všech šest bodů ze specu:

1. `harness init` založí strom adresářů podle workflow — Task 10, `test_init_creates_layout_and_default_workflow`
2. Task doputuje z `tasks/` do `done/` přes všech pět kroků a jednu zpětnou hranu — Task 11, `test_task_travels_from_submit_to_done`
3. Na stdout je z eventů čitelné, co se s taskem v každém kroku stalo — Task 8 + ruční ověření v Tasku 11, Step 5
4. `history` doputovaného tasku ten průběh věrně popisuje — Task 11, `test_task_travels_from_submit_to_done`
5. Task s neznámým `workflowTemplate` skončí v `failed/` a smyčka běží dál — Task 11, `test_unknown_workflow_lands_in_failed_and_loop_survives`
6. Zabití procesu uprostřed běhu a restart vede k dokončení tasku — Task 11, Step 6
