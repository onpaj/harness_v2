"""`ScheduledTrigger`: a `Trigger` that fires a check once per occurrence.

The cadence is a clock-gate, not a loop: `poll()` computes the current
occurrence identity and compares it against the last one it fired, returning
`[]` between fires. One occurrence function serves both cadences — an interval
cadence's occurrence is the bucket `floor(epoch(now) / interval)`; a cron
cadence's occurrence is `CronSchedule.occurrence_at_or_before(now)`, the most
recent matching UTC timestamp <= `now` (see `ports/triggers.py`). On a fresh
occurrence it runs the injected `Check` and emits one task per `Observation`,
targeting either a workflow or a single step (never both) — placement stays
the dispatcher's.

The `dedup_key` is deliberately non-constant so `SourcePoller._seen` doesn't
suppress every fire after the first: `per-interval` keys on the occurrence (one
fire per period/occurrence, for either cadence), `per-state` keys on the
observation's `state_key` (re-fire when the observed state changes, regardless
of cadence). No `data.source` is stamped — a trigger reflects nothing outward
(see `Trigger` in `ports/source.py`).

A Process's `sink` rides along as data: a non-`none` `sink` is stamped into the
task as `data["sink"] = {"kind": ...}` — the *destination* identity an
outbound-only sink driver (e.g. `SlackWebhookSink`) routes on, distinct from
the origin `data.source` this trigger deliberately never writes (invariant
#40). The stamp lands after the observation's data is merged, so a check can
never clobber the operator's declared destination.
"""

from __future__ import annotations

from datetime import datetime
from math import floor
from typing import Callable, Hashable

from harness.ids import new_task_id
from harness.models import Task
from harness.ports.clock import Clock
from harness.ports.source import Trigger, dedup_key
from harness.ports.triggers import Check, CronSchedule, Observation


class ScheduledTrigger(Trigger):
    def __init__(
        self,
        *,
        name: str,
        clock: Clock,
        check: Check,
        interval: float | None = None,
        cron: CronSchedule | None = None,
        workflow: str | None = None,
        step: str | None = None,
        repository: str | None = None,
        worktree_root: str | None = None,
        dedup: str = "per-interval",
        sink: dict | None = None,
    ) -> None:
        if (workflow is None) == (step is None):
            raise ValueError("exactly one of workflow/step must be set")
        if (interval is None) == (cron is None):
            raise ValueError("exactly one of interval/cron must be set")
        if dedup not in ("per-interval", "per-state"):
            raise ValueError(f"unknown dedup strategy: {dedup!r}")
        self.kind = f"scheduled:{name}"
        # Public on purpose: wiring (`cli._run`) reads it to warn when a
        # process declares a slack sink but no `SLACK_WEBHOOK_URL` is set.
        self.sink = sink
        self._clock = clock
        self._check = check
        self._workflow = workflow
        self._step = step
        self._repository = repository
        self._worktree_root = worktree_root
        self._dedup = dedup
        self._interval = interval
        self._cron = cron
        self._occurrence: Callable[[str], Hashable]
        if cron is not None:
            self._occurrence = self._cron_occurrence
        else:
            self._occurrence = self._interval_occurrence
        self._last_occurrence: Hashable | None = None

    def poll(self) -> list[Task]:
        now = self._clock.now()
        occurrence = self._occurrence(now)
        if occurrence == self._last_occurrence:
            return []
        self._last_occurrence = occurrence
        observations = self._check.evaluate()
        return [self._task_for(obs, occurrence, now) for obs in observations]

    def _interval_occurrence(self, now: str) -> int:
        epoch = datetime.fromisoformat(now.replace("Z", "+00:00")).timestamp()
        return floor(epoch / self._interval)

    def _cron_occurrence(self, now: str) -> str:
        return self._cron.occurrence_at_or_before(now)

    def _task_for(self, obs: Observation, occurrence: Hashable, now: str) -> Task:
        task_id = new_task_id()
        data = {**obs.data}
        # Destination identity, after the merge — an observation's data must
        # never overwrite the operator's declared sink.
        if self.sink is not None and self.sink.get("kind") != "none":
            data["sink"] = {"kind": self.sink["kind"]}
        return Task(
            id=task_id,
            created=now,
            workflow_template=self._workflow,
            step=self._step,
            repository=obs.repository or self._repository,
            worktree=(f"{self._worktree_root}/{task_id}" if self._worktree_root else None),
            dedup_key=self._dedup_key(occurrence, obs),
            data=data,
        )

    @property
    def _target_str(self) -> str:
        return f"wf:{self._workflow}" if self._workflow else f"step:{self._step}"

    def _dedup_key(self, occurrence: Hashable, obs: Observation) -> str:
        if self._dedup == "per-interval":
            return dedup_key(self.kind, self._target_str, occurrence)
        if obs.state_key is None:
            raise ValueError("a per-state check must supply a state_key")
        return dedup_key(self.kind, self._target_str, obs.state_key)
