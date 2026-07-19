import json

import pytest
import yaml
from typer.testing import CliRunner

from agentharness.cli import app
from agentharness.config import load_config

runner = CliRunner()


def run(*args):
    return runner.invoke(app, list(args))


@pytest.fixture()
def inited(home):
    assert run("init").exit_code == 0
    return load_config()


def write_agent(cfg, name, targets=(), **extra):
    body = {
        "name": name,
        "description": f"{name} agent",
        "allowed_tools": ["Read"],
        "can_handoff_to": list(targets),
    }
    body.update(extra)
    (cfg.agents_dir / f"{name}.yaml").write_text(yaml.safe_dump(body))


def test_init_creates_the_home_layout(home):
    result = run("init")

    assert result.exit_code == 0
    for name in ("agents", "repos", "queues", "worktrees", "locks", "logs"):
        assert (home / name).is_dir()
    assert (home / "config.yaml").exists()
    assert (home / "runs.db").exists()
    assert (home / "repos" / "_scratch.git").is_dir()


def test_init_is_idempotent(home):
    run("init")
    (home / "config.yaml").write_text("max_concurrency: 9\n")

    assert run("init").exit_code == 0
    assert load_config().max_concurrency == 9, "init must not clobber an existing config"


def test_commands_fail_readably_before_init(home, monkeypatch):
    monkeypatch.setenv("AGENTHARNESS_HOME", str(home / "missing"))
    result = run("agents", "list")

    assert result.exit_code == 1
    assert "init" in result.output


def test_agents_list_shows_routing(inited):
    write_agent(inited, "planner", ["implementer"])
    write_agent(inited, "implementer")

    result = run("agents", "list")

    assert result.exit_code == 0
    assert "planner" in result.output
    assert "implementer" in result.output
    assert "terminal" in result.output


def test_agents_validate_passes_on_a_good_directory(inited):
    write_agent(inited, "planner", ["implementer"])
    write_agent(inited, "implementer")

    result = run("agents", "validate")

    assert result.exit_code == 0
    assert "2 agent(s) valid" in result.output


def test_agents_validate_names_the_offender(inited):
    write_agent(inited, "planner", ["ghost"])

    result = run("agents", "validate")

    assert result.exit_code == 1
    assert "ghost" in result.output
    assert "Traceback" not in result.output


def test_agents_show_prints_the_definition(inited):
    write_agent(inited, "planner", max_turns=11)

    result = run("agents", "show", "planner")

    assert result.exit_code == 0
    assert json.loads(result.output)["max_turns"] == 11


def test_agents_show_unknown_fails_cleanly(inited):
    result = run("agents", "show", "ghost")
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_repos_add_and_list(inited, origin_repo):
    add = run("repos", "add", "app", str(origin_repo))
    assert add.exit_code == 0

    listing = run("repos", "list")
    assert "app" in listing.output
    assert "harness/integration" in listing.output


def test_submit_enqueues_and_prints_ids(inited, origin_repo):
    write_agent(inited, "planner")
    run("repos", "add", "app", str(origin_repo))

    result = run("submit", "planner", "plan", "--repo", "app")

    assert result.exit_code == 0
    assert "task_id" in result.output
    assert "trace_id" in result.output
    assert run("queue", "peek", "planner").output.startswith("1 pending")


def test_submit_rejects_an_unknown_agent(inited):
    write_agent(inited, "planner")

    result = run("submit", "ghost", "plan")

    assert result.exit_code == 1
    assert "ghost" in result.output
    assert "Traceback" not in result.output


def test_submit_rejects_an_unknown_repo(inited):
    write_agent(inited, "planner")

    result = run("submit", "planner", "plan", "--repo", "nope")

    assert result.exit_code == 1
    assert "nope" in result.output


def test_submit_rejects_malformed_payload(inited):
    write_agent(inited, "planner")

    result = run("submit", "planner", "plan", "--payload", "{not json")

    assert result.exit_code == 1
    assert "valid JSON" in result.output


def test_submit_carries_the_payload(inited):
    write_agent(inited, "planner")

    result = run("submit", "planner", "plan", "--payload", '{"topic": "x"}')
    assert result.exit_code == 0


def test_queue_list_reports_depth(inited):
    write_agent(inited, "planner")
    run("submit", "planner", "plan")

    result = run("queue", "list")

    assert result.exit_code == 0
    assert "planner" in result.output
    assert "PENDING" in result.output


def test_queue_dead_is_empty_initially(inited):
    write_agent(inited, "planner")
    assert "no dead letters" in run("queue", "dead", "planner").output


def test_queue_replay_of_a_missing_task_fails_cleanly(inited):
    write_agent(inited, "planner")

    result = run("queue", "replay", "planner", "t_nope")

    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_runs_list_on_an_empty_store(inited):
    result = run("runs", "list")

    assert result.exit_code == 0
    assert "AGENT" in result.output


def test_runs_show_unknown_fails_cleanly(inited):
    result = run("runs", "show", "r_nope")
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_trace_show_unknown_fails_cleanly(inited):
    result = run("trace", "show", "tr_nope")
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_schedule_list_is_empty_initially(inited):
    assert "no schedules" in run("schedule", "list").output


def test_gc_dry_run_deletes_nothing(inited, origin_repo):
    run("repos", "add", "app", str(origin_repo))

    result = run("gc", "--dry-run")

    assert result.exit_code == 0
    assert "run branch(es) present" in result.output
