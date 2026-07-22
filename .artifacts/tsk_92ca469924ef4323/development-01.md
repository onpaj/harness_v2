# Admin UI: agent catalog and workflow editor — implementation

Implements `plan-01.md`/`design-01.md` as refined by `architecture-01.md`. All
eight functional requirements (FR-1..FR-8) are built: a structured form for
agent CRUD and a raw-JSON editor for workflow CRUD, both served from the
existing FastAPI app alongside the board, with JSON endpoints for scripting.

## What changed

### New ports

- `src/harness/ports/agent_admin.py` — `AgentAdmin` ABC (`list`/`read`/
  `write`/`delete`), `AgentFields` (raw strings in), `AgentValidationError`
  (field → message dict). Mirrors `TaskControl` sitting beside `BoardView`:
  the runtime reads personas via `AgentCatalog`, an operator edits them via
  `AgentAdmin`.
- `src/harness/ports/workflow_admin.py` — `WorkflowAdmin` ABC (`list`/
  `read_raw`/`write_raw`/`delete`), `WorkflowValidationError`. Stays raw text
  end to end — never parses and re-serializes, so an untouched save is
  byte-identical.

### Refactored drivers (pure extraction, existing tests pass unchanged)

- `src/harness/drivers/fs_agents.py` — pulled `_parse_agent_spec(name, raw)`
  out of `FilesystemAgentCatalog.get`'s body; it raises a plain `ValueError`
  for every validation failure (missing prompt, bad `allowed_outcomes`),
  which `.get` wraps into `AgentNotFound` exactly as before. Renamed
  `_invalid_agent_name` → public `invalid_agent_name` (matches
  `fs_workflows.py`'s already-public `invalid_workflow_name`). Added
  `FilesystemAgentAdmin(AgentAdmin)`: `list()` globs `*.json`, `read()` wraps
  `ValueError` into `AgentNotFound`, `write()` wraps it into
  `AgentValidationError` and writes only on success (atomic temp-file +
  `os.replace`, copied verbatim from `FilesystemTaskQueue._write`'s idiom in
  `drivers/fs_queue.py`), `delete()` returns whether a file existed. `write()`
  additionally rejects an empty `prompt` explicitly (`AgentFields.prompt` is
  always *present* as a dict key once built from a form/JSON body, so the
  existing presence-only check in `_parse_agent_spec` doesn't catch a blank
  submission — this is the one behavior addition beyond the pure refactor,
  needed to satisfy FR-3's "prompt-less submit is rejected" acceptance
  criterion).
- `src/harness/drivers/fs_workflows.py` — same shape: extracted
  `_parse_workflow(name, raw)` raising `ValueError` uniformly (including the
  `KeyError`/`TypeError` from a malformed transition, now caught and
  re-raised as `ValueError` so both call sites share one exception contract).
  Added `FilesystemWorkflowAdmin(WorkflowAdmin)` with the same four
  operations, `write_raw` validates via `_parse_workflow` before writing the
  *original submitted text* verbatim (not `json.dumps(parsed)`).

### API surface (`src/harness/api/routes.py`, `api/app.py`)

- `build_json_router`/`build_html_router` grow two new required parameters,
  `agent_admin: AgentAdmin` and `workflow_admin: WorkflowAdmin` — same
  pattern already used for `artifacts`/`output`/`control`.
- New JSON endpoints: `GET/PUT/DELETE /api/agents[/{name}]`,
  `GET/PUT/DELETE /api/workflows[/{name}]`. Agent responses/requests are a
  plain dict matching the on-disk shape; workflow `PUT`'s body *is* the raw
  JSON text (not wrapped in an envelope), `GET` returns `PlainTextResponse`
  of the exact file text. Validation failures: agents 422 with
  `{"errors": {...}}`, workflows 422 with `{"error": "..."}"`; success on a
  workflow write returns `{"warnings": [...]}`.
- New HTML endpoints: `GET /admin/agents[/new|/{name}]`,
  `POST /admin/agents[/{name}]` (collection-level create vs. member-level
  update — the split the design calls for, since an HTML form creating a
  brand-new name has no `{name}` to put in the action URL until submitted),
  `POST /admin/agents/{name}/delete` (redirects 303 to the list). Same five
  shapes for `/admin/workflows`. `GET .../new` is registered **before**
  `GET .../{name}` in both cases, per the architecture review's routing
  gotcha (Starlette matches in registration order; the reverse order would
  have the dynamic route swallow `new` and 404/misroute).
- On a validation failure the HTML handlers respond **200** with the
  form/editor re-rendered, inline errors, and the operator's own submitted
  values intact (no data loss); on success they also respond 200, re-
  rendering the same page with a "saved" banner — no redirect-plus-flash
  complexity needed since the handler already has everything to render.
  Delete is the only action that redirects (303 to the list).
- The create route additionally checks the submitted name isn't already
  taken (via `agent_admin.list()`/`workflow_admin.list()`) *before* calling
  `write`/`write_raw` — closes the "new agent silently overwrites an
  existing one" foot-gun the architecture review flagged, which the design
  document hadn't stated explicitly for the collection-level create path.
- `_new_step_warnings(view, raw_json)` computes the FR-6 restart warning by
  diffing the just-written workflow's referenced steps against
  `view.snapshot().columns` (minus `todo`/`done`/`failed`) — **not** inside
  `WorkflowAdmin`, since a filesystem driver has no idea what the running
  process's `step_queues` contains; `BoardView` is already threaded through
  both router factories and its columns are, by construction, exactly the
  live `step_queues` keys (confirmed in `architecture-01.md`).
- `api/app.py` gained `_EmptyAgentAdmin`/`_EmptyWorkflowAdmin` null objects
  (same shape as `_EmptyArtifactView`/`_NullTaskControl`) so every existing
  `create_app(...)` caller that doesn't pass the two new ports keeps
  working — the admin routes just come back empty/422 instead of crashing.

### Templates (`src/harness/api/templates/`)

- `_nav.html` — a three-link bar (`Board`/`Agents`/`Workflows`), included at
  the top of `board.html` and every new template (`board.html` gained a
  matching `.nav` CSS block).
- `admin/agents_list.html`, `admin/agent_form.html` — list + structured form
  (name, prompt textarea, model, fallback_model, comma-separated
  allowed_tools, two allowed_outcomes checkboxes, inline field errors, a
  native-`confirm()` delete button, a saved banner).
- `admin/workflows_list.html`, `admin/workflow_editor.html` — list + a plain
  `<textarea>` holding the file's exact text (skeleton
  `{"start": "", "transitions": []}` for `new`), a parse-error banner, a
  restart-warning banner per new step, and a saved banner.

