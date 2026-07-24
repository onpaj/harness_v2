# Review — Process editor should allow selecting target repositories

Reviewed `development-01.md` and the actual diff (`git show --stat HEAD`,
17 files, +1149/-6) against `plan-01.md`/`design-01.md`/`architecture-01.md`
and the task's acceptance criteria. Verdict: **done**.

## Conformance check

- **FR-1 (schema)** — confirmed in `drivers/fs_processes.py`: absent key →
  `"*"`; `"*"` accepted; list of names accepted; `[]` raises
  `ProcessValidationError`. Matches design's validation table exactly.
- **FR-2 (`Process` model)** — `models.py:169-203`: `RepositoryScope`,
  `Process.applies_to`, `ProcessSummary`, all as designed. `applies_to` is a
  pure function on `models.py` (no package import), matching the module-map
  invariant.
- **FR-3 (`compile_process`)** — `FilesystemProcessRepository.compile_process`
  delegates `start`/`transitions` parsing to
  `FilesystemWorkflowRepository.get` (one parser, as required), re-reads the
  file for `repositories`, validates via a shared `_validate_repositories`.
  Path-safety is inherited for free through `FilesystemWorkflowRepository.get`
  (which calls `invalid_workflow_name` before touching disk) — confirmed this
  runs before `_read_raw`, so an unsafe name never reaches the second read.
  Error message includes the bad repo name(s) and the known-repositories list,
  satisfying the "helpful feedback" acceptance criterion verbatim.
- **FR-4 (`FilesystemProcessAdmin`)** — `list_processes`/`load_process`/
  `save_repositories` implemented as designed. Verified the lenient-load/
  strict-save asymmetry explicitly: `load_process` has no
  `_validate_repositories` call (only `_parse_repositories`), `save_repositories`
  validates before the atomic `tmp.replace(path)` write and leaves the file
  untouched on failure. Test `test_load_process_is_lenient_about_deregistered_repository`
  plus the e2e test's save-then-direct-write-then-compile sequence both
  exercise this asymmetry concretely, not just by inspection.
- **FR-5 (editor UI)** — `GET /processes`, `GET`/`POST /processes/{name}/edit`
  implemented with the exact status-code contract from the design (200/404/
  422/303). Stale (deregistered) repos are shown, not dropped, on both the
  list (⚠ marker) and the edit form (checked-but-stale checkbox) — matches
  the design's explicit UX requirement. `process_form.js` degrades
  gracefully (form still POSTs meaningfully with JS off, per its own
  comment and the plain `<form method="post">`).
- **FR-6 (E2E test)** — `tests/test_process_repository_scoping_e2e.py`
  covers all five points from the plan: specific-repo round trip, `"*"`
  round trip, unregistered-repo rejection on both save and compile, and two
  passes through the FastAPI `TestClient` (successful save reflected by
  `compile_process`, and a rejected save returning 422 with the stored scope
  left untouched). This is real editor-level E2E coverage, not just the
  admin backend.

## Architecture adherence

- Wiring is confined to `api/app.py` (two new null objects,
  `_NullProcessAdmin`/`_EmptyRepositoryRegistry`, following the
  `_NullTaskControl` precedent exactly) and `cli.py:_run`/`serve` (passes the
  already-built `registry` plus a new `FilesystemProcessAdmin`). No change to
  `app.py`'s `build()` / the routing-dispatch path, as scoped.
- `api/process_routes.py` imports only `ports/processes.py` and
  `ports/repos.py` — both ports, no driver import — consistent with how
  `routes.py` already touches `BoardView`/`ArtifactView`/`TaskControl`.
  `dispatcher.py`/`consumer.py` are untouched and still don't import
  `ports/processes.py` or `ports/repos.py`.
- Full test suite: **507 passed, 1 skipped** (pre-existing opt-in
  `HARNESS_SMOKE_CLAUDE` skip, unrelated). `test_architecture.py`'s
  structural checks (`test_ports_do_not_import_drivers`,
  `test_only_app_and_cli_wire_drivers`, `test_api_does_not_import_drivers`,
  etc.) pass unmodified, confirming the new files respect the layering rules
  without needing test changes, as the architecture doc predicted.

## Deviation noted, judged acceptable

`python-multipart` was added as a genuine new runtime dependency (FastAPI
requires it for any `Form(...)` field, even plain urlencoded). The design's
non-functional note said "no new runtime dependency" — but this is an
unavoidable consequence of building any HTML form with FastAPI, was flagged
explicitly in `development-01.md` rather than silently slipped in, and
doesn't change the architecture or introduce a client-side framework (the
concern the note was actually guarding against).

## Minor, non-blocking observation

`GET /processes` calls `admin.load_process(name)` for every listed process;
`load_process` calls `_parse_repositories`, which raises
`ProcessValidationError` (not just returns a warning) if a hand-edited file's
`repositories` value is neither `"*"` nor a list of strings (e.g. a number or
a dict) — that would 500 the whole list page instead of degrading to a
per-row warning like the stale-repo-name case does. Not covered by any
acceptance criterion (those only cover *unknown repository names*, not
*malformed field shape*) and not exercised by any test either way, so it
isn't a spec conflict — worth a follow-up hardening pass, not a blocker for
this task.

## Verdict

All six acceptance criteria are met, the implementation follows the
architecture and design faithfully (including the one subtle invariant —
lenient load / strict save — with explicit test coverage), and the full test
suite passes. No functional requirement is unmet, no architecture conflict,
no missing required test, no correctness bug that blocks acceptance.
