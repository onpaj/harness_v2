# Plan — Process editor should allow selecting target repositories

## Summary

Operators need a way to scope a process (the harness's state-machine
definition, today called `Workflow` in code) to a subset of registered
repositories, or explicitly to all of them. This adds a `repositories` field to
the process definition, validates it against the repository registry at
build/compile time, and gives operators a form-based editor to set it — the
first write-capable admin surface in an app whose UI has so far been read-only.

## Context

**Important finding from codebase investigation:** the task's acceptance
criteria name classes that do not exist yet anywhere in this repository —
`FilesystemProcessAdmin`, `FilesystemProcessRepository`, `ProcessValidationError`,
a "process editor" UI, and "ADR-0015". There is no `docs/**/adr/` directory and
no ADR is numbered anywhere in the repo. What exists today, and is the closest
analogue, is:

- `Workflow` / `WorkflowRepository` / `FilesystemWorkflowRepository`
  (`src/harness/models.py`, `ports/workflows.py`, `drivers/fs_workflows.py`) —
  an immutable state-machine template loaded from `<layout.workflows>/<name>.json`.
  It is read-only at runtime: today's only way to create or edit one is to hand-write
  the JSON or run `harness init`, which templates a single fixed definition.
- `RepositoryRegistry` (`ports/repos.py`, `drivers/fs_repos.py`) — already
  exposes `.names()` (all registered repo names, empty list if the registry is
  missing/broken) and `.resolve(name)`. This is exactly what a repository
  multiselect and build-time validation need, and it already exists — no new
  registry work required.
- The API (`src/harness/api/`) is a read-only FastAPI + Jinja2 + HTMX board
  (SSE for live updates). It has no forms, no POST-to-mutate-state endpoint,
  and no client-side JS framework. The one write-capable port that exists
  today, `TaskControl` (`ports/control.py`), is a useful precedent: a small
  ABC mirroring a read port (`BoardView`), injected into `create_app`, with a
  no-op `_NullTaskControl` default for backward compatibility.
- `HarnessLayout.workflows` already points at the directory where process
  definitions live on disk; `HarnessLayout.repos` points at `repos.json`, the
  registry backing store.

Given this, the plan below treats "Process" as the admin/editor-facing name
for what the code currently calls `Workflow`, **without renaming the existing
`Workflow` concept** (that name is deeply load-bearing: `Task.workflow_template`,
CLI `--workflow` flags, `ports/workflows.py`, and several invariants in
`CLAUDE.md` reference it explicitly). Instead:

- `repositories` is added as one more key in the *same* `<name>.json` file
  `FilesystemWorkflowRepository` already reads. The running harness's routing
  path (`Workflow`/`WorkflowRepository`) is untouched and simply ignores the
  new key — zero behavior change for existing deployments.
- A new, additive `Process` layer (models/ports/drivers) reads and writes that
  same file, adding parsing and validation of `repositories`, and is the thing
  the new editor UI talks to.

This keeps the change architecturally minimal and avoids a project-wide rename,
while satisfying every acceptance criterion as literally named in the request.

## Functional requirements

**FR-1 — `repositories` field in the process JSON schema**
The per-process JSON file gains an optional key:
```json
{ "repositories": ["repo-a", "repo-b"] }
```
or
```json
{ "repositories": "*" }
```
- Acceptance: `["repo-a", "repo-b"]` is accepted (a list of specific names, order
  preserved); `"*"` is accepted (means "all currently-registered repositories").
  If the key is absent, the process behaves exactly as it does today (treated
  as `"*"`, i.e. unscoped/global) — preserves the current implicit behavior for
  every existing process file with no migration needed.
