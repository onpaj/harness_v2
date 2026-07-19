from pathlib import Path

import pytest
import yaml

from agentharness.models import AgentDef
from agentharness.registry.agents import AgentRegistry, AgentValidationError


def write_agent(agents_dir: Path, stem: str, **fields) -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    data = {"name": stem, "description": f"{stem} agent"}
    data.update(fields)
    path = agents_dir / f"{stem}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=True))
    return path


@pytest.fixture()
def agents_dir(tmp_path):
    return tmp_path / "agents"


@pytest.fixture()
def two_agents(agents_dir):
    write_agent(agents_dir, "planner", can_handoff_to=["coder"], model="sonnet")
    write_agent(agents_dir, "coder", max_turns=40)
    return agents_dir


# --- happy path -------------------------------------------------------------


def test_loads_two_agents(two_agents):
    reg = AgentRegistry.load(two_agents)
    assert len(reg.names()) == 2


def test_get_returns_parsed_agent_def(two_agents):
    reg = AgentRegistry.load(two_agents)
    agent = reg.get("coder")
    assert isinstance(agent, AgentDef)
    assert agent.name == "coder"
    assert agent.max_turns == 40
    assert agent.permission_mode == "acceptEdits"


def test_get_unknown_raises_key_error(two_agents):
    reg = AgentRegistry.load(two_agents)
    with pytest.raises(KeyError):
        reg.get("nope")


def test_names_are_sorted(agents_dir):
    write_agent(agents_dir, "zulu")
    write_agent(agents_dir, "alpha")
    write_agent(agents_dir, "mike")
    reg = AgentRegistry.load(agents_dir)
    assert reg.names() == ["alpha", "mike", "zulu"]


def test_load_empty_dir(agents_dir):
    agents_dir.mkdir(parents=True)
    reg = AgentRegistry.load(agents_dir)
    assert reg.names() == []


def test_can_handoff_true_for_declared_target(two_agents):
    reg = AgentRegistry.load(two_agents)
    assert reg.can_handoff("planner", "coder") is True


def test_can_handoff_false_for_undeclared_target(two_agents):
    reg = AgentRegistry.load(two_agents)
    assert reg.can_handoff("coder", "planner") is False


def test_can_handoff_false_for_unknown_source(two_agents):
    reg = AgentRegistry.load(two_agents)
    assert reg.can_handoff("ghost", "coder") is False


def test_can_handoff_false_for_unknown_target(two_agents):
    reg = AgentRegistry.load(two_agents)
    assert reg.can_handoff("planner", "ghost") is False


def test_forward_reference_between_agents(agents_dir):
    """Cross-reference validation is a second pass, so order does not matter."""
    write_agent(agents_dir, "aaa", can_handoff_to=["zzz"])
    write_agent(agents_dir, "zzz", can_handoff_to=["aaa"])
    reg = AgentRegistry.load(agents_dir)
    assert reg.can_handoff("aaa", "zzz") is True
    assert reg.can_handoff("zzz", "aaa") is True


def test_system_prompt_returns_file_contents(agents_dir):
    write_agent(agents_dir, "coder", system_prompt_file="prompts/coder.md")
    (agents_dir / "prompts").mkdir()
    (agents_dir / "prompts" / "coder.md").write_text("You are a coder.\n")
    reg = AgentRegistry.load(agents_dir)
    assert reg.system_prompt("coder") == "You are a coder.\n"


def test_system_prompt_none_when_not_configured(two_agents):
    reg = AgentRegistry.load(two_agents)
    assert reg.system_prompt("coder") is None


def test_system_prompt_unknown_agent_raises_key_error(two_agents):
    reg = AgentRegistry.load(two_agents)
    with pytest.raises(KeyError):
        reg.system_prompt("ghost")


def test_repos_validated_against_known_repos(agents_dir):
    write_agent(agents_dir, "coder", repos=["app"])
    reg = AgentRegistry.load(agents_dir, known_repos={"app", "docs"})
    assert reg.get("coder").repos == ["app"]


def test_repos_not_validated_when_known_repos_omitted(agents_dir):
    write_agent(agents_dir, "coder", repos=["whatever"])
    reg = AgentRegistry.load(agents_dir)
    assert reg.get("coder").repos == ["whatever"]


# --- validation failures ----------------------------------------------------


def test_filename_stem_must_equal_name(agents_dir):
    agents_dir.mkdir(parents=True)
    (agents_dir / "coder.yaml").write_text(
        yaml.safe_dump({"name": "reviewer", "description": "d"})
    )
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(agents_dir)
    msg = str(exc.value)
    assert "coder" in msg
    assert "reviewer" in msg


def test_unknown_handoff_target_rejected(agents_dir):
    write_agent(agents_dir, "planner", can_handoff_to=["missing-agent"])
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(agents_dir)
    msg = str(exc.value)
    assert "planner" in msg
    assert "missing-agent" in msg


def test_unknown_repo_rejected(agents_dir):
    write_agent(agents_dir, "coder", repos=["ghost-repo"])
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(agents_dir, known_repos={"app"})
    msg = str(exc.value)
    assert "coder" in msg
    assert "ghost-repo" in msg


def test_missing_system_prompt_file_rejected(agents_dir):
    write_agent(agents_dir, "coder", system_prompt_file="prompts/nope.md")
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(agents_dir)
    msg = str(exc.value)
    assert "coder" in msg
    assert "prompts/nope.md" in msg


def test_duplicate_names_rejected(agents_dir):
    agents_dir.mkdir(parents=True)
    (agents_dir / "coder.yaml").write_text(
        yaml.safe_dump({"name": "coder", "description": "one"})
    )
    (agents_dir / "coder.yml").write_text(
        yaml.safe_dump({"name": "coder", "description": "two"})
    )
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(agents_dir)
    assert "coder" in str(exc.value)


def test_unknown_yaml_keys_rejected(agents_dir):
    write_agent(agents_dir, "coder", bogus_key="x")
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(agents_dir)
    msg = str(exc.value)
    assert "coder" in msg
    assert "bogus_key" in msg


def test_missing_agents_dir_rejected(tmp_path):
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(tmp_path / "does-not-exist")
    assert "does-not-exist" in str(exc.value)


def test_non_mapping_yaml_rejected(agents_dir):
    agents_dir.mkdir(parents=True)
    (agents_dir / "coder.yaml").write_text("- just\n- a list\n")
    with pytest.raises(AgentValidationError) as exc:
        AgentRegistry.load(agents_dir)
    assert "coder" in str(exc.value)
