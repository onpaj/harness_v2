"""End-to-end fáze 4 na in-memory driverech.

Issue-ekvivalent (MemoryTaskSource) → task → smyčka → done → terminální projekce
ven. Ověřuje se celý konektor: poll plní inbox, reflector promítá stav do zdroje.
Bez disku (kromě front) a bez reálného čekání.
"""

import json

from harness.app import Harness, HarnessLayout, build
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryTaskSource,
    MemoryWorkspace,
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


async def drive_until_quiet(harness: Harness) -> int:
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
    raise AssertionError("smyčka se nezklidnila")


def build_harness(tmp_path, source):
    seed(tmp_path)
    return build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        delay=0.0,
        sources=[source] if source else None,
    )


async def test_task_from_source_reaches_done_and_is_projected_out(tmp_path):
    source = MemoryTaskSource(clock=FakeClock(), repository="app-backend")
    issue = source.submit("Fix bug")
    harness = build_harness(tmp_path, source)

    await drive_until_quiet(harness)

    # task doputoval do done
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"
    assert finished.data["source"]["kind"] == "memory"

    # projekce ven: aspoň jedna progress + finish(ok=True)
    projections = source.states[issue]
    assert ("finish", True) in projections
    assert any(kind == "progress" for kind, _ in projections)


async def test_submit_task_without_source_flows_through_untouched(tmp_path):
    source = MemoryTaskSource(clock=FakeClock())
    harness = build_harness(tmp_path, source)

    # task vložený přímo do inboxu (bez data.source) — zpětná kompatibilita
    task = Task(
        id="tsk_plain",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        data={"title": "ruční task"},
    )
    (tmp_path / "tasks" / "tsk_plain.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / "tsk_plain.json").read_text())
    )
    assert finished.status == "end"
    # zdroj o něm nic neví — projekce ven neproběhla
    assert source.states == {}