- Acceptance: an empty list `[]` is a **validation error** ("no repositories
  selected — choose specific repos or 'all'"), not silently treated as "none" or
  "all" — an empty scope is never useful and is almost certainly an operator
  mistake.

**FR-2 — `Process` model**
A new frozen dataclass in `models.py`, alongside `Workflow`:
```python
RepositoryScope = tuple[str, ...] | str  # tuple of names, or the literal "*"

@dataclass(frozen=True)
class Process:
    name: str
    workflow: Workflow
    repositories: RepositoryScope

    def applies_to(self, repository: str) -> bool:
        return self.repositories == "*" or repository in self.repositories
```
- Acceptance: `applies_to` returns `True` for any repo when scope is `"*"`,
  and only for named repos otherwise.

**FR-3 — `FilesystemProcessRepository.compile_process` (read + validate)**
New `ports/processes.py` (`ProcessRepository` ABC, `ProcessValidationError`)
and `drivers/fs_processes.py` (`FilesystemProcessRepository`), reading from
the same directory as `FilesystemWorkflowRepository` (`layout.workflows`).
```python
class ProcessRepository(ABC):
    def compile_process(self, name: str) -> Process: ...
```
- Delegates the state-machine part (`start`/`transitions`) to the existing
  `FilesystemWorkflowRepository` parsing logic (either by composition or by
  sharing the helper) so there is exactly one place that parses `start`/
  `transitions` — no duplicated, divergence-prone logic.
- Additionally parses `repositories` (defaulting to `"*"` when absent) and
  validates every named repo against `RepositoryRegistry.names()`.
- Acceptance: an unknown repo name raises `ProcessValidationError` whose
  message names the bad repo *and* lists the currently-registered repos, e.g.
  `"process 'conflict-detector' names unknown repository 'repo-z'; known
  repositories: repo-a, repo-b"` (satisfies "helpful feedback" from the
  acceptance criteria).
- Acceptance: a missing/malformed workflow file still raises the existing
  `WorkflowNotFound`-equivalent error path (reuse, don't replace, the existing
  validation in `FilesystemWorkflowRepository`).

**FR-4 — `FilesystemProcessAdmin` (write side)**
New `ProcessAdmin` ABC in `ports/processes.py` (mirrors the existing
`TaskControl` write-port pattern) and `FilesystemProcessAdmin` in
`drivers/fs_processes.py`:
```python
class ProcessAdmin(ABC):
    def list_processes(self) -> list[str]: ...
    def load_process(self, name: str) -> ProcessDraft: ...   # for pre-filling the form
    def save_process(self, name: str, draft: ProcessDraft) -> None: ...  # validates, then writes
```
- `save_process` runs the same repository validation as `compile_process`
  *before* writing to disk — an operator can never save a process that
  references a non-existent repo. Raises `ProcessValidationError` on failure;
  the file on disk is left untouched.
- `ProcessDraft` is a small, permissive shape (raw strings/lists) used only to
  move data between the HTML form and the admin — it does not need to be a
  valid `Workflow`/`Process` yet (so the edit form can re-render a draft that
  failed validation, with the offending value still visible).
- Acceptance: saving a process with specific repos, then compiling it, returns
  those exact repos; saving with "all repositories" selected persists `"*"` and
  compiling returns a scope that matches every registered name.

**FR-5 — Process editor UI**
The first write-capable form in the API. New routes (new module,
e.g. `api/process_routes.py`, wired the same way `routes.py` is — sees only
`ProcessAdmin`/`ProcessRepository` ports, never a driver):
- `GET /processes` — list of process names with a link to edit each.
- `GET /processes/{name}/edit` — form: process name (read-only), a `<select
  multiple>` of all `RepositoryRegistry.names()` plus a checkbox/radio "All
  repositories" that, when checked, disables the multiselect (no new JS
  framework — a few lines of vanilla JS alongside the existing `sse.js`).
  Pre-filled from `ProcessAdmin.load_process`.
- `POST /processes/{name}/edit` — calls `save_process`; on
  `ProcessValidationError`, re-renders the form with the submitted values and
  an inline error message; on success, redirects to `GET /processes`.
- Acceptance: an operator can open the form, pick specific repos or "all", and
  save; a save with a since-removed repo name shows the validation error
  inline instead of a 500 or a silent no-op.

**FR-6 — E2E test for repository scoping**
A new test module (in-memory-friendly where possible, following
`test_board_e2e.py`/`test_phase2_e2e.py` conventions; `tmp_path` for the
filesystem parts, matching `test_fs_workflows.py`) that:
1. Sets up a `repos.json` registry with two or three repos.
2. Saves a process scoped to one specific repo via `FilesystemProcessAdmin`,
   compiles it via `FilesystemProcessRepository.compile_process`, and asserts
   `.repositories` and `.applies_to(...)` behave correctly for an in-scope and
   an out-of-scope repo.
3. Saves a process with `"*"`, compiles it, asserts `applies_to` is `True` for
   every registered repo.
4. Attempts to save/compile a process naming an unregistered repo and asserts
   `ProcessValidationError` is raised with a message naming the bad repo.
5. Drives at least one of the above through the FastAPI test client
   (`GET`/`POST /processes/...`), so the "editor form" part of the acceptance
   criteria is exercised for real, not just the admin backend.

## Non-functional requirements

- **No new runtime dependency.** The editor stays server-rendered
  Jinja2 + HTMX + vanilla JS, consistent with the existing board UI; no
  client-side framework, no JSON-schema library (validation stays hand-rolled,
  matching the existing style in `fs_workflows.py`).
- **Backward compatible.** Every process file written before this change
  (no `repositories` key) continues to work unchanged, both for the running
  harness (`Workflow`/`WorkflowRepository`, untouched) and for the new
  `Process` layer (defaults to `"*"`).
- **Path-safety on the new write endpoint.** The process name arrives from
  the URL; reuse the existing `invalid_workflow_name` guard (or an equivalent)
  before touching the filesystem, the same way `FilesystemWorkflowRepository`
  and `cli.py` already guard workflow names against path separators / `..`.
- **Architecture invariants hold with no test changes needed.** The generic
  architecture tests (`test_api_does_not_import_drivers`,
  `test_only_app_and_cli_wire_drivers`, `test_ports_do_not_import_drivers`)
  check by module-name prefix (`harness.drivers`), so a new `ports/processes.py`
  + `drivers/fs_processes.py`, wired only in `app.py`/`cli.py` and consumed by
  `api/` only through the port, satisfies them without modification.

