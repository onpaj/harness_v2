# Architecture assessment — Process editor should allow selecting target repositories

Reviewed against the actual code (`models.py`, `ports/{workflows,control,repos}.py`,
`drivers/{fs_workflows,fs_repos}.py`, `api/{app,routes}.py`, `cli.py`,
`tests/test_architecture.py`), not just against `plan-01.md`/`design-01.md`. Verdict:
**approved, build it as designed**, with the tightenings and prerequisites below.

## Alignment check

Every load-bearing claim in `design-01.md` checks out against the current tree:

- `Workflow.steps()` already exists (`models.py:157`) and returns `tuple[str, ...]`
  — `ProcessSummary.steps` can use it as-is, no new method needed.
- `RepositoryRegistry` (`ports/repos.py`) is already a **port**, and
  `test_orchestration_does_not_import_work_ports` already lists
  `harness.ports.repos` as a work-port the dispatcher/consumer must not see —
  importing it into `api/process_routes.py` is architecturally clean today
  (invariant #5 forbids `drivers/`, not `ports/repos.py`). `FilesystemRepositoryRegistry`
  stays a driver, built once in `cli.py:656` — reuse that instance, don't build a second.
- `TaskControl` / `_NullTaskControl` (`ports/control.py`, `api/app.py:44-50`) is
  exactly the precedent to copy: an ABC with one write verb, a null object behind
  it, injected as an optional `create_app` kwarg. `ProcessAdmin` should be the
  fourth null-object in `api/app.py`, not a new pattern.
- `invalid_workflow_name` (`drivers/fs_workflows.py:12`) is already the single
  source of truth for path-safety on a workflow/process name — reuse the
  function, don't duplicate the rule.
- `test_architecture.py`'s checks are all structural (module-name prefix,
  directory glob, whole-module AST scan) — a new `ports/processes.py` +
  `drivers/fs_processes.py`, wired only from `app.py`/`cli.py`, passes every
  existing test unmodified. Confirmed by reading the test file directly; no
  test changes are needed for this task, only additions.
- `cli.py:649-714` (`_run`) is confirmed as the one call site that builds a real
  `FilesystemRepositoryRegistry` and calls `create_app(...)` — the two places
  design says to touch are exactly the two places that exist.

No conflict found between the plan/design and the codebase. The rest of this
document is steering, not correction.

## Proposed architecture (confirmed)

Three additive layers, nothing renamed:

1. **Schema**: one optional key, `"repositories"`, added to the JSON file
   `FilesystemWorkflowRepository` already reads (`<layout.workflows>/<name>.json`).
   Absent → `"*"`. `"*"` → all repos, evaluated live. `[...]` → exact set,
   non-empty, every name must resolve in `RepositoryRegistry.names()`.
2. **`Process` layer**: `models.Process` (+ `applies_to`), `models.ProcessSummary`,
   `ports/processes.py` (`ProcessRepository`, `ProcessAdmin`,
   `ProcessValidationError`), `drivers/fs_processes.py`
   (`FilesystemProcessRepository`, `FilesystemProcessAdmin`). Delegates all
   `start`/`transitions` parsing to the existing `FilesystemWorkflowRepository` —
   there is exactly one parser for the state-machine shape.
3. **Editor UI**: `api/process_routes.py` (`GET /processes`,
   `GET`/`POST /processes/{name}/edit`), two Jinja2 templates, one small vanilla-JS
   file. Wired into `create_app` behind `ProcessAdmin`/`RepositoryRegistry` ports,
   with null-object defaults so every existing caller of `create_app` keeps working.

### Key decision: `Process` reads two files' worth of concerns, but one file on disk

The state machine (`Workflow`) and the new scope (`repositories`) live in the
*same* JSON file and are parsed by *two* collaborating pieces
(`FilesystemWorkflowRepository.get` + repository-scope parsing in
`fs_processes.py`). This is correct: it means the running harness's routing
path never has to know `Process` exists, and a rename of `Workflow` → `Process`
(if ever desired) stays a future, separate, deliberate decision. Rejected
alternative: a new file format / new directory for processes — this would fork
the definition into two documents describing one thing, immediately going stale
relative to each other.

### Key decision: load is lenient, save is strict

`ProcessAdmin.load_process` must not raise `ProcessValidationError` — if it did,
a process whose scope drifted invalid (a referenced repo was deregistered) could
never be opened in the one UI meant to fix it. `ProcessRepository.compile_process`
(the build-time/consumption path) must raise, per the acceptance criteria. Same
validation function, called from two call sites with different tolerance for the
*current* state of the file — not two different rules. This is the one subtle
invariant in this design; get the test coverage for it explicit (see below).

### Key decision: `ProcessAdmin` write surface is `repositories`-only, not a full `ProcessDraft`

The design narrows the plan's original `ProcessDraft` (which covered
`start`/`transitions` too) down to `save_repositories(name, repositories)`, a
read-modify-write that touches only one JSON key. Endorsed: the task asks for
repository scoping, not a state-machine editor, and the first write-capable form
in this app should have the smallest possible blast radius. Do not build a
generic draft object for a field that doesn't need editing yet — that's
speculative surface for a future task, not this one.

## Implementation guidance

