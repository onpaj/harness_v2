"""End-to-end for processes on in-memory drivers.

A Process is a compile-time aggregate: `FilesystemProcessRepository` reads
`processes/*.json` and compiles each into a `ScheduledTrigger` — a `TaskSource`
that rides the existing `sources` list. This mirrors `test_generic_triggers_e2e`
but starts from the *authoring* surface (files on disk), proving the whole
compile → poll → dispatch → done path with no disk beyond the queues and no real
waiting.
"""

import json
from pathlib import Path

from harness.app import HarnessLayout, build
from harness.drivers.fs_processes import FilesystemProcessRepository
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryWorkspace,
    ScriptedBehavior,
)
from harness.models import Task

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}

MAX_STEPS = 1000


def seed(tmp_path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


def write_process(tmp_path: Path, name: str, body: dict) -> None:
    processes = tmp_path / "processes"
    processes.mkdir(parents=True, exist_ok=True)
    (processes / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


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


def build_harness(tmp_path, *, clock, behavior=None):
    seed(tmp_path)
    sources = FilesystemProcessRepository(tmp_path / "processes").build(clock=clock)
    return build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=clock,
        behavior=behavior,
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        delay=0.0,
        sources=sources or None,
    )


async def test_process_fires_a_task_that_reaches_done_and_reflects_nothing(tmp_path):
    write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
            "sink": {"kind": "none"},
        },
    )
    clock = FakeClock()
    harness = build_harness(tmp_path, clock=clock)

    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"
    # v1: a Process compiles to a Trigger that stamps no data.source, so the
    # reflector ignores it — nothing is projected outward (sink is none).
    assert "source" not in finished.data


async def test_process_fires_again_after_the_clock_crosses_a_boundary(tmp_path):
    write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
        },
    )
    clock = FakeClock()
    harness = build_harness(tmp_path, clock=clock)

    await drive_until_quiet(harness)
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    # Same bucket → no new task.
    await drive_until_quiet(harness)
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    # Cross the interval boundary → a second, distinct task.
    clock.instant = "2026-07-19T11:00:00Z"
    await drive_until_quiet(harness)
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 2
    ids = {Task.from_dict(json.loads(p.read_text())).id for p in done_files}
    assert len(ids) == 2


async def test_process_step_target_is_placed_by_the_dispatcher(tmp_path):
    """A workflow-less `step` target: the process emits, the dispatcher places.

    Nobody writes into the step queue directly — the compiled trigger puts the
    task in the inbox and the dispatcher routes it into `development`.
    """
    write_process(
        tmp_path,
        "cleanup",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"step": "development"},
        },
    )
    clock = FakeClock()
    behavior = ScriptedBehavior()
    harness = build_harness(tmp_path, clock=clock, behavior=behavior)

    await drive_until_quiet(harness)

    assert "development" in behavior.seen
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    assert Task.from_dict(json.loads(done_files[0].read_text())).status == "end"


async def test_no_processes_behaves_as_before(tmp_path):
    """Backward-compat: with an empty/absent processes/ the harness runs exactly
    as today — no pollers — and a submitted task still flows to done."""
    clock = FakeClock()
    harness = build_harness(tmp_path, clock=clock)
    assert harness.pollers == []

    task = Task(
        id="tsk_plain",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={"title": "manual task"},
    )
    (tmp_path / "tasks" / "tsk_plain.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / "tsk_plain.json").read_text())
    )
    assert finished.status == "end"


def test_github_issues_process_ingests_a_labelled_issue_once_per_bucket(tmp_path):
    from pathlib import Path

    from harness.cli import _process_sources
    from harness.drivers.github_client import FakeGithubClient, Issue
    from harness.drivers.memory import FakeClock, MemoryRepositoryRegistry

    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "harness-todo.json").write_text(
        '{"trigger": {"interval": "30s"},'
        ' "action": {"check": "github-issues", "params": {"label": "harness:todo"}},'
        ' "target": {"workflow": "default"}, "dedup": "per-state",'
        ' "sink": {"kind": "none"}}'
    )
    client = FakeGithubClient(
        [Issue(42, "Do the thing", "body", "https://gh/i/42", ("harness:todo",))]
    )
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo"}
    clock = FakeClock("2026-07-22T10:00:00Z")

    import argparse

    args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
    # Inject the slug resolver via a subclassed check factory path: pass a client
    # and rely on the registry; slug resolution uses git origin in production, so
    # monkeypatch github_slug for the test.
    import harness.drivers.github_issues_check as mod

    orig = mod.github_slug
    mod.github_slug = slugs.get  # type: ignore[assignment]
    try:
        (source,) = _process_sources(
            args, tmp_path, registry,
            clock=clock, known_targets={"default"}, client=client,
        )

        first = source.poll()
        assert len(first) == 1
        task = first[0]
        assert task.workflow_template == "default"
        assert task.repository == "heblo"
        assert task.data["source"] == {
            "kind": "github", "repo": "onpaj/Anela.Heblo",
            "issue": 42, "url": "https://gh/i/42",
        }

        # Same 30s bucket → no re-fire.
        clock.instant = "2026-07-22T10:00:20Z"
        assert source.poll() == []

        # The issue was claimed (label swapped) → next bucket sees nothing new.
        clock.instant = "2026-07-22T10:01:00Z"
        assert source.poll() == []
    finally:
        mod.github_slug = orig  # type: ignore[assignment]
