"""`ScheduledTrigger`: a `Trigger` that fires a check once per interval bucket.

The cadence is a clock-gate, not a loop: `poll()` compares the current time's
interval bucket (`floor(epoch(now) / interval)`) against the last one it fired
and returns `[]` between fires. On a fresh bucket it runs the injected `Check`
and emits one task per `Observation`, targeting either a workflow or a single
step (never both) — placement stays the dispatcher's.

The `dedup_key` is deliberately non-constant so `SourcePoller._seen` doesn't
suppress every fire after the first: `per-interval` keys on the bucket (one
fire per period), `per-state` keys on the observation's `state_key` (re-fire
when the observed state changes). No `data.source` is stamped — a trigger
reflects nothing outward (see `Trigger` in `ports/source.py`).
"""

from __future__ import annotations

from datetime import datetime
from math import floor

from harness.ids import new_task_id
from harness.models import Task
from harness.ports.clock import Clock
from harness.ports.source import Trigger, dedup_key
from harness.ports.triggers import Check, Observation


class ScheduledTrigger(Trigger):
    def __init__(
        self,
        *,
        name: str,
        clock: Clock,
        interval: float,
        check: Check,
        workflow: str | None = None,
        step: str | None = None,
        repository: str | None = None,
        worktree_root: str | None = None,
        dedup: str = "per-interval",
    ) -> None:
        if (workflow is None) == (step is None):
            raise ValueError("exactly one of workflow/step must be set")
        if dedup not in ("per-interval", "per-state"):
            raise ValueError(f"unknown dedup strategy: {dedup!r}")
        self.kind = f"scheduled:{name}"
        self._clock = clock
        self._interval = interval
        self._check = check
        self._workflow = workflow
        self._step = step
        self._repository = repository
        self._worktree_root = worktree_root
        self._dedup = dedup
        self._last_bucket: int | None = None

    def poll(self) -> list[Task]:
        now = self._clock.now()
        bucket = self._bucket(now)
        if bucket == self._last_bucket:
            return []
        self._last_bucket = bucket
        observations = self._check.evaluate()
        return [self._task_for(obs, bucket, now) for obs in observations]

    def _bucket(self, now: str) -> int:
        epoch = datetime.fromisoformat(now.replace("Z", "+00:00")).timestamp()
        return floor(epoch / self._interval)

    def _task_for(self, obs: Observation, bucket: int, now: str) -> Task:
        task_id = new_task_id()
        return Task(
            id=task_id,
            created=now,
            workflow_template=self._workflow,
            step=self._step,
            repository=obs.repository or self._repository,
            worktree=(f"{self._worktree_root}/{task_id}" if self._worktree_root else None),
            dedup_key=self._dedup_key(bucket, obs),
            data={**obs.data},
        )

    @property
    def _target_str(self) -> str:
        return f"wf:{self._workflow}" if self._workflow else f"step:{self._step}"

    def _dedup_key(self, bucket: int, obs: Observation) -> str:
        if self._dedup == "per-interval":
            return dedup_key(self.kind, self._target_str, bucket)
        if obs.state_key is None:
            raise ValueError("a per-state check must supply a state_key")
        return dedup_key(self.kind, self._target_str, obs.state_key)
