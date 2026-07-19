"""Opt-in smoke test against the real `claude` CLI.

CONSUMES SUBSCRIPTION USAGE. Excluded from the default run by the `live` marker;
run deliberately with:

    .venv/bin/pytest -m live -v

Everything else in the suite uses FakeExecutor and costs nothing. This test
exists to catch the one class of bug the fakes cannot: the real CLI's flags or
JSON envelope changing under us.
"""

import shutil

import pytest
import yaml

from agentharness.config import load_config
from agentharness.dispatch.dispatcher import Dispatcher
from agentharness.queue.filesystem import FilesystemQueue
from agentharness.registry.agents import AgentRegistry
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.executor import LocalExecutor
from agentharness.runner.runner import Runner
from agentharness.store.runs import RunStore

pytestmark = pytest.mark.live

AUTH_MARKERS = ("authenticate", "oauth", "login", "unauthorized")


def _auth_failed(store, trace_id) -> bool:
    for event in store.events_for_trace(trace_id):
        blob = str(event.get("data") or "").lower()
        if any(marker in blob for marker in AUTH_MARKERS):
            return True
    return False


@pytest.fixture()
def live_harness(home, origin_repo):
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")

    cfg = load_config()
    cfg.ensure_dirs()
    (cfg.agents_dir / "scribe.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "scribe",
                "description": "Writes one short file and reports.",
                "allowed_tools": ["Read", "Write"],
                "permission_mode": "acceptEdits",
                "max_turns": 5,
                "timeout_seconds": 300,
                "can_handoff_to": [],
            }
        )
    )

    agents = AgentRegistry.load(cfg.agents_dir)
    repos = RepoRegistry(cfg)
    repos.add("app", str(origin_repo))
    store = RunStore(cfg.db_path)
    queue = FilesystemQueue(cfg.queues_dir)
    runner = Runner(cfg, agents, repos, store, LocalExecutor(cfg.claude_binary))
    return Dispatcher(cfg, agents, repos, queue, store, runner), store, repos


async def test_a_real_claude_run_honours_the_result_contract(live_harness):
    dispatcher, store, repos = live_harness

    task = dispatcher.submit(
        "scribe",
        "write_haiku",
        repo="app",
        payload={"instruction": "Write a three-line haiku about git into haiku.txt"},
    )

    await dispatcher.tick()
    await dispatcher.drain()

    runs = store.trace_runs(task.trace_id)
    assert len(runs) == 1

    run = runs[0]

    # An expired login is an environment problem, not a contract break. Skip
    # rather than fail, so a stale token cannot masquerade as a regression.
    if run.status != "ok" and _auth_failed(store, task.trace_id):
        pytest.skip(
            "claude CLI could not authenticate (OAuth session expired). "
            "Re-authenticate with `claude` interactively, then rerun."
        )

    assert run.status == "ok", f"run failed: {run.model_dump_json(indent=2)}"
    assert run.output_ref is not None
    assert run.claude_session_id, "the CLI JSON envelope no longer carries session_id"
    assert run.num_turns is not None, "the CLI JSON envelope no longer carries num_turns"
    assert not run.degraded, "the agent did not write a valid result.json"
