# Admin UI: agent catalog and workflow editor — design

Builds on `.artifacts/tsk_92ca469924ef4323/plan-01.md`. That document fixed the
scope (FR-1..FR-8, two new ports, raw-JSON for workflows, no auth). This
document fixes the concrete shapes: routes, templates, port interfaces, file
schemas, and the two places where this design refines the plan (how
create-vs-update is routed in HTML, and where the "new step needs a restart"
warning is computed).

## UX/UI

Two new sections beside the existing board, same FastAPI app, same htmx/plain
CSS style as `board.html`. A one-line nav bar is added to all pages (including
the board) so the three sections are reachable from each other:

```
 board.html / admin/*.html  (top of <body>)
 ┌───────────────────────────────────────────────┐
 │ harness │ Board │ Agents │ Workflows           │
 └───────────────────────────────────────────────┘
```

### Agents list — `GET /admin/agents`

```
 ┌───────────────────────────────────────────────┐
 │ harness │ Board │ Agents │ Workflows           │
 │                                                 │
 │ Agents                          [+ new agent]  │
 │ ┌─────────────────────────────────────────────┐│
 │ │ planner                                      ││
 │ │ design                                       ││
 │ │ reviewer                                     ││
 │ │ land                                         ││
 │ └─────────────────────────────────────────────┘│
 │ (empty state: "No agents defined yet.")         │
 └───────────────────────────────────────────────┘
```

Each row links to `/admin/agents/{name}`. Rows are plain `<a>` links (no htmx
needed — full navigation is fine at this scale, mirrors how the board's own
detail dialog is the only place htmx fetches partials).

### Agent form — `GET /admin/agents/{name}` and `GET /admin/agents/new`

```
 ┌───────────────────────────────────────────────┐
 │ harness │ Board │ Agents │ Workflows           │
 │                                                 │
 │ ← back to agents                                │
 │                                                 │
 │ Agent: reviewer                 (name fixed)   │
 │ ┌─ new agent instead shows: ───────────────────┐│
 │ │ Name  [___________________]  (editable)      ││
 │ └───────────────────────────────────────────────┘
 │                                                 │
 │ Prompt *                                       │
 │ ┌─────────────────────────────────────────────┐ │
 │ │ You are the reviewer step...                │ │
 │ │ (multi-line textarea, ~10 rows)              │ │
 │ └─────────────────────────────────────────────┘ │
 │ [!] prompt is required          <- inline error │
 │                                                 │
 │ Model            [ claude-opus-4-8__________ ] │
 │ Fallback model   [_______________________]     │
 │ Allowed tools    [ Read, Grep, Edit__________ ] │
 │                  (comma-separated)              │
 │ Allowed outcomes ( x ) done   ( x ) request_changes │
 │                                                 │
 │              [ Save ]     [ Delete ]           │
 │                                                 │
 │ ✓ saved — picked up on the next run, no restart │
 └───────────────────────────────────────────────┘
```

- `Name` is a plain text input only on the `new` form; on an existing agent's
  form it renders as static text (`<h2>Agent: {{ name }}</h2>`), never as an
  editable field — renaming is out of scope (plan, Open questions).
- `Allowed tools` is one comma-separated text field, split/joined by the HTML
  layer only — the port still deals in `tuple[str, ...]`.
- `Allowed outcomes` is two checkboxes, always both rendered; only checked
  ones are sent. At least one of `done`/`request_changes` must reach the
  backend, but the backend does not additionally reject an empty set beyond
  what `Outcome(...)` parsing already requires — matching the plan's "don't
  invent a stricter contract than the runtime enforces" stance. An empty
  `allowed_outcomes` is valid data (mirrors what hand-editing the JSON already
  allows).
- Field errors render inline under the offending field, the rest of the form
  keeps the values the operator typed (no data loss on a rejected submit).
- Delete asks for confirmation via the browser's native `confirm()` (one line
  of vanilla JS on the button's `onclick`, no server round trip for the
  confirmation itself) — this is the only page where an action is destructive.

