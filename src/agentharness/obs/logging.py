"""Structured logging.

Every lifecycle event goes through :func:`log_event`, which writes the durable
store row *and* the JSON log line from the same call. Two sinks, one source --
they cannot drift, and the log file is a replayable mirror of the events table.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from agentharness.store.runs import RunStore

LOGGER_NAME = "agentharness"
LOG_FILENAME = "harness.jsonl"

#: Event fields the store models as columns; everything else lands in `data`.
_COLUMN_FIELDS = ("task_id", "trace_id", "run_id", "agent")


def configure_logging(home: Path, level: str = "INFO") -> None:
    """Emit JSON lines to ``<home>/logs/harness.jsonl`` and to stderr.

    Safe to call repeatedly: existing handlers on our logger are replaced, so a
    re-configure (tests, or a second `init`) does not duplicate output.
    """
    logs_dir = Path(home) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(message)s")
    for handler in (
        logging.FileHandler(logs_dir / LOG_FILENAME, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ):
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(level.upper())
    # The harness owns this logger tree; don't leak into whatever root config
    # the embedding process happens to have.
    logger.propagate = False

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger() -> Any:
    return structlog.get_logger(LOGGER_NAME)


def log_event(store: "RunStore", kind: str, **fields: Any) -> None:
    """Persist an event row and emit the matching log line.

    `task_id`/`trace_id`/`run_id`/`agent` map onto event columns; any other
    keyword lands in the event's JSON `data` blob and in the log line.
    """
    columns = {name: fields.pop(name, None) for name in _COLUMN_FIELDS}
    data = fields or None

    store.event(kind, data=data, **columns)

    get_logger().info(kind, kind=kind, **{k: v for k, v in columns.items()}, **fields)


def emit(store: "RunStore", kind: str, *, data: dict | None = None, **columns: Any) -> None:
    """Adapter for call sites that already build a `data` dict.

    Equivalent to `log_event`, but accepts the dict form used by the dispatcher
    and runner so both paths land in the same place and cannot drift.
    """
    log_event(store, kind, **columns, **(data or {}))
