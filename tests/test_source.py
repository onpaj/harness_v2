"""`ports/source.py`'s pure routing helpers — `effective_sink_kind`, `dedup_key`."""

from harness.models import Task
from harness.ports.source import effective_sink_kind


def _task(data: dict) -> Task:
    return Task(id="t-1", created="2026-07-23T10:00:00Z", data=data)


def test_explicit_sink_wins_over_source():
    task = _task({"sink": {"kind": "slack"}, "source": {"kind": "github"}})

    assert effective_sink_kind(task) == "slack"


def test_falls_back_to_source_kind_when_sink_absent():
    task = _task({"source": {"kind": "github"}})

    assert effective_sink_kind(task) == "github"


def test_neither_sink_nor_source_present_is_none():
    task = _task({})

    assert effective_sink_kind(task) is None


def test_empty_sink_dict_falls_back_to_source():
    task = _task({"sink": {}, "source": {"kind": "github"}})

    assert effective_sink_kind(task) == "github"


def test_sink_with_falsy_kind_falls_back_to_source():
    task = _task({"sink": {"kind": None}, "source": {"kind": "github"}})

    assert effective_sink_kind(task) == "github"


def test_sink_but_no_source_returns_sink_kind():
    task = _task({"sink": {"kind": "github"}})

    assert effective_sink_kind(task) == "github"
