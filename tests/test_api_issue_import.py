"""The Ahanas board's manual "Add issue" button — `POST /issues/import` and
the board page's button/dialog markup."""

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.memory import FakeClock
from harness.ports.board import Board, BoardColumn, BoardTab
from harness.ports.issue_import import IssueImportResult
from tests.fakes import FakeBoardView, FakeIssueImport


@pytest.fixture
def empty_board() -> Board:
    return Board(
        revision=1,
        workflows=(
            BoardTab(
                name="default",
                columns=(
                    BoardColumn(name="todo", tasks=()),
                    BoardColumn(name="done", tasks=()),
                    BoardColumn(name="failed", tasks=()),
                ),
            ),
        ),
    )


def make_client(empty_board, issue_import=None) -> TestClient:
    view = FakeBoardView(empty_board, {})
    return TestClient(
        create_app(view=view, clock=FakeClock(), issue_import=issue_import)
    )


# --- board page shows the button and dialog ---------------------------------


def test_board_page_has_the_add_issue_button(empty_board):
    client = make_client(empty_board)

    body = client.get("/").text

    assert 'id="add-issue-open"' in body
    assert "Add issue" in body


def test_board_page_has_the_add_issue_dialog_with_a_textarea(empty_board):
    client = make_client(empty_board)

    body = client.get("/").text

    assert '<dialog id="add-issue">' in body
    assert 'name="refs"' in body
    assert 'hx-post="/issues/import"' in body
    assert 'hx-target="#add-issue-results"' in body


# --- POST /issues/import -----------------------------------------------------


def test_import_single_ref_reports_queued(empty_board):
    issue_import = FakeIssueImport()
    client = make_client(empty_board, issue_import)

    response = client.post("/issues/import", data={"refs": "onpaj/harness_v2#42"})

    assert response.status_code == 200
    assert issue_import.calls == ["onpaj/harness_v2#42"]
    assert "queued as tsk_onpaj/harness_v2#42" in response.text


def test_import_splits_on_commas_spaces_and_newlines(empty_board):
    issue_import = FakeIssueImport()
    client = make_client(empty_board, issue_import)

    client.post(
        "/issues/import",
        data={"refs": "onpaj/a#1, onpaj/b#2\nonpaj/c#3"},
    )

    assert issue_import.calls == ["onpaj/a#1", "onpaj/b#2", "onpaj/c#3"]


def test_import_empty_submission_reports_no_refs_without_calling_add(empty_board):
    issue_import = FakeIssueImport()
    client = make_client(empty_board, issue_import)

    response = client.post("/issues/import", data={"refs": "   "})

    assert issue_import.calls == []
    assert "no refs given" in response.text


def test_import_mixed_batch_shows_each_outcome(empty_board):
    issue_import = FakeIssueImport(
        {
            "onpaj/not-a-repo#9": IssueImportResult(
                ref="onpaj/not-a-repo#9",
                ok=False,
                error="repo 'onpaj/not-a-repo' is not registered",
            )
        }
    )
    client = make_client(empty_board, issue_import)

    response = client.post(
        "/issues/import",
        data={"refs": "onpaj/a#1\nonpaj/not-a-repo#9"},
    )

    body = response.text
    assert "queued as tsk_onpaj/a#1" in body
    assert "not registered" in body
    ok_index = body.index("queued as tsk_onpaj/a#1")
    error_index = body.index("not registered")
    assert ok_index < error_index  # submission order preserved


def test_import_already_queued_is_reported_as_success(empty_board):
    issue_import = FakeIssueImport(
        {
            "onpaj/a#1": IssueImportResult(
                ref="onpaj/a#1", ok=True, already_queued=True, task_id="tsk_existing"
            )
        }
    )
    client = make_client(empty_board, issue_import)

    response = client.post("/issues/import", data={"refs": "onpaj/a#1"})

    assert "already queued as tsk_existing" in response.text


def test_import_without_configured_issue_import_reports_not_configured(empty_board):
    client = make_client(empty_board, issue_import=None)

    response = client.post("/issues/import", data={"refs": "onpaj/a#1"})

    assert "not configured" in response.text
