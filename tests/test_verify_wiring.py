"""build() serves a workflow whose verify step is finisher-bound to VerifyBehavior."""

from __future__ import annotations

import json

from harness.app import HarnessLayout, build
from harness.behaviors.verify import VerifyBehavior
from harness.drivers.memory import MemoryCommandRunner, MemoryRepositoryRegistry

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "verify"},
        {"from": "verify", "on": "done", "to": "land"},
        {"from": "verify", "on": "request_changes", "to": "development"},
        {"from": "land", "on": "done", "to": "end"},
    ],
    "finishers": {"verify": "verify"},
}


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


def test_verify_step_gets_verify_behavior(tmp_path):
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        command_runner=MemoryCommandRunner(),
        repository_registry=MemoryRepositoryRegistry({}),
    )
    by_step = {consumer.step: consumer for consumer in harness.consumers}
    assert isinstance(by_step["verify"].behavior, VerifyBehavior)
    # And the other steps keep their non-verify behavior.
    assert not isinstance(by_step["plan"].behavior, VerifyBehavior)
