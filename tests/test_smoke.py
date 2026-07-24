"""A single run on the real filesystem with a shortened interval.

Both test functions drive the loop via `asyncio.create_task` and wait for
`(tmp_path / "done" / ...).exists()` through bounded polling — which on its own
waits at most 6 s (600 × 0.01 s). But `stop.set(); await runner` at the end is
bounded by nothing of the sort: if a regression in `Harness.run`/`_dispatcher_loop`/
`_consumer_loop` stopped respecting `stop`, `await runner` would hang forever and
the test would freeze instead of failing. `asyncio.wait_for` around `await runner`
handles that — on timeout it cancels `runner` and the test fails with a clear
`TimeoutError` within a few seconds, rather than hanging indefinitely. See the
mutation-check in task-11-report.md."""

import asyncio
import json

from harness.app import build
from harness.cli import DEFAULT_WORKFLOW, main
from harness.models import Task

RUNNER_TIMEOUT = 5.0


async def test_task_travels_from_submit_to_done(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    main(["submit", "--root", str(tmp_path), "--repo", "app-backend"])
    task_id = capsys.readouterr().out.strip().splitlines()[-1]

    harness = build(
        tmp_path, DEFAULT_WORKFLOW, delay=0.0, request_changes_once_at="review"
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
        id="tsk_broken", workflow_template="nonexistent", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_broken.json").write_text(json.dumps(broken.to_dict()))
    healthy = Task(
        id="tsk_ok", workflow_template=DEFAULT_WORKFLOW, created="2026-07-19T10:00:01Z"
    )
    (tmp_path / "tasks" / "tsk_ok.json").write_text(json.dumps(healthy.to_dict()))

    harness = build(tmp_path, DEFAULT_WORKFLOW, delay=0.0)
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
