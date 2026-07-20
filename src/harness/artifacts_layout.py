"""Convention for laying out artifacts in the worktree — the single source of truth.

Artifacts live under `.artifacts/<task_id>/` as flat files:

- task-level: `plan.md`, `architecture-decisions.md` (no number)
- step-attempt: `<step>-<NN>.md` (two-digit zero-pad, per-step counter from 01)

The module imports nothing from the `harness` package — it is a pure domain
utility like `models`/`ids`. Both the write side (`next_attempt` in the behavior)
and the read side (`WorktreeArtifactView`) use it, so the naming rule isn't
duplicated.
"""

from __future__ import annotations

import re
from pathlib import Path

# `<step>-<NN>.md` — step-attempt; any other `.md` is task-level.
STEP_ATTEMPT = re.compile(r"^(?P<step>.+)-(?P<nn>\d+)\.md$")


def artifacts_dir(worktree: Path, task_id: str) -> Path:
    """The task's artifacts directory inside the worktree."""
    return Path(worktree) / ".artifacts" / task_id


def next_attempt(worktree: Path, task_id: str, step: str) -> tuple[int, str]:
    """Allocate the next attempt of a step in the worktree.

    Counts the existing `.artifacts/<task_id>/<step>-<number>.md` files and returns
    `(NN, relpath)`, where `NN = count + 1` (first attempt = 1) and `relpath` is
    relative to the worktree root. A missing directory → `NN = 1`.
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
