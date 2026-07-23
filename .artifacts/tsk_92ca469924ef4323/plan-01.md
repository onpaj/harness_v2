# Admin UI: agent catalog and workflow editor

## Summary

The board (`api/`) is read-mostly today: it shows tasks and lets an operator
restart a failed one. There is no way to see or change *what the harness runs*
— agent personas (`agents/<step>.json`) and workflow definitions
(`workflows/<name>.json`) can only be edited by hand on disk. This adds an
admin section to the same FastAPI app: a structured editor for agents (name,
prompt, model, fallback model, allowed tools, allowed outcomes) and a raw
JSON editor for workflows, both served next to the existing board.

## Context

Both catalogs are already plain JSON files read fresh on every lookup
(`FilesystemAgentCatalog.get`, `FilesystemWorkflowRepository.get` —
`src/harness/drivers/fs_agents.py`, `fs_workflows.py`), so there is no cache to
invalidate: a file written by the admin UI is picked up by the next
dispatch/agent run without a harness restart, **except** that the dispatcher's
`step_queues` dict is built once at process start from the *initial* workflow's
`steps()` (`app.py` `build()`, ~line 254). Adding a transition between existing
steps takes effect live; adding a **new step name** does not get a queue until
the process restarts (`route()` would return `MoveTo(new_step)`, but
`Dispatcher.tick` finds no queue for it and fails the task). This is a real
limitation the UI must surface, not paper over.

