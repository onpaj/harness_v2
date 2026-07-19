"""Per-repo advisory file lock guarding ref mutations in a mirror."""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockTimeout(Exception):
    """Raised when a repo lock could not be acquired within the timeout."""


@contextmanager
def repo_lock(home: Path, repo_id: str, timeout: float = 120.0) -> Iterator[Path]:
    """Hold an exclusive `flock` on `<home>/locks/<repo_id>.lock`.

    Raises `LockTimeout` if the lock is still held elsewhere after `timeout`
    seconds. Polls rather than blocking so the timeout is honoured.
    """
    lock_dir = Path(home) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{repo_id}.lock"

    deadline = time.monotonic() + timeout
    fh = lock_path.open("a+")
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"could not acquire lock for {repo_id!r} within {timeout}s "
                        f"({lock_path})"
                    ) from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            yield lock_path
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
