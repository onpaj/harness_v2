# Design — Process editor should allow selecting target repositories

Builds on `plan-01.md`. That plan already settled the architectural shape (an
additive `Process` layer over the existing `Workflow` JSON file, reusing
`RepositoryRegistry`, mirroring the `TaskControl`/`_NullTaskControl` write-port
pattern). This document makes that shape concrete: exact schemas, exact
interfaces, and the editor's screens and interactions.

## Overview

Three new pieces, all additive — nothing existing is renamed or changed in
behavior:

1. **Schema** — one new optional key, `"repositories"`, in the process JSON
   file already read by `FilesystemWorkflowRepository`.
2. **`Process` layer** (`ports/processes.py`, `drivers/fs_processes.py`) —
   reads/writes/validates that key, sitting next to (not instead of)
   `WorkflowRepository`.
3. **Editor UI** — two new server-rendered pages (`GET /processes`,
   `GET`/`POST /processes/{name}/edit`), the first write-capable HTML surface
   in the app besides the board's "restart" button.

## UX/UI

Server-rendered Jinja2 + HTMX, visually consistent with `board.html`
(same font stack, same card/table conventions). No SSE here — process
definitions are edited by one operator at a time, not a live multi-viewer
board, so plain page loads/redirects are enough; adding SSE would be
unrequested complexity.

### Screen 1 — `GET /processes`

```
┌────────────────────────────────────────────────────────────┐
│ harness · processes                                          │
│                                                                │
│  Process              Repositories                            │
│  ──────────────────────────────────────────────────────────  │
│  conflict-detector     repo-a, repo-b              [Edit]     │
│  disk-threshold        * (all repositories)         [Edit]     │
│  release-notes         repo-c, repo-z ⚠ unknown     [Edit]     │
└────────────────────────────────────────────────────────────┘
```

- One row per `*.json` file under `layout.workflows` (`ProcessAdmin.list_processes`).
- The "⚠ unknown" marker appears when a stored repo name is no longer in the
  registry (computed by the route from `ProcessSummary.repositories` vs.
  `RepositoryRegistry.names()` — see Component design) — an operator can spot
  and fix drift without opening every process.
- Plain link, no JS needed for this page.

### Screen 2 — `GET /processes/{name}/edit`

```
┌────────────────────────────────────────────────────────────┐
│ ← back to processes                                           │
│ Edit process: conflict-detector                               │
│                                                                 │
│ start step: fetch                                              │
│ steps: fetch → review → land → end        (read-only)          │
│                                                                 │
│ Repositories                                                   │
│  (•) All repositories                                          │
│  ( ) Specific repositories:                                    │
│      [ ] repo-a                                                │
│      [x] repo-b                                                │
│      [x] repo-c                                                │
│      [x] repo-z  ⚠ no longer registered                        │
│                                                                 │
│              [ Save ]                                          │
└────────────────────────────────────────────────────────────┘
```

On a failed save (`POST` with an unknown repo, or an empty specific-repos
selection), the **same page re-renders** (HTTP 422) with the operator's
submitted selection preserved and an inline banner above the control:

```
│  ⚠ process 'conflict-detector' names unknown repository 'repo-z';        │
│    known repositories: repo-a, repo-b, repo-c                            │
```

- The steps line is informational only — editing the state machine itself
  (start/transitions) is out of scope (per plan); only `repositories` is
  writable through this form.
- Radio choice `All repositories` / `Specific repositories` — when
  "Specific" isn't selected, the checkbox list is visually disabled. This is
  the one bit of interaction that needs JS: a ~15-line vanilla script
  (`api/static/process_form.js`, sibling to the existing `sse.js`), no new
  framework.
- A repo name present in the stored scope but absent from
  `RepositoryRegistry.names()` (stale/removed repo) still renders as a
  checked, labeled checkbox — visible and toggleable, not silently dropped —
  so the operator can consciously uncheck it rather than have the page erase
  their data.
- Plain `<form method="post">` — works with JS disabled (full page reload);
  no `hx-post`/fragment-swap trickery, since round-tripping the whole page on
  every save is cheap and this form isn't on the hot path of any live view.

### Template hierarchy

```
processes_list.html   (standalone page, own <head>, mirrors board.html's head)
process_edit.html     (standalone page; used for both GET and the 422 re-render)
static/process_form.js
```