Build in this order (matches the plan's rough plan; sequencing rationale added):

1. **`models.py`**: `RepositoryScope`, `Process.applies_to`, `ProcessSummary`.
   Pure data, no I/O — write and unit-test this first since everything else
   depends on the shape.
2. **`ports/processes.py`**: `ProcessValidationError`, `ProcessRepository`,
   `ProcessAdmin`. Keep docstrings on each verb stating the lenient/strict
   contract explicitly (mirroring the existing style in `ports/control.py` and
   `ports/repos.py`) — the load/save asymmetry is exactly the kind of thing a
   future reader needs spelled out, not inferred.
3. **`drivers/fs_processes.py`**: one shared `_validate_repositories(repositories,
   name, registry)` helper, called from both `compile_process` and
   `save_repositories` — do not let these two call sites drift into two
   validation rules. `compile_process` composes
   `FilesystemWorkflowRepository(root).get(name)` rather than reimplementing
   `start`/`transitions` parsing. Atomic write via `tmp.replace(path)` for
   `save_repositories`, as designed — this is the only writer of a live process
   file and must not leave a torn write for the running harness or a concurrent
   `compile_process` to observe.
4. **Unit tests** for both classes before touching HTTP — valid/empty/unknown-repo/
   malformed/path-traversal/missing-file, plus one test each for the lenient-load
   vs strict-save asymmetry (load a file with a deregistered repo → succeeds and
   returns it in `ProcessSummary`; save the same scope → raises). This asymmetry
   is easy to accidentally collapse into "always strict" during implementation;
   pin it down with a test before it can regress silently.
5. **`api/process_routes.py`** + templates + `process_form.js`. Reuse
   `TEMPLATES` conventions from `api/routes.py` (same `Jinja2Templates` root,
   same `HTMLResponse`/`HTTPException` idioms). `WorkflowNotFound` → 404,
   `ProcessValidationError` → 422 re-render, success → 303 redirect, exactly as
   designed.
6. **Wiring**: `_NullProcessAdmin`/`_EmptyRepositoryRegistry` in `api/app.py`
   next to the three existing null objects; two new optional `create_app`
   kwargs; `cli.py:_run` passes the already-constructed `registry` and a new
   `FilesystemProcessAdmin(layout.workflows, registry)`. No change to
   `app.py`'s `build()` — confirmed out of scope, routing/dispatch untouched.
7. **E2E test** (FR-6): put it alongside `test_phase2_e2e.py`/`test_board_e2e.py`
   (e.g. `tests/test_process_repository_scoping_e2e.py`), following
   `test_fs_workflows.py`'s `tmp_path` convention for the filesystem parts and
   FastAPI's `TestClient` for the one HTTP pass. Cover, at minimum: save+compile
   round trip for a specific-repo scope, for `"*"`, and the unknown-repo failure
   surfacing through `POST /processes/{name}/edit` as a 422 with the operator's
   selection preserved (not just through the admin object directly) — the
   acceptance criteria explicitly ask for the *editor* to be exercised
   end-to-end, not only the driver.
8. **Docs**: update `CLAUDE.md`'s module map (`docs:` commit) once the files
   exist, per repo convention.

## Risks and mitigations

- **Concurrent edits / read-modify-write race on `save_repositories`.** Two
  operators (or an operator and a script) saving the same process file at once
  could lose one write. Mitigation: none needed for this task — matches the
  board's existing no-auth, single-operator-in-practice posture, and the
  read-modify-write is already atomic at the filesystem level (`tmp.replace`)
  so the failure mode is "last write wins," not corruption. Call this out
  explicitly in the PR description rather than silently accepting it.
- **`compile_process` re-reads the file twice** (once via
  `FilesystemWorkflowRepository.get`, once directly for `repositories`).
  Accepted as designed — these are small, infrequently-read, hand-edited JSON
  files; a second `read_text` is not a real cost, and merging the two reads
  would mean duplicating `FilesystemWorkflowRepository`'s parsing instead of
  delegating to it. Do not "optimize" this into one shared parse without
  re-checking that it doesn't reintroduce a second copy of the transition-parsing logic.
- **No auth on the first write-capable form.** Flagged by the plan as an open
  question; my recommendation is to ship without auth, consistent with the
  existing board's posture and the operator-only deployment model this project
  targets today (see `CLAUDE.md`: single operator, machine-local). This is a
  product decision, not an architectural blocker — but it should be a conscious
  sign-off before merge, not a default nobody decided.
- **Stale repository names in a stored scope.** Already designed for: shown,
  not silently dropped, on both `GET /processes` (⚠ marker) and the edit form
  (checked-but-stale checkbox). No further mitigation needed; this is the
  correct behavior for build-time validation catching drift after the fact.
- **`"*"` semantics ambiguity (snapshot vs. live).** Design resolves this in
  favor of live re-evaluation (`applies_to` never snapshots the registry).
  Endorsed — the alternative (snapshot at save time) would silently exclude
  newly-registered repos from every globally-scoped process until a no-op
  re-save, which is a worse surprise than the live behavior.

## Prerequisites / open items before implementation starts

- **ADR-0015 does not exist in this repo** (confirmed: no `docs/**/adr/`
  directory, no ADR numbered anywhere). Do not block implementation waiting
  for it to appear from elsewhere. Recommendation: don't invent a new ADR
  convention for this one task — the plan/design/architecture triad already
  being produced for this task *is* the durable record of the decision
  ("Process is an additive layer over Workflow, not a rename"). Reference this
  document trio in the PR description in place of the missing ADR number so
  the traceability the acceptance criteria imply is still satisfied.
- **Confirm the no-auth decision** with the task owner before merge (see risk
  above) — a one-line sign-off is enough, this shouldn't gate starting the work.
- No other prerequisite work is required — `RepositoryRegistry`,
  `FilesystemWorkflowRepository`, and the `TaskControl` null-object precedent
  are all already in place and unchanged by this task.
