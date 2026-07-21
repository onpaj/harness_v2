import json

import pytest

from harness.drivers.fs_workflows import FilesystemWorkflowRepository, invalid_step_name
from harness.models import END, FAILED, Transition
from harness.ports.board import DONE_COLUMN, TODO_COLUMN
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

    with pytest.raises(WorkflowNotFound, match="unknown"):
        repository.get("unknown")


def test_malformed_definition_raises(tmp_path):
    (tmp_path / "broken.json").write_text("{this is not json")
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("broken")


def test_definition_without_start_raises(tmp_path):
    (tmp_path / "without.json").write_text(
        json.dumps({"name": "without", "transitions": []})
    )
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound, match="start"):
        repository.get("without")


def test_malformed_transition_raises(tmp_path):
    (tmp_path / "bad.json").write_text(
        json.dumps(
            {"name": "bad", "start": "plan", "transitions": [{"from": "plan", "to": "review"}]}
        )
    )
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("bad")


def test_name_with_path_separator_is_rejected(tmp_path):
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("../secret")


@pytest.mark.parametrize(
    "raw_json",
    [
        "42",
        "null",
        '["start", "x"]',
        '"start line"',
    ],
    ids=["number", "null", "list", "string-containing-start"],
)
def test_non_dict_top_level_raises(tmp_path, raw_json):
    """Fuzzing revealed that top-level JSON need not be an object. Without an
    explicit check, `"start" not in raw` would fail with TypeError on a
    number/None, and on a string that contains "start" as a substring (e.g.
    "start line") the check would pass and fail only later with AttributeError
    from `raw.get(...)` further down. Both had to be caught earlier and turned
    into WorkflowNotFound."""
    (tmp_path / "bad_shape.json").write_text(raw_json)
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("bad_shape")


def test_path_separator_is_rejected_even_when_escape_target_exists(tmp_path):
    """A weaker version of this test (no real file at the escaped path)
    would pass even if the separator guard were deleted entirely: the
    escape would simply resolve to a nonexistent path and still raise
    WorkflowNotFound via the FileNotFoundError branch, for the wrong
    reason. Planting a real, valid definition at the escape target makes
    the guard's absence observable: without it, this would load
    successfully instead of raising."""
    root = tmp_path / "workflows"
    root.mkdir()
    (tmp_path / "secret.json").write_text(json.dumps(DEFINITION))
    repository = FilesystemWorkflowRepository(root)

    with pytest.raises(WorkflowNotFound):
        repository.get("../secret")


def test_names_lists_every_valid_workflow_file(tmp_path):
    (tmp_path / "default.json").write_text(json.dumps(DEFINITION))
    (tmp_path / "hotfix.json").write_text(json.dumps({**DEFINITION, "name": "hotfix"}))
    repository = FilesystemWorkflowRepository(tmp_path)

    assert repository.names() == ("default", "hotfix")


def test_names_is_empty_for_a_missing_root(tmp_path):
    repository = FilesystemWorkflowRepository(tmp_path / "nonexistent")

    assert repository.names() == ()


@pytest.mark.parametrize("reserved", [END, FAILED, DONE_COLUMN, TODO_COLUMN])
def test_invalid_step_name_rejects_reserved_board_names(reserved):
    assert invalid_step_name(reserved) is True


def test_invalid_step_name_accepts_an_ordinary_name():
    assert invalid_step_name("development") is False


def test_invalid_step_name_rejects_path_separators():
    assert invalid_step_name("../secret") is True