### Workflows list — `GET /admin/workflows`

Same list shape as agents, rows link to `/admin/workflows/{name}`.

### Workflow raw editor — `GET /admin/workflows/{name}` and `.../new`

```
 ┌───────────────────────────────────────────────┐
 │ harness │ Board │ Agents │ Workflows           │
 │                                                 │
 │ ← back to workflows                             │
 │                                                 │
 │ Workflow: default            (name fixed)      │
 │ ┌─ new workflow instead shows: ────────────────┐│
 │ │ Name  [___________________]  (editable)      ││
 │ └───────────────────────────────────────────────┘
 │                                                 │
 │ ┌─────────────────────────────────────────────┐ │
 │ │ {                                            │ │
 │ │   "start": "plan",                          │ │
 │ │   "transitions": [                          │ │
 │ │     {"from": "plan", "on": "done", "to": "design"} │
 │ │   ]                                          │ │
 │ │ }                                            │ │
 │ │ (plain <textarea>, monospace, ~20 rows,       │ │
 │ │  exact file text — not re-serialized)         │ │
 │ └─────────────────────────────────────────────┘ │
 │                                                 │
 │              [ Save ]     [ Delete ]           │
 │                                                 │
 │ [!] line 4: transition missing "to"  <- parser error, file unchanged │
 │ ⚠ step "review_v2" is new — the running harness │
 │   won't route to it until it is restarted       │
 └───────────────────────────────────────────────┘
```

- The textarea holds exactly the bytes of the file (or the skeleton
  `{"start": "", "transitions": []}` for `new`) — no pretty-printing pass, so
  round-tripping an existing file through "open, don't touch, save" is a
  byte-identical no-op.
- The validation error and the restart warning are two different banners: the
  error means nothing was written; the warning means something *was* written
  but needs an operator action (a restart) to take full effect.

### Navigation (FR-8)

`board.html` gains the same one-line nav (`_nav.html`, included at the top of
`<body>`, three links: Board / Agents / Workflows). No shared page chrome
beyond that partial — each page stays a small standalone Jinja template like
`board.html` is today, consistent with "no new top-level app."

## Component design

### Two new ports

`src/harness/ports/agent_admin.py`:

```python
class AgentAdmin(ABC):
    @abstractmethod
    def list(self) -> tuple[str, ...]: ...

    @abstractmethod
    def read(self, name: str) -> AgentSpec:
        """Raises AgentNotFound (reused from ports/agent.py)."""

    @abstractmethod
    def write(self, name: str, fields: AgentFields) -> AgentSpec:
        """Validates `fields` the same way `FilesystemAgentCatalog.get` parses
        a file, writes `agents/{name}.json` only on success, and returns the
        resulting AgentSpec. Raises AgentValidationError — never partially
        writes."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """True if a file by that name existed and was removed."""


@dataclass(frozen=True)
class AgentFields:
    """The raw, unvalidated shape a form/JSON body submits — a name is
    supplied separately to `write`, never taken from here, so a submitted
    body can never smuggle in a different name than the URL path names."""

    prompt: str
    model: str | None = None
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[str, ...] = ()  # raw strings; AgentAdmin does the Outcome(...) parse


class AgentValidationError(Exception):
    """Field name -> human-readable message. Raised by `write`, never leaves
    a partially written file behind."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
```

`src/harness/ports/workflow_admin.py`:

```python
class WorkflowAdmin(ABC):
    @abstractmethod
    def list(self) -> tuple[str, ...]: ...

    @abstractmethod
    def read_raw(self, name: str) -> str:
        """Raises WorkflowNotFound (reused from ports/workflows.py). Returns
        the file's exact text."""

    @abstractmethod
    def write_raw(self, name: str, raw_json: str) -> None:
        """Parses raw_json through the same rules `FilesystemWorkflowRepository
        .get` applies (valid JSON, `start` present, every transition has
        from/on/to) and writes the file verbatim (not re-serialized) only if
        it parses. Raises WorkflowValidationError — never partially writes."""

    @abstractmethod
    def delete(self, name: str) -> bool: ...


class WorkflowValidationError(Exception):
    """A single message describing the first parse/schema failure — mirrors
    the exception `FilesystemWorkflowRepository.get` already raises today, so
    the same text an operator would see in the harness's own logs is shown in
    the editor."""
```

