"""Handoff routing.

Agents *propose* handoffs; the orchestrator *accepts* them. A target outside the
emitting agent's `can_handoff_to` allow-list is rejected and recorded, never
enqueued. This is what keeps the topology auditable and prevents runaway fan-out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from agentharness.ids import new_task_id
from agentharness.models import AgentDef, Handoff, Result, Task, TaskArtifacts
from agentharness.registry.agents import AgentRegistry


@dataclass
class RoutedHandoff:
    handoff: Handoff
    accepted: bool
    task: Task | None = None
    reason: str | None = None


def idempotency_key(parent: Task, index: int, handoff: Handoff) -> str:
    """Deterministic, so a crash between 'handoff written' and 'handoff enqueued'
    cannot produce a duplicate child on retry."""
    return f"{parent.task_id}:{index}:{handoff.agent}:{handoff.intent}"


def route_handoffs(
    parent: Task,
    agent: AgentDef,
    result: Result,
    output_ref: str,
    registry: AgentRegistry,
) -> list[RoutedHandoff]:
    routed: list[RoutedHandoff] = []

    for index, handoff in enumerate(result.handoffs):
        if handoff.agent not in registry.names():
            routed.append(
                RoutedHandoff(
                    handoff=handoff,
                    accepted=False,
                    reason=f"unknown agent {handoff.agent!r}",
                )
            )
            continue

        if handoff.agent not in agent.can_handoff_to:
            routed.append(
                RoutedHandoff(
                    handoff=handoff,
                    accepted=False,
                    reason=(
                        f"agent {agent.name!r} may not hand off to {handoff.agent!r}"
                    ),
                )
            )
            continue

        child = Task(
            task_id=new_task_id(),
            trace_id=parent.trace_id,
            parent_task_id=parent.task_id,
            agent=handoff.agent,
            repo=parent.repo,
            intent=handoff.intent,
            payload=dict(handoff.payload),
            artifacts=TaskArtifacts(
                base_ref=output_ref,
                inputs=list(handoff.artifacts.inputs),
            ),
            idempotency_key=idempotency_key(parent, index, handoff),
            priority=parent.priority,
            attempt=1,
            created_at=datetime.now(timezone.utc),
            schedule_id=parent.schedule_id,
        )
        routed.append(RoutedHandoff(handoff=handoff, accepted=True, task=child))

    return routed
