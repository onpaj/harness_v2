import json

import pytest

from harness.drivers.fs_workflows import FilesystemWorkflowRepository
from harness.models import END, Transition
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

    with pytest.raises(WorkflowNotFound, match="neznamy"):
        repository.get("neznamy")


def test_malformed_definition_raises(tmp_path):
    (tmp_path / "rozbity.json").write_text("{tohle neni json")
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("rozbity")


def test_definition_without_start_raises(tmp_path):
    (tmp_path / "bez.json").write_text(json.dumps({"name": "bez", "transitions": []}))
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound, match="start"):
        repository.get("bez")


def test_malformed_transition_raises(tmp_path):
    (tmp_path / "spatny.json").write_text(
        json.dumps(
            {"name": "spatny", "start": "plan", "transitions": [{"from": "plan", "to": "review"}]}
        )
    )
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("spatny")


def test_name_with_path_separator_is_rejected(tmp_path):
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("../tajne")


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
    """Fuzzování odhalilo, že top-level JSON nemusí být objekt. Bez explicitní
    kontroly by `"start" not in raw` na čísle/None spadlo na TypeError a na
    stringu, který podřetězcem obsahuje "start" (např. "start line"), by
    kontrola prošla a spadlo by až na AttributeError z `raw.get(...)` o kus
    níž. Obojí muselo být odchyceno dřív a převedeno na WorkflowNotFound."""
    (tmp_path / "spatny_tvar.json").write_text(raw_json)
    repository = FilesystemWorkflowRepository(tmp_path)

    with pytest.raises(WorkflowNotFound):
        repository.get("spatny_tvar")


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
    (tmp_path / "tajne.json").write_text(json.dumps(DEFINITION))
    repository = FilesystemWorkflowRepository(root)

    with pytest.raises(WorkflowNotFound):
        repository.get("../tajne")