Both ports live under `ports/`, import only `models`/`ports.agent`/
`ports.workflows` — never a driver (guarded by the existing
`test_ports_do_not_import_drivers`).

**Design refinement over the plan:** the plan sketched `write(name, spec:
AgentSpec)`. This design instead threads an `AgentFields` (raw strings, not a
constructed `AgentSpec`) through `write`, because the caller — the HTML form
handler — has raw form strings (`allowed_outcomes` as `["done"]` from checked
boxes, `allowed_tools` as one comma-separated string split into a tuple by the
route, not the port) and constructing an `AgentSpec` before validation would
mean either the router duplicates the `Outcome(...)` parsing (defeating the
point of sharing validation) or `AgentSpec` grows a "maybe invalid" variant.
Keeping the boundary as one plain-strings dataclass in and one exception or
`AgentSpec` out is the shape both the JSON and HTML routers can hand-build
directly from their respective inputs. Workflow stays as sketched
(`write_raw(name, raw_json: str)`) since there raw text is already the entire
payload.

### Shared parsing, refactored out of the read-only drivers

`drivers/fs_agents.py` gains a module-level function extracted out of
`FilesystemAgentCatalog.get`'s body:

```python
def _parse_agent_spec(name: str, raw: dict) -> AgentSpec:
    """Raises ValueError on a missing prompt or invalid allowed_outcomes —
    the same checks `FilesystemAgentCatalog.get` makes today."""
```

