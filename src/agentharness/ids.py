"""Sortable, prefixed identifiers for tasks, traces, and runs."""

from ulid import ULID


def _new(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def new_task_id() -> str:
    return _new("t")


def new_trace_id() -> str:
    return _new("tr")


def new_run_id() -> str:
    return _new("r")
