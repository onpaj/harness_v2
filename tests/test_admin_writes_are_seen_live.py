"""Both catalogs are read fresh on every lookup (no cache to invalidate), so a
write through the admin port must be visible to the runtime read port without
a restart. Covers plan-01.md's rough-plan step 7 / architecture-01.md's
confirmation that this holds for both agents and workflow routing."""

import json

from harness.drivers.fs_agents import FilesystemAgentAdmin, FilesystemAgentCatalog
from harness.drivers.fs_workflows import FilesystemWorkflowAdmin, FilesystemWorkflowRepository
from harness.ports.agent_admin import AgentFields


def test_agent_written_through_admin_is_seen_by_the_runtime_catalog(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)
    catalog = FilesystemAgentCatalog(tmp_path)

    admin.write(
        "reviewer",
        AgentFields(prompt="you are a reviewer", model="opus", allowed_outcomes=("done",)),
    )

    spec = catalog.get("reviewer")
    assert spec.prompt == "you are a reviewer"
    assert spec.model == "opus"

    admin.write(
        "reviewer",
        AgentFields(prompt="you are a stricter reviewer", allowed_outcomes=("done",)),
    )

    assert catalog.get("reviewer").prompt == "you are a stricter reviewer"


def test_workflow_written_through_admin_is_seen_by_the_runtime_repository(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)
    repository = FilesystemWorkflowRepository(tmp_path)

    admin.write_raw(
        "default",
        json.dumps(
            {"start": "plan", "transitions": [{"from": "plan", "on": "done", "to": "review"}]}
        ),
    )

    workflow = repository.get("default")
    assert workflow.target("plan", "done") == "review"

    # Editing the transition through the admin — a new edge to "land" —
    # is routed by the dispatcher's next tick without a harness restart:
    # `Dispatcher.tick` resolves the workflow per task via `WorkflowRepository
    # .get`, which re-reads the file fresh every time (no cache to invalidate).
    admin.write_raw(
        "default",
        json.dumps(
            {"start": "plan", "transitions": [{"from": "plan", "on": "done", "to": "land"}]}
        ),
    )

    assert repository.get("default").target("plan", "done") == "land"