No shared fragment include is needed (unlike `_columns.html`/`_task.html`)
because neither page is refreshed piecemeal via SSE.

## Component design

### `models.py` — `Process` (new, alongside `Workflow`)

```python
RepositoryScope = tuple[str, ...] | str   # tuple of names, or the literal "*"

@dataclass(frozen=True)
class Process:
    name: str
    workflow: Workflow
    repositories: RepositoryScope

    def applies_to(self, repository: str) -> bool:
        return self.repositories == "*" or repository in self.repositories
```

`applies_to` takes only a bare `repository: str`, not a `RepositoryRegistry` —
`models.py` imports nothing from the package (module map), so registry-aware
resolution of `"*"` happens one layer up, in the driver. This also gives
`"*"` **live** semantics for free: `applies_to` never snapshots the
registry, so a repo added to `repos.json` after a process was saved as `"*"`
is in scope immediately, with no re-save needed (resolves the plan's open
question in favor of live re-evaluation).

`ProcessSummary` (new, admin/UI-facing read shape — deliberately *not* the
same as `Process`):

```python
@dataclass(frozen=True)
class ProcessSummary:
    name: str
    start: str
    steps: tuple[str, ...]
    repositories: RepositoryScope   # exactly as stored on disk, unvalidated
```

The read path for the *editor* must tolerate a currently-invalid
`repositories` value (see "load is lenient, save is strict" below), so it
cannot be built by calling `compile_process` (which raises on exactly that
condition). `ProcessSummary` is the honest, unvalidated read.

### `ports/processes.py` (new)

```python
class ProcessValidationError(Exception):
    """`repositories` is empty, or names a repo not in the registry."""

class ProcessRepository(ABC):
    @abstractmethod
    def compile_process(self, name: str) -> Process:
        """Load + fully validate. WorkflowNotFound: missing/broken file.
        ProcessValidationError: repositories is empty or names an unknown repo."""

class ProcessAdmin(ABC):
    @abstractmethod
    def list_processes(self) -> list[str]: ...

    @abstractmethod
    def load_process(self, name: str) -> ProcessSummary:
        """Lenient read for the edit form — does not raise ProcessValidationError,
        so a process with a since-invalidated scope can still be opened and fixed.
        WorkflowNotFound if the file is missing/broken."""

    @abstractmethod
    def save_repositories(self, name: str, repositories: RepositoryScope) -> None:
        """Validate (same rule as compile_process), then persist only the
        `repositories` key — every other key in the file is passed through
        unchanged. WorkflowNotFound if the process doesn't exist.
        ProcessValidationError leaves the file untouched."""
```

Design choice: `ProcessAdmin` is scoped to *only* the repository field, not a
general `ProcessDraft` covering `start`/`transitions` too (the plan sketched a
broader `ProcessDraft`; narrowing it here). The task asks for repository
scoping, not a state-machine editor, and a narrower write surface is less to
get wrong on the first write-capable form in the app. `save_repositories`
read-modify-writes the existing JSON, so `start`/`transitions`/any future key
survive untouched.

### `drivers/fs_processes.py` (new)

```python
class FilesystemProcessRepository(ProcessRepository):
    def __init__(self, root: Path, registry: RepositoryRegistry) -> None:
        self._workflows = FilesystemWorkflowRepository(root)  # reused, not reimplemented
        self._root = Path(root)
        self._registry = registry

    def compile_process(self, name: str) -> Process:
        workflow = self._workflows.get(name)          # WorkflowNotFound path reused as-is
        raw = _read_json(self._root / f"{name}.json")  # already proven to exist/parse by the line above
        repositories = _parse_repositories(raw.get("repositories", "*"), name)
        _validate_repositories(repositories, name, self._registry)
        return Process(name=name, workflow=workflow, repositories=repositories)
```

`compile_process` delegates all `start`/`transitions` parsing to
`FilesystemWorkflowRepository.get` — there is exactly one place that logic
lives, satisfying the plan's "no duplicated, divergence-prone logic"
requirement. It re-reads the file a second time to pull `repositories`; for a
small hand-edited JSON file this is a deliberate simplicity-over-micro-
optimization trade-off, not an oversight.

