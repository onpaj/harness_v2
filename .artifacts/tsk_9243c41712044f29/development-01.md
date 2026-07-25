# Development — manual "Add issue" button on the Ahanas board

Implements `plan-01.md`/`design-01.md`, with the wiring corrected exactly as
`architecture-01.md` specified: `GithubIssueImportService` is built *inside*
`app.build()` from an `IssueImportFactory` `cli.py` supplies, not read back
out of a live `Harness` in `cli.py` — the same shape `FailedTasksCheck`
already uses (ADR-0018).

## What was implemented

**`GithubClient.get_issue(repo, number)`** (`src/harness/drivers/github_client.py`)
A point lookup by issue number, independent of label — the one new capability
the whole feature needed. Same 404→`None` idiom as `get_issue_state`; every
other error propagates. Implemented on both `FakeGithubClient` and
`HttpGithubClient`.

**`ports/issue_import.py`** (new)
- `IssueImportResult` — `ref`/`ok`/`task_id`/`already_queued`/`error`.
- `IssueImport(ABC)` — `add(ref) -> IssueImportResult`, never raises.
- `NullIssueImport` — the "GitHub is not configured" fallback, shared by
  `build()`'s own default and `create_app`'s default parameter (one class,
  not two).
- `IssueImportFactory` — the `Callable` type `cli.py` hands to `build()`,
  mirroring `CheckFactory`/`extra_checks`/`finishers`.

**`drivers/github_issue_import.py`** (new) — `GithubIssueImportService`
`add(ref)`: parses `owner/repo#number` or a full issue URL → resolves the
repo through `RepositoryRegistry`/`github_slug` (same pattern as
`GithubIssuesCheck`) → `GithubClient.get_issue` (no label required) → checks
`dedup_key` against every task across `inbox`/step queues/`done`/`failed`/
`healed`/`archived` (a deliberate superset of the pollers'/reconcilers' usual
four, so a pasted ref for an already-*resolved* issue still reads
"already queued") → builds a `Task` byte-for-byte shaped like
`GithubTaskSource.poll()`'s (same `data.source`, same `dedup_key`) →
best-effort claims the issue with `harness:queued` (swallowed on failure,
never fatal) → `inbox.put` + emits the same `"ingested"` event
`SourcePoller.tick()` emits, so the board's SSE path and every downstream
consumer (dispatcher, label reflection, the reconcilers) need zero changes.
Every failure path (malformed ref, unregistered repo, issue not found,
client exception) returns `ok=False` with a clear `error` — `add()` never
raises.

**`app.py`** — `build()` gains `issue_import_factory: IssueImportFactory | None`.
Constructed right after the queue block (`inbox`/`step_queues`/`done`/
`failed`/`healed_queue`/`archived`) and after `events` is wrapped in
`CompositeEventSink` — the same spot `FailedTasksCheck`'s factory closes over
those queues — falling back to `NullIssueImport()` with no factory. `Harness`
gains a public `self.issue_import` attribute (always concrete, mirrors
`self.control`).

**`cli.py`** — `_issue_import_factory(args, root, registry, *, client=None)`:
returns `None` without a client (no `GITHUB_TOKEN`), otherwise a closure
mirroring `_process_check_factories`' shape, reusing `--github-workflow`/
`--github-step` (defaulting to `DEFAULT_WORKFLOW`, exactly `_github_sources`'
own defaulting) and the same `worktree_root` computation. `_run` builds it
(closing over the already-constructed `github_client`/`registry` — no second
client) and passes it into `build(...)`. `serve()` threads
`harness.issue_import` into `create_app`.

