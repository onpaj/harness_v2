"""Fronta jako adresář s JSON soubory.

claim() je atomický rename do <root>/.processing/. Jedna operace řeší lease,
idempotenci i původ po pádu: protože má .processing/ každá fronta vlastní,
recovery ví, kam task vrátit, aniž by se to kamkoli ukládalo.
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

    def recover(self) -> int:
        count = 0
        for path in sorted(self._processing.glob("*.json")):
            task = self._read(path, quarantine=False)
            if task is None:
                self._quarantine_file(path)
                continue
            self._write(path, replace(task, lock_id=None))
            os.replace(path, self._root / path.name)
            count += 1
        return count

    def _read(self, path: Path, *, quarantine: bool = True) -> Task | None:
        try:
            return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            # Soubor zmizel mezi glob() a čtením — to je vyhraný závod jiného
            # zabírajícího (přesně to, co claim() toleruje), ne poškození.
            # Tiše přeskočit: žádný event, žádný pokus o karanténu.
            return None
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as error:
            self._events.emit("corrupt", queue=self.name, path=str(path), reason=str(error))
            if quarantine:
                self._quarantine_file(path)
            return None

    def _quarantine_file(self, path: Path) -> None:
        """Task se nedá deserializovat, takže mu nelze připsat historii.
        Soubor se přesune tak, jak je; důvod nese jen event."""
        if self._quarantine is None:
            return
        if isinstance(self._quarantine, FilesystemTaskQueue):
            try:
                shutil.move(str(path), str(self._quarantine.root / path.name))
            except FileNotFoundError:
                # Mezitím zmizel i on — nic k přesunutí, nic se neděje.
                pass
        else:
            path.unlink(missing_ok=True)

    def _write(self, path: Path, task: Task) -> None:
        # Unikátní jméno per zápis, aby si dva writery cílící na stejné id
        # nesdíleli jeden temp soubor. Přípona zůstává ".json.tmp", takže ji
        # glob("*.json") v list()/claim()/recover() nikdy nezachytí.
        temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.json.tmp")
        temporary.write_text(
            json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, path)
