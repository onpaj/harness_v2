"""Acceptance test: the shipped example agent set, end to end.

This is the closest thing to the real thing that costs nothing to run. It uses
the actual examples/agents/*.yaml definitions -- not test fixtures -- so a
mistake in the shipped agent set fails the build.
"""

import json
import shutil
from pathlib import Path

import pytest

from agentharness.config import load_config
from agentharness.dispatch.dispatcher import Dispatcher
from agentharness.git.mirror import git, resolve_ref
from agentharness.queue.filesystem import FilesystemQueue
from agentharness.registry.agents import AgentRegistry
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.executor import ExecResult, Executor
from agentharness.runner.runner import Runner
from agentharness.store.runs import RunStore

EXAMPLES = Path(__file__).parent.parent / "examples" / "agents"


def artifact_dir(req) -> str:
    for token in req.prompt.split():
        if token.startswith(".harness/runs/") and token.endswith("/result.json"):
            return token[: -len("/result.json")]
    raise AssertionError("prompt did not name a result.json path")


class PipelineExecutor(Executor):
    """Stands in for claude -p, producing what each real agent would produce."""

    OUTPUTS = {
        "planner": ("plan.md", "1. add a greeting module\n"),
        "implementer": ("greeting.py", "def hello():\n    return 'hi'\n"),
        "reviewer": ("review.md", "verdict: approved\n"),
    }
    HANDOFFS = {"planner": "implementer", "implementer": "reviewer", "reviewer": None}

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.saw: dict[str, list[str]] = {}

    def run(self, req):
        agent = req.prompt.split('"')[1]
        self.calls.append(agent)
        # Record which predecessors' artifacts were visible in this worktree.
        self.saw[agent] = sorted(
            name for name, _ in self.OUTPUTS.values() if (req.cwd / name).exists()
        )

        filename, body = self.OUTPUTS[agent]
        (req.cwd / filename).write_text(body)

        payload = {
            "status": "ok",
            "summary": f"{agent} finished",
            "outputs": [filename],
            "handoffs": [],
            "metrics": {"files": 1},
        }
        nxt = self.HANDOFFS[agent]
        if nxt:
            payload["handoffs"] = [
                {"agent": nxt, "intent": f"{nxt}_step", "artifacts": {"inputs": [filename]}}
            ]

        target = req.cwd / artifact_dir(req)
        target.mkdir(parents=True, exist_ok=True)
        (target / "result.json").write_text(json.dumps(payload))

        return ExecResult(
            exit_code=0, is_error=False, result_text="ok",
            total_cost_usd=0.02, num_turns=3, session_id=f"sess_{agent}",
        )


@pytest.fixture()
def pipeline(home, origin_repo):
    cfg = load_config()
    cfg.retry_base_seconds = 0.01
    cfg.ensure_dirs()

    # Use the shipped agent definitions verbatim.
    for src in EXAMPLES.iterdir():
        shutil.copy(src, cfg.agents_dir / src.name)

    agents = AgentRegistry.load(cfg.agents_dir)
    repos = RepoRegistry(cfg)
    repos.add("app", str(origin_repo))
    store = RunStore(cfg.db_path)
    queue = FilesystemQueue(cfg.queues_dir)
    executor = PipelineExecutor()
    runner = Runner(cfg, agents, repos, store, executor)
    return Dispatcher(cfg, agents, repos, queue, store, runner), executor, store, repos, cfg


async def drive(dispatcher, limit=40):
    import asyncio

    idle = 0
    for _ in range(limit):
        dispatched = await dispatcher.tick()
        await dispatcher.drain()
        if dispatched == 0 and not dispatcher.queue.agents_with_work():
            idle += 1
            if idle >= 3:
                return
        else:
            idle = 0
        await asyncio.sleep(0.02)


def test_the_shipped_agent_set_is_valid():
    """A broken example agent must fail the build, not the operator's first run."""
    registry = AgentRegistry.load(EXAMPLES)
    assert registry.names() == ["implementer", "planner", "reviewer"]
    assert registry.can_handoff("planner", "implementer")
    assert registry.can_handoff("implementer", "reviewer")
    assert not registry.can_handoff("reviewer", "planner")
    assert registry.get("reviewer").can_handoff_to == []


def test_reviewer_cannot_write_to_the_repo():
    """The terminal reviewer reads and runs tests; it must not have Write/Edit."""
    reviewer = AgentRegistry.load(EXAMPLES).get("reviewer")
    assert "Write" not in reviewer.allowed_tools
    assert "Edit" not in reviewer.allowed_tools


async def test_all_three_agents_run_in_order(pipeline):
    d, ex, store, repos, cfg = pipeline
    d.submit("planner", "plan_feature", repo="app", payload={"request": "add a greeting"})

    await drive(d)

    assert ex.calls == ["planner", "implementer", "reviewer"]


async def test_each_agent_inherits_its_predecessors_artifacts(pipeline):
    d, ex, store, repos, cfg = pipeline
    d.submit("planner", "plan_feature", repo="app")

    await drive(d)

    assert ex.saw["planner"] == []
    assert ex.saw["implementer"] == ["plan.md"]
    assert ex.saw["reviewer"] == ["greeting.py", "plan.md"]


async def test_the_trace_merges_into_the_integration_branch(pipeline):
    d, ex, store, repos, cfg = pipeline
    task = d.submit("planner", "plan_feature", repo="app")

    await drive(d)

    mirror = repos.mirror_path("app")
    integration = resolve_ref(mirror, "harness/integration")
    listing = git("ls-tree", "-r", "--name-only", integration, cwd=mirror).stdout

    assert "plan.md" in listing
    assert "greeting.py" in listing
    assert "review.md" in listing


async def test_main_is_never_touched(pipeline):
    d, ex, store, repos, cfg = pipeline
    mirror = repos.mirror_path("app")
    before = resolve_ref(mirror, "main")

    d.submit("planner", "plan_feature", repo="app")
    await drive(d)

    assert resolve_ref(mirror, "main") == before


async def test_every_run_leaves_a_task_and_result_pair_in_the_repo(pipeline):
    d, ex, store, repos, cfg = pipeline
    task = d.submit("planner", "plan_feature", repo="app")

    await drive(d)

    mirror = repos.mirror_path("app")
    integration = resolve_ref(mirror, "harness/integration")
    listing = git("ls-tree", "-r", "--name-only", integration, cwd=mirror).stdout

    prefix = f".harness/runs/{task.trace_id}/"
    assert listing.count(f"{prefix}") >= 3
    assert listing.count("/task.json") == 3
    assert listing.count("/result.json") == 3


async def test_the_traces_cost_is_the_sum_of_its_runs(pipeline):
    d, ex, store, repos, cfg = pipeline
    task = d.submit("planner", "plan_feature", repo="app")

    await drive(d)

    runs = store.trace_runs(task.trace_id)
    assert len(runs) == 3
    assert sum(r.total_cost_usd for r in runs) == pytest.approx(0.06)
    assert {r.status for r in runs} == {"ok"}


async def test_the_trace_is_recorded_as_merged(pipeline):
    d, ex, store, repos, cfg = pipeline
    task = d.submit("planner", "plan_feature", repo="app")

    await drive(d)

    kinds = [e["kind"] for e in store.events_for_trace(task.trace_id)]
    assert "trace.merged" in kinds
    assert "trace.merge_conflict" not in kinds
