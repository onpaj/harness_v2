"""A queue as a directory of JSON files.

claim() is an atomic rename into <root>/.processing/. A single operation handles
the lease, idempotency, and origin after a crash: because each queue has its own
.processing/, recovery knows where to return a task without storing that anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from harness.models import Task
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue

PROCESSING = ".processing"


class _Corrupt(Exception):
    """Internal signal: the file exists but cannot be deserialized.

    Distinct from a vanished file (FileNotFoundError), so callers of
    _load() need not guess the reason from a single None value."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FilesystemTaskQueue(TaskQueue):
    def __init__(
        self,
        *,
        name: str,
        root: Path,
        events: EventSink,
        quarantine: TaskQueue | None = None,
    ) -> None:
        super().__init__(name)
        self._root = Path(root)
        self._events = events
        self._quarantine = quarantine
        self._root.mkdir(parents=True, exist_ok=True)
        self._processing.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def _processing(self) -> Path:
        return self._root / PROCESSING

    def list(self) -> list[Task]:
        tasks: list[Task] = []
        for path in sorted(self._root.glob("*.json")):
            task = self._read(path)
            if task is not None:
                tasks.append(task)
        return tasks

    def claim(self, task: Task, lock_id: str) -> Task | None:
        source = self._root / f"{task.id}.json"
        target = self._processing / f"{task.id}.json"
        try:
            os.replace(source, target)
        except (FileNotFoundError, IsADirectoryError):
            return None
        claimed = replace(task, lock_id=lock_id)
        self._write(target, claimed)
        return claimed

    def put(self, task: Task) -> None:
        self._write(self._root / f"{task.id}.json", task)

    def transfer(self, task: Task, destination: TaskQueue) -> None:
        held = self._processing / f"{task.id}.json"
        if isinstance(destination, FilesystemTaskQueue):
            self._write(held, task)
            os.replace(held, destination.root / f"{task.id}.json")
            return
        destination.put(task)
        held.unlink(missing_ok=True)

    def discard(self, task: Task) -> None:
        (self._processing / f"{task.id}.json").unlink(missing_ok=True)

    def recover(self) -> int:
        count = 0
        for path in sorted(self._processing.glob("*.json")):
            try:
                task = self._load(path)
            except FileNotFoundError:
                # The file vanished between glob() and reading — a race lost to
                # another claimant, not corruption. Skip silently: no event,
                # no quarantine attempt (that could sweep away a healthy task
                # if a new file appeared at the same path in the meantime).
                continue
            except _Corrupt as error:
                self._events.emit(
                    "corrupt", queue=self.name, path=str(path), reason=str(error)
                )
                self._quarantine_file(path)
                continue
            self._write(path, replace(task, lock_id=None))
            os.replace(path, self._root / path.name)
            count += 1
        return count

    def _load(self, path: Path) -> Task:
        """Reads and deserializes a task, or raises exactly why it failed —
        FileNotFoundError (vanished) vs. _Corrupt (corrupted). A single read,
        no existence re-check: that would only reopen the same TOCTOU window
        this distinction is meant to close."""
        try:
            return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as error:
            raise _Corrupt(str(error)) from error

    def _read(self, path: Path) -> Task | None:
        try:
            return self._load(path)
        except FileNotFoundError:
            # The file vanished between glob() and reading — a race lost to
            # another claimant (exactly what claim() tolerates), not corruption.
            # Skip silently: no event, no quarantine attempt.
            return None
        except _Corrupt as error:
            self._events.emit("corrupt", queue=self.name, path=str(path), reason=str(error))
            self._quarantine_file(path)
            return None

    def _quarantine_file(self, path: Path) -> None:
        """The task cannot be deserialized, so no history can be attributed to it.
        The file is moved as-is; only the event carries the reason."""
        if self._quarantine is None:
            return
        if isinstance(self._quarantine, FilesystemTaskQueue):
            try:
                shutil.move(str(path), str(self._quarantine.root / path.name))
            except FileNotFoundError:
                # It vanished in the meantime too — nothing to move, nothing happens.
                pass
        else:
            path.unlink(missing_ok=True)

    def _write(self, path: Path, task: Task) -> None:
        # A unique name per write, so two writers targeting the same id don't
        # share a single temp file. The suffix stays ".json.tmp", so
        # glob("*.json") in list()/claim()/recover() never picks it up.
        temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.json.tmp")
        try:
            temporary.write_text(
                json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temporary, path)
        except Exception:
            # A failed write or rename must not leave the temp file hanging
            # forever — unlike the old deterministic name, nothing overwrites it
            # anymore. A hard SIGKILL exactly between write_text and this branch
            # can still leave it behind; for this phase that's an accepted risk,
            # not a reason to build a directory sweeper.
            temporary.unlink(missing_ok=True)
            raise