`api/` today only knows `BoardView`, `ArtifactView`, `StageOutputView`,
`TaskControl` (invariant #5, #11, guarded by `test_architecture.py`). There is
no read/write port for agents or workflows yet — this work adds two.

## Functional requirements

**FR-1 — List agents.**
`GET /admin/agents` (HTML) and `GET /api/agents` (JSON) list every agent
defined under `<root>/agents/*.json` by name.
- Acceptance: with three files in `agents/`, the endpoint returns exactly
  those three names; an empty directory returns an empty list, not an error.

**FR-2 — View/edit one agent as a structured form.**
`GET /admin/agents/{name}` renders a form with fields: name (read-only once
created), prompt (multi-line textarea), model (text, optional), fallback_model
(text, optional), allowed_tools (repeatable text list or comma-separated
field), allowed_outcomes (checkboxes: `done`, `request_changes`).
- Acceptance: opening an existing agent pre-fills every field from its JSON
  file; the two allowed-outcome checkboxes reflect the file's
  `allowed_outcomes` array exactly (including an agent with only `["done"]`).

**FR-3 — Save an agent.**
`POST /admin/agents/{name}` (create-or-update) validates the submitted fields
into an `AgentSpec` the same way `FilesystemAgentCatalog.get` would parse it
(same `Outcome(...)` conversion, same required-`prompt` rule) and writes
`agents/{name}.json`.
- Acceptance: a prompt-less submit is rejected with a 422 and the file on
  disk is left untouched; an invalid outcome value never reaches disk;
  a successful save is immediately visible to `GET /admin/agents/{name}`
  and to a real agent run of that step without restarting the harness.
- Acceptance: the name must pass the same rule `fs_agents._invalid_agent_name`
  already enforces (no `/`, `\`, not `""`/`"."`/`".."`) — reuse that function,
  don't reimplement it.

**FR-4 — Delete an agent.**
`POST /admin/agents/{name}/delete` removes `agents/{name}.json`.
- Acceptance: deleting a name a running workflow step still points at is
  allowed (no cross-check against workflow steps in this iteration — see
  Open questions); the next `AgentCatalog.get(name)` on that step then raises
  `AgentNotFound`, which is already handled by the existing behavior/dispatcher
  failure path.

**FR-5 — List workflows.**
`GET /admin/workflows` lists every `<root>/workflows/*.json` by name.

**FR-6 — Raw JSON editor for a workflow.**
`GET /admin/workflows/{name}` shows the file's exact raw JSON text (not a
re-serialization — preserves key order/formatting the operator left it in) in
a textarea. `POST /admin/workflows/{name}` parses the submitted text through
the *same* validation `FilesystemWorkflowRepository.get` applies (`start`
required, each transition has `from`/`on`/`to`) before writing the file.
- Acceptance: submitting text that isn't valid JSON, or valid JSON missing
  `start`, or a transition missing `to`, is rejected with the parser's error
  message shown inline and the file on disk unchanged.
- Acceptance: a transition referencing a **new** step name is accepted (the
  file is schema-valid) but the response includes a visible warning that new
  steps need a harness restart before they can receive tasks (see Context).

**FR-7 — Create a new agent / workflow.**
`GET /admin/agents/new` and `GET /admin/workflows/new` present the same
form/editor with empty defaults; submitting creates the file if the name
doesn't already exist. A workflow's raw editor for `new` starts from a minimal
valid skeleton (`{"start": "", "transitions": []}`) so the validator has
something to parse.

**FR-8 — Navigation.**
The board page (`board.html`) gains a link to `/admin/agents` and
`/admin/workflows`; the admin pages link back to the board. No new top-level
app — same FastAPI instance, same static assets (htmx), same look.

## Non-functional requirements

- **No new runtime dependency.** Reuse FastAPI + Jinja2 + htmx already in
  `api/`; no client-side framework, no JSON-editor JS library (a plain
  `<textarea>` is enough for FR-6 at this scale).
- **Consistency with existing invariants.** `api/` must not import anything
  from `drivers/` (invariant #5, `test_api_does_not_import_drivers`) —
  the two new admin ports get filesystem-backed drivers wired only in
  `app.py`/`cli.py`, exactly like `BoardView`/`ArtifactView` today.
- **No auth.** The existing board has none; this is a single-operator local
  tool (`harness run` binds to a local port). Out of scope to add auth here —
  flagged in Open questions in case that assumption is wrong for how this is
  deployed.
- **Concurrent-write safety is best-effort, not transactional.** Same
  guarantee level as hand-editing the file today: last write wins. No file
  locking is introduced.
- **Validation errors never partially write.** Parse-then-write, never
  write-then-validate — a rejected submission must leave the existing file
  (or its absence) untouched.

## Data model

No new persisted entities — this exposes read/write access to the two JSON
catalogs that already exist on disk:

- **Agent definition** (`agents/{name}.json`) ↔ `AgentSpec`
  (`ports/agent.py`): `prompt: str`, `model: str | None`,
  `fallback_model: str | None`, `allowed_tools: tuple[str, ...]`,
  `allowed_outcomes: tuple[Outcome, ...]`. `name` is the filename stem, not a
  field inside the JSON (mirrors `FilesystemAgentCatalog.get`, which takes
  `name` as a parameter and stamps it onto the returned `AgentSpec`).
- **Workflow definition** (`workflows/{name}.json`) ↔ `Workflow`
  (`models.py`): `name: str` (defaults to the filename stem if absent from the
  JSON, per `FilesystemWorkflowRepository.get`'s `raw.get("name", name)`),
  `start: str`, `transitions: tuple[Transition(from_step, on, to_step), ...]`.
  The admin UI keeps this one as opaque raw text, not a parsed/re-rendered
  form — round-tripping through `Workflow` and re-serializing would silently
  reformat/reorder whatever the operator typed, which is explicitly not
  wanted for FR-6.

Two new ports, both under `harness/ports/`, following the existing
read-port/write-port split (`BoardView` / `TaskControl`):

- `ports/agent_admin.py` — `AgentAdmin` ABC: `list() -> tuple[str, ...]`,
  `read(name) -> AgentSpec` (raises `AgentNotFound`), `write(name, spec) ->
  None`, `delete(name) -> bool`.
- `ports/workflow_admin.py` — `WorkflowAdmin` ABC: `list() -> tuple[str,
  ...]`, `read_raw(name) -> str` (raises `WorkflowNotFound`), `write_raw(name,
  raw_json: str) -> None` (raises on invalid JSON/schema — same exceptions
  `FilesystemWorkflowRepository.get` raises today), `delete(name) -> bool`.

Both drivers live beside the existing read-only ones (`drivers/fs_agents.py`,
`drivers/fs_workflows.py`) and factor out the parsing logic
(`_parse_agent_spec`, `_parse_workflow`) so the read path (`AgentCatalog.get`
/ `WorkflowRepository.get`) and the new write path's pre-write validation
call the exact same code — no risk of the two drifting apart.

## Interfaces

HTML (Jinja2 templates, matching `board.html`'s style, htmx for the
save/delete round trip without a full page reload):

- `GET /admin/agents` — list, each row links to `/admin/agents/{name}`,
  plus a "new agent" link.
- `GET /admin/agents/{name}` / `GET /admin/agents/new` — form.
- `POST /admin/agents/{name}` — save (create or update); re-renders the form
  with either a success flash or inline field errors.
- `POST /admin/agents/{name}/delete` — delete, redirects to the list.
- `GET /admin/workflows`, `GET /admin/workflows/{name}` / `/new`,
  `POST /admin/workflows/{name}`, `POST /admin/workflows/{name}/delete` —
  the same four shapes over the raw-JSON editor.

JSON (for scripting/tests, mirrors the existing `/api/board`,
`/api/tasks/{id}` style):

- `GET /api/agents`, `GET /api/agents/{name}`, `PUT /api/agents/{name}`,
  `DELETE /api/agents/{name}`.
- `GET /api/workflows`, `GET /api/workflows/{name}` (raw text),
  `PUT /api/workflows/{name}` (raw text body), `DELETE /api/workflows/{name}`.

Both routers are built the same way `build_json_router`/`build_html_router`
are today — factory functions taking the two new ports, wired in
`create_app` (`api/app.py`) with a null/empty default so existing callers of
`create_app` that don't pass agent/workflow admin ports keep working
(same pattern as `_EmptyArtifactView`/`_NullTaskControl`).

## Dependencies and scope

Rests on:
- `HarnessLayout.agents` / `HarnessLayout.workflows` (`app.py`) for the
  directories to read/write.
- The existing parsing rules in `fs_agents.py`/`fs_workflows.py` (reused, not
  duplicated).
- `cli.py`'s `_run`/`build` wiring pattern, to construct the two new drivers
  and pass them into `create_app` alongside the existing ones.

Explicitly out of scope for this iteration:
- Editing `repos.json` (repository registry) — not mentioned in the request.
- A structured (non-raw) workflow editor (drag-and-drop transitions, a graph
  view) — the request specifically asks for a raw JSON editor.
- Restarting the harness process from the UI to pick up a new step's queue —
  flagged as a warning only (FR-6), not automated.
- Authentication/authorization on the admin routes.
- Multi-workflow-per-task-run awareness beyond what already exists (a task
  is pinned to the `workflow_template` it was created with; this UI edits
  definitions, not in-flight task bindings).

## Rough plan

1. Extract `_parse_agent_spec`/`_parse_workflow` helpers out of
   `FilesystemAgentCatalog.get` / `FilesystemWorkflowRepository.get` in
   `drivers/fs_agents.py` / `drivers/fs_workflows.py`, with existing tests
   for those classes still passing unchanged (pure refactor, no behavior
   change).
2. Add `ports/agent_admin.py` (`AgentAdmin`) and `ports/workflow_admin.py`
   (`WorkflowAdmin`), plus `FilesystemAgentAdmin`/`FilesystemWorkflowAdmin`
   drivers implementing them using the extracted helpers for validation.
   Unit tests against a temp directory: list/read/write/delete, plus the
   validation-rejects-and-leaves-file-untouched cases from FR-3/FR-6.
3. Extend `test_architecture.py`'s existing `WORK_PORTS`-style checks (or add
   a parallel pair) confirming `dispatcher.py`/`consumer.py` never import the
   two new ports/drivers, and `api/` never imports the driver module — same
   guardrail shape already in place for `TaskControl`/`BoardView`.
4. Build `build_agent_admin_router`/`build_workflow_admin_router` in `api/`
   (JSON first, since it's the simpler surface and the HTML forms can reuse
   the same validation errors), with Jinja2 templates for list/form/editor
   pages under `api/templates/`, styled consistently with `board.html`.
5. Wire the new ports into `create_app` (`api/app.py`) with empty-default
   fallbacks, and into `cli.py`'s real run path so `harness run` actually
   passes the filesystem-backed drivers.
6. Add the nav links between the board and the two admin sections
   (`board.html` + a shared minimal layout for the admin pages).
7. Extend `tests/test_smoke.py`-style coverage (or a new smoke test) that
   exercises the admin routes against a real filesystem layout end-to-end:
   create an agent, edit it, confirm a subsequent `AgentCatalog.get` sees the
   change; edit a workflow's transitions and confirm `Dispatcher.tick` routes
   accordingly without a restart.
8. Update `CLAUDE.md`'s module map / "what is responsible for what" with the
   two new ports/drivers, following the existing documentation style for
   `TaskControl`/`BoardView`.

## Open questions

- **Should deleting/renaming an agent or workflow that's still referenced by
  a workflow/step warn the operator first?** Defaulting to *no cross-check* —
  the existing failure path (`AgentNotFound` → task fails) already handles a
  dangling reference safely, and adding reference-tracking is extra scope the
  request didn't ask for. Revisit if this proves surprising in practice.
- **Does `harness run` need a way to reload `step_queues` without a full
  restart**, now that workflows are editable at runtime? Left out of scope
  (see Dependencies/scope) — the UI surfaces the restart requirement as a
  warning (FR-6) rather than solving it, since building live queue
  provisioning is a materially bigger change than an admin editor.
- **Should the admin JSON API additionally validate agent
  `allowed_tools`/`model` values against a known list** (e.g. real Claude
  model names)? Defaulting to no — `AgentSpec.model`/`fallback_model` are
  already free-form strings passed through to `AgentRunner`/`ClaudeCliRunner`
  with no such validation today; the admin form shouldn't invent a stricter
  contract than the runtime enforces.
- **Renaming**: an agent/workflow name is the filename stem, and FR-3/FR-6
  treat name as fixed once created. Renaming (copy-to-new-name-then-delete)
  is left out — not mentioned in the request, and easy to do manually via
  create+delete with the primitives this plan already provides.
