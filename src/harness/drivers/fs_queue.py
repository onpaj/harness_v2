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


class _Corrupt(Exception):
    """Interní signál: soubor existuje, ale nejde deserializovat.

    Odlišuje se od zmizelého souboru (FileNotFoundError), aby volající
    _load() nemuseli hádat důvod z jediné hodnoty None."""

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

    def recover(self) -> int:
        count = 0
        for path in sorted(self._processing.glob("*.json")):
            try:
                task = self._load(path)
            except FileNotFoundError:
                # Soubor zmizel mezi glob() a čtením — vyhraný závod jiného
                # zabírajícího, ne poškození. Tiše přeskočit: žádný event,
                # žádný pokus o karanténu (ten by mohl smést i zdravý task,
                # kdyby mezitím na stejné cestě vznikl nový soubor).
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
        """Přečte a deserializuje task, nebo vyhodí přesně to, proč se to
        nepovedlo — FileNotFoundError (zmizel) vs. _Corrupt (poškozený).
        Jediné čtení, žádný re-check existence: ten by jen znovu otevřel
        stejné TOCTOU okno, které má toto rozlišení zavřít."""
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
            # Soubor zmizel mezi glob() a čtením — to je vyhraný závod jiného
            # zabírajícího (přesně to, co claim() toleruje), ne poškození.
            # Tiše přeskočit: žádný event, žádný pokus o karanténu.
            return None
        except _Corrupt as error:
            self._events.emit("corrupt", queue=self.name, path=str(path), reason=str(error))
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
        try:
            temporary.write_text(
                json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temporary, path)
        except Exception:
            # Selhání zápisu nebo přejmenování nesmí nechat temp soubor viset
            # navždy — na rozdíl od starého deterministického jména ho už nic
            # nepřepíše. Tvrdý SIGKILL přesně mezi write_text a touto větví
            # ho přesto může zanechat; to je pro tuto fázi akceptované riziko,
            # ne důvod stavět adresářový sweeper.
            temporary.unlink(missing_ok=True)
            raise
