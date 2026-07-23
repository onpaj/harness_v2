from fastapi.testclient import TestClient
from tests.fakes import FakeBoardView

from harness.api.app import create_app
from harness.drivers.memory import FakeClock
from harness.ports.board import Board
from harness.ports.updater import Updater, UpdateError, UpdateResult


class FakeUpdater(Updater):
    """Records that it was called and returns a scripted result — or raises."""

    def __init__(self, result: UpdateResult | None = None, error: str | None = None):
        self._result = result
        self._error = error
        self.calls = 0

    def update(self) -> UpdateResult:
        self.calls += 1
        if self._error is not None:
            raise UpdateError(self._error)
        assert self._result is not None
        return self._result


def make_client(updater: Updater | None = None) -> TestClient:
    board = Board(revision=1, workflows=())
    view = FakeBoardView(board, {})
    return TestClient(create_app(view=view, clock=FakeClock(), updater=updater))


def test_index_renders_the_update_button():
    client = make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert 'hx-post="/admin/update"' in response.text


def test_update_reports_a_version_change():
    updater = FakeUpdater(
        UpdateResult(
            before="0.9.1",
            after="0.9.2",
            changed=True,
            restarted=True,
            detail="updated to harness 0.9.2; restarting service com.harness…",
        )
    )
    client = make_client(updater)

    response = client.post("/admin/update")

    assert response.status_code == 200
    assert updater.calls == 1
    assert "updated to harness 0.9.2" in response.text
    assert 'class="update-result-ok"' in response.text


def test_update_reports_no_change():
    updater = FakeUpdater(
        UpdateResult(
            before="0.9.2",
            after="0.9.2",
            changed=False,
            restarted=False,
            detail="already up to date (harness 0.9.2)",
        )
    )
    client = make_client(updater)

    response = client.post("/admin/update")

    assert response.status_code == 200
    assert "already up to date" in response.text
    assert 'class="update-result-ok"' not in response.text  # muted, not the "ok" green


def test_update_surfaces_an_error_as_a_swappable_fragment():
    updater = FakeUpdater(error="uv is not installed")
    client = make_client(updater)

    response = client.post("/admin/update")

    # 200 so htmx swaps the message in rather than dropping it on the floor.
    assert response.status_code == 200
    assert "uv is not installed" in response.text
    assert 'class="update-result-error"' in response.text


def test_update_without_a_wired_updater_says_unavailable():
    """The default `_NullUpdater` keeps the button on every board, but a board
    that cannot upgrade itself (tests, from-source) says so instead of pretending."""
    client = make_client()

    response = client.post("/admin/update")

    assert response.status_code == 200
    assert "not available" in response.text
