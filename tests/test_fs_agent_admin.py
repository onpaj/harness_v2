import json

import pytest

from harness.drivers.fs_agents import FilesystemAgentAdmin
from harness.models import DONE, REQUEST_CHANGES
from harness.ports.agent import AgentNotFound
from harness.ports.agent_admin import AgentFields, AgentValidationError


def test_list_is_empty_for_an_empty_directory(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)

    assert admin.list() == ()


def test_list_returns_every_agent_name_sorted(tmp_path):
    (tmp_path / "reviewer.json").write_text(json.dumps({"prompt": "x"}))
    (tmp_path / "design.json").write_text(json.dumps({"prompt": "y"}))
    admin = FilesystemAgentAdmin(tmp_path)

    assert admin.list() == ("design", "reviewer")


def test_read_returns_the_spec(tmp_path):
    (tmp_path / "reviewer.json").write_text(
        json.dumps(
            {
                "prompt": "you are a reviewer",
                "model": "opus",
                "allowed_outcomes": ["done", "request_changes"],
            }
        )
    )
    admin = FilesystemAgentAdmin(tmp_path)

    spec = admin.read("reviewer")

    assert spec.prompt == "you are a reviewer"
    assert spec.model == "opus"
    assert spec.allowed_outcomes == (DONE, REQUEST_CHANGES)


def test_read_missing_agent_raises_not_found(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)

    with pytest.raises(AgentNotFound):
        admin.read("missing")


def test_write_creates_the_file(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)

    spec = admin.write(
        "planner",
        AgentFields(prompt="you plan", model="opus", allowed_outcomes=("done",)),
    )

    assert spec.prompt == "you plan"
    on_disk = json.loads((tmp_path / "planner.json").read_text())
    assert on_disk["prompt"] == "you plan"
    assert on_disk["model"] == "opus"


def test_write_is_immediately_visible_to_a_read(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)
    admin.write("planner", AgentFields(prompt="you plan"))

    spec = admin.read("planner")

    assert spec.prompt == "you plan"


def test_write_overwrites_an_existing_agent(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)
    admin.write("planner", AgentFields(prompt="v1"))

    admin.write("planner", AgentFields(prompt="v2"))

    assert admin.read("planner").prompt == "v2"


def test_write_without_prompt_is_rejected_and_leaves_no_file(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)

    with pytest.raises(AgentValidationError):
        admin.write("planner", AgentFields(prompt=""))

    assert not (tmp_path / "planner.json").exists()


def test_write_invalid_outcome_is_rejected_and_leaves_no_file(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)

    with pytest.raises(AgentValidationError):
        admin.write("weird", AgentFields(prompt="x", allowed_outcomes=("maybe",)))

    assert not (tmp_path / "weird.json").exists()


def test_write_rejected_submission_leaves_existing_file_untouched(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)
    admin.write("planner", AgentFields(prompt="original"))

    with pytest.raises(AgentValidationError):
        admin.write("planner", AgentFields(prompt="x", allowed_outcomes=("maybe",)))

    assert admin.read("planner").prompt == "original"


def test_write_invalid_name_is_rejected(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)

    with pytest.raises(AgentValidationError):
        admin.write("../secret", AgentFields(prompt="x"))


def test_delete_removes_the_file_and_reports_true(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)
    admin.write("planner", AgentFields(prompt="x"))

    assert admin.delete("planner") is True
    assert admin.list() == ()


def test_delete_unknown_agent_reports_false(tmp_path):
    admin = FilesystemAgentAdmin(tmp_path)

    assert admin.delete("missing") is False
