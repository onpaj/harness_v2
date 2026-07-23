import json
import os
from pathlib import Path

import pytest

from harness.drivers.fs_queue import FilesystemTaskQueue
from harness.drivers.memory import MemoryEventSink, MemoryTaskQueue
from harness.models import Task


def make_task(task_id="tsk_1") -> Task:
    return Task(id=task_id, workflow_template="default", created="2026-07-19T10:00:00Z")


def build(tmp_path, name="tasks", quarantine=None):
    events = MemoryEventSink()
    queue = FilesystemTaskQueue(
        name=name, root=tmp_path / name, events=events, quarantine=quarantine
    )
    return queue, events


def test_creates_its_directories(tmp_path):
    queue, _ = build(tmp_path)

    assert (tmp_path / "tasks").is_dir()
    assert (tmp_path / "tasks" / ".processing").is_dir()


def test_put_writes_json_and_list_reads_it(tmp_path):
    queue, _ = build(tmp_path)
    task = make_task()

    queue.put(task)

    raw = json.loads((tmp_path / "tasks" / "tsk_1.json").read_text())
    assert raw["workflowTemplate"] == "default"
    assert queue.list() == [task]


def test_claim_moves_file_into_processing(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())

    claimed = queue.claim(queue.list()[0], "lck_1")

    assert claimed.lock_id == "lck_1"
    assert not (tmp_path / "tasks" / "tsk_1.json").exists()
    assert (tmp_path / "tasks" / ".processing" / "tsk_1.json").exists()
    assert queue.list() == []


