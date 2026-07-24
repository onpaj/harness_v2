"""End-to-end self-healing as a Process (ADR-0018/ADR-0019), on in-memory drivers.

A task fails and lands in `failed/`. The `autoheal` process (`failed-tasks`
check, targeting the `heal` workflow) claims it on its next tick, settles the
original to `healed/`, and fires a fresh `heal`-workflow task through the
ordinary dispatcher/consumer path — now three steps: `heal`
(`ClaudeCliBehavior` + the `healer` persona) triages the failure and returns
`file` (a harness bug worth filing) or `skip` (nothing to file); `file` routes
into `dedup` (`ClaudeCliBehavior` + the `dedup` persona), which reads the
harness repo's open issues and returns `unique` (routes to `file-issue`) or
`duplicate` (routes straight to `end`, silently); `file-issue` (the
`open-issue` finisher) opens the issue. No disk except the queues
(`FilesystemTaskQueue` under `tmp_path`), no real waiting — `FakeClock` gates
the process's interval bucket.

`HEAL_DEFINITION` here mirrors `src/harness/cli.py`'s shipped definition
exactly, and the two `FakeAgentRunner`-scripted outcomes per test
(`heal` -> `file`/`skip`, `dedup` -> `unique`/`duplicate`) are only accepted
by the dispatcher because the workflow itself declares those edges
(`Workflow.outcomes_for`, invariant #42) — so driving these three routing
paths through the real router/dispatcher is itself the end-to-end proof of
that invariant, not just of the heal/dedup wiring.
"""

import json

from harness.app import HarnessLayout, build
from harness.behaviors.open_issue import OpenIssueBehavior
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryAgentCatalog,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryIssueTracker,
    MemoryWorkspace,
)
from harness.models import HEALED, Task
from harness.ports.agent import AgentRun, AgentSpec

HEAL_DEFINITION = {
    "name": "heal",
    "start": "heal",
    "transitions": [
        {"from": "heal", "on": "file", "to": "dedup",
         "hint": "a harness bug, or an operational/tuning problem worth filing"},
        {"from": "heal", "on": "skip", "to": "end",
         "hint": "external/transient, or the task's own request was impossible — nothing to file"},
        {"from": "dedup", "on": "unique", "to": "file-issue",
         "hint": "nothing similar is open in the harness repo"},
        {"from": "dedup", "on": "duplicate", "to": "end",
         "hint": "a correlated issue is already open — settle silently"},
        {"from": "file-issue", "on": "done", "to": "end"},
    ],
    "descriptions": {
        "heal": "diagnose the failed task from its report; decide whether it warrants a GitHub issue",
        "dedup": "read the harness repo's open issues; decide whether the drafted issue is new",
    },
    "finishers": {"file-issue": "open-issue"},
}

HEAL_SPEC = AgentSpec(
    name="heal",
    prompt="you are the healer",
    allowed_outcomes=("file", "skip"),
)

DEDUP_SPEC = AgentSpec(
    name="dedup",
    prompt="you are the dedup triager",
    allowed_outcomes=("unique", "duplicate"),
)

MAX_STEPS = 1000


def seed(tmp_path, *, interval="1s", repository=None) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "heal.json").write_text(json.dumps(HEAL_DEFINITION))
    layout.processes.mkdir(parents=True, exist_ok=True)
    params = {"repository": repository} if repository is not None else {}
    (layout.processes / "autoheal.json").write_text(
        json.dumps(
            {
                "trigger": {"interval": interval},
                "action": {"check": "failed-tasks", "params": params},
                "target": {"workflow": "heal"},
                "dedup": "per-state",
                "sink": {"kind": "none"},
            }
        )
    )


async def drive_until_quiet(harness) -> int:
    for step in range(MAX_STEPS):
        acted = False
        for poller in harness.pollers:
            if poller.tick():
                acted = True
        if harness.dispatcher.tick():
            acted = True
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return step
    raise AssertionError("loop did not settle")


def build_harness(tmp_path, *, runner, clock, tracker=None):
    artifacts = MemoryArtifactStore()
    tracker = tracker if tracker is not None else MemoryIssueTracker()
    finishers = {
        "open-issue": lambda step, config, inner: OpenIssueBehavior(
            tracker=tracker,
            repo="onpaj/harness_v2",
            artifacts=artifacts,
            clock=clock,
        )
    }
    harness = build(
        tmp_path,
        "heal",
        events=MemoryEventSink(),
        clock=clock,
        workspace=MemoryWorkspace(),
        artifacts=artifacts,
        runner=runner,
        catalog=MemoryAgentCatalog({"heal": HEAL_SPEC, "dedup": DEDUP_SPEC}),
        finishers=finishers,
        delay=0.0,
    )
    return harness, tracker


