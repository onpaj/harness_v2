"""Read-side pohled na artefakty ve worktree pro UI.

Layout a konvenci pojmenování vlastní `harness.artifacts_layout`; tento driver
z ní čte. Zápisová strana `ArtifactStore` z fáze 2 zaniká — artefakty píše
agent přímo do worktree, číslo pokusu počítá `artifacts_layout.next_attempt`.
"""

from __future__ import annotations

from pathlib import Path

from harness.artifacts_layout import STEP_ATTEMPT, artifacts_dir
from harness.ports.artifacts import ArtifactRef, ArtifactView


class WorktreeArtifactView(ArtifactView):
    """Read-only pohled na `.artifacts/` ve worktree tasku."""

    def __init__(self, worktrees_root: Path) -> None:
        self._worktrees_root = Path(worktrees_root)

    def _dir(self, task_id: str) -> Path:
        return artifacts_dir(self._worktrees_root / task_id, task_id)

    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        directory = self._dir(task_id)
        if not directory.is_dir():
            return ()
        refs: list[ArtifactRef] = []
        for child in directory.iterdir():
            if not child.is_file():
                continue
            match = STEP_ATTEMPT.match(child.name)
            if match is not None:
                refs.append(
                    ArtifactRef(match.group("step"), int(match.group("nn")), child.name)
                )
            elif child.name.endswith(".md"):
                stem = child.name[: -len(".md")]
                refs.append(ArtifactRef(stem, 0, child.name))
        return tuple(sorted(refs, key=lambda ref: (ref.step, ref.attempt, ref.name)))

    def read(self, task_id: str, step: str, attempt: int, name: str) -> str | None:
        path = self._dir(task_id) / name
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            return None
