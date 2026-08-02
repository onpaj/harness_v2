"""The port the UI looks through.

All the API knows about the harness. Today's driver is an in-memory projection,
tomorrow it could be a read model in a database — the API won't notice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from harness.models import Task

TODO_COLUMN = "todo"
"""Column for freshly loaded inbox tasks that have not started yet (status=None)."""

DONE_COLUMN = "done"
"""Column for tasks that reached END — whether the workflow ran to completion
or the self-healer took the failure over (ADR-0024). Which of the two it was is
in the task's history, not in a column of its own."""

FAILED_COLUMN = "failed"
"""Column for tasks that cannot be routed. With self-healing enabled it drains
as the healer takes each failure over into `done` — what remains is what the
healer declined to heal, and is the operator's to deal with."""

UNKNOWN_WORKFLOW = "unknown"
"""Reserved tab for tasks whose workflow_template names no discovered
definition. Never a real workflow's name (workflow names come from filenames,
and "unknown" collides with nothing in practice)."""

UNKNOWN_WORKFLOW_LABEL = "No workflow"
"""What the UI calls the `unknown` tab. The internal name stays `unknown` (it is
the tab's key, in `Board.workflow()` and in the DOM), but "unknown" names the
harness's failure to classify rather than the thing an operator is looking at —
these are tasks that run *outside* any workflow definition."""

UNKNOWN_WORKFLOW_NOTE = (
    "Tasks that belong to no workflow file: a Process targeting a bare step, "
    "or a task whose workflow was renamed or removed. They still run — the "
    "dispatcher just has no graph to route them along after the current step."
)
"""One-line explanation rendered above the `unknown` tab's columns."""

COLUMN_INBOX = "inbox"
"""Kind of the `todo` column — arrived, not yet dispatched into a workflow."""

COLUMN_STEP = "step"
"""Kind of a column that is a real step of the tab's workflow."""

COLUMN_TERMINAL = "terminal"
"""Kind of `done`/`failed` — where a task ends up, not somewhere it works.

The three kinds exist so the board can stop rendering two different vocabularies
identically: a step column is a place *in* a workflow, an inbox/terminal column is
a state of the task *relative to* any workflow. Purely a view concern — nothing in
the router or dispatcher has ever read a column kind (invariant #8)."""

LIFECYCLE_DESCRIPTIONS = {
    TODO_COLUMN: "Arrived from a source, waiting for the dispatcher to place it.",
    DONE_COLUMN: (
        "Reached `end` — the workflow ran to completion, or the self-healer "
        "took the failure over. The task's history says which."
    ),
    FAILED_COLUMN: (
        "Could not be routed or delivered. Restartable from the task detail. "
        "The self-healer drains this queue when a process is configured; what "
        "stays here is what it declined to heal — that one is yours."
    ),
}
"""What the three non-step columns mean. They are the same on every tab — unlike a
step's description, which is the workflow's own (`Workflow.descriptions`)."""


@dataclass(frozen=True)
class AgentActivity:
    """One handling of a task by an agent — a single `consumer:<agent>` line of
    a task's history, lifted out and tagged with the task it belongs to.

    An agent's name is a step's name (invariant: `behavior_for(step)` looks the
    persona up by the step), so "tasks handled by agent X" is exactly the set of
    `consumer:X` history entries across every task the projection still holds. A
    task re-run through the same step (the `request_changes` loop) contributes one
    entry per pass — so multiple rows for one task is expected, not a duplicate.
    """

    task_id: str
    title: str
    at: str
    outcome: str | None
    summary: str | None
    reason: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "title": self.title,
            "at": self.at,
            "outcome": self.outcome,
            "summary": self.summary,
            "reason": self.reason,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "model": self.model,
        }


@dataclass(frozen=True)
class BoardColumn:
    name: str
    tasks: tuple[Task, ...]
    kind: str = COLUMN_STEP
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "tasks": [task.to_dict() for task in self.tasks],
        }


@dataclass(frozen=True)
class BoardTab:
    name: str
    columns: tuple[BoardColumn, ...]

    def column(self, name: str) -> BoardColumn | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    @property
    def is_unknown(self) -> bool:
        return self.name == UNKNOWN_WORKFLOW

    @property
    def label(self) -> str:
        """What to call this tab in the UI."""
        return UNKNOWN_WORKFLOW_LABEL if self.is_unknown else self.name

    @property
    def task_count(self) -> int:
        return sum(len(column.tasks) for column in self.columns)

    def visible_columns(self) -> tuple[BoardColumn, ...]:
        """The columns worth rendering.

        A workflow tab renders all of them — an empty step column is meaningful
        (it is part of the graph, just idle). The `unknown` tab is a catch-all
        whose column set is the union of *every* step in the harness, so its
        empty columns say nothing about anything; only the occupied ones are
        rendered. Filtering here rather than in the projection keeps the port's
        snapshot complete for the callers that read the full step set out of it
        (`routes._target_option_groups`, `routes._new_step_warnings`)."""
        if not self.is_unknown:
            return self.columns
        return tuple(column for column in self.columns if column.tasks)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "columns": [column.to_dict() for column in self.columns]}


@dataclass(frozen=True)
class Board:
    revision: int
    workflows: tuple[BoardTab, ...]
    repository_names: tuple[str, ...] = ()
    """Sorted names of every repository the harness has registered — for
    populating the board's repository filter `<select>`. Pure view data: never
    consulted by anything that routes a task (invariant #8)."""

    def workflow(self, name: str) -> BoardTab | None:
        for tab in self.workflows:
            if tab.name == name:
                return tab
        return None

    def default_tab(self) -> str | None:
        """The primary workflow `"development"` if present, else the first tab
        alphabetically, else None (an empty board — no workflow definitions and
        no orphaned tasks)."""
        names = [tab.name for tab in self.workflows]
        if "development" in names:
            return "development"
        return names[0] if names else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "workflows": [tab.to_dict() for tab in self.workflows],
            "repository_names": list(self.repository_names),
        }


class BoardView(ABC):
    """Read-only view of the harness state."""

    @abstractmethod
    def snapshot(self) -> Board:
        """The current board."""

    @abstractmethod
    def get(self, task_id: str) -> Task | None:
        """Task by id, or None."""

    @abstractmethod
    def agent_history(self, name: str) -> tuple[AgentActivity, ...]:
        """Every task-handling this agent did, newest first.

        Read straight off the `consumer:<name>` entries of the tasks the view
        still holds (active plus archived-while-in-memory) — no separate store,
        so it goes only as deep as the running projection. A restart that
        re-hydrates from the queues starts the log over; that is by design."""

    @abstractmethod
    def subscribe(self) -> AsyncIterator[int]:
        """A stream of revision numbers. The first value is the current revision."""