def put_failed_task(tmp_path, task_id="tsk_e2e", *, data=None) -> None:
    task = Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-23T10:00:00Z",
        status="development",
        last_outcome=None,
        repository="app",
        data=data if data is not None else {"request": "Do the thing"},
    )
    failed_dir = tmp_path / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    (failed_dir / f"{task_id}.json").write_text(json.dumps(task.to_dict()))


async def test_heal_file_dedup_unique_opens_exactly_one_issue(tmp_path):
    """Path 1: `heal` -> `file` -> `dedup` -> `unique` -> `file-issue`. The
    dispatcher only accepts `file`/`unique` because `HEAL_DEFINITION` declares
    those exact edges (invariant #42) — this is the full triage+dedup path."""
    seed(tmp_path)
    put_failed_task(
        tmp_path, data={"request": "Do the thing", "source": {"url": "https://gh/i/9"}}
    )
    runner = FakeAgentRunner(
        runs={
            "heal": AgentRun("file", "Add the missing edge"),
            "dedup": AgentRun("unique", "nothing similar is open"),
        }
    )
    clock = FakeClock()
    harness, tracker = build_harness(tmp_path, runner=runner, clock=clock)

    await drive_until_quiet(harness)

    # the original task left failed/ for healed/
    assert list((tmp_path / "failed").glob("*.json")) == []
    healed_files = list((tmp_path / "healed").glob("*.json"))
    assert len(healed_files) == 1
    healed = Task.from_dict(json.loads(healed_files[0].read_text()))
    assert healed.status == HEALED
    assert "queued for healing" in healed.history[-1].summary

    # both agent steps ran, in order — proof `dedup` was actually reached.
    assert [call["spec"].name for call in runner.calls] == ["heal", "dedup"]

    # the prompt the heal persona actually saw carries the rendered failure
    # report (FR-1's rendered-body fix) — not just structured fields nobody
    # renders into the prompt.
    heal_call = runner.calls[0]
    assert "tsk_e2e" in heal_call["prompt"]
    assert "## Failure report" in heal_call["prompt"]

    # exactly one issue filed, keyed by the *original* failed task's id,
    # carrying the Origin footer sourced from the original task's
    # data.source (FR-3).
    assert len(tracker.opened) == 1
    opened = tracker.opened[0]
    assert opened["marker"] == "tsk_e2e"
    assert "Origin: https://gh/i/9" in opened["body"]

    # the fresh heal-workflow task itself reached a clean end.
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    assert Task.from_dict(json.loads(done_files[0].read_text())).status == "end"


async def test_heal_file_dedup_duplicate_settles_silently(tmp_path):
    """Path 2: `heal` -> `file` -> `dedup` -> `duplicate` -> `end`. `dedup`
    runs (unlike the `skip` path below) but the correlated issue already
    exists, so `file-issue`/`OpenIssueBehavior` is never reached and zero
    issues are opened — the workflow's `duplicate -> end` edge is what makes
    this silent rather than routing through `file-issue`."""
    seed(tmp_path)
    put_failed_task(tmp_path)
    runner = FakeAgentRunner(
        runs={
            "heal": AgentRun("file", "Add the missing edge"),
            "dedup": AgentRun("duplicate", "issue #42 already covers this"),
        }
    )
    harness, tracker = build_harness(tmp_path, runner=runner, clock=FakeClock())

    await drive_until_quiet(harness)

    assert list((tmp_path / "failed").glob("*.json")) == []
    assert len(list((tmp_path / "healed").glob("*.json"))) == 1
    assert tracker.opened == []  # no issue filed — settled as a duplicate

    # both agent steps ran — `dedup` was reached and made the call.
    assert [call["spec"].name for call in runner.calls] == ["heal", "dedup"]

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    assert Task.from_dict(json.loads(done_files[0].read_text())).status == "end"


async def test_heal_skip_never_reaches_dedup(tmp_path):
    """Path 3: `heal` -> `skip` -> `end`. `skip` routes straight to `end` per
    `HEAL_DEFINITION`'s own transitions — `dedup` is provably never run (only
    one agent call total) and zero issues are opened."""
    seed(tmp_path)
    put_failed_task(tmp_path)
    runner = FakeAgentRunner(
        runs={"heal": AgentRun("skip", "external flake")}
    )
    harness, tracker = build_harness(tmp_path, runner=runner, clock=FakeClock())

    await drive_until_quiet(harness)

    assert list((tmp_path / "failed").glob("*.json")) == []
    assert len(list((tmp_path / "healed").glob("*.json"))) == 1
    assert tracker.opened == []  # no issue filed

    # only `heal` ran — `dedup` was never invoked.
    assert [call["spec"].name for call in runner.calls] == ["heal"]

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    assert Task.from_dict(json.loads(done_files[0].read_text())).status == "end"


