"""The stats subpage and its JSON twin (ADR-0026).

These also cover the template itself: a Jinja error in `stats.html` only
surfaces at render time, so the HTML test is the one that catches it.
"""

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeBoardView

from harness.api.app import create_app
from harness.drivers.memory import FakeClock, MemoryTaskQueue
from harness.models import END, FAILED, HistoryEntry, Task
from harness.ports.board import Board
from harness.stats import QueueStatsView

NOW = "2026-08-02T12:00:00Z"


def settled_task(task_id, *, to=END, at=NOW, **data) -> Task:
    return Task(
        id=task_id,
        workflow_template="development",
        repository="harness_v2",
        created="2026-08-01T09:00:00Z",
        status=END if to == END else FAILED,
        history=(
            HistoryEntry(
                at=at, actor="dispatcher", from_step="development", to_step=to
            ),
        ),
        data={
            "source": {"kind": "github", "repo": "o/r", "issue": 1, "labels": ["bug"]},
            **data,
        },
    )


@pytest.fixture
def client() -> TestClient:
    done = MemoryTaskQueue("done")
    done.put(settled_task("a", pr={"url": "https://gh/pr/1", "number": 1}))
    done.put(settled_task("b"))
    failed = MemoryTaskQueue("failed")
    failed.put(settled_task("c", to=FAILED))
    stats = QueueStatsView(queues=[done, failed], clock=FakeClock(NOW))
    view = FakeBoardView(Board(revision=1, workflows=()), {})
    return TestClient(create_app(view=view, clock=FakeClock(NOW), stats=stats))


def test_json_report_counts_the_window(client):
    payload = client.get("/api/stats?days=7").json()

    assert payload["windowDays"] == 7
    assert payload["overall"] == {
        "completed": 2,
        "failed": 1,
        "retired": 0,
        "settled": 3,
        "successRate": pytest.approx(2 / 3),
    }
    assert payload["byKind"][0]["name"] == "issue"


def test_json_report_clamps_a_nonsense_window_instead_of_erroring(client):
    """A hand-typed `?days=` is not worth a 422 — the report states the window
    it actually used."""
    assert client.get("/api/stats?days=nonsense").json()["windowDays"] == 7
    assert client.get("/api/stats?days=0").json()["windowDays"] == 7
    assert client.get("/api/stats?days=99999").json()["windowDays"] == 3650


def test_json_report_defaults_to_a_week(client):
    assert client.get("/api/stats").json()["windowDays"] == 7


def test_stats_page_renders(client):
    response = client.get("/stats?days=30")

    assert response.status_code == 200
    body = response.text
    assert "Stats" in body
    assert "success rate" in body
    assert "67%" in body  # 2 of 3
    assert "Where it fails" in body
    assert "development" in body  # the failing step


def test_stats_page_is_in_the_nav(client):
    assert 'href="/stats"' in client.get("/stats").text


def test_a_board_wired_without_stats_still_renders_the_page():
    """`create_app` stays backward compatible — the empty view reports zeroes
    rather than 500ing, the `_EmptyArtifactView` pattern."""
    view = FakeBoardView(Board(revision=1, workflows=()), {})
    client = TestClient(create_app(view=view, clock=FakeClock(NOW)))

    assert client.get("/stats").status_code == 200
    # No settled tasks: a dash, never 0%.
    assert client.get("/api/stats").json()["overall"]["successRate"] is None
