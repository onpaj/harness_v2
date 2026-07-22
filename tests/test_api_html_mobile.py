"""Contract tests for the mobile-first markup added on top of the existing
board/detail templates. These assert only new attributes/classes — they must
never weaken or replace an assertion already pinned by test_api_html.py."""

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.memory import FakeClock
from harness.models import HistoryEntry, Task
from harness.ports.board import Board, BoardColumn, BoardTab
from tests.fakes import FakeBoardView

WORKING = Task(
    id="tsk_1",
    workflow_template="default",
    created="2026-07-19T10:00:00Z",
    repository="app-backend",
    status="development",
    last_outcome="request_changes",
    lock_id="lck_1",
    history=(
        HistoryEntry(
            at="2026-07-19T10:00:05Z",
            actor="dispatcher",
            from_step="review",
            to_step="development",
            outcome="request_changes",
        ),
    ),
)

EMPTY = Task(
    id="tsk_2",
    workflow_template="default",
    created="2026-07-19T10:00:01Z",
    status="development",
)


@pytest.fixture
def client() -> TestClient:
    board = Board(
        revision=1,
        workflows=(
            BoardTab(
                name="default",
                columns=(
                    BoardColumn(name="todo", tasks=()),
                    BoardColumn(name="development", tasks=(WORKING,)),
                    BoardColumn(name="done", tasks=()),
                ),
            ),
        ),
    )
    view = FakeBoardView(board, {"tsk_1": WORKING, "tsk_2": EMPTY})
    return TestClient(create_app(view=view, clock=FakeClock()))


def test_index_links_shared_stylesheet(client):
    body = client.get("/").text

    assert '<link rel="stylesheet" href="/static/app.css">' in body


def test_stylesheet_is_served_and_has_no_external_url(client):
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "://" not in response.text


def test_index_has_mobile_viewport_and_ios_chrome_meta(client):
    body = client.get("/").text

    assert 'viewport-fit=cover' in body
    assert 'apple-mobile-web-app-capable' in body


def test_stylesheet_paints_safe_area_insets(client):
    css = client.get("/static/app.css").text

    assert "env(safe-area-inset-top" in css
    assert "env(safe-area-inset-bottom" in css


def test_stylesheet_switches_board_layout_at_768px(client):
    css = client.get("/static/app.css").text

    assert "flex-direction: column" in css
    assert "@media (min-width: 768px)" in css
    assert "flex-direction: row" in css


def test_nav_renders_a_board_entry_for_both_appbar_and_tabbar(client):
    body = client.get("/").text

    assert body.count('data-section="board"') == 2


def test_empty_column_collapses_to_a_slim_placeholder(client):
    body = client.get("/fragment/board").text

    assert "No tasks" in body
    assert "column--empty" in body


def test_card_carries_a_status_class_for_the_stripe(client):
    body = client.get("/fragment/board").text

    assert "is-working" in body


def test_task_detail_has_info_history_output_tab_strip(client):
    body = client.get("/fragment/task/tsk_1").text

    assert 'class="tab active" data-tab="info"' in body
    assert 'data-tab="history"' in body
    assert 'data-tab="output"' in body
    assert 'data-panel="info"' in body
    assert 'data-panel="history"' in body
    assert 'data-panel="output"' in body


def test_task_detail_output_panel_keeps_sse_hooks_verbatim(client):
    body = client.get("/fragment/task/tsk_1").text

    assert 'hx-ext="sse"' in body
    assert 'sse-connect="/api/tasks/tsk_1/output/events"' in body
    assert 'sse-close="end"' in body
    assert 'id="stage-output"' in body
    assert 'sse-swap="line"' in body
    assert 'hx-swap="beforeend scroll:bottom"' in body


def test_task_detail_history_table_is_wrapped_in_a_scroll_container(client):
    body = client.get("/fragment/task/tsk_1").text

    scroll_start = body.index('<section class="tab-panel" data-panel="history"')
    scroll_section = body[scroll_start:]
    assert "table-scroll" in scroll_section.split("</section>", 1)[0]


def test_task_detail_shows_short_time_with_full_iso_in_title(client):
    body = client.get("/fragment/task/tsk_1").text

    assert 'title="2026-07-19T10:00:05Z"' in body
    assert "Jul 19, 10:00" in body