async def test_heal_time_error_does_not_loop_back_to_failed(tmp_path):
    """A `heal` agent error settles the *fresh* heal task into `failed/`
    normally (no bespoke try/except) — the recursion guard (`data.heal`), not
    construction, is what stops the *next* process tick from re-observing it."""
    seed(tmp_path, interval="1h")
    put_failed_task(tmp_path)

    class BoomRunner(FakeAgentRunner):
        async def run(self, **kwargs):
            raise RuntimeError("claude timed out")

    clock = FakeClock()  # 2026-07-19T10:00:00Z
    harness, tracker = build_harness(tmp_path, runner=BoomRunner(), clock=clock)

    await drive_until_quiet(harness)

    # First bucket: the original settles to healed/; the fresh heal task's own
    # agent error lands it in failed/ like any other step failure — no
    # special-cased re-entry, just the ordinary Consumer._fail path.
    assert len(list((tmp_path / "failed").glob("*.json"))) == 1
    assert len(list((tmp_path / "healed").glob("*.json"))) == 1

    # Cross the interval boundary so the process's next tick actually fires.
    clock.instant = "2026-07-19T11:00:00Z"
    await drive_until_quiet(harness)

    # The recursion guard retires the failed heal attempt too — settled, not
    # looped: no second heal task, no issue ever filed, drive_until_quiet
    # settling at all is itself proof a failed->failed bounce didn't happen.
    assert list((tmp_path / "failed").glob("*.json")) == []
    healed_files = list((tmp_path / "healed").glob("*.json"))
    assert len(healed_files) == 2  # the original, plus the failed heal attempt
    notes = {
        Task.from_dict(json.loads(f.read_text())).history[-1].summary
        for f in healed_files
    }
    assert any("queued for healing" in note for note in notes)
    assert any("heal-failed" in note for note in notes)
    assert tracker.opened == []


async def test_no_autoheal_process_leaves_the_task_in_failed(tmp_path):
    """Backward-compatible path: with no `failed-tasks`-driving process
    configured, `failed/` simply has no reader and stays a dead end."""
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "heal.json").write_text(json.dumps(HEAL_DEFINITION))
    # no processes/autoheal.json written
    put_failed_task(tmp_path)

    harness, tracker = build_harness(
        tmp_path, runner=FakeAgentRunner(), clock=FakeClock()
    )

    await drive_until_quiet(harness)

    assert len(list((tmp_path / "failed").glob("*.json"))) == 1
    assert list((tmp_path / "healed").glob("*.json")) == []
    assert tracker.opened == []


async def test_autoheal_process_repository_is_stamped_on_the_fired_heal_task(
    tmp_path,
):
    """A repo-configured autoheal process stamps its `repository` param onto
    the fired heal task (`Observation.repository` -> `ScheduledTrigger._task_for`),
    so the heal step gets a worktree."""
    seed(tmp_path, repository="onpaj/harness_v2")
    put_failed_task(tmp_path)
    runner = FakeAgentRunner(
        runs={
            "heal": AgentRun("file", "Add the missing edge"),
            "dedup": AgentRun("unique", "nothing similar is open"),
        }
    )
    harness, tracker = build_harness(tmp_path, runner=runner, clock=FakeClock())

    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    heal_task = Task.from_dict(json.loads(done_files[0].read_text()))
    assert heal_task.status == "end"
    assert heal_task.repository == "onpaj/harness_v2"


async def test_autoheal_process_without_repository_leaves_it_unset(tmp_path):
    """Back-compat: an autoheal process with no `repository` param (the
    module's existing default, `params: {}`) fires a heal task with no
    repository — unchanged behavior."""
    seed(tmp_path)
    put_failed_task(tmp_path)
    runner = FakeAgentRunner(
        runs={
            "heal": AgentRun("file", "Add the missing edge"),
            "dedup": AgentRun("unique", "nothing similar is open"),
        }
    )
    harness, tracker = build_harness(tmp_path, runner=runner, clock=FakeClock())

    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    heal_task = Task.from_dict(json.loads(done_files[0].read_text()))
    assert heal_task.status == "end"
    assert heal_task.repository is None
