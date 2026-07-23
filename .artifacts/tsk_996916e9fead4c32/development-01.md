# Development — Process editor should allow selecting target repositories

Implements `plan-01.md`/`design-01.md`/`architecture-01.md` as designed: an
additive `Process` layer over the existing `Workflow` JSON file, no rename, no
change to the running harness's routing path.

## What was implemented

**Model (`src/harness/models.py`)**
- `RepositoryScope = tuple[str, ...] | str` (`"*"` = all repositories, live-evaluated).
- `Process` (`name`, `workflow`, `repositories`) with `applies_to(repository)`.
- `ProcessSummary` — unvalidated admin/UI read shape (`name`, `start`, `steps`, `repositories`).

**Ports (`src/harness/ports/processes.py`, new)**
- `ProcessValidationError`.
- `ProcessRepository.compile_process(name) -> Process` — strict (build-time) read.
- `ProcessAdmin.{list_processes, load_process, save_repositories}` — lenient
  read (`load_process` never raises `ProcessValidationError`) + validated write.

**Drivers (`src/harness/drivers/fs_processes.py`, new)**
- `FilesystemProcessRepository` — delegates `start`/`transitions` parsing to
  the existing `FilesystemWorkflowRepository`; parses/validates `repositories`.
- `FilesystemProcessAdmin` — `list_processes` (glob `*.json`), `load_process`
  (lenient), `save_repositories` (strict validate-then-atomic-write via
  `tmp.replace`, read-modify-write so `start`/`transitions` survive untouched).
- Shared `_validate_repositories`/`_parse_repositories` so `compile_process`
  and `save_repositories` can never drift into two different validation rules.
- Reuses `invalid_workflow_name` from `fs_workflows.py` for path-safety.
- Validation rules: absent → `"*"`; `"*"` → valid, evaluated live; `[]` →
  `ProcessValidationError` ("no repositories selected…"); unknown name(s) →
  `ProcessValidationError` naming the bad repo(s) and listing known ones.

**Editor UI (`src/harness/api/process_routes.py`, new, + 2 templates + 1 JS file)**
- `GET /processes` — lists every process with its scope; flags a stale
  (deregistered) repo name with a warning instead of hiding it.
- `GET /processes/{name}/edit` — form: read-only start/steps, a radio
  "all repositories" / "specific repositories" with a checkbox multiselect of
  `RepositoryRegistry.names()`; a stale selected repo still renders, checked,
  so the operator can consciously fix it. `process_form.js` disables the
  checkbox list while "all" is selected (form still works with JS off).
  404 (`WorkflowNotFound`) for an unknown process.
- `POST /processes/{name}/edit` — validates then saves; `ProcessValidationError`
  re-renders the form (422) with the submitted selection and an inline error;
  success → `303` redirect to `/processes`.

**Wiring**
- `api/app.py`: `_NullProcessAdmin`/`_EmptyRepositoryRegistry` null objects
  (same pattern as `_NullTaskControl`); two new optional `create_app` kwargs
  (`processes`, `repos`); process router mounted.
- `cli.py`: `_run` builds `FilesystemProcessAdmin(layout.workflows, registry)`
  and forwards it plus the existing `registry` through `serve()` into `create_app`.
- `pyproject.toml`: added `python-multipart>=0.0.9` — required by FastAPI/
  Starlette to parse *any* `Form(...)` field (including
  `application/x-www-form-urlencoded`), not only multipart uploads. This is a
  genuine new runtime dependency the design's "no new dependency" note didn't
  anticipate; flagging it here since it's the one deviation from the design doc.

## Tests added

- `tests/test_models.py` — `Process.applies_to` for specific repos and `"*"`.
- `tests/test_fs_processes.py` — `FilesystemProcessRepository`/
  `FilesystemProcessAdmin`: defaults, specific/`"*"` scopes, empty/unknown-repo
  rejection, missing file, path-traversal name, and the lenient-load/strict-save
  asymmetry (a deregistered repo loads fine but fails to save/compile).
- `tests/test_api_process_routes.py` — the editor's HTTP surface: list with
  stale-repo warning, edit form pre-fill, save (specific/`"*"`), 422 with
  preserved selection on validation failure, 404s, and the
  no-`processes`/`repos`-wired backward-compat default.
- `tests/test_process_repository_scoping_e2e.py` (FR-6) — full round trip on
  real filesystem drivers (`repos.json` + process JSON via `HarnessLayout`):
  specific-repo scoping, `"*"` scoping, unregistered-repo rejection on both
  save and compile, and two passes through the FastAPI `TestClient` (a
  successful save reflected by `compile_process`, and a rejected save showing
  the inline error while leaving the stored scope untouched).
- `tests/test_cli.py` — updated the two `fake_serve` monkeypatches to accept
  `**kwargs`, since `serve()` now also receives `processes=`/`registry=`.

## Verification

```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Full suite: **507 passed, 1 skipped** (the skip is the pre-existing opt-in
`HARNESS_SMOKE_CLAUDE` test, unrelated to this change). Architecture tests
(`test_architecture.py`) pass unmodified — the new `ports/processes.py` +
`drivers/fs_processes.py`, wired only from `app.py`/`cli.py` and consumed by
`api/` only through the port, satisfy every existing structural check with no
test changes needed there.

Manual check of the editor: build the app with a `tmp_path` layout seeded with
a `repos.json` and a process JSON, `create_app(processes=FilesystemProcessAdmin(...),
repos=FilesystemRepositoryRegistry(...), ...)`, then `GET /processes`,
`GET /processes/<name>/edit`, and `POST` a save — all covered directly by
`tests/test_process_repository_scoping_e2e.py`, which drives this exact path.

## Deviations from the design docs

- `pyproject.toml` gained `python-multipart` as a new runtime dependency (see
  above) — the design's non-functional note said "no new runtime dependency";
  this one is unavoidable for any `Form(...)`-based POST in FastAPI, including
  a plain `application/x-www-form-urlencoded` submit.
- Everything else (schema, `Process`/`ProcessSummary` shapes, `ProcessRepository`/
  `ProcessAdmin` interfaces, lenient-load/strict-save split, HTTP routes/status
  codes, null-object wiring pattern) was built as specified.

## Acceptance criteria checklist

- [x] Process JSON schema includes a `repositories` field (array of names or `"*"`).
- [x] `FilesystemProcessAdmin` and `FilesystemProcessRepository.compile_process`
      validate selected repositories.
- [x] Process editor form includes a repository multiselect control.
- [x] Operator can save a process with specific repos or "all".
- [x] E2E test confirms repository scoping works end-to-end.
- [x] `ProcessValidationError` includes helpful feedback (names the bad repo(s)
      and lists the known ones) when a named repository doesn't exist.
