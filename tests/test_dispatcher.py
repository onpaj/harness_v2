import asyncio
import json
from pathlib import Path

import pytest
import yaml

from agentharness.config import load_config
from agentharness.dispatch.dispatcher import Dispatcher
from agentharness.dispatch.limits import ConcurrencyLimiter, RateLimitGate
from agentharness.git.mirror import git, resolve_ref
from agentharness.models import RetryPolicy
from agentharness.queue.filesystem import FilesystemQueue
from agentharness.registry.agents import AgentRegistry
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.executor import ExecResult, Executor
from agentharness.runner.runner import Runner
from agentharness.store.runs import RunStore

CHAIN = {"planner": ["implementer"], "implementer": ["reviewer"], "reviewer": []}


class ScriptedExecutor(Executor):
    """Per-agent scripts, keyed off the agent named in the prompt."""

    def __init__(self, scripts: dict, concurrency_probe: list | None = None) -> None:
        self.scripts = scripts
        self.calls: list[str] = []
        self.probe = concurrency_probe
        self._active = 0

    def run(self, req):
        agent = _agent_of(req.prompt)
        self.calls.append(agent)
        self._active += 1
        if self.probe is not None:
            self.probe.append(self._active)
        try:
            return self.scripts[agent](req)
        finally:
            self._active -= 1


def _agent_of(prompt: str) -> str:
    return prompt.split('"')[1]


def writes(payload: dict, files: dict | None = None, **execkw):
    """Build a script that writes files plus a result.json into the run dir."""

    def script(req):
        artifact_dir = _artifact_dir(req)
        for name, body in (files or {}).items():
            target = req.cwd / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        (req.cwd / artifact_dir).mkdir(parents=True, exist_ok=True)
        (req.cwd / artifact_dir / "result.json").write_text(json.dumps(payload))
        base = dict(exit_code=0, is_error=False, result_text="ok", total_cost_usd=0.05, num_turns=2)
        base.update(execkw)
        return ExecResult(**base)

    return script


def _artifact_dir(req) -> str:
    """Read the run's own artifact dir out of the prompt, as a real agent would.

    Globbing the worktree is wrong: a child run inherits its ancestors'
    .harness/runs/<trace>/<task>/ directories from base_ref, so a glob can
    land on the parent's directory and the handoff silently disappears.
    """
    for token in req.prompt.split():
        if token.startswith(".harness/runs/") and token.endswith("/result.json"):
            return token[: -len("/result.json")]
    raise AssertionError("prompt did not name a result.json path")


def fails(**execkw):
    def script(req):
        base = dict(exit_code=1, is_error=True, stderr="boom")
        base.update(execkw)
        return ExecResult(**base)

    return script


def handoff(agent, intent="next", inputs=None):
    return {"agent": agent, "intent": intent, "artifacts": {"inputs": inputs or []}}


@pytest.fixture()
def build(home, origin_repo):
    def _build(scripts, routes=CHAIN, max_concurrency=3, max_attempts=3, probe=None):
        cfg = load_config()
        cfg.max_concurrency = max_concurrency
        cfg.retry_base_seconds = 0.01  # keep backoff observable in tests
        cfg.ensure_dirs()

        for name, targets in routes.items():
            (cfg.agents_dir / f"{name}.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": name,
                        "description": f"{name} agent",
                        "allowed_tools": ["Read", "Write"],
                        "can_handoff_to": targets,
                        "retries": {"max_attempts": max_attempts, "backoff": "fixed"},
                        "concurrency": 3,
                    }
                )
            )

        agents = AgentRegistry.load(cfg.agents_dir)
        repos = RepoRegistry(cfg)
        repos.add("app", str(origin_repo))
        store = RunStore(cfg.db_path)
        queue = FilesystemQueue(cfg.queues_dir)
        executor = ScriptedExecutor(scripts, probe)
        runner = Runner(cfg, agents, repos, store, executor)
        gate = RateLimitGate(cfg.rate_limit_patterns, 60.0, 480.0)
        limiter = ConcurrencyLimiter(max_concurrency)
        d = Dispatcher(cfg, agents, repos, queue, store, runner, gate, limiter)
        return d, executor, store, queue, repos, cfg

    return _build