## Data model

```
Process
 ├─ name: str
 ├─ workflow: Workflow            # existing model, unchanged (start + transitions)
 └─ repositories: tuple[str,...] | "*"

ProcessDraft (admin-facing, permissive — for the form round-trip)
 ├─ start: str
 ├─ transitions: list[{from, on, to}]
 └─ repositories: list[str] | "*"
```
Storage: one JSON file per process at `<layout.workflows>/<name>.json` — the
same file and directory `FilesystemWorkflowRepository` already reads; this
task adds one key (`repositories`) to that file's schema, no new directory.

## Interfaces

- `ports/processes.py`: `ProcessRepository.compile_process(name) -> Process`,
  `ProcessAdmin.{list_processes, load_process, save_process}`,
  `ProcessValidationError`.
- `drivers/fs_processes.py`: `FilesystemProcessRepository`,
  `FilesystemProcessAdmin` — both rooted at `layout.workflows`.
- HTTP (new): `GET /processes`, `GET /processes/{name}/edit`,
  `POST /processes/{name}/edit`.
- `RepositoryRegistry.names()` (existing, unchanged) is the source of truth
  for both the multiselect's options and build-time validation.

## Dependencies and scope

**Depends on (already built, no changes needed):**
`RepositoryRegistry`/`FilesystemRepositoryRegistry` (repo names + validation
target), `FilesystemWorkflowRepository`/`Workflow` parsing (reused for the
state-machine part of a process), `HarnessLayout.workflows`, the existing
`TaskControl` write-port pattern as the template for `ProcessAdmin`.

**Explicitly out of scope for this task:**
- **Wiring `repositories` into runtime task routing/ingestion.** Today a
  `GithubTaskSource` is configured 1:1 with one `workflow` name and one
  `repository` — there is no "given an incoming repo, pick the matching
  process among several" resolution logic anywhere, and no acceptance
  criterion asks for one. Adding that would touch `SourcePoller`/
  `GithubTaskSource`/dispatch and is a materially larger, separate change.
  This task delivers the schema, validation, storage and editor; consuming
  `repositories` to auto-select a process is a natural follow-up.
- **Authentication/authorization on the new endpoints.** The existing board
  UI has none either, but a write endpoint that edits live process definitions
  is a bigger deal than a read-only board — flagged as an open question below
  rather than assumed away.
- **CLI parity** (e.g. `harness process list/show`) — not required by the
  acceptance criteria; the web editor is the primary surface for this task.
- Renaming `Workflow` → `Process` throughout the codebase.

## Rough plan

1. `models.py`: add `Process` dataclass + `applies_to`.
2. `ports/processes.py`: `ProcessValidationError`, `ProcessRepository`,
   `ProcessAdmin`, `ProcessDraft`.
3. `drivers/fs_processes.py`: `FilesystemProcessRepository.compile_process`
   (delegates state-machine parsing to the existing workflow-loading logic;
   adds `repositories` parsing + validation against `RepositoryRegistry`);
   `FilesystemProcessAdmin` (`list_processes`/`load_process`/`save_process`),
   reusing the existing path-safety guard for process names.
4. Unit tests for both (mirroring `test_fs_workflows.py`'s structure: valid
   load, missing file, malformed JSON, unknown repo, empty `repositories`,
   path-traversal name).
5. `api/process_routes.py` + two Jinja2 templates (`processes_list.html`,
   `process_edit.html`) + a few lines of vanilla JS for the "all repositories"
   toggle; wire into `api/app.py` behind a default no-op `ProcessAdmin`/
   `ProcessRepository`, following the `_NullTaskControl` precedent.
6. Wire `FilesystemProcessRepository`/`FilesystemProcessAdmin` into `app.py`'s
   `build()`/`create_app()` call sites, rooted at `layout.workflows`.
7. E2E test (FR-6) covering the full save → compile → validate round trip,
   including one pass through the FastAPI test client.
8. Update `CLAUDE.md`'s module map with the new files (documentation only,
   `docs:`/`chore:` commit, no behavior change).

## Open questions

- **Should the new endpoints require any auth?** Defaulted to "no" (matches
  the existing board), but this is the first *write* surface exposed by the
  API and deserves an explicit decision rather than silent inheritance from
  the read-only board's posture.
- **Does "all repositories" mean "all repos at compile time" (a snapshot) or
  "all repos, re-evaluated live" (so a newly-registered repo is automatically
  in scope)?** Defaulted to live re-evaluation — `"*"` is stored literally and
  `applies_to`/`compile_process` always check against the *current*
  `RepositoryRegistry.names()`, so adding a repo to the registry doesn't
  require re-saving every globally-scoped process.
- **Is a rename of `Workflow` → `Process` desired eventually?** Out of scope
  here (see above); flagging so a future ADR can make that call deliberately
  rather than the naming drifting piecemeal.
- **No ADR-0015 exists in the repo.** Treated the task's reference to it as
  aspirational/planned rather than something to look up; if it exists outside
  this repo, worth reconciling this plan against it before implementation.
