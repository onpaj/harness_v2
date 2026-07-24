import json

import pytest

from harness.drivers.fs_agents import FilesystemAgentCatalog
from harness.models import DONE, REQUEST_CHANGES
from harness.ports.agent import AgentNotFound


def test_roundtrip_reads_full_spec(tmp_path):
    (tmp_path / "reviewer.json").write_text(
        json.dumps(
            {
                "prompt": "you are a reviewer",
                "model": "opus",
                "fallback_model": "sonnet",
                "allowed_tools": ["Read", "Grep"],
                "allowed_outcomes": ["done", "request_changes"],
                "timeout": 300,
            }
        ),
        encoding="utf-8",
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    spec = catalog.get("reviewer")

    assert spec.name == "reviewer"
    assert spec.prompt == "you are a reviewer"
    assert spec.model == "opus"
    assert spec.fallback_model == "sonnet"
    assert spec.allowed_tools == ("Read", "Grep")
    assert spec.allowed_outcomes == (DONE, REQUEST_CHANGES)
    assert spec.timeout == 300.0


def test_defaults_when_fields_missing(tmp_path):
    (tmp_path / "planner.json").write_text(
        json.dumps({"prompt": "you are a planner"}), encoding="utf-8"
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    spec = catalog.get("planner")

    assert spec.model is None
    assert spec.fallback_model is None
    assert spec.allowed_tools == ()
    assert spec.allowed_outcomes == (DONE,)
    assert spec.timeout is None


def test_null_timeout_means_inherit(tmp_path):
    (tmp_path / "planner.json").write_text(
        json.dumps({"prompt": "you are a planner", "timeout": None}),
        encoding="utf-8",
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    assert catalog.get("planner").timeout is None


def test_bool_timeout_raises(tmp_path):
    (tmp_path / "weird.json").write_text(
        json.dumps({"prompt": "x", "timeout": True}), encoding="utf-8"
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("weird")


def test_zero_timeout_raises(tmp_path):
    (tmp_path / "weird.json").write_text(
        json.dumps({"prompt": "x", "timeout": 0}), encoding="utf-8"
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("weird")


def test_negative_timeout_raises(tmp_path):
    (tmp_path / "weird.json").write_text(
        json.dumps({"prompt": "x", "timeout": -5}), encoding="utf-8"
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("weird")


def test_non_numeric_timeout_raises(tmp_path):
    (tmp_path / "weird.json").write_text(
        json.dumps({"prompt": "x", "timeout": "soon"}), encoding="utf-8"
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("weird")


def test_missing_file_raises(tmp_path):
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("missing")


def test_malformed_json_raises(tmp_path):
    (tmp_path / "broken.json").write_text("{this is not json", encoding="utf-8")
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("broken")


def test_invalid_name_raises(tmp_path):
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("../secret")


def test_unknown_outcome_raises(tmp_path):
    (tmp_path / "weird.json").write_text(
        json.dumps({"prompt": "x", "allowed_outcomes": ["done", "invented"]}),
        encoding="utf-8",
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("weird")


def test_names_lists_every_valid_agent_file(tmp_path):
    (tmp_path / "planner.json").write_text(
        json.dumps({"prompt": "x"}), encoding="utf-8"
    )
    (tmp_path / "reviewer.json").write_text(
        json.dumps({"prompt": "y"}), encoding="utf-8"
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    assert catalog.names() == ("planner", "reviewer")


def test_names_is_empty_for_a_missing_root(tmp_path):
    catalog = FilesystemAgentCatalog(tmp_path / "nonexistent")

    assert catalog.names() == ()
