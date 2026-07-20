"""Filesystem artifact store.

On-disk layout: `<root>/<task_id>/<step>/<attempt>/<name>`. Attempt is the
ordinal of the attempt — the count of already existing subdirectories of the
step. A step re-run therefore never overwrites a previous attempt; it just
creates the next one.
"""

from __future__ import annotations

from pathlib import Path

from harness.ports.artifacts import ArtifactRef, ArtifactSlot, ArtifactStore


class FilesystemArtifactSlot(ArtifactSlot):
    """A single attempt on disk. `put` writes a file into the attempt's directory."""

    def __init__(self, directory: Path, attempt: int) -> None:
        self._directory = directory
        self._attempt = attempt

    @property
    def attempt(self) -> int:
        return self._attempt

    def put(self, name: str, content: str) -> None:
        (self._directory / name).write_text(content, encoding="utf-8")


class FilesystemArtifactStore(ArtifactStore):
    """Artifacts as a tree of directories under `root`."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def begin(self, task_id: str, step: str) -> FilesystemArtifactSlot:
        step_dir = self._root / task_id / step
        attempt = self._count_attempts(step_dir)
        attempt_dir = step_dir / str(attempt)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return FilesystemArtifactSlot(attempt_dir, attempt)

    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        task_dir = self._root / task_id
        if not task_dir.is_dir():
            return ()
        refs: list[ArtifactRef] = []
        for step_dir in task_dir.iterdir():
            if not step_dir.is_dir():
                continue
            step = step_dir.name
            for attempt_dir in step_dir.iterdir():
                if not attempt_dir.is_dir():
                    continue
                try:
                    attempt = int(attempt_dir.name)
                except ValueError:
                    continue
                for artifact in attempt_dir.iterdir():
                    if artifact.is_file():
                        refs.append(ArtifactRef(step, attempt, artifact.name))
        return tuple(sorted(refs, key=lambda ref: (ref.step, ref.attempt, ref.name)))

    def read(self, task_id: str, step: str, attempt: int, name: str) -> str | None:
        path = self._root / task_id / step / str(attempt) / name
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    @staticmethod
    def _count_attempts(step_dir: Path) -> int:
        if not step_dir.is_dir():
            return 0
        return sum(1 for child in step_dir.iterdir() if child.is_dir())