**`api/app.py` / `api/routes.py`** — `create_app`/`build_html_router` gain an
`issue_import: IssueImport | None` parameter (`NullIssueImport()` default).
New route `POST /issues/import`: splits the submitted `refs` textarea on
`[,\s]+`, calls `issue_import.add(ref)` once per ref **sequentially** (so a
duplicate ref within one batch sees the first ref's task already queued),
renders `_issue_import_result.html`. An empty/whitespace submission renders
"no refs given" without calling `add()`. `api/` imports only the
`IssueImport` port — no driver import.

**`board.html`** — a `+ Add issue` button in the page header
(`.btn.small.primary.page-header__action`, the same class already used by
"+ New agent"/etc.) and a new `<dialog id="add-issue">` — a textarea posting
via htmx to `/issues/import`, swapping only the results panel, reusing the
existing `#detail`-style open/close script idiom (`showModal()`/`close()`,
backdrop click). New template `_issue_import_result.html` renders one line
per ref: `✓ … queued as <id>` / `✓ … already queued as <id>` / `✗ … <error>`.

**`app.css`** — a `dialog#add-issue` rule mirroring `dialog#detail`'s
full-screen-on-phone / centered-on-desktop sheet (kept as a separate rule, not
a shared selector, so the existing `dialog#detail {` CSS test assertion still
matches literally), plus `.issue-import-results` styling (`--text` for a
success line, `--failed-fg` for an error line — no new color tokens).

**`CLAUDE.md`** — added invariant #43 (the "live queue + external dependency
→ built inside `build()` from a `cli.py`-supplied factory" shape),
documented `ports/issue_import.py`/`drivers/github_issue_import.py` in the
module map and the "what is responsible for what" prose, and added
`IssueImport` to the `api/` bullet's port list.

**`tests/test_architecture.py`** — new
`test_orchestration_does_not_import_issue_import_port`, guarding that
`dispatcher.py`/`consumer.py` never import `ports.issue_import` (mirrors
invariants #23/#32/#34's existing guards).

## Files created

- `src/harness/ports/issue_import.py`
- `src/harness/drivers/github_issue_import.py`
- `src/harness/api/templates/_issue_import_result.html`
- `tests/test_github_issue_import.py`
- `tests/test_api_issue_import.py`

## Files changed

- `src/harness/drivers/github_client.py` — `get_issue` (abstract + Fake + Http)
- `src/harness/app.py` — `issue_import_factory` param, `Harness.issue_import`
- `src/harness/cli.py` — `_issue_import_factory`, wiring into `_run`/`serve`
- `src/harness/api/app.py` — `create_app(issue_import=...)`
- `src/harness/api/routes.py` — `POST /issues/import`, `_split_refs`
- `src/harness/api/templates/board.html` — button + dialog + open/close JS
- `src/harness/api/static/app.css` — dialog + results styling
- `CLAUDE.md` — invariant #43, module map, prose
- `tests/fakes.py` — `FakeIssueImport`
- `tests/test_architecture.py`, `tests/test_app.py`, `tests/test_cli.py`,
  `tests/test_github_client.py` — new/updated coverage

## Acceptance criteria coverage

- Visible, theme-consistent "Add issue" button — `board.html` (reuses
  existing `.btn`/`.page-header__action` classes; `--text`/`--failed-fg`
  results styling, no new colors).
- Dialog accepting comma/space/newline-separated refs —
  `_split_refs`/textarea.
- Fetches even label-less issues — `GithubClient.get_issue` bypasses the
  label scan entirely.
- `data.source` stamped identically to auto-ingestion — verified by
  `test_add_creates_a_task_with_no_label_required` and the
  `test_build_issue_import_events_reach_the_projection` app-level test.
- Lands in the inbox, routed by the dispatcher — plain `inbox.put`, no queue
  placement (invariant #35 unaffected: the manual path produces a task like
  any other producer).
- Goes through a write-side port, not a driver reach-in from `api/` — new
  `IssueImport` port; guarded by the existing `test_api_does_not_import_drivers`
  and the new `test_orchestration_does_not_import_issue_import_port`.
- Invalid/non-existent issue reported per-ref, batch continues —
  `test_import_mixed_batch_shows_each_outcome`.
- Idempotent duplicates — `test_add_already_queued_task_reports_success_without_duplicating`
  plus cross-queue variants (step queue, healed, archived) and the
  same-batch race case.

## How to verify

```sh
.venv/bin/pytest -q
```

All 1391 tests pass (1 pre-existing skip unrelated to this change). Notable
new/updated suites:

```sh
.venv/bin/pytest -q tests/test_github_client.py tests/test_github_issue_import.py \
  tests/test_app.py tests/test_cli.py tests/test_api_issue_import.py \
  tests/test_architecture.py
```

Manually: `harness run --agent claude` with a `GITHUB_TOKEN` set and at least
one registered repo, open the board, click "+ Add issue", paste an
`owner/repo#number` for an issue with no label — it appears in `todo` without
a page reload (the existing SSE path), and re-submitting the same ref reports
"already queued".
