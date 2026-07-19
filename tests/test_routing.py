from datetime import datetime, timezone

import pytest
import yaml

from agentharness.dispatch.routing import route_handoffs
from agentharness.models import AgentDef, Handoff, Result, Task, TaskArtifacts
from agentharness.registry.agents import AgentRegistry

OUTPUT_REF = "9f3a1c0000000000000000000000000000000000"


@pytest.fixture()
def registry(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    for name, targets in (("writer", ["reviewer"]), ("reviewer", []), ("publisher", [])):
        (d / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "description": f"{name} agent",
                    "allowed_tools": ["Read"],
                    "can_handoff_to": targets,
                }
            )
        )
    return AgentRegistry.load(d)


@pytest.fixture()
def writer(registry):
    return registry.get("writer")


def parent(**over) -> Task:
    base = dict(
        task_id="t_parent",
        trace_id="tr_1",
        agent="writer",
        repo="app",
        intent="draft",
        idempotency_key="k",
        priority=2,
        created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    base.update(over)
    return Task(**base)


def handoff(agent="reviewer", **over) -> Handoff:
    base = dict(agent=agent, intent="review")
    base.update(over)
    return Handoff(**base)


def test_allowed_handoff_becomes_a_child_task(registry, writer):
    routed = route_handoffs(parent(), writer, Result(status="ok", handoffs=[handoff()]), OUTPUT_REF, registry)

    assert len(routed) == 1
    assert routed[0].accepted is True
    child = routed[0].task
    assert child.agent == "reviewer"
    assert child.trace_id == "tr_1"
    assert child.parent_task_id == "t_parent"
    assert child.repo == "app"
    assert child.attempt == 1
    assert child.task_id != "t_parent"


def test_child_inherits_the_parents_output_commit_as_its_base(registry, writer):
    """Artifact inheritance: the child builds its worktree from the parent's commit."""
    routed = route_handoffs(parent(), writer, Result(status="ok", handoffs=[handoff()]), OUTPUT_REF, registry)
    assert routed[0].task.artifacts.base_ref == OUTPUT_REF


def test_child_carries_payload_and_declared_inputs(registry, writer):
    h = handoff(payload={"checklist": "editorial"}, artifacts=TaskArtifacts(inputs=["draft.md"]))
    routed = route_handoffs(parent(), writer, Result(status="ok", handoffs=[h]), OUTPUT_REF, registry)

    assert routed[0].task.payload == {"checklist": "editorial"}
    assert routed[0].task.artifacts.inputs == ["draft.md"]


def test_child_inherits_priority_and_schedule(registry, writer):
    p = parent(schedule_id="daily-news")
    routed = route_handoffs(p, writer, Result(status="ok", handoffs=[handoff()]), OUTPUT_REF, registry)

    assert routed[0].task.priority == 2
    assert routed[0].task.schedule_id == "daily-news"


def test_handoff_outside_the_allow_list_is_rejected(registry, writer):
    """publisher is a real agent, but writer may not route to it."""
    routed = route_handoffs(
        parent(), writer, Result(status="ok", handoffs=[handoff("publisher")]), OUTPUT_REF, registry
    )

    assert routed[0].accepted is False
    assert routed[0].task is None
    assert "writer" in routed[0].reason and "publisher" in routed[0].reason


def test_handoff_to_an_unregistered_agent_is_rejected_distinctly(registry, writer):
    routed = route_handoffs(
        parent(), writer, Result(status="ok", handoffs=[handoff("ghost")]), OUTPUT_REF, registry
    )

    assert routed[0].accepted is False
    assert "unknown agent" in routed[0].reason


def test_no_handoffs_routes_nothing(registry, writer):
    assert route_handoffs(parent(), writer, Result(status="ok"), OUTPUT_REF, registry) == []


def test_mixed_handoffs_accept_and_reject_independently(registry, writer):
    result = Result(status="ok", handoffs=[handoff("reviewer"), handoff("publisher")])
    routed = route_handoffs(parent(), writer, result, OUTPUT_REF, registry)

    assert [r.accepted for r in routed] == [True, False]


def test_routing_is_idempotent_across_a_crash_retry(registry, writer):
    """Same parent + same result must yield the same keys, so a re-route dedupes."""
    result = Result(status="ok", handoffs=[handoff("reviewer"), handoff("reviewer")])
    first = route_handoffs(parent(), writer, result, OUTPUT_REF, registry)
    second = route_handoffs(parent(), writer, result, OUTPUT_REF, registry)

    keys_a = [r.task.idempotency_key for r in first]
    keys_b = [r.task.idempotency_key for r in second]
    assert keys_a == keys_b
    assert len(set(keys_a)) == 2, "sibling handoffs must not collide with each other"
