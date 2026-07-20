"""Konvence rozmístění artefaktů ve worktree — jediné místo pravdy.

Artefakty žijí pod `.artifacts/<task_id>/` jako ploché soubory:

- task-level: `plan.md`, `architecture-decisions.md` (bez čísla)
- step-attempt: `<step>-<NN>.md` (dvouciferný zero-pad, per-step counter od 01)

Modul neimportuje nic z balíku `harness` — je to čistá doménová utilita jako
`models`/`ids`. Sahá na něj zápisová strana (`next_attempt` v behaviru) i
čtecí (`WorktreeArtifactView`), aby pravidlo pojmenování nebylo duplikované.
"""

from __future__ import annotations

import re
from pathlib import Path

# `<step>-<NN>.md` — step-attempt; cokoliv jiného `.md` je task-level.
STEP_ATTEMPT = re.compile(r"^(?P<step>.+)-(?P<nn>\d+)\.md$")


def artifacts_dir(worktree: Path, task_id: str) -> Path:
    """Adresář artefaktů tasku uvnitř worktree."""
    return Path(worktree) / ".artifacts" / task_id


def next_attempt(worktree: Path, task_id: str, step: str) -> tuple[int, str]:
    """Alokuj další pokus kroku ve worktree.

    Spočítá existující `.artifacts/<task_id>/<step>-<číslo>.md` a vrátí
    `(NN, relpath)`, kde `NN = počet + 1` (první pokus = 1) a `relpath` je
    relativní k worktree kořeni. Neexistující adresář → `NN = 1`.
    """
    directory = artifacts_dir(worktree, task_id)
    count = 0
    if directory.is_dir():
        for child in directory.iterdir():
            if not child.is_file():
                continue
            match = STEP_ATTEMPT.match(child.name)
            if match is not None and match.group("step") == step:
                count += 1
    nn = count + 1
    relpath = f".artifacts/{task_id}/{step}-{nn:02d}.md"
    return nn, relpath
