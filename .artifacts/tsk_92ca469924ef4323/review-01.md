# Review: admin UI (agents CRUD + workflow raw-JSON editor)

## Verdict: done

## What I checked

- Read `plan-01.md`, `design-01.md`, `architecture-01.md`, `development-01.md`.
- Read the actual diff (`git show --stat 1e3b63e`, then full contents of
  `ports/agent_admin.py`, `ports/workflow_admin.py`, `drivers/fs_agents.py`,
  `drivers/fs_workflows.py`, `api/routes.py`, `api/app.py`, `cli.py` diff,
  `agent_form.html`, `CLAUDE.md` diff, `pyproject.toml` diff).
- Ran the full suite: `.venv/bin/pytest -q` → **544 passed, 1 skipped**
  (the skip is the pre-existing opt-in `HARNESS_SMOKE_CLAUDE` smoke, unrelated).
- Cross-checked each FR-1..FR-8 acceptance criterion in `plan-01.md`/`design-01.md`
  against both the implementation and the corresponding test in
  `tests/test_api_agents.py` / `tests/test_api_workflows.py` /
  `tests/test_fs_agent_admin.py` / `tests/test_fs_workflow_admin.py` /
  `tests/test_admin_writes_are_seen_live.py`.

## Conformance to spec

All eight functional requirements are implemented and covered by tests:

- **FR-1/FR-5 (list)**: `GET /admin/agents`, `GET /api/agents`, same shape for
  workflows; empty-directory case explicitly tested.
- **FR-2 (structured agent form)**: fields match exactly (name, prompt, model,
  fallback_model, comma-separated allowed_tools, two allowed_outcomes
  checkboxes bound to the actual `Outcome` enum's two values). Pre-fill tested
  in `test_agent_form_prefills_existing_fields`.
- **FR-3 (save agent)**: reuses `_parse_agent_spec` for validation (same code
  path as the runtime read), rejects empty prompt (added correctly since
  `AgentFields.prompt` is always key-present), rejects before write
  (`write()` validates fully before `_write` is called) — confirmed by
  `test_write_rejected_submission_leaves_existing_file_untouched` at the
  driver level and the API-level equivalents. Name validation reuses
  `invalid_agent_name` (renamed from private, as the design called for).
- **FR-4 (delete agent)**: no cross-reference check, matching the plan's
  explicit "Open questions" decision to defer that.
- **FR-6 (raw workflow editor)**: `read_raw`/`write_raw` never
  parse-then-reserialize — `write_raw` writes the *original submitted text*
  verbatim on success. New-step restart warning computed via `BoardView`
  columns (not inside `WorkflowAdmin`, matching the architecture review's
  guidance), tested in `test_put_workflow_warns_about_a_new_step` and
  `test_update_workflow_shows_restart_warning`.
- **FR-7 (create)**: `/admin/agents/new` and `/admin/workflows/new` routes are
  registered *before* the dynamic `/{name}` routes — verified in the source
  and exercised by `test_new_agent_page_is_reachable_before_dynamic_route` /
  `test_new_workflow_page_is_reachable_before_dynamic_route`. Create-vs-exists
  name collision check added on both create paths, matching the architecture
  review's flagged gap.
- **FR-8 (navigation)**: `_nav.html` included in `board.html` and both admin
  template families.

## Architecture / invariants

- `api/` still imports only ports (`AgentAdmin`, `WorkflowAdmin`, plus the
  pre-existing ones) — no driver import, verified by reading `routes.py`/
  `app.py` and confirmed by the existing glob-based
  `test_api_does_not_import_drivers` (which the architecture doc correctly
  predicted needs no extension).
- Filesystem drivers wired exclusively in `cli.py::serve()`, matching the new
  invariant #24 added to `CLAUDE.md`.
- `_EmptyAgentAdmin`/`_EmptyWorkflowAdmin` null objects preserve
  backward-compatible `create_app(...)` calls, same pattern as the existing
  `_EmptyArtifactView`/`_NullTaskControl`.
- Atomic write idiom (`os.replace` via a uuid-suffixed temp file) is copied
  verbatim from `fs_queue.py`'s pattern, as the architecture doc directed.
- `_parse_agent_spec`/`_parse_workflow` are shared between the read path
  (`FilesystemAgentCatalog.get`/`FilesystemWorkflowRepository.get`) and the
  write path — single validation contract, no drift risk. Existing catalog
  tests still pass unchanged, confirming the extraction was behavior-neutral.

## Correctness

- No logic errors found. Validation-before-write is consistently applied
  everywhere (agent `write`, workflow `write_raw`, both HTML and JSON
  routes) — a rejected submission never touches disk, confirmed by dedicated
  tests at both the driver and API layers.
- The packaging gap (`templates/admin/*.html` missing from
  `[tool.setuptools.package-data]`) was caught and fixed with real
  verification (wheel build + `zipfile.namelist()` check per the dev notes);
  `python-multipart` was correctly identified as needed for
  `request.form()` and added to `[project.dependencies]`.
- `test_cli.py`'s `FakeHarness` was updated to carry a real `HarnessLayout`
  since `serve()` now reads `harness.layout.agents`/`.workflows` — a
  necessary and correctly scoped test fixture update.

## Non-blocking observations (not requiring changes)

- The HTML delete routes (`delete_agent_page`/`delete_workflow_page`) don't
  check the boolean return of `.delete()` before redirecting — deleting an
  already-gone name silently redirects to the list same as a successful
  delete. This matches the JSON API's more precise 404 behavior being
  intentionally looser on the HTML side (idempotent-delete UX), and isn't a
  spec violation.
- Everything else (concurrent-write safety, no auth, no rename support) is
  explicitly out of scope per the plan's "Open questions"/"Explicitly out of
  scope" sections and matches the implementation.

## Conclusion

Implementation is complete, matches the plan/design/architecture documents
precisely (including every finding the architecture review raised), is
thoroughly tested (unit tests at the driver layer, integration tests at the
API layer for both JSON and HTML surfaces, a live-visibility test, and a
real end-to-end manual check against the CLI per the dev notes), and the
full suite passes. No changes requested.
