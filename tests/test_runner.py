import json
from datetime import datetime, timezone

import pytest
import yaml

from agentharness.config import load_config
from agentharness.git.mirror import git, resolve_ref
from agentharness.ids import new_task_id, new_trace_id
from agentharness.models import Task, TaskArtifacts
from agentharness.registry.agents import AgentRegistry
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.executor import ExecResult, FakeExecutor, fake_ok
from agentharness.runner.runner import Runner
from agentharness.store.runs import RunStore


@pytest.fixture()
def harness(home):
    cfg = load_config()
    cfg.ensure_dirs()

    agents_dir = cfg.agents_dir
    for name, targets in (("writer", ["reviewer"]), ("reviewer", [])):
        (agents_dir / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "description": f"{name} agent",
                    "allowed_tools": ["Read", "Write"],
                    "can_handoff_to": targets,
                }
            )
        )

    repos = RepoRegistry(cfg)
    repos.ensure_scratch()
    return cfg, AgentRegistry.load(agents_dir), repos, RunStore(cfg.db_path)


def make_task(agent="writer", base_ref=None, **over) -> Task:
    base = dict(
        task_id=new_task_id(),
        trace_id=new_trace_id(),
        agent=agent,
        repo=None,
        intent="draft",
        idempotency_key="k",
        artifacts=TaskArtifacts(base_ref=base_ref),
        created_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return Task(**base)


def runner_for(harness, script) -> Runner:
    cfg, agents, repos, store = harness
    return Runner(cfg, agents, repos, store, FakeExecutor(script))


def show(mirror, ref, path) -> str:
    return git("show", f"{ref}:{path}", cwd=mirror).stdout


def test_successful_run_produces_an_output_commit(harness):
    task = make_task()
    r = runner_for(harness, fake_ok({"status": "ok", "summary": "did it"}, task.artifact_dir))

    outcome = r.execute(task)

    assert outcome.run.status == "ok"
    assert outcome.run.output_ref is not None
    assert outcome.run.degraded is False


def test_worktree_is_removed_but_the_branch_survives(harness):
    """The directory is disposable; the branch is the durable audit record."""
    cfg, _, repos, _ = harness
    task = make_task()
    r = runner_for(harness, fake_ok({"status": "ok"}, task.artifact_dir))

    outcome = r.execute(task)

    assert not (cfg.worktrees_dir / task.trace_id / task.task_id).exists()
    mirror = repos.mirror_path("_scratch")
    assert resolve_ref(mirror, f"run/{task.task_id}") == outcome.run.output_ref


def test_task_json_is_committed_and_matches_the_input(harness):
    cfg, _, repos, _ = harness
    task = make_task()
    r = runner_for(harness, fake_ok({"status": "ok"}, task.artifact_dir))

    outcome = r.execute(task)

    committed = json.loads(
        show(repos.mirror_path("_scratch"), outcome.run.output_ref, f"{task.artifact_dir}/task.json")
    )
    assert committed["task_id"] == task.task_id
    assert committed["intent"] == "draft"


def test_result_json_and_logs_are_committed(harness):
    cfg, _, repos, _ = harness
    task = make_task()
    r = runner_for(harness, fake_ok({"status": "ok", "summary": "did it"}, task.artifact_dir))

    outcome = r.execute(task)
    mirror = repos.mirror_path("_scratch")

    result = json.loads(show(mirror, outcome.run.output_ref, f"{task.artifact_dir}/result.json"))
    assert result["summary"] == "did it"
    assert show(mirror, outcome.run.output_ref, f"{task.artifact_dir}/logs/stdout.log") is not None


def test_a_run_without_result_json_is_degraded_but_still_commits(harness):
    task = make_task()

    def script(req):
        return ExecResult(exit_code=0, is_error=False, result_text="I forgot the contract")

    outcome = runner_for(harness, script).execute(task)

    assert outcome.run.degraded is True
    assert outcome.run.status == "ok"
    assert outcome.run.output_ref is not None


def test_cli_error_marks_the_run_failed(harness):
    def script(req):
        return ExecResult(exit_code=1, is_error=True, stderr="boom")

    assert runner_for(harness, script).execute(make_task()).run.status == "failed"


def test_timeout_is_its_own_status(harness):
    def script(req):
        return ExecResult(exit_code=-1, is_error=True, timed_out=True)

    assert runner_for(harness, script).execute(make_task()).run.status == "timeout"


def test_agent_reported_failure_marks_the_run_failed(harness):
    task = make_task()
    r = runner_for(harness, fake_ok({"status": "failed", "summary": "could not"}, task.artifact_dir))

    assert r.execute(task).run.status == "failed"


def test_metrics_from_the_cli_land_on_the_run_record(harness):
    task = make_task()
    r = runner_for(harness, fake_ok({"status": "ok"}, task.artifact_dir, cost=0.37, turns=9))

    run = r.execute(task).run
    assert run.total_cost_usd == 0.37
    assert run.num_turns == 9
    assert run.claude_session_id == "sess_fake"


def test_a_child_run_inherits_the_parents_committed_artifacts(harness):
    """This is the core mechanism: state travels through git, not memory."""
    cfg, _, repos, _ = harness
    parent = make_task()

    def parent_script(req):
        (req.cwd / "brief.md").write_text("the brief\n")
        return fake_ok({"status": "ok", "outputs": ["brief.md"]}, parent.artifact_dir)(req)

    parent_out = runner_for(harness, parent_script).execute(parent)

    seen = {}
    child = make_task(agent="reviewer", base_ref=parent_out.run.output_ref)

    def child_script(req):
        seen["brief"] = (req.cwd / "brief.md").read_text()
        return fake_ok({"status": "ok"}, child.artifact_dir)(req)

    runner_for(harness, child_script).execute(child)

    assert seen["brief"] == "the brief\n"


def test_an_exploding_executor_is_recorded_not_raised(harness):
    cfg = harness[0]
    task = make_task()

    def script(req):
        raise RuntimeError("executor exploded")

    outcome = runner_for(harness, script).execute(task)

    assert outcome.run.status == "failed"
    assert "executor exploded" in outcome.error
    assert not (cfg.worktrees_dir / task.trace_id / task.task_id).exists()


def test_unknown_agent_fails_the_run_without_raising(harness):
    outcome = runner_for(harness, lambda req: None).execute(make_task(agent="ghost"))
    assert outcome.run.status == "failed"
    assert "preparation failed" in outcome.error


def test_both_lifecycle_events_are_recorded(harness):
    store = harness[3]
    task = make_task()
    runner_for(harness, fake_ok({"status": "ok"}, task.artifact_dir)).execute(task)

    kinds = [e["kind"] for e in store.events_for_trace(task.trace_id)]
    assert "run.started" in kinds and "run.finished" in kinds


def test_the_executor_is_confined_to_the_worktree(harness):
    cfg = harness[0]
    task = make_task()
    seen = {}

    def script(req):
        seen["cwd"] = req.cwd
        return fake_ok({"status": "ok"}, task.artifact_dir)(req)

    runner_for(harness, script).execute(task)
    assert seen["cwd"] == cfg.worktrees_dir / task.trace_id / task.task_id
