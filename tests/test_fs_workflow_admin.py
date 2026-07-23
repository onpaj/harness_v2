import json

import pytest

from harness.drivers.fs_workflows import FilesystemWorkflowAdmin
from harness.ports.workflow_admin import WorkflowValidationError
from harness.ports.workflows import WorkflowNotFound

DEFINITION_TEXT = json.dumps(
    {
        "start": "plan",
        "transitions": [{"from": "plan", "on": "done", "to": "review"}],
    },
    indent=2,
)


def test_list_is_empty_for_an_empty_directory(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    assert admin.list() == ()


def test_list_returns_every_workflow_name_sorted(tmp_path):
    (tmp_path / "default.json").write_text(DEFINITION_TEXT)
    (tmp_path / "alt.json").write_text(DEFINITION_TEXT)
    admin = FilesystemWorkflowAdmin(tmp_path)

    assert admin.list() == ("alt", "default")


def test_read_raw_returns_exact_file_text(tmp_path):
    (tmp_path / "default.json").write_text(DEFINITION_TEXT)
    admin = FilesystemWorkflowAdmin(tmp_path)

    assert admin.read_raw("default") == DEFINITION_TEXT


def test_read_raw_missing_workflow_raises_not_found(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    with pytest.raises(WorkflowNotFound):
        admin.read_raw("missing")


def test_write_raw_creates_the_file_verbatim(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    admin.write_raw("default", DEFINITION_TEXT)

    assert (tmp_path / "default.json").read_text() == DEFINITION_TEXT


def test_write_raw_is_immediately_visible_to_a_read(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)
    admin.write_raw("default", DEFINITION_TEXT)

    assert admin.read_raw("default") == DEFINITION_TEXT


def test_write_raw_invalid_json_is_rejected_and_leaves_no_file(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    with pytest.raises(WorkflowValidationError):
        admin.write_raw("default", "{not json")

    assert not (tmp_path / "default.json").exists()


def test_write_raw_missing_start_is_rejected_and_leaves_no_file(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    with pytest.raises(WorkflowValidationError):
        admin.write_raw("default", json.dumps({"transitions": []}))

    assert not (tmp_path / "default.json").exists()


def test_write_raw_transition_missing_to_is_rejected(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    with pytest.raises(WorkflowValidationError):
        admin.write_raw(
            "default",
            json.dumps({"start": "plan", "transitions": [{"from": "plan", "on": "done"}]}),
        )

    assert not (tmp_path / "default.json").exists()


def test_write_raw_rejected_submission_leaves_existing_file_untouched(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)
    admin.write_raw("default", DEFINITION_TEXT)

    with pytest.raises(WorkflowValidationError):
        admin.write_raw("default", "{not json")

    assert admin.read_raw("default") == DEFINITION_TEXT


def test_write_raw_invalid_name_is_rejected(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    with pytest.raises(WorkflowValidationError):
        admin.write_raw("../secret", DEFINITION_TEXT)


def test_write_raw_accepts_a_new_step_name(tmp_path):
    """Schema-valid but referencing a step no running harness has a queue for
    is still accepted at this layer — the restart warning is computed by the
    route layer via BoardView, not here (WorkflowAdmin is a pure filesystem
    port)."""
    admin = FilesystemWorkflowAdmin(tmp_path)

    admin.write_raw(
        "default",
        json.dumps(
            {"start": "plan", "transitions": [{"from": "plan", "on": "done", "to": "review_v2"}]}
        ),
    )

    assert "review_v2" in admin.read_raw("default")


def test_delete_removes_the_file_and_reports_true(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)
    admin.write_raw("default", DEFINITION_TEXT)

    assert admin.delete("default") is True
    assert admin.list() == ()


def test_delete_unknown_workflow_reports_false(tmp_path):
    admin = FilesystemWorkflowAdmin(tmp_path)

    assert admin.delete("missing") is False
