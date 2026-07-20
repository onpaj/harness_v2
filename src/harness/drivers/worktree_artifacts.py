"""Artefakty ve worktree — ploché, attempt-suffixované soubory.

Fáze 3 stěhuje artefakty *do worktree* pod `.artifacts/<task_id>/`, aby je
reálný subprocess agent viděl ve svém cwd. Layout je plochý:

- task-level: `plan.md`, `architecture-decisions.md` (bez čísla)
- step-attempt: `<step>-<NN>.md` (dvouciferný zero-pad, per-step counter od 01)

Zápisová strana se scvrkává na `next_attempt` — výpočet cesty dalšího pokusu.
Read-side `WorktreeArtifactView` čte `.artifacts/` ve worktree pro UI.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness.ports.artifacts import ArtifactRef, ArtifactView

# `<step>-<NN>.md` — step-attempt; cokoliv jiného `.md` je task-level.
_STEP_ATTEMPT = re.compile(r"^(?P<step>.+)-(?P<nn>\d+)\.md$")


def _artifacts_dir(worktree: Path, task_id: str) -> Path:
    return Path(worktree) / ".artifacts" / task_id


def next_attempt(worktree: Path, task_id: str, step: str) -> tuple[int, str]:
    """Alokuj další pokus kroku ve worktree.

    Spočítá existující `.artifacts/<task_id>/<step>-<číslo>.md` a vrátí
    `(NN, relpath)`, kde `NN = počet + 1` (první pokus = 1) a `relpath` je
    relativní k worktree kořeni. Neexistující adresář → `NN = 1`.
    """
    directory = _artifacts_dir(worktree, task_id)
    count = 0
    if directory.is_dir():
        for child in directory.iterdir():
            if not child.is_file():
                continue
            match = _STEP_ATTEMPT.match(child.name)
            if match is not None and match.group("step") == step:
                count += 1
    nn = count + 1
    relpath = f".artifacts/{task_id}/{step}-{nn:02d}.md"
    return nn, relpath


class WorktreeArtifactView(ArtifactView):
    """Read-only pohled na `.artifacts/` ve worktree tasku."""

    def __init__(self, worktrees_root: Path) -> None:
        self._worktrees_root = Path(worktrees_root)

    def _dir(self, task_id: str) -> Path:
        return _artifacts_dir(self._worktrees_root / task_id, task_id)

    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        directory = self._dir(task_id)
        if not directory.is_dir():
            return ()
        refs: list[ArtifactRef] = []
        for child in directory.iterdir():
            if not child.is_file():
                continue
            match = _STEP_ATTEMPT.match(child.name)
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
