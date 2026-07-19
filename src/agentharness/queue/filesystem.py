"""A durable queue built from atomic filesystem operations.

The whole design rests on two POSIX guarantees:

* ``os.rename`` within a filesystem is atomic, so moving a file out of
  ``pending/`` either wins outright or raises ``FileNotFoundError``. That is how
  two workers racing for the same task are resolved -- no locks needed.
* ``os.open(..., O_CREAT | O_EXCL)`` fails when the path already exists, which
  makes idempotency-key registration atomic.

Layout under ``<root>/<agent>/``::

    pending/<priority:02d>-<created_epoch_ms:013d>-<task_id>.json
    delayed/<ready_epoch_ms:013d>-<task_id>.json
    processing/<deadline_epoch_ms:013d>-<task_id>.json
    dead/<task_id>.json  +  dead/<task_id>.reason.txt
    .keys/<sha256(idempotency_key)>

Pending filenames sort lexicographically by priority and then by creation time,
so leasing is just "take the first name in sorted order".
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from agentharness.models import Task
from agentharness.queue.base import Queue

_PENDING = "pending"
_DELAYED = "delayed"
_PROCESSING = "processing"
_DEAD = "dead"
_KEYS = ".keys"

_SUBDIRS = (_PENDING, _DELAYED, _PROCESSING, _DEAD, _KEYS)


def _ms(epoch_seconds: float) -> int:
    return int(epoch_seconds * 1000)


class FilesystemQueue(Queue):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- paths

    def _agent_dir(self, agent: str) -> Path:
        return self.root / agent

    def _dir(self, agent: str, name: str) -> Path:
        return self._agent_dir(agent) / name

    def _ensure(self, agent: str) -> Path:
        agent_dir = self._agent_dir(agent)
        for sub in _SUBDIRS:
            (agent_dir / sub).mkdir(parents=True, exist_ok=True)
        return agent_dir

    def _agents(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    # ------------------------------------------------------------ file i/o

    @staticmethod
    def _read_task(path: Path) -> Task:
        return Task.model_validate_json(path.read_text())

    @staticmethod
    def _write_task_atomically(task: Task, dest: Path) -> None:
        """Materialise the task at `dest` via a rename, so readers never see a partial file."""
        payload = task.model_dump_json()
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            os.replace(tmp_name, dest)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    @staticmethod
    def _pending_name(task: Task) -> str:
        created_ms = _ms(task.created_at.timestamp())
        return f"{task.priority:02d}-{created_ms:013d}-{task.task_id}.json"

    def _place_pending(self, task: Task) -> Path:
        self._ensure(task.agent)
        dest = self._dir(task.agent, _PENDING) / self._pending_name(task)
        self._write_task_atomically(task, dest)
        return dest

    def _place_delayed(self, task: Task, ready_at: float) -> Path:
        self._ensure(task.agent)
        dest = self._dir(task.agent, _DELAYED) / f"{_ms(ready_at):013d}-{task.task_id}.json"
        self._write_task_atomically(task, dest)
        return dest

    def _find_processing(self, task: Task) -> Path | None:
        processing = self._dir(task.agent, _PROCESSING)
        if not processing.exists():
            return None
        for path in processing.iterdir():
            if path.name.endswith(f"-{task.task_id}.json"):
                return path
        return None

    def _drop_processing(self, task: Task) -> None:
        path = self._find_processing(task)
        if path is not None:
            path.unlink(missing_ok=True)

    # ------------------------------------------------------------ enqueue

    def enqueue(self, task: Task) -> bool:
        self._ensure(task.agent)
        digest = hashlib.sha256(task.idempotency_key.encode("utf-8")).hexdigest()
        marker = self._dir(task.agent, _KEYS) / digest
        try:
            fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(task.task_id)
        self._place_pending(task)
        return True

    # -------------------------------------------------------------- lease

    def lease(self, agent: str, visibility_timeout: int) -> Task | None:
        pending = self._dir(agent, _PENDING)
        if not pending.exists():
            return None
        processing = self._dir(agent, _PROCESSING)
        processing.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                names = sorted(p.name for p in pending.iterdir() if p.name.endswith(".json"))
            except FileNotFoundError:
                return None
            if not names:
                return None

            for name in names:
                deadline = _ms(time.time() + visibility_timeout)
                task_id = name.split("-", 2)[2].removesuffix(".json")
                dest = processing / f"{deadline:013d}-{task_id}.json"
                try:
                    # Atomic: exactly one racer's rename can succeed.
                    os.rename(pending / name, dest)
                except (FileNotFoundError, NotADirectoryError):
                    continue  # someone else took it; try the next candidate
                return self._read_task(dest)
            # Every candidate was stolen while we looked; re-list and retry.

    # ------------------------------------------------------- ack and nack

    def ack(self, task: Task) -> None:
        self._drop_processing(task)

    def nack(self, task: Task, *, requeue: bool = True, delay_seconds: float = 0.0) -> None:
        self._drop_processing(task)
        if not requeue:
            return
        retried = task.model_copy(update={"attempt": task.attempt + 1})
        if delay_seconds > 0:
            self._place_delayed(retried, time.time() + delay_seconds)
        else:
            self._place_pending(retried)

    # --------------------------------------------------------- dead letter

    def dead_letter(self, task: Task, reason: str) -> None:
        self._ensure(task.agent)
        self._drop_processing(task)
        dead = self._dir(task.agent, _DEAD)
        (dead / f"{task.task_id}.reason.txt").write_text(reason)
        self._write_task_atomically(task, dead / f"{task.task_id}.json")

    def list_dead(self, agent: str) -> list[Task]:
        dead = self._dir(agent, _DEAD)
        if not dead.exists():
            return []
        return [
            self._read_task(p)
            for p in sorted(dead.iterdir())
            if p.name.endswith(".json")
        ]

    def replay_dead(self, agent: str, task_id: str) -> bool:
        dead = self._dir(agent, _DEAD)
        path = dead / f"{task_id}.json"
        if not path.exists():
            return False
        task = self._read_task(path)
        self._place_pending(task)
        path.unlink(missing_ok=True)
        (dead / f"{task_id}.reason.txt").unlink(missing_ok=True)
        return True

    # ------------------------------------------------------------- depth

    def depth(self, agent: str) -> int:
        pending = self._dir(agent, _PENDING)
        if not pending.exists():
            return 0
        return sum(1 for p in pending.iterdir() if p.name.endswith(".json"))

    # ----------------------------------------------- timers and scheduling

    def reclaim_expired(self, now: float | None = None) -> list[Task]:
        cutoff = _ms(time.time() if now is None else now)
        reclaimed: list[Task] = []
        for agent in self._agents():
            processing = self._dir(agent, _PROCESSING)
            if not processing.exists():
                continue
            for path in sorted(processing.iterdir()):
                if not path.name.endswith(".json"):
                    continue
                deadline = int(path.name.split("-", 1)[0])
                if deadline > cutoff:
                    continue
                try:
                    task = self._read_task(path)
                except FileNotFoundError:
                    continue
                self._place_pending(task)
                path.unlink(missing_ok=True)
                reclaimed.append(task)
        return reclaimed

    def promote_delayed(self, now: float | None = None) -> list[Task]:
        cutoff = _ms(time.time() if now is None else now)
        promoted: list[Task] = []
        for agent in self._agents():
            delayed = self._dir(agent, _DELAYED)
            if not delayed.exists():
                continue
            for path in sorted(delayed.iterdir()):
                if not path.name.endswith(".json"):
                    continue
                ready = int(path.name.split("-", 1)[0])
                if ready > cutoff:
                    continue
                try:
                    task = self._read_task(path)
                except FileNotFoundError:
                    continue
                self._place_pending(task)
                path.unlink(missing_ok=True)
                promoted.append(task)
        return promoted

    def agents_with_work(self) -> list[str]:
        now_ms = _ms(time.time())
        busy: list[str] = []
        for agent in self._agents():
            if self.depth(agent) > 0:
                busy.append(agent)
                continue
            delayed = self._dir(agent, _DELAYED)
            if not delayed.exists():
                continue
            for path in delayed.iterdir():
                if path.name.endswith(".json") and int(path.name.split("-", 1)[0]) <= now_ms:
                    busy.append(agent)
                    break
        return busy