### Wiring (`src/harness/cli.py`)

`serve()`'s single `create_app(...)` call now also passes
`agent_admin=FilesystemAgentAdmin(harness.layout.agents)` and
`workflow_admin=FilesystemWorkflowAdmin(harness.layout.workflows)` —
`HarnessLayout` needed no change, both properties already existed.

### Packaging fix found during verification

`pyproject.toml`'s `[tool.setuptools.package-data]` only globbed
`templates/*.html` (non-recursive), so a wheel build silently dropped the
new `templates/admin/*.html` files — the app would 500 on `/admin/*` routes
after a real `pip install`/`uv tool install`, invisible to the test suite
(which serves templates from source, not the installed wheel). Building the
wheel and checking `zipfile.namelist()` confirmed the gap; fixed by adding
`"templates/admin/*.html"` to the glob list and re-verified the fix with
another wheel build. Also added `python-multipart>=0.0.9` to
`[project.dependencies]` — FastAPI's `await request.form()` (used by the new
HTML POST handlers) raises at request time without it; this only surfaces
when a route actually parses a form body, which no existing route did before
this change, so the missing dependency wasn't caught by any prior test.

### CLAUDE.md

Added invariant **#24** (`AgentAdmin`/`WorkflowAdmin` are unknown to the
dispatcher/consumer, UI-facing like `BoardView`/`TaskControl`, wired only in
`cli.py::serve()`), and two new module-map bullets for `ports/agent_admin.py`
/ `ports/workflow_admin.py`, updating the `api/` bullet to name all five
ports it now touches.

## Tests added

- `tests/test_fs_agent_admin.py`, `tests/test_fs_workflow_admin.py` — driver
  unit tests: list/read/write/delete, rejected-submission-leaves-file-
  untouched, invalid-name rejection, overwrite-on-update.
- `tests/test_api_agents.py`, `tests/test_api_workflows.py` — JSON and HTML
  route tests against the real filesystem drivers (via `tmp_path`, no mocks):
  every FR-1..FR-7 acceptance criterion, the create-vs-exists-name check, the
  `/new`-before-`/{name}` route-ordering fix, the restart-warning banner, and
  the delete redirect.
- `tests/test_admin_writes_are_seen_live.py` — a write through `AgentAdmin`/
  `WorkflowAdmin` is immediately visible to the runtime's `AgentCatalog`/
  `WorkflowRepository` (same file, no cache), including workflow routing
  (`Workflow.target(...)`) picking up an edited transition without a
  restart — this is plan step 7's "confirm a subsequent `AgentCatalog.get`/
  `Dispatcher` sees the change" requirement, done as a fast synchronous test
  rather than a real-time smoke (no new step was introduced in this test, so
  no `asyncio.sleep`-based smoke was needed; the restart-warning behavior
  itself is covered by the API tests via `BoardView`).
- `tests/test_cli.py` — updated the pre-existing `FakeHarness` in
  `test_serve_returns_when_uvicorn_stops_before_the_loop` to carry a real
  `HarnessLayout(tmp_path)`, since `serve()` now reads `harness.layout.agents`
  /`.workflows` to build the two new drivers.

Per `architecture-01.md`'s finding, `test_architecture.py` needed **no**
changes — its guards are glob-based (`ports/*.py`, `api/**/*.py`) and
automatically cover the two new port/driver files.

## How to verify

```sh
.venv/bin/pytest -q          # 544 passed, 1 skipped (unrelated opt-in smoke)
```

Manual end-to-end check performed against the real CLI (not just the test
suite): `harness init` → `harness run --agent dummy --forge fake --api-port
...` → exercised `GET/POST /admin/agents`, `GET /admin/agents/new` (route
ordering), `GET/PUT /api/workflows/default` (including a malformed-JSON 422),
and confirmed the nav bar links Board/Agents/Workflows from `/`. Also built
the wheel and inspected its file list to confirm `templates/admin/*.html`
ships (this caught the packaging gap described above).

No new step names were introduced by this change, so the "new step needs a
harness restart" warning path was exercised via the API tests
(`test_put_workflow_warns_about_a_new_step`,
`test_update_workflow_shows_restart_warning`) rather than a live process
restart.
