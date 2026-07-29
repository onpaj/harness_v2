import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.api.routes import _outcome_step
from harness.drivers.memory import FakeClock
from harness.models import HistoryEntry, Task
from harness.ports.board import (
    COLUMN_INBOX,
    COLUMN_STEP,
    COLUMN_TERMINAL,
    UNKNOWN_WORKFLOW,
    UNKNOWN_WORKFLOW_LABEL,
    UNKNOWN_WORKFLOW_NOTE,
    Board,
    BoardColumn,
    BoardTab,
)
from tests.fakes import FakeBoardView, FakeTaskControl

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

WAITING = Task(
    id="tsk_2",
    workflow_template="default",
    created="2026-07-19T10:00:01Z",
    status="development",
)

TITLED = Task(
    id="tsk_3",
    workflow_template="default",
    created="2026-07-19T10:00:02Z",
    repository="/Users/x/repos/my-repo",
    worktree="/Users/x/worktrees/wt_sacramento",
    status="development",
    data={"title": "Fix the login bug"},
)


# The shape that made a card read as "this task is finished": a step's `done`
# verdict sitting next to "processing", two columns from the end.
MIDFLIGHT = Task(
    id="tsk_5",
    workflow_template="default",
    created="2026-07-19T09:59:00Z",
    status="development",
    last_outcome="done",
    lock_id="lck_5",
    history=(
        HistoryEntry(
            at="2026-07-19T09:59:10Z",
            actor="worker",
            from_step="plan",
            to_step=None,
            outcome="done",
        ),
        HistoryEntry(
            at="2026-07-19T09:59:11Z",
            actor="dispatcher",
            from_step="plan",
            to_step="development",
            outcome="done",
        ),
    ),
)

# Idle in a step column after a clean hand-off: the shape whose green accent
# stripe read as "this task is finished".
HANDED_OFF = Task(
    id="tsk_8",
    workflow_template="default",
    created="2026-07-19T09:58:00Z",
    status="development",
    last_outcome="done",
    history=(
        HistoryEntry(
            at="2026-07-19T09:58:10Z",
            actor="dispatcher",
            from_step="plan",
            to_step="development",
            outcome="done",
        ),
    ),
)

# Bounced back for rework, idle in the same step column. "It came back" is not
# something a column name says, so this one keeps its accent everywhere.
BOUNCED = Task(
    id="tsk_10",
    workflow_template="default",
    created="2026-07-19T09:58:30Z",
    status="development",
    last_outcome="request_changes",
    history=(
        HistoryEntry(
            at="2026-07-19T09:58:40Z",
            actor="dispatcher",
            from_step="review",
            to_step="development",
            outcome="request_changes",
        ),
    ),
)

# An outcome with no history to attribute it to — the badge falls back to the
# bare word rather than inventing a step.
UNATTRIBUTED = Task(
    id="tsk_6",
    workflow_template="default",
    created="2026-07-19T10:00:06Z",
    status="done",
    last_outcome="done",
)


WORKFLOW_LESS = Task(
    id="tsk_4",
    workflow_template=None,
    step="development",
    created="2026-07-19T10:00:04Z",
    status="development",
)


BROKEN = Task(
    id="tsk_9",
    workflow_template="default",
    created="2026-07-19T10:00:03Z",
    status="failed",
    history=(
        HistoryEntry(
            at="2026-07-19T10:00:09Z",
            actor="dispatcher",
            from_step="plan",
            to_step="failed",
            reason="step 'plan' has no queue",
        ),
    ),
)


@pytest.fixture
def client() -> TestClient:
    board = Board(
        revision=9,
        workflows=(
            BoardTab(
                name="default",
                columns=(
                    BoardColumn(name="todo", tasks=(), kind=COLUMN_INBOX),
                    BoardColumn(
                        name="development",
                        tasks=(MIDFLIGHT, WORKING, WAITING, TITLED, HANDED_OFF, BOUNCED),
                    ),
                    BoardColumn(
                        name="done", tasks=(UNATTRIBUTED,), kind=COLUMN_TERMINAL
                    ),
                    BoardColumn(name="failed", tasks=(), kind=COLUMN_TERMINAL),
                ),
            ),
        ),
        repository_names=("app-backend", "other-repo"),
    )
    # BROKEN is retrievable via get() (for the detail fragment) without cluttering
    # the rendered columns, so the board-rendering tests stay undisturbed.
    view = FakeBoardView(
        board, {"tsk_1": WORKING, "tsk_4": WORKFLOW_LESS, "tsk_9": BROKEN}
    )
    return TestClient(create_app(view=view, clock=FakeClock()))


