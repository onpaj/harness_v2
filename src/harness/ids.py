"""Identity generators. The only place where randomness originates."""

from __future__ import annotations

import uuid


def new_task_id() -> str:
    return f"tsk_{uuid.uuid4().hex[:16]}"


def new_lock_id() -> str:
    return f"lck_{uuid.uuid4().hex[:16]}"