```python
class FilesystemProcessAdmin(ProcessAdmin):
    def __init__(self, root: Path, registry: RepositoryRegistry) -> None:
        self._root = Path(root)
        self._registry = registry

    def list_processes(self) -> list[str]:
        return sorted(p.stem for p in self._root.glob("*.json"))

    def load_process(self, name: str) -> ProcessSummary:
        if invalid_workflow_name(name):
            raise WorkflowNotFound(f"invalid workflow name: {name!r}")
        raw = _read_json(self._root / f"{name}.json")   # WorkflowNotFound on missing/broken
        workflow = FilesystemWorkflowRepository(self._root).get(name)
        repositories = _parse_repositories(raw.get("repositories", "*"), name)
        # NB: no _validate_repositories call here — load is lenient by design.
        return ProcessSummary(
            name=name, start=workflow.start, steps=workflow.steps(),
            repositories=repositories,
        )

    def save_repositories(self, name: str, repositories: RepositoryScope) -> None:
        if invalid_workflow_name(name):
            raise WorkflowNotFound(f"invalid workflow name: {name!r}")
        path = self._root / f"{name}.json"
        raw = _read_json(path)                # WorkflowNotFound if missing/broken
        _validate_repositories(repositories, name, self._registry)  # raises before any write
        raw["repositories"] = list(repositories) if repositories != "*" else "*"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)                     # atomic — no reader ever sees a half-written file
```

**Load is lenient, save is strict** — the one non-obvious design decision in
this layer. If a process was scoped to `repo-z` and `repo-z` is later removed
from `repos.json`, `compile_process` (used at "build"/runtime-consumption
time) correctly refuses to compile it. But `load_process` (used to *open the
editor*) must not refuse — otherwise the only UI meant to fix the drift
becomes the one thing the drift breaks. `save_repositories` re-validates
strictly regardless of what was previously stored, so a save always leaves
the file in a state `compile_process` can load.

`_validate_repositories(repositories, name, registry)`:
- `repositories == "*"` → always valid (no per-name check; validity is
  re-evaluated live against the registry wherever `applies_to`/consumption
  happens).
- `repositories == ()` → `ProcessValidationError(f"process {name!r} has no "
  "repositories selected — choose specific repositories or 'all'")`.
- otherwise, for each name not in `registry.names()`, collect it; if any:
  `ProcessValidationError(f"process {name!r} names unknown repositor{'y' if
  len(unknown)==1 else 'ies'} {', '.join(sorted(unknown))}; known
  repositories: {', '.join(registry.names()) or '(none registered)'}")`.

`invalid_workflow_name` and `FilesystemWorkflowRepository` are imported and
reused as-is (no copy of the path-safety guard).

### `api/process_routes.py` (new)

Mirrors `routes.py`'s `build_html_router` shape — a `build_process_router`
function taking only ports:

```python
def build_process_router(admin: ProcessAdmin, registry: RepositoryRegistry) -> APIRouter:
    router = APIRouter()

    @router.get("/processes", response_class=HTMLResponse)
    def list_processes(request: Request) -> HTMLResponse: ...

    @router.get("/processes/{name}/edit", response_class=HTMLResponse)
    def edit_process(request: Request, name: str) -> HTMLResponse: ...
        # 404 (via WorkflowNotFound) -> HTTPException(404)

    @router.post("/processes/{name}/edit", response_class=HTMLResponse)
    def save_process(
        request: Request, name: str,
        scope: str = Form(...),                       # "all" | "specific"
        repositories: list[str] = Form(default_factory=list),
    ) -> Response:
        selected: RepositoryScope = "*" if scope == "all" else tuple(repositories)
        try:
            admin.save_repositories(name, selected)
        except ProcessValidationError as error:
            return TEMPLATES.TemplateResponse(
                request=request, name="process_edit.html", status_code=422,
                context={...,"error": str(error)},
            )
        except WorkflowNotFound:
            raise HTTPException(status_code=404, detail=f"process {name} does not exist")
        return RedirectResponse(url="/processes", status_code=303)

    return router
```

`RepositoryRegistry` is a **port**, so importing it into `api/` does not
violate invariant #5 (`api/`/`projection.py` must not import `drivers/`) —
only the concrete `FilesystemRepositoryRegistry` is a driver, and that stays
in `app.py`/`cli.py` wiring, same as every other port consumed here.

### Wiring (`api/app.py`, `app.py`, `cli.py`)

