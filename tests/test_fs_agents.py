import json

import pytest

from harness.drivers.fs_agents import FilesystemAgentCatalog
from harness.models import Outcome
from harness.ports.agent import AgentNotFound


def test_roundtrip_reads_full_spec(tmp_path):
    (tmp_path / "reviewer.json").write_text(
        json.dumps(
            {
                "prompt": "jsi reviewer",
                "model": "opus",
                "fallback_model": "sonnet",
                "allowed_tools": ["Read", "Grep"],
                "allowed_outcomes": ["done", "request_changes"],
            }
        ),
        encoding="utf-8",
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    spec = catalog.get("reviewer")

    assert spec.name == "reviewer"
    assert spec.prompt == "jsi reviewer"
    assert spec.model == "opus"
    assert spec.fallback_model == "sonnet"
    assert spec.allowed_tools == ("Read", "Grep")
    assert spec.allowed_outcomes == (Outcome.DONE, Outcome.REQUEST_CHANGES)


def test_defaults_when_fields_missing(tmp_path):
    (tmp_path / "planner.json").write_text(
        json.dumps({"prompt": "jsi planner"}), encoding="utf-8"
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    spec = catalog.get("planner")

    assert spec.model is None
    assert spec.fallback_model is None
    assert spec.allowed_tools == ()
    assert spec.allowed_outcomes == (Outcome.DONE,)


def test_missing_file_raises(tmp_path):
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("chybi")


def test_malformed_json_raises(tmp_path):
    (tmp_path / "rozbity.json").write_text("{tohle neni json", encoding="utf-8")
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("rozbity")


def test_invalid_name_raises(tmp_path):
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("../secret")


def test_unknown_outcome_raises(tmp_path):
    (tmp_path / "divny.json").write_text(
        json.dumps({"prompt": "x", "allowed_outcomes": ["done", "vymysleny"]}),
        encoding="utf-8",
    )
    catalog = FilesystemAgentCatalog(tmp_path)

    with pytest.raises(AgentNotFound):
        catalog.get("divny")
