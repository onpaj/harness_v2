import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeBoardView

from harness.api.app import create_app
from harness.drivers.memory import FakeClock
from harness.ports.board import Board


def make_client(*, version: str, build_time: str | None) -> TestClient:
    board = Board(revision=1, columns=())
    view = FakeBoardView(board, {})
    return TestClient(
        create_app(view=view, clock=FakeClock(), version=version, build_time=build_time)
    )


def test_index_shows_the_version_and_build_time():
    client = make_client(version="0.2.1", build_time="2026-07-21T10:32:00Z")

    response = client.get("/")

    assert response.status_code == 200
    assert "0.2.1" in response.text
    assert "2026-07-21T10:32:00Z" in response.text


def test_index_shows_unknown_when_build_time_is_absent():
    client = make_client(version="0.2.1", build_time=None)

    response = client.get("/")

    assert response.status_code == 200
    assert "unknown" in response.text


def test_board_fragment_does_not_carry_the_status_bar():
    """The SSE-refresh partial (`_columns.html`) rewrites only `#board`'s
    innerHTML — the status bar sits outside it and must not be re-fetched or
    blanked on every refresh."""
    client = make_client(version="0.2.1", build_time="2026-07-21T10:32:00Z")

    fragment = client.get("/fragment/board")
    index = client.get("/")

    assert "0.2.1" not in fragment.text
    assert "0.2.1" in index.text


def test_version_endpoint_returns_the_values():
    client = make_client(version="0.2.1", build_time="2026-07-21T10:32:00Z")

    response = client.get("/api/version")

    assert response.status_code == 200
    assert response.json() == {"version": "0.2.1", "build_time": "2026-07-21T10:32:00Z"}


def test_version_endpoint_returns_null_build_time_when_absent():
    client = make_client(version="unknown (not installed)", build_time=None)

    response = client.get("/api/version")

    assert response.status_code == 200
    assert response.json() == {"version": "unknown (not installed)", "build_time": None}


def test_create_app_defaults_version_when_not_supplied():
    """Every existing call site (14 across tests/) doesn't pass version info —
    create_app must keep compiling and rendering without it."""
    board = Board(revision=1, columns=())
    view = FakeBoardView(board, {})
    client = TestClient(create_app(view=view, clock=FakeClock()))

    response = client.get("/")

    assert response.status_code == 200
    assert "unknown" in response.text