def test_claim_of_already_claimed_task_returns_none(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    task = queue.list()[0]
    queue.claim(task, "lck_1")

    assert queue.claim(task, "lck_2") is None


def test_transfer_moves_between_directories(tmp_path):
    source, _ = build(tmp_path, "tasks")
    destination, _ = build(tmp_path, "design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(claimed, destination)

    assert not (tmp_path / "tasks" / ".processing" / "tsk_1.json").exists()
    assert (tmp_path / "design" / "tsk_1.json").exists()
    assert destination.list()[0].id == "tsk_1"


def test_transfer_writes_the_updated_task(tmp_path):
    from dataclasses import replace

    source, _ = build(tmp_path, "tasks")
    destination, _ = build(tmp_path, "design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(replace(claimed, status="design", lock_id=None), destination)

    assert destination.list()[0].status == "design"
    assert destination.list()[0].lock_id is None


def test_transfer_to_foreign_queue_type_still_works(tmp_path):
    source, _ = build(tmp_path, "tasks")
    destination = MemoryTaskQueue("design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(claimed, destination)

    assert destination.list()[0].id == "tsk_1"
    assert not (tmp_path / "tasks" / ".processing" / "tsk_1.json").exists()


def test_discard_removes_claimed_task_permanently(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    claimed = queue.claim(queue.list()[0], "lck_1")

    queue.discard(claimed)

    assert queue.list() == []
    assert not (tmp_path / "tasks" / ".processing" / "tsk_1.json").exists()
    assert queue.recover() == 0


def test_discard_of_already_removed_task_is_a_no_op(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    claimed = queue.claim(queue.list()[0], "lck_1")

    queue.discard(claimed)
    queue.discard(claimed)  # does not raise

    assert queue.list() == []


def test_recover_returns_claimed_tasks_and_clears_lock(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    queue.claim(queue.list()[0], "lck_1")

    recovered = queue.recover()

    assert recovered == 1
    assert queue.list()[0].lock_id is None
    assert not any((tmp_path / "tasks" / ".processing").iterdir())


def test_corrupt_file_goes_to_quarantine_and_emits(tmp_path):
    quarantine = MemoryTaskQueue("failed")
    queue, events = build(tmp_path, quarantine=quarantine)
    queue.put(make_task())
    (tmp_path / "tasks" / "broken.json").write_text("{this is not json")

    listed = queue.list()

    assert [task.id for task in listed] == ["tsk_1"]
    assert not (tmp_path / "tasks" / "broken.json").exists()
    assert "corrupt" in events.names()


def test_corrupt_file_does_not_stop_listing_without_quarantine(tmp_path):
    queue, events = build(tmp_path)
    queue.put(make_task())
    (tmp_path / "tasks" / "broken.json").write_text("{this is not json")

    assert [task.id for task in queue.list()] == ["tsk_1"]
    assert "corrupt" in events.names()


def test_list_ignores_non_json_files(tmp_path):
    queue, _ = build(tmp_path)
    queue.put(make_task())
    (tmp_path / "tasks" / "README.txt").write_text("hi")

    assert len(queue.list()) == 1


def test_vanished_file_is_skipped_silently_not_quarantined(tmp_path):
    quarantine, _ = build(tmp_path, "failed")
    queue, events = build(tmp_path, quarantine=quarantine)
    queue.put(make_task())
    path = tmp_path / "tasks" / "tsk_1.json"
    path.unlink()

    task = queue._read(path)

    assert task is None
    assert "corrupt" not in events.names()
    assert not any((tmp_path / "failed").glob("*.json"))


def test_corrupt_file_goes_to_real_filesystem_quarantine(tmp_path):
    quarantine, _ = build(tmp_path, "failed")
    queue, events = build(tmp_path, quarantine=quarantine)
    queue.put(make_task())
    broken_path = tmp_path / "tasks" / "broken.json"
    broken_path.write_text("{this is not json")

    listed = queue.list()

    assert [task.id for task in listed] == ["tsk_1"]
    assert not broken_path.exists()
    quarantined = tmp_path / "failed" / "broken.json"
    assert quarantined.exists()
    assert quarantined.read_text() == "{this is not json"
    assert "corrupt" in events.names()


def test_file_vanishing_mid_recover_is_skipped_silently(tmp_path, monkeypatch):
    quarantine, _ = build(tmp_path, "failed")
    queue, events = build(tmp_path, quarantine=quarantine)
    queue.put(make_task("tsk_1"))
    queue.put(make_task("tsk_2"))
    for task in queue.list():
        queue.claim(task, f"lck_{task.id}")

    # tsk_1 vanishes exactly between glob() and reading inside recover() — a
    # simulated lost race, not corruption. tsk_2 stays healthy and must
    # recover normally.
    #
    # Watching only for an empty quarantine directory isn't enough:
    # _quarantine_file has its own `except FileNotFoundError: pass`, so even
    # the old (buggy) recover() that called quarantine unconditionally would
    # silently no-op on a vanished file and the test would pass on broken
    # code too. So we watch the _quarantine_file call directly — it must be
    # zero, not just its result.
    vanished_path = tmp_path / "tasks" / ".processing" / "tsk_1.json"
    original_read_text = Path.read_text

    def spy_read_text(self, *args, **kwargs):
        if self == vanished_path:
            vanished_path.unlink()
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    quarantine_calls: list[Path] = []
    original_quarantine_file = queue._quarantine_file

    def spy_quarantine_file(path):
        quarantine_calls.append(path)
        return original_quarantine_file(path)

    monkeypatch.setattr(queue, "_quarantine_file", spy_quarantine_file)

    recovered = queue.recover()

    assert recovered == 1
    assert "corrupt" not in events.names()
    assert not any((tmp_path / "failed").glob("*.json"))
    assert quarantine_calls == [], "vanished file must not trigger a quarantine attempt at all"
    remaining = queue.list()
    assert [task.id for task in remaining] == ["tsk_2"]
    assert remaining[0].lock_id is None


def test_write_cleans_up_temp_file_when_replace_fails(tmp_path, monkeypatch):
    queue, _ = build(tmp_path)

    def failing_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        queue.put(make_task())

    assert list((tmp_path / "tasks").glob("*.tmp")) == []
    assert list((tmp_path / "tasks").glob("*.json")) == []


def test_recover_quarantines_stranded_corrupt_file_and_still_counts_the_rest(tmp_path):
    quarantine, _ = build(tmp_path, "failed")
    queue, events = build(tmp_path, quarantine=quarantine)
    queue.put(make_task())
    queue.claim(queue.list()[0], "lck_1")
    broken_path = tmp_path / "tasks" / ".processing" / "broken.json"
    broken_path.write_text("{this is not json")

    recovered = queue.recover()

    assert recovered == 1
    assert queue.list()[0].lock_id is None
    assert not broken_path.exists()
    assert (tmp_path / "failed" / "broken.json").exists()
    assert "corrupt" in events.names()