async def pump(dispatcher, rounds=6, settle=0.05):
    """Drive the loop until the system is quiescent.

    Counting rounds is wrong: retries are released by a wall-clock backoff, so a
    fixed number of ticks can finish before a delayed task is even eligible.
    Instead, keep ticking until nothing is queued, delayed, or in flight.
    """
    deadline = asyncio.get_event_loop().time() + 10.0
    idle = 0
    while asyncio.get_event_loop().time() < deadline:
        dispatched = await dispatcher.tick()
        await dispatcher.drain()
        pending = sum(dispatcher.queue.depth(a) for a in dispatcher.agents.names())
        waiting = bool(dispatcher.queue.agents_with_work())
        if dispatched == 0 and pending == 0 and not waiting:
            idle += 1
            if idle >= 3:
                return
        else:
            idle = 0
        await asyncio.sleep(settle)


async def test_a_submitted_task_runs_and_is_marked_done(build):
    d, ex, store, queue, *_ = build({"planner": writes({"status": "ok", "summary": "planned"})})
    task = d.submit("planner", "plan", repo="app")

    await pump(d, rounds=1)

    assert ex.calls == ["planner"]
    assert store.trace_open_count(task.trace_id) == 0
    assert queue.depth("planner") == 0


async def test_an_allowed_handoff_enqueues_exactly_one_child(build):
    d, ex, store, queue, *_ = build(
        {
            "planner": writes({"status": "ok", "handoffs": [handoff("implementer")]}),
            "implementer": writes({"status": "ok"}),
            "reviewer": writes({"status": "ok"}),
        }
    )
    d.submit("planner", "plan", repo="app")

    await d.tick()
    await d.drain()

    assert queue.depth("implementer") == 1


async def test_a_child_inherits_the_parents_output_commit(build):
    seen = {}

    def implementer(req):
        seen["plan"] = (req.cwd / "plan.md").read_text()
        return writes({"status": "ok"})(req)

    d, ex, store, *_ = build(
        {
            "planner": writes(
                {"status": "ok", "handoffs": [handoff("implementer")]},
                files={"plan.md": "the plan\n"},
            ),
            "implementer": implementer,
            "reviewer": writes({"status": "ok"}),
        }
    )
    d.submit("planner", "plan", repo="app")

    await pump(d)

    assert seen["plan"] == "the plan\n"


async def test_a_disallowed_handoff_is_rejected_and_recorded(build):
    """planner may only reach implementer; reviewer is off-limits to it."""
    d, ex, store, queue, *_ = build(
        {"planner": writes({"status": "ok", "handoffs": [handoff("reviewer")]})}
    )
    task = d.submit("planner", "plan", repo="app")

    await pump(d, rounds=2)

    assert queue.depth("reviewer") == 0
    assert ex.calls == ["planner"]
    rejected = [e for e in store.events_for_trace(task.trace_id) if e["kind"] == "handoff.rejected"]
    assert len(rejected) == 1
    assert "may not hand off" in rejected[0]["data"]["reason"]


async def test_fan_out_enqueues_and_runs_both_children(build):
    d, ex, store, queue, *_ = build(
        {
            "planner": writes(
                {"status": "ok", "handoffs": [handoff("implementer", "a"), handoff("implementer", "b")]}
            ),
            "implementer": writes({"status": "ok"}),
        },
        routes={"planner": ["implementer"], "implementer": []},
    )
    d.submit("planner", "plan", repo="app")

    await pump(d)

    assert ex.calls.count("implementer") == 2


async def test_a_linear_chain_completes_and_merges_into_the_integration_branch(build):
    d, ex, store, queue, repos, cfg = build(
        {
            "planner": writes(
                {"status": "ok", "handoffs": [handoff("implementer")]}, files={"plan.md": "plan\n"}
            ),
            "implementer": writes(
                {"status": "ok", "handoffs": [handoff("reviewer")]}, files={"code.py": "x = 1\n"}
            ),
            "reviewer": writes({"status": "ok"}, files={"review.md": "looks good\n"}),
        }
    )
    task = d.submit("planner", "plan", repo="app")

    await pump(d)

    assert ex.calls == ["planner", "implementer", "reviewer"]

    mirror = repos.mirror_path("app")
    integration = resolve_ref(mirror, "harness/integration")
    listing = git("ls-tree", "-r", "--name-only", integration, cwd=mirror).stdout

    assert "plan.md" in listing
    assert "code.py" in listing
    assert "review.md" in listing
    assert f".harness/runs/{task.trace_id}" in listing