def test_index_renders_board_shell(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "/static/htmx.min.js" in body
    assert "/static/sse.js" in body
    assert "/static/format-local-time.js" in body
    assert "/api/events" in body
    assert "https://" not in body
    assert 'name="viewport"' in body
    assert "width=device-width" in body


def test_index_has_viewport_meta_and_scrollable_dialog(client):
    body = client.get("/").text

    assert (
        '<meta name="viewport" '
        'content="width=device-width, initial-scale=1, viewport-fit=cover">'
    ) in body
    assert "/static/app.css" in body

    css = client.get("/static/app.css").text
    dialog_rule = css[css.index("dialog#detail {") : css.index("}", css.index("dialog#detail {"))]
    assert "max-height" in dialog_rule
    terminal_rule = css[css.index(".terminal {") : css.index("}", css.index(".terminal {"))]
    assert "overflow: auto" in terminal_rule
    assert "overscroll-behavior: contain" in terminal_rule


def test_index_contains_columns(client):
    body = client.get("/").text

    assert "development" in body
    assert "done" in body
    assert "failed" in body


def test_fragment_board_lists_cards_in_order(client):
    body = client.get("/fragment/board").text

    assert body.index("tsk_1") < body.index("tsk_2")
    assert "app-backend" in body


def test_card_shows_working_badge_only_when_locked(client):
    body = client.get("/fragment/board").text

    card_one, card_two = body.split("tsk_2", 1)
    assert "processing" in card_one
    assert "processing" not in card_two


def test_card_shows_title_instead_of_id_when_present(client):
    body = client.get("/fragment/board").text

    assert "Fix the login bug" in body
    # Titled task shows its title as the card label, not its raw id.
    assert ">tsk_3<" not in body


def test_card_falls_back_to_id_when_no_title(client):
    body = client.get("/fragment/board").text

    assert "tsk_1" in body
    assert "tsk_2" in body


def test_card_shows_repo_and_worktree_basename_not_path(client):
    body = client.get("/fragment/board").text

    assert "my-repo" in body
    assert "wt_sacramento" in body
    assert "/Users/x/" not in body


def test_card_shows_last_outcome(client):
    body = client.get("/fragment/board").text

    assert "request_changes" in body


def test_index_renders_filter_bar_with_repository_options(client):
    body = client.get("/").text

    assert 'id="filter-repo"' in body
    assert 'id="filter-text"' in body
    assert "All repositories" in body
    assert '<option value="app-backend">app-backend</option>' in body
    assert '<option value="other-repo">other-repo</option>' in body


def test_card_carries_data_repository_and_data_search_attributes(client):
    body = client.get("/fragment/board").text

    assert 'data-repository="app-backend"' in body
    # WAITING has no repository — the attribute is present but empty, not
    # omitted, so the client-side script never has to null-check it.
    assert 'data-repository=""' in body
    assert 'data-search="fix the login bug tsk_3' in body


def test_outcome_badge_names_the_step_that_reported_it(client):
    """A `done` badge is a *step's* verdict, not the task's — a card mid-flow
    showing a bare "done" next to "processing" read as a finished task."""
    body = client.get("/fragment/board").text

    card = body[body.index("tsk_5") : body.index("tsk_1")]
    assert '<span class="badge__step">plan</span>' in card
    assert "step 'plan' reported done" in card


def test_outcome_badge_falls_back_to_bare_outcome_without_history(client):
    """Nothing to attribute the outcome to — the badge stays a bare word rather
    than inventing a step."""
    body = client.get("/fragment/board").text

    card = body[body.index("tsk_6") :]
    assert "badge__step" not in card
    assert "badge done" in card


def _card_open_tag(body: str, task_id: str) -> str:
    """The opening `<div class="card …">` of one card. The accent class sits
    before the card's id in the markup, so splitting the page on the id would
    cut it off."""
    marker = f'hx-get="/fragment/task/{task_id}"'
    end = body.index(marker)
    return body[body.rindex('<div class="card', 0, end) : end]


def test_green_accent_is_only_for_a_task_that_actually_finished(client):
    """The stripe says what is happening to the *task*. A `done` outcome in a
    step column is the previous step's verdict — green there read as complete."""
    body = client.get("/fragment/board").text

    assert "is-done" not in _card_open_tag(body, "tsk_8")
    assert "is-done" in _card_open_tag(body, "tsk_6")


def test_request_changes_accent_survives_in_a_step_column(client):
    """Unlike `done`, "it came back" is not something a column name says — so
    the amber stripe stays wherever the task sits."""
    body = client.get("/fragment/board").text

    assert "is-changes" in _card_open_tag(body, "tsk_10")


def test_working_accent_wins_over_a_finished_column(client):
    """A claimed task is blue whatever its last outcome was — the accent is
    ordered, and "being worked on right now" outranks a stale verdict."""
    body = client.get("/fragment/board").text

    assert "is-working" in _card_open_tag(body, "tsk_5")
    assert "is-done" not in _card_open_tag(body, "tsk_5")


def test_outcome_step_reads_the_consumer_delivery_entry():
    """The dispatcher's routing entry usually lands last, but a task claimed and
    delivered with no dispatch yet has only the consumer's own entry (`to_step`
    unset) — `from_step` names the reporting step in both shapes."""
    delivered = Task(
        id="tsk_7",
        created="2026-07-19T10:00:07Z",
        status="development",
        last_outcome="done",
        history=(
            HistoryEntry(
                at="2026-07-19T10:00:08Z",
                actor="worker",
                from_step="design",
                to_step=None,
                outcome="done",
            ),
        ),
    )

    assert _outcome_step(delivered) == "design"
    assert _outcome_step(WAITING) == ""


def test_card_shows_time_in_state_only_when_history_exists(client):
    body = client.get("/fragment/board").text

    card_one, card_two = body.split("tsk_2", 1)
    assert "2026-07-19T10:00:05Z" in card_one
    assert "since" not in card_two


def test_card_time_is_a_time_element_for_client_side_localization(client):
    body = client.get("/fragment/board").text

    assert '<time datetime="2026-07-19T10:00:05Z"' in body
    assert 'title="2026-07-19T10:00:05Z UTC"' in body


def test_fragment_task_shows_metadata_and_history(client):
    body = client.get("/fragment/task/tsk_1").text

    assert "app-backend" in body
    assert "review" in body
    assert "development" in body
    assert "2026-07-19T10:00:05Z" in body
    assert "dispatcher" in body


def test_fragment_task_times_are_time_elements_for_client_side_localization(client):
    body = client.get("/fragment/task/tsk_1").text

    # "created" field
    assert '<time datetime="2026-07-19T10:00:00Z"' in body
    assert 'title="2026-07-19T10:00:00Z UTC"' in body
    # history table "time" column
    assert '<time datetime="2026-07-19T10:00:05Z"' in body
    assert 'title="2026-07-19T10:00:05Z UTC"' in body


def test_fragment_task_shows_copy_id_button(client):
    body = client.get("/fragment/task/tsk_1").text

    assert 'class="copy-id-btn"' in body
    assert 'data-copy-id="tsk_1"' in body


def test_fragment_task_shows_pipeline_workflow_and_dash_step(client):
    """FR-9: a pipeline task (workflow set, step unset) shows its workflow
    name and a dash — never a literal 'None' — in the step cell."""
    body = client.get("/fragment/task/tsk_1").text

    assert "default" in body
    assert ">None<" not in body


def test_fragment_task_shows_dash_workflow_and_real_step_when_workflow_less(client):
    """FR-9: a workflow-less task (workflow unset, step set) shows a dash —
    never a literal 'None' — in the workflow cell, and its real step."""
    body = client.get("/fragment/task/tsk_4").text

    assert ">None<" not in body
    assert "development" in body


def test_fragment_task_unknown_returns_404(client):
    assert client.get("/fragment/task/neznamy").status_code == 404


def test_static_files_are_served(client):
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/format-local-time.js").status_code == 200


def test_local_time_js_never_mixes_style_and_component_options(client):
    """ECMA-402 forbids combining dateStyle/timeStyle with a component option
    such as timeZoneName — `new Intl.DateTimeFormat` then throws a TypeError,
    which the script's swallowing try/catch turns into every timestamp
    silently staying raw UTC (the #111 regression). timeStyle 'long' already
    carries the timezone name, so the two families never need to meet."""
    source = client.get("/static/format-local-time.js").text

    uses_styles = "dateStyle" in source or "timeStyle" in source
    assert not (uses_styles and "timeZoneName" in source)


def test_no_endpoint_mutates(client):
    assert client.post("/api/board").status_code == 405
    assert client.delete("/api/tasks/tsk_1").status_code == 405


# --- Restart a failed task -------------------------------------------------


def _board_with_broken() -> Board:
    return Board(
        revision=9,
        workflows=(
            BoardTab(
                name="default",
                columns=(
                    BoardColumn(name="todo", tasks=()),
                    BoardColumn(name="development", tasks=(WORKING,)),
                    BoardColumn(name="failed", tasks=(BROKEN,)),
                ),
            ),
        ),
    )


def test_restart_button_shown_only_for_failed_task(client):
    failed_body = client.get("/fragment/task/tsk_9").text
    working_body = client.get("/fragment/task/tsk_1").text

    assert "/tasks/tsk_9/restart" in failed_body
    assert "Restart" in failed_body
    assert "/tasks/tsk_1/restart" not in working_body


def test_restart_invokes_control_and_returns_refreshed_fragment():
    view = FakeBoardView(_board_with_broken(), {"tsk_9": BROKEN})
    control = FakeTaskControl(result=True)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_9/restart")

    assert response.status_code == 200
    assert control.restarted == ["tsk_9"]
    assert "tsk_9" in response.text


def test_restart_returns_404_when_control_reports_nothing():
    view = FakeBoardView(_board_with_broken(), {"tsk_9": BROKEN})
    control = FakeTaskControl(result=False)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_9/restart")

    assert response.status_code == 404
    assert control.restarted == ["tsk_9"]


def test_restart_without_control_reports_nothing(client):
    # The default board is wired with the null control (create_app default).
    assert client.post("/tasks/tsk_9/restart").status_code == 404


# --- Delete a task -----------------------------------------------------


def test_delete_button_shown_unconditionally(client):
    failed_body = client.get("/fragment/task/tsk_9").text
    working_body = client.get("/fragment/task/tsk_1").text

    assert "/tasks/tsk_9/delete" in failed_body
    assert "/tasks/tsk_1/delete" in working_body
    assert "hx-confirm" in failed_body


def test_delete_invokes_control_and_returns_empty_body():
    view = FakeBoardView(_board_with_broken(), {"tsk_9": BROKEN})
    control = FakeTaskControl(delete_result=True)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_9/delete")

    assert response.status_code == 200
    assert response.text == ""
    assert control.deleted == ["tsk_9"]


def test_delete_returns_404_when_control_reports_nothing():
    view = FakeBoardView(_board_with_broken(), {"tsk_9": BROKEN})
    control = FakeTaskControl(delete_result=False)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_9/delete")

    assert response.status_code == 404
    assert control.deleted == ["tsk_9"]


def test_delete_without_control_reports_nothing(client):
    # The default board is wired with the null control (create_app default).
    assert client.post("/tasks/tsk_9/delete").status_code == 404


def test_delete_does_not_affect_restart_call_log():
    view = FakeBoardView(_board_with_broken(), {"tsk_9": BROKEN})
    control = FakeTaskControl(result=True, delete_result=True)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    api.post("/tasks/tsk_9/delete")

    assert control.deleted == ["tsk_9"]
    assert control.restarted == []


# --- Multi-workflow tab strip (FR-5, FR-6) ----------------------------------


def _two_tab_board() -> Board:
    return Board(
        revision=1,
        workflows=(
            BoardTab(name="default", columns=(BoardColumn(name="plan", tasks=(WORKING,)),)),
            BoardTab(name="hotfix", columns=(BoardColumn(name="patch", tasks=()),)),
        ),
    )


def test_single_workflow_renders_no_tab_strip(client):
    # `client`'s fixture board has only one tab ("default").
    body = client.get("/fragment/board").text

    assert "tab-strip" not in body


def test_multiple_workflows_render_a_tab_per_workflow():
    view = FakeBoardView(_two_tab_board(), {"tsk_1": WORKING})
    api = TestClient(create_app(view=view, clock=FakeClock()))

    body = api.get("/fragment/board").text

    assert 'data-workflow="default"' in body
    assert 'data-workflow="hotfix"' in body
    assert body.index('role="tab" data-workflow="default"') < body.index(
        'role="tab" data-workflow="hotfix"'
    )


def test_tab_strip_shows_a_per_tab_task_count():
    view = FakeBoardView(_two_tab_board(), {"tsk_1": WORKING})
    api = TestClient(create_app(view=view, clock=FakeClock()))

    body = api.get("/fragment/board").text

    assert '<span class="tab__count">1</span>' in body
    assert '<span class="tab__count">0</span>' in body


# --- Column kinds, descriptions and the "no workflow" tab -------------------


def _kinded_board(*, unknown_tasks: tuple[Task, ...] = ()) -> Board:
    """A board shaped the way the projection actually builds one: lifecycle
    columns carry their kind, step columns carry the workflow's description."""
    return Board(
        revision=1,
        workflows=(
            BoardTab(
                name="default",
                columns=(
                    BoardColumn(name="todo", tasks=(), kind=COLUMN_INBOX,
                                description="waiting for the dispatcher"),
                    BoardColumn(name="plan", tasks=(), kind=COLUMN_STEP,
                                description="break the request down"),
                    BoardColumn(name="development", tasks=(WORKING,), kind=COLUMN_STEP),
                    BoardColumn(name="done", tasks=(), kind=COLUMN_TERMINAL),
                    BoardColumn(name="failed", tasks=(), kind=COLUMN_TERMINAL),
                ),
            ),
            BoardTab(
                name=UNKNOWN_WORKFLOW,
                columns=(
                    BoardColumn(name="todo", tasks=(), kind=COLUMN_INBOX),
                    BoardColumn(name="development", tasks=unknown_tasks, kind=COLUMN_STEP),
                    BoardColumn(name="zzz-idle-step", tasks=(), kind=COLUMN_STEP),
                    BoardColumn(name="done", tasks=(), kind=COLUMN_TERMINAL),
                ),
            ),
        ),
    )


def _board_client(board: Board) -> TestClient:
    return TestClient(create_app(view=FakeBoardView(board, {}), clock=FakeClock()))


def test_columns_are_grouped_by_kind_with_labels():
    body = _board_client(_kinded_board()).get("/fragment/board").text

    assert "board-group--inbox" in body
    assert "board-group--steps" in body
    assert "board-group--terminal" in body
    assert ">Waiting<" in body
    assert ">Finished<" in body
    # The steps group is labelled with the workflow it belongs to, so a step
    # column is never read as one of the harness's own lifecycle queues.
    assert "default workflow" in body


def test_column_description_is_rendered_under_the_head():
    body = _board_client(_kinded_board()).get("/fragment/board").text

    assert '<p class="column__desc">break the request down</p>' in body
    assert '<p class="column__desc">waiting for the dispatcher</p>' in body


def test_consecutive_step_columns_are_joined_by_a_flow_arrow():
    body = _board_client(_kinded_board()).get("/fragment/board").text

    # Two step columns in the default tab -> exactly one arrow between them
    # (none before the first, and none in the unordered unknown tab).
    assert body.count('class="board-flow"') == 1


def test_unknown_tab_is_labelled_no_workflow_and_explained():
    body = _board_client(_kinded_board(unknown_tasks=(WORKFLOW_LESS,))).get(
        "/fragment/board"
    ).text

    assert UNKNOWN_WORKFLOW_LABEL in body
    assert UNKNOWN_WORKFLOW_NOTE[:40] in body
    # The tab's key is still the internal name, so board.html's tab switching
    # and Board.workflow() keep working.
    assert 'data-workflow="unknown"' in body


def test_unknown_tab_renders_only_occupied_columns():
    body = _board_client(_kinded_board(unknown_tasks=(WORKFLOW_LESS,))).get(
        "/fragment/board"
    ).text

    assert "zzz-idle-step" not in body


def test_index_marks_default_tab_active_via_data_attribute():
    view = FakeBoardView(_two_tab_board(), {"tsk_1": WORKING})
    api = TestClient(create_app(view=view, clock=FakeClock()))

    body = api.get("/").text

    assert 'data-active-workflow="default"' in body
