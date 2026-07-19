"""Point-in-time metrics derived from the queue, the store, and the limiters.

Everything here is read-only and cheap enough to compute per dashboard request;
there is no metrics daemon and no separate counter state to fall out of sync.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agentharness.config import Config
from agentharness.models import RunRecord
from agentharness.queue.base import Queue
from agentharness.store.runs import RunStore

#: How many runs to pull when computing windowed aggregates. Generous enough
#: that a 24h window is never truncated in practice, bounded so a long-lived
#: install cannot page the whole table into memory.
RUN_SCAN_LIMIT = 5000

AGENT_YAML_SUFFIXES = (".yaml", ".yml")


@dataclass
class Snapshot:
    queue_depths: dict[str, int] = field(default_factory=dict)
    dead_depths: dict[str, int] = field(default_factory=dict)
    active: dict[str, int] = field(default_factory=dict)
    runs_24h: int = 0
    failures_24h: int = 0
    cost_24h: float = 0.0
    paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- helpers -----------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _runs_since(store: RunStore, since: datetime) -> list[RunRecord]:
    cutoff = _aware(since)
    return [
        run
        for run in store.recent_runs(limit=RUN_SCAN_LIMIT)
        if _aware(run.started_at) >= cutoff
    ]


def known_agents(cfg: Config, queue: Queue) -> list[str]:
    """Every agent the harness knows about: defined, or with a queue directory.

    Agents are listed even at depth zero, so the dashboard shows an idle agent
    rather than silently omitting it.
    """
    names: set[str] = set()

    agents_dir = Path(cfg.agents_dir)
    if agents_dir.is_dir():
        names.update(
            p.stem
            for p in agents_dir.iterdir()
            if p.is_file() and p.suffix in AGENT_YAML_SUFFIXES
        )

    for root in {Path(cfg.queues_dir), Path(getattr(queue, "root", cfg.queues_dir))}:
        if root.is_dir():
            names.update(p.name for p in root.iterdir() if p.is_dir())

    return sorted(names)


# --- aggregates --------------------------------------------------------------


def cost_by_agent(store: RunStore, since: datetime) -> dict[str, float]:
    """Total USD per agent for runs started at or after `since`.

    Runs with no recorded cost contribute nothing and do not create an entry.
    """
    totals: dict[str, float] = {}
    for run in _runs_since(store, since):
        if run.total_cost_usd is None:
            continue
        totals[run.agent] = totals.get(run.agent, 0.0) + run.total_cost_usd
    return totals


def cost_by_trace(store: RunStore, trace_id: str) -> float:
    """Total USD across every run in a trace. Unknown traces cost nothing."""
    return sum(
        run.total_cost_usd or 0.0 for run in store.trace_runs(trace_id)
    )


def latency_percentiles(store: RunStore, since: datetime) -> dict[str, float]:
    """p50/p95 of run duration in milliseconds, linearly interpolated."""
    durations = sorted(
        float(run.duration_ms)
        for run in _runs_since(store, since)
        if run.duration_ms is not None
    )
    return {
        "p50": _percentile(durations, 0.50),
        "p95": _percentile(durations, 0.95),
    }


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


# --- snapshot ----------------------------------------------------------------


def snapshot(
    cfg: Config,
    queue: Queue,
    store: RunStore,
    limiter: Any,
    gate: Any,
) -> Snapshot:
    agents = known_agents(cfg, queue)
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = _runs_since(store, since)

    return Snapshot(
        queue_depths={agent: queue.depth(agent) for agent in agents},
        dead_depths={agent: len(queue.list_dead(agent)) for agent in agents},
        active={agent: limiter.active(agent) for agent in agents},
        runs_24h=len(recent),
        failures_24h=sum(1 for run in recent if run.status != "ok"),
        cost_24h=sum(run.total_cost_usd or 0.0 for run in recent),
        paused=bool(gate.paused),
    )