async def test_the_harness_never_writes_to_main(build):
    d, ex, store, queue, repos, cfg = build(
        {
            "planner": writes({"status": "ok", "handoffs": [handoff("implementer")]}),
            "implementer": writes({"status": "ok"}, files={"code.py": "x = 1\n"}),
        },
        routes={"planner": ["implementer"], "implementer": []},
    )
    mirror = repos.mirror_path("app")
    before = resolve_ref(mirror, "main")

    d.submit("planner", "plan", repo="app")
    await pump(d)

    assert resolve_ref(mirror, "main") == before


async def test_every_run_of_a_trace_is_recorded_with_its_cost(build):
    d, ex, store, *_ = build(
        {
            "planner": writes({"status": "ok", "handoffs": [handoff("implementer")]}),
            "implementer": writes({"status": "ok"}),
        },
        routes={"planner": ["implementer"], "implementer": []},
    )
    task = d.submit("planner", "plan", repo="app")

    await pump(d)

    runs = store.trace_runs(task.trace_id)
    assert len(runs) == 2
    assert sum(r.total_cost_usd for r in runs) == pytest.approx(0.10)


async def test_a_failing_task_retries_then_dead_letters(build):
    d, ex, store, queue, *_ = build({"planner": fails()}, max_attempts=3)
    task = d.submit("planner", "plan", repo="app")

    await pump(d, rounds=6)

    assert ex.calls == ["planner"] * 3
    dead = queue.list_dead("planner")
    assert [t.task_id for t in dead] == [task.task_id]


async def test_a_rate_limit_pauses_dispatch_without_burning_an_attempt(build):
    d, ex, store, queue, *_ = build({"planner": fails(stderr="429 rate limit exceeded")})
    d.submit("planner", "plan", repo="app")

    await d.tick()
    await d.drain()

    assert d.gate.paused is True
    assert ex.calls == ["planner"]

    # Paused: the next tick dispatches nothing, and the task is still queued.
    d.gate.wait_until_clear = _never_clear
    assert await d.tick() == 0
    assert queue.depth("planner") == 1


async def _never_clear(*a, **k):
    return None


async def test_needs_input_blocks_the_task_without_handing_off(build):
    d, ex, store, queue, *_ = build(
        {"planner": writes({"status": "needs_input", "handoffs": [handoff("implementer")]})}
    )
    task = d.submit("planner", "plan", repo="app")

    await pump(d, rounds=2)

    assert queue.depth("implementer") == 0
    kinds = [e["kind"] for e in store.events_for_trace(task.trace_id)]
    assert "task.blocked" in kinds


async def test_conflicting_leaves_report_a_merge_conflict(build):
    """Two siblings editing the same line cannot be auto-merged."""
    d, ex, store, queue, repos, cfg = build(
        {
            "planner": writes(
                {"status": "ok", "handoffs": [handoff("implementer", "a"), handoff("implementer", "b")]}
            ),
            "implementer": None,
        },
        routes={"planner": ["implementer"], "implementer": []},
    )

    bodies = iter(["version A\n", "version B\n"])
    ex.scripts["implementer"] = lambda req: writes({"status": "ok"}, files={"same.txt": next(bodies)})(req)

    task = d.submit("planner", "plan", repo="app")
    await pump(d)

    kinds = [e["kind"] for e in store.events_for_trace(task.trace_id)]
    assert "trace.merge_conflict" in kinds


async def test_the_global_concurrency_ceiling_is_respected(build):
    import time

    probe: list[int] = []

    def slow(req):
        time.sleep(0.05)
        return writes({"status": "ok"})(req)

    d, ex, store, queue, *_ = build(
        {"planner": writes({"status": "ok", "handoffs": [handoff("implementer", f"i{i}") for i in range(4)]}),
         "implementer": slow},
        routes={"planner": ["implementer"], "implementer": []},
        max_concurrency=1,
        probe=probe,
    )
    d.submit("planner", "plan", repo="app")

    await pump(d)

    assert ex.calls.count("implementer") == 4
    assert max(probe) == 1, "no two claude -p processes may overlap at max_concurrency=1"


async def test_submit_rejects_an_unknown_agent(build):
    d, *_ = build({"planner": writes({"status": "ok"})})
    with pytest.raises(KeyError):
        d.submit("ghost", "plan", repo="app")


async def test_no_dispatch_errors_are_swallowed(build):
    """Guard: a crash inside handle_task must surface, not vanish."""
    d, ex, store, queue, *_ = build({"planner": fails()}, max_attempts=2)
    task = d.submit("planner", "plan", repo="app")
    await pump(d, rounds=4)

    errors = [e for e in store.recent_events(200) if e["kind"] == "dispatch.error"]
    assert errors == [], f"dispatch raised: {errors}"
