"""Jeden běh na skutečném filesystemu se zkráceným intervalem.

Obě testovací funkce ženou smyčku přes `asyncio.create_task` a čekají na
`(tmp_path / "done" / ...).exists()` přes ohraničené pollování — to samo o
sobě čeká nejvýš 6 s (600 × 0.01 s). Jenže `stop.set(); await runner` na konci
na nic takového ohraničené není: pokud by regrese v `Harness.run`/`_dispatcher_loop`/
`_consumer_loop` přestala respektovat `stop`, `await runner` visí navždy a test
zamrzne, místo aby selhal. `asyncio.wait_for` kolem `await runner` to řeší —
při timeoutu `runner` zruší a test spadne s jasnou `TimeoutError` během pár
vteřin, ne že se zavěsí na neurčito. Viz mutation-check v task-11-report.md."""

import asyncio
import json

from harness.app import build
from harness.cli import main
from harness.models import Task

RUNNER_TIMEOUT = 5.0


async def test_task_travels_from_submit_to_done(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    main(["submit", "--root", str(tmp_path), "--repo", "app-backend"])
    task_id = capsys.readouterr().out.strip().splitlines()[-1]

    harness = build(
        tmp_path, "default", delay=0.0, request_changes_once_at="review"
    )
    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(600):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / f"{task_id}.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / f"{task_id}.json").read_text())
    )
    assert finished.status == "end"
    assert finished.repository == "app-backend"

    routed = [entry.to_step for entry in finished.history if entry.actor == "dispatcher"]
    assert routed == [
        "plan",
        "design",
        "architecture",
        "development",
        "review",
        "development",
        "review",
        "land",
        "end",
    ]
    assert any(entry.outcome == "request_changes" for entry in finished.history)


async def test_unknown_workflow_lands_in_failed_and_loop_survives(tmp_path):
    main(["init", "--root", str(tmp_path)])
    broken = Task(
        id="tsk_broken", workflow_template="neexistuje", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_broken.json").write_text(json.dumps(broken.to_dict()))
    healthy = Task(
        id="tsk_ok", workflow_template="default", created="2026-07-19T10:00:01Z"
    )
    (tmp_path / "tasks" / "tsk_ok.json").write_text(json.dumps(healthy.to_dict()))

    harness = build(tmp_path, "default", delay=0.0)
    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(600):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_ok.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "failed" / "tsk_broken.json").exists()
    assert (tmp_path / "done" / "tsk_ok.json").exists()