`create_app` gains two optional params, following the existing
`control: TaskControl | None` precedent exactly:

```python
def create_app(*, view, artifacts=None, output=None, control=None,
                processes: ProcessAdmin | None = None,
                repos: RepositoryRegistry | None = None,
                clock, coalesce_seconds=0.25) -> FastAPI:
    processes = processes or _NullProcessAdmin()
    repos = repos or _EmptyRepositoryRegistry()
    ...
    app.include_router(build_process_router(processes, repos))
```

`_NullProcessAdmin` (list → `[]`, load/save → raise `WorkflowNotFound`) and
`_EmptyRepositoryRegistry` (`names()` → `[]`, `resolve` → raises) are two
more instances of the same null-object pattern already used four times in
`api/app.py` — keeps `create_app` backward compatible for every caller that
doesn't pass them (e.g. tests building a bare board).

`cli.py`'s `_run` (the only place that already builds a real
`FilesystemRepositoryRegistry`) constructs
`FilesystemProcessAdmin(layout.workflows, registry)` and passes both it and
`registry` into `create_app` alongside the existing `control=harness.control`.
No change to `app.py`'s `build()` — the routing/dispatch path is untouched,
per the plan's explicit scope boundary.

## Data schemas

### Process JSON file (`<layout.workflows>/<name>.json`)

Extends the existing workflow schema with one optional key; every existing
file (no `repositories` key) continues to parse unchanged, defaulting to
`"*"`.

```json
{
  "name": "conflict-detector",
  "start": "fetch",
  "transitions": [
    {"from": "fetch", "on": "done", "to": "review"},
    {"from": "review", "on": "done", "to": "land"},
    {"from": "review", "on": "request_changes", "to": "fetch"},
    {"from": "land", "on": "done", "to": "end"}
  ],
  "repositories": ["repo-a", "repo-b"]
}
```

```json
{ "...": "...", "repositories": "*" }
```

Validation rules (enforced by `_validate_repositories`, applied by both
`compile_process` and `save_repositories`):

| value | result |
|---|---|
| absent | `"*"` (all repositories, unscoped — today's implicit behavior) |
| `"*"` | valid; always re-evaluated live against the current registry |
| `["a", "b"]` (non-empty, all names known to `RepositoryRegistry.names()`) | valid |
| `[]` | `ProcessValidationError` — empty scope is never useful |
| `["a", "z"]` where `"z"` isn't registered | `ProcessValidationError`, message names `"z"` and lists known repos |
| any other JSON type/shape | `ProcessValidationError` |

### `ProcessSummary` (admin read shape, unvalidated `repositories`)

```python
ProcessSummary(name="conflict-detector", start="fetch",
                steps=("fetch", "review", "land"),
                repositories=("repo-a", "repo-b"))
```

### HTTP

`GET /processes` → 200 HTML (`processes_list.html`), plus (for the ⚠
markers) each row computed as `stale = () if p.repositories == "*" else
tuple(r for r in p.repositories if r not in registry.names())`.

`GET /processes/{name}/edit` → 200 HTML (`process_edit.html`) or 404
(`WorkflowNotFound`).

`POST /processes/{name}/edit`
- Request body (`application/x-www-form-urlencoded`):
  - `scope`: `"all" | "specific"`
  - `repositories`: zero or more values (checkbox names), only meaningful
    when `scope=specific`
- Response:
  - success → `303 See Other`, `Location: /processes`
  - validation failure → `422`, HTML, form re-rendered with submitted
    `scope`/`repositories` and an inline `error` string
  - unknown process → `404`

No event is emitted to the board's `EventSink`/SSE stream for a process
edit — processes aren't tasks and the editor isn't a live-updated view, so
there is nothing for `BoardProjection`/`StageOutputProjection` to react to.
This keeps the change fully outside the task-event stream invariants (#7,
#21) — no new event payload shape to design.

## Non-functional notes carried from the plan (unchanged)

No new runtime dependency; no auth on the new endpoints (matches the
existing read-only board's posture — flagged, not resolved, same as the
plan); path-safety on `name` reuses `invalid_workflow_name` on every
filesystem-touching call; architecture tests need no changes since the new
files follow the existing `ports`/`drivers`/wiring-only-in `app.py`/`cli.py`
placement.