`FilesystemAgentCatalog.get` becomes: load JSON (catching not-found/bad-JSON
as `AgentNotFound`, as today) → call `_parse_agent_spec` → wrap `ValueError` as
`AgentNotFound`. Existing tests for `FilesystemAgentCatalog` pass unchanged
(pure refactor, per the plan's rough-plan step 1).

`drivers/fs_workflows.py` gains the equivalent `_parse_workflow(name, raw:
dict) -> Workflow`, used the same way by `FilesystemWorkflowRepository.get`.

### Two new drivers, beside the existing ones

`drivers/fs_agents.py` additionally defines `FilesystemAgentAdmin(AgentAdmin)`:

- `list()` — `sorted(p.stem for p in root.glob("*.json"))`.
- `read(name)` — same not-found/bad-JSON handling as `.get`, but raises
  `AgentNotFound` (reusing that existing exception — the admin UI's 404 case
  is identical to the runtime's "no such agent" case).
- `write(name, fields)` — validates `name` via the (now public, see below)
  name-validity check, builds the `raw` dict from `fields`, runs it through
  `_parse_agent_spec` to catch a bad `allowed_outcomes` value before touching
  disk, and only then writes `agents/{name}.json` (write to a temp file in the
  same directory then `os.replace`, so a crash mid-write can't leave a
  half-written JSON file — the read path already has to tolerate bad JSON, but
  there is no reason to manufacture that case here).
- `delete(name)` — `Path.unlink(missing_ok=True)`-style, returns whether the
  file existed.

`_invalid_agent_name` is renamed to the public `invalid_agent_name` (matching
`fs_workflows.py`'s already-public `invalid_workflow_name`) so the admin
driver and any future caller can import it without reaching for a
single-underscore name across modules — a naming fix, not a behavior change.

`drivers/fs_workflows.py` additionally defines
`FilesystemWorkflowAdmin(WorkflowAdmin)` — same four operations, `write_raw`
runs the submitted text through `json.loads` then `_parse_workflow` (catching
`JSONDecodeError`/`KeyError`/`TypeError` into `WorkflowValidationError` with
the underlying message) before writing the *original text* (not
`json.dumps(parsed)` — preserving operator formatting is the point of FR-6).

### Routing: reusing `build_json_router`/`build_html_router`

Rather than four new router-factory functions, the two existing factories in
`api/routes.py` grow two new optional parameters each, following the exact
pattern `create_app` already uses for `artifacts`/`output`/`control`
(null-object default, so the routes always exist and are always mounted,
just inert without a real driver):

```python
def build_json_router(
    view: BoardView,
    artifacts: ArtifactView,
    agent_admin: AgentAdmin,
    workflow_admin: WorkflowAdmin,
) -> APIRouter: ...

def build_html_router(
    view: BoardView,
    artifacts: ArtifactView,
    output: StageOutputView,
    control: TaskControl,
    clock: Clock,
    coalesce_seconds: float,
    agent_admin: AgentAdmin,
    workflow_admin: WorkflowAdmin,
) -> APIRouter: ...
```

`api/app.py` adds `_EmptyAgentAdmin`/`_EmptyWorkflowAdmin` next to
`_EmptyArtifactView` et al. (`list()` → `()`, `read`/`read_raw` → raise
not-found, `write`/`write_raw` → raise a validation error saying "no agent
admin configured", `delete` → `False`), and `create_app` grows matching
optional `agent_admin`/`workflow_admin` parameters defaulting to these empty
drivers — so every existing caller of `create_app` keeps working, and the
`/admin/*` routes 404/empty gracefully rather than crashing when nobody wired
a real backend (e.g. a test that only cares about the board).

New JSON endpoints, added inside `build_json_router`:

- `GET /api/agents` → `{"agents": [name, ...]}`
- `GET /api/agents/{name}` → the spec as a dict (404 → `AgentNotFound`)
- `PUT /api/agents/{name}` → body is the spec fields (see Data schemas); 200
  with the saved spec, 422 with `{"errors": {...}}` on
  `AgentValidationError`, file untouched
- `DELETE /api/agents/{name}` → 204 if deleted, 404 if it didn't exist
- `GET /api/workflows` → `{"workflows": [name, ...]}`
- `GET /api/workflows/{name}` → `PlainTextResponse` of the raw file text
  (same shape as the existing `task_artifact` endpoint, which already returns
  raw text for a similar reason: don't reformat what's on disk)
- `PUT /api/workflows/{name}` → the request **body is the raw JSON text
  itself** (`Content-Type: application/json`, read via `await
  request.body()`, not wrapped in an envelope object) — because the payload
  *is* "whatever the operator typed," including a body that fails to parse.
  Wrapping it (`{"raw": "..."}`) would force the client to pre-escape JSON
  inside JSON for no benefit. 200 with `{"warnings": [...]}` (empty list when
  none) on success, 422 with `{"error": "<message>"}` on
  `WorkflowValidationError`, file untouched.
- `DELETE /api/workflows/{name}` → 204/404.

New HTML endpoints, added inside `build_html_router`:

- `GET /admin/agents`, `GET /admin/agents/new`, `GET /admin/agents/{name}`
- `POST /admin/agents` (create — name comes from the form body's `name`
  field; only reachable from the `new` page)
- `POST /admin/agents/{name}` (update — name comes from the path, the form
  has no name field to tamper with)
- `POST /admin/agents/{name}/delete` → redirect (303) to `/admin/agents`
- The same five shapes for `/admin/workflows`.

**Design refinement over the plan:** the plan listed a single `POST
/admin/agents/{name}` for "create-or-update." That works cleanly for JSON
(`PUT` is naturally idempotent on a path that already names the resource) but
not for an HTML form creating a *brand new* name — there is no `{name}` to
put in the action URL until the operator has typed one and the browser has
submitted the form. Splitting into a collection-level `POST /admin/agents`
(create, name from the body) and a member-level `POST /admin/agents/{name}`
(update, name from the path, immutable in the form) resolves that without
JavaScript. Both ultimately call the same `AgentAdmin.write` — the split is
only about where the name comes from, not about two different write paths.

On a validation failure, the HTML handler responds **200** with the form
re-rendered and inline errors (a standard server-rendered-form pattern —
htmx's default only auto-swaps 2xx/3xx responses, and re-showing the form
with the operator's own input intact is exactly what should happen on 200).
The JSON API instead responds **422**, per FR-3's acceptance criterion — the
two surfaces intentionally differ here because an HTML form redisplay and a
script's error-handling branch are different consumers with different
idiomatic status expectations.

### Where the "new step needs a restart" warning is computed

Not inside `WorkflowAdmin` — a filesystem driver that only knows a directory
of JSON files has no idea which steps the *running* harness currently has
queues for. That knowledge already exists one hop away: `BoardView.snapshot()`
returns `Board.columns`, whose names are exactly the live queues (`todo`,
every step, `done`, `failed` — invariant #22). So the HTML/JSON workflow-save
handlers, which already receive `view: BoardView` as a parameter (it's
threaded through `build_html_router`/`build_json_router` already), compute:

```python
known_steps = {c.name for c in view.snapshot().columns} - {"todo", "done", "failed"}
new_steps = {t.to_step for t in parsed.transitions} | {parsed.start} 
new_steps -= known_steps
```

and include `new_steps` (sorted) as the warning list in the response, after
`WorkflowAdmin.write_raw` has already succeeded. This keeps `WorkflowAdmin`
itself a pure filesystem port (no coupling to a running dispatcher's
in-memory state) while still surfacing the warning FR-6 asks for, and it costs
no new port — `view` is already in scope wherever the workflow write handler
lives.

### Wiring (`app.py` / `cli.py`)

`cli.py`'s `serve()` (the only place `create_app` is called for a real run,
`~cli.py:708`) constructs the two new filesystem drivers from
`harness.layout.agents` / `harness.layout.workflows` — the same `Path`
properties `FilesystemAgentCatalog`/`FilesystemWorkflowRepository` are already
built from in `app.py::build()` — and passes them in:

```python
app = create_app(
    view=harness.projection,
    artifacts=harness.artifacts,
    output=harness.stage_output,
    control=harness.control,
    clock=SystemClock(),
    agent_admin=FilesystemAgentAdmin(harness.layout.agents),
    workflow_admin=FilesystemWorkflowAdmin(harness.layout.workflows),
)
```

`HarnessLayout` itself needs no change — `.agents` and `.workflows` already
exist (`app.py:53`, `app.py:77`). No new driver wiring touches
`dispatcher.py`/`consumer.py`; `test_only_app_and_cli_wire_drivers` and
`test_api_does_not_import_drivers` cover this without modification, since the
new drivers live in existing driver modules (`fs_agents.py`, `fs_workflows.py`)
that are already exempt-by-being-drivers, and `api/` only ever imports the two
new ports.

### Templates

New files under `api/templates/`:

- `_nav.html` — the three-link bar, included by `board.html` and every new
  template.
- `admin/agents_list.html`, `admin/agent_form.html`
- `admin/workflows_list.html`, `admin/workflow_editor.html`

Reuse the inline `<style>` block's classes from `board.html` where they
already fit (`.card`, `table`/`td`/`th`); add a handful of form-specific
classes (`.field`, `.field.error`, `.banner.warning`, `.banner.error`) in the
same inline-`<style>` convention rather than introducing a separate CSS file.

## Data schemas

### Agent file (`agents/{name}.json`) — unchanged on-disk shape

```json
{
  "prompt": "You are the reviewer step...",
  "model": null,
  "fallback_model": null,
  "allowed_tools": ["Read", "Grep"],
  "allowed_outcomes": ["done", "request_changes"]
}
```

`name` is the filename stem, never a key inside the file (matches
`FilesystemAgentCatalog.get`'s existing contract).

### `PUT /api/agents/{name}` request body

```json
{
  "prompt": "You are the reviewer step...",
  "model": "claude-opus-4-8",
  "fallback_model": null,
  "allowed_tools": ["Read", "Grep"],
  "allowed_outcomes": ["done", "request_changes"]
}
```

Same shape as the file; the server writes it as `agents/{name}.json` after
validation. Missing optional keys default the same way `_parse_agent_spec`
already defaults them (`model`/`fallback_model` → `null`, `allowed_tools` →
`[]`, `allowed_outcomes` → `["done"]`).

### `PUT /api/agents/{name}` responses

Success (200):

```json
{
  "name": "reviewer",
  "prompt": "...",
  "model": "claude-opus-4-8",
  "fallback_model": null,
  "allowed_tools": ["Read", "Grep"],
  "allowed_outcomes": ["done", "request_changes"]
}
```

Validation failure (422), file untouched:

```json
{ "errors": { "prompt": "prompt is required" } }
```

```json
{ "errors": { "allowed_outcomes": "invalid outcome: 'maybe'" } }
```

### HTML agent form submission (`POST /admin/agents` / `/admin/agents/{name}`)

Standard `application/x-www-form-urlencoded` fields:

| field | example | notes |
|---|---|---|
| `name` | `reviewer` | only present on the create form |
| `prompt` | `You are...` | required |
| `model` | `claude-opus-4-8` | optional, empty string → `null` |
| `fallback_model` | (empty) | optional, empty string → `null` |
| `allowed_tools` | `Read, Grep, Edit` | comma-separated, split/stripped server-side |
| `allowed_outcomes` | `done` (repeated key per checked box) | zero, one or two values |

### Workflow file (`workflows/{name}.json`) — unchanged on-disk shape

```json
{
  "start": "plan",
  "transitions": [
    { "from": "plan", "on": "done", "to": "design" },
    { "from": "design", "on": "done", "to": "review" },
    { "from": "review", "on": "request_changes", "to": "design" },
    { "from": "review", "on": "done", "to": "land" },
    { "from": "land", "on": "done", "to": "end" }
  ]
}
```

`name` inside the JSON is optional (defaults to the filename stem per
`raw.get("name", name)`); the admin editor never adds it if it wasn't already
there, since it edits raw text.

### `PUT /api/workflows/{name}` — request

Raw bytes of the body **are** the JSON text (not `{"raw": "..."}"`):

```
PUT /api/workflows/default
Content-Type: application/json

{"start": "plan", "transitions": [{"from": "plan", "on": "done", "to": "design"}]}
```

### `PUT /api/workflows/{name}` — responses

Success, no new steps (200):

```json
{ "warnings": [] }
```

Success, with a new step (200) — the file is written; this is a warning, not
a rejection:

```json
{ "warnings": ["step 'review_v2' is new — restart the harness before it can receive tasks"] }
```

Rejected (422), file untouched:

```json
{ "error": "workflow 'default' has an invalid transition: 'to'" }
```

```json
{ "error": "Expecting value: line 3 column 5 (char 42)" }
```

(the second is `json.JSONDecodeError`'s own message, surfaced verbatim — same
principle as the agent form: show the operator the same text the harness
itself would log.)

### Skeleton for a brand-new workflow (`GET /admin/workflows/new` textarea default)

```json
{
  "start": "",
  "transitions": []
}
```

Matches FR-7 — schema-valid enough for the validator to run over it and
report something more specific than "not an object" on first save attempt.

## Dependencies and scope

Unchanged from the plan. This design adds no new runtime dependency (still
FastAPI + Jinja2 + htmx + stdlib), touches only: two new port files, two
driver files (extended, not replaced), `api/routes.py`, `api/app.py`,
`api/templates/*`, `cli.py`'s `serve()`, and `CLAUDE.md`'s module map. It does
not touch `dispatcher.py`, `consumer.py`, `router.py`, `models.py`, or any
`WORK_PORTS`/`WORK_DRIVERS` list in `test_architecture.py` (the two new ports
are UI-facing admin ports, not orchestration work ports — they don't belong
in that list, mirroring `TaskControl`/`BoardView`, which also aren't in it).
