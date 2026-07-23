# Process admin UI — Implementation Plan

> **For agentic workers:** implement task by task. Each task: write a failing
> test → run it (red) → implement → run it (green) → commit. Steps have a
> checkbox (`- [ ]`). Run `.venv/bin/pytest -q` for the whole suite.

**Goal:** A structured board editor for `processes/*.json`, mirroring the
Agent editor. A write-side `ProcessAdmin` port beside the read-side process
repository, a `FilesystemProcessAdmin` driver, api routes + templates + a nav
entry, wired only in `cli.serve()`. Plus a Process node on the architecture
diagram.

**Spec:** `docs/superpowers/specs/2026-07-22-process-admin-ui-design.md`
**Depends on:** the Process feature (ADR-0015, `drivers/fs_processes.py`) — already on this branch.

**Tech stack:** Python 3.11, `pytest` + `pytest-asyncio`, FastAPI + Jinja2 +
htmx (already in use). No new dependency.

## Global constraints

- **`api/` imports no driver** (invariant #5). The form's check/sink options come
  through the `ProcessAdmin` port (`check_names()`/`sink_kinds()`); the target
  options come from `BoardView.snapshot()` (as `_new_step_warnings` already does).
- **The admin ports are UI-only** (invariant #33, extended to name `ProcessAdmin`).
  `dispatcher`/`consumer` import neither `ports.process_admin` nor
  `drivers.fs_processes`. Filesystem admin driver wired only in `cli.serve()`.
- **One validator, two callers.** `compile_process` in `fs_processes.py` validates
  both at startup (repository) and on submit (admin). Do not duplicate rules.
- **Mirror the existing Agent editor** in structure, template style and route
  shape — deviate only where a Process's fields genuinely differ.
- Branch: `claude/triggers-actions-architecture-7i2avm` (build on top; it is
  being merged, keep adding commits here).

**Already drafted on the branch (verify, don't rewrite from scratch):**
`src/harness/ports/process_admin.py` — `ProcessAdmin`, `ProcessFields`,
`ProcessNotFound`, `ProcessAdminValidationError`, `check_names()`,
`sink_kinds()`. Adjust only if a task below needs it.

---

### Task 1: `compile_process` refactor + `FilesystemProcessAdmin`

**Files:** `src/harness/drivers/fs_processes.py`, `tests/test_fs_processes.py`.

**Interfaces:**
- `ProcessValidationError` gains an optional `field: str | None` attribute:
  `__init__(self, message, field=None)`. `str()` is still just the message (the
  repository's file-naming behaviour and the existing tests are unchanged).
- Extract `compile_process(name, raw, *, clock, checks=BUILTIN_CHECKS,
  repository=None, worktree_root=None, known_targets=None, where=None)
  -> ScheduledTrigger`:
  - `where` (defaults to `name`) is the label in messages.
  - Move the interval/action/target/dedup/sink validation from `_build_one` here,
    each raising `ProcessValidationError(msg, field=...)` with `field` one of
    `"interval"|"check"|"params"|"target"|"dedup"|"sink"`.
  - **Wrap the check-factory call**: `checks[name](params)` in a
    `try/except (KeyError, ValueError, TypeError)` → `ProcessValidationError(...,
    field="params")`. (Fixes the latent raw-`KeyError` for `disk-threshold`
    missing `params`.)
  - `_build_one` keeps the JSON read + non-object guard (raising
    `ProcessValidationError` naming `path.name`, `field=None`), computes
    `name = raw.get("name", path.stem)`, then returns `compile_process(name, raw,
    ..., where=path.name)`.
- `invalid_process_name(name)` — same rule as `invalid_agent_name` (no separators,
  not `""`/`.`/`..`).
- `FilesystemProcessAdmin(ProcessAdmin)` in the same module:
  - `__init__(root)`: `mkdir(parents=True, exist_ok=True)`.
  - `list()`: sorted stems of `*.json`.
  - `read(name)`: parse the file → `ProcessFields`; `ProcessNotFound` on invalid
    name / missing / broken JSON. Reconstruct: `interval=raw["trigger"]["interval"]`,
    `check=raw["action"]["check"]`, `params=raw["action"].get("params", {})`,
    `target_kind`/`target` from the single `target` key, `sink_kind=(raw.get("sink")
    or {}).get("kind", "none")`, `dedup=raw.get("dedup", "per-interval")`. Be
    defensive (a malformed file → `ProcessNotFound`, not a crash).
  - `write(name, fields)`: reject an invalid name (`{"name": ...}`); assemble the
    nested dict; `compile_process(name, raw, clock=SystemClock(),
    known_targets=None)` to validate; on `ProcessValidationError` raise
    `ProcessAdminValidationError({error.field or "_": str(error)})`; write
    atomically (the `_write` temp-then-`os.replace` idiom from `FilesystemAgentAdmin`);
    return the normalized `ProcessFields`.
  - `delete(name)`: unlink, `True`/`False`.
  - `check_names()`: `tuple(sorted(BUILTIN_CHECKS))`. `sink_kinds()`: `("none",)`.

- [ ] **Step 1:** Tests (`tmp_path`):
  - round-trip: `write("nightly", ProcessFields(interval="1h", check="always",
    target_kind="workflow", target="wf"))` → the file compiles via
    `FilesystemProcessRepository(...).build(...)`; `read("nightly")` equals the
    written fields (params `{}`, sink `"none"`, dedup `"per-interval"`).
  - a `disk-threshold`/`step` round-trip with `params={"path":"/","percent":80}`,
    `dedup="per-state"`.
  - `write` raises `ProcessAdminValidationError` with the right field key for:
    bad interval (`"interval"`), unknown check (`"check"`), a `disk-threshold`
    missing `params` (`"params"`), unknown dedup (`"dedup"`), non-`none` sink
    (`"sink"`), invalid name (`"name"`).
  - `read` raises `ProcessNotFound` for a missing name and an invalid name.
  - `delete` returns `True` then `False`; `list()` reflects it.
  - `check_names()` contains `"always"` and `"disk-threshold"`; `sink_kinds() ==
    ("none",)`.
  - **Regression:** a `disk-threshold` *repository* file missing `params` now
    raises `ProcessValidationError` (not `KeyError`) from `.build()`.
- [ ] **Step 2:** Red → **Step 3:** implement → **Step 4:** green (`test_fs_processes.py`).
- [ ] **Step 5:** Commit `feat: FilesystemProcessAdmin + compile_process (shared validator)`.

---

### Task 2: API routes + templates + nav

**Files:** `src/harness/api/routes.py`, `src/harness/api/app.py`,
`src/harness/api/templates/admin/processes_list.html`,
`src/harness/api/templates/admin/process_form.html`,
`src/harness/api/templates/_nav.html`, `tests/test_process_admin_api.py`.

**Interfaces:**
- `api/app.py`:
  - `_EmptyProcessAdmin(ProcessAdmin)` — `list()→()`, `read` raises
    `ProcessNotFound`, `write` raises `ProcessAdminValidationError({"name": "no
    process admin configured"})`, `delete()→False`, `check_names()→()`,
    `sink_kinds()→("none",)`.
  - `create_app(..., process_admin: ProcessAdmin | None = None)`; default to
    `_EmptyProcessAdmin()`; pass into both routers.
- `api/routes.py`:
  - `build_json_router(..., process_admin)` and `build_html_router(..., process_admin)`
    gain the param (thread it through `create_app`).
  - JSON routes: `GET /api/processes` (`{"processes":[...]}`), `GET
    /api/processes/{name}` (fields as JSON; 404 on `ProcessNotFound`), `PUT`
    (422 `{"errors":...}` on `ProcessAdminValidationError`), `DELETE` (404/204).
  - Helpers: `_process_fields_from_form(form)` (parse the `params` JSON textarea —
    empty → `{}`; invalid JSON → treat as `ProcessAdminValidationError({"params":
    ...})` surfaced on the form), `_process_form_context(*, name, is_new, fields,
    check_names, sink_kinds, target_options, errors, saved)`.
  - `target_options`: the running board's workflows + steps, from
    `view.snapshot()` (union of every column name across `workflows` tabs, minus
    `todo/done/failed`, plus the workflow tab names) — reuse the set built in
    `_new_step_warnings`; factor a small `_known_targets(view)` helper if handy.
  - HTML routes mirroring agents, **registering `/admin/processes/new` before
    `/admin/processes/{name}`**: list page, new page, edit page, `POST
    /admin/processes` (create, with name-required + name-collision checks), `POST
    /admin/processes/{name}` (update), `POST /admin/processes/{name}/delete`.
- Templates: copy the agent pair's structure/classes.
  - `processes_list.html`: title "Processes", "+ New process", a table of names
    linking to `/admin/processes/{name}`, empty-state hint.
  - `process_form.html`: fields — Name (only when `is_new`), Interval (text),
    Check (`<select>` over `check_names`), Params (`<textarea>`, JSON, hint
    "JSON object, optional"), Target kind (`<select>` workflow/step), Target
    (`<select>` over `target_options`, plus allow a free value via `<input
    list=...>`/datalist so an as-yet-unqueued target is still submittable),
    Sink (`<select>` over `sink_kinds`), Dedup (`<select>` per-interval/per-state).
    Per-field `.field-error`, the `saved` banner, and a Delete form when not new —
    all copied from `agent_form.html`.
- `_nav.html`: add `{"label": "Processes", "href": "/admin/processes",
  "section": "processes"}` after Workflows.

- [ ] **Step 1:** Tests (`fastapi.testclient.TestClient` over `create_app` with a
  `FilesystemProcessAdmin(tmp_path)` and a small fake/real `BoardView`; follow the
  existing admin-api test file for the pattern):
  - `GET /admin/processes` renders and lists a seeded process.
  - `GET /admin/processes/new` renders; `POST /admin/processes` with valid form
    creates the file and shows the saved banner; `read` confirms it.
  - `POST` with a bad interval re-renders with the interval `.field-error` and
    writes nothing.
  - `GET /admin/processes/{name}` shows current values; `POST
    /admin/processes/{name}` updates; `POST .../delete` removes and 303-redirects.
  - JSON: `GET /api/processes`, `GET /api/processes/{name}`, `PUT` (422 on bad
    input), `DELETE`.
  - `create_app` with no `process_admin` boots and `/admin/processes` lists
    nothing.
  - `_nav.html` includes a Processes link (assert on any rendered admin page).
- [ ] **Step 2:** Red → **Step 3:** implement → **Step 4:** green (new test file +
  existing api tests still pass).
- [ ] **Step 5:** Commit `feat: process admin UI — structured editor + routes + nav`.

---

### Task 3: Wire `cli.serve()` + `HarnessLayout.processes`

**Files:** `src/harness/app.py` (`HarnessLayout`), `src/harness/cli.py`,
`tests/test_cli.py` (or the serve test that already covers admin wiring).

**Interfaces:**
- `HarnessLayout.processes` property → `self.root / "processes"` (mirror `agents`).
- `cli.serve()`: pass `process_admin=FilesystemProcessAdmin(harness.layout.processes)`
  into `create_app(...)` (beside the two existing admins).

- [ ] **Step 1:** Test — `serve()`-level check that the wired app serves
  `/admin/processes` over the real filesystem admin (extend the existing serve
  test if there is one; else a focused `create_app`-with-`FilesystemProcessAdmin`
  test already covers the driver, so a light assertion that `cli.serve` passes it
  through suffices).
- [ ] **Step 2:** Red → **Step 3:** implement → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: wire FilesystemProcessAdmin in cli.serve()`.

---

### Task 4: Architecture diagram + docs + CLAUDE.md

**Files:** `src/harness_docs_site/architecture.py`, `CLAUDE.md`,
`tests/test_architecture.py` (if a targeted guard is warranted).

- [ ] **Step 1 (diagram):** Read `architecture.py` fully. Add a `Part(id="process",
  kind="driver", …, adrs=("0015-process-authoring-aggregate",), sources=(
  "src/harness/drivers/fs_processes.py", "src/harness/drivers/scheduled_trigger.py"),
  x=…, y=…)` and **at least one `Edge`** connecting it into the graph (a Process
  compiles to a scheduled `TaskSource` feeding the inbox — connect to the
  source/poller/inbox part that already exists) so it is not an orphan. Keep the
  hand-model's `validate()` green (it runs in `tests/test_architecture_model.py` /
  `test_docs_site.py`). Pick coordinates that don't overlap existing parts.
- [ ] **Step 2 (docs):** `CLAUDE.md`:
  - extend invariant **#33** to name `ProcessAdmin` / `FilesystemProcessAdmin`
    (per the spec's refined wording).
  - add module-map rows: `ports/{…,process_admin}` and note `FilesystemProcessAdmin`
    on the `fs_processes` driver bullet.
  - one line under the Process responsibilities bullet: "a structured board
    editor exists (`ProcessAdmin`), wired in `serve()` like the agent/workflow
    editors."
  - (`test_claude_md_module_map` requires the stem `process_admin` to appear.)
- [ ] **Step 3:** `.venv/bin/pytest -q` — whole suite green.
- [ ] **Step 4:** Commit `docs+diagram: ProcessAdmin invariant, module map, explorer node`.

---

## Ordering

```
T1 (port already drafted → driver) ─> T2 (api + templates + nav) ─> T3 (serve wiring) ─> T4 (diagram + docs)
```

## Notes

- **Do not let `api/` import `BUILTIN_CHECKS` or any driver.** Options flow
  through the port (`check_names`/`sink_kinds`) and `BoardView`.
- **`compile_process` is the only validator.** The admin passes
  `known_targets=None`; unknown-target feedback, if any, is a soft form hint from
  `BoardView`, never a hard driver error (mirror `WorkflowAdmin`).
- **Name is the path, never a form field on edit.** Only the *create* form has a
  Name input; update takes the name from the URL.
- **Saved semantics match agents:** a saved process is picked up on the next run
  (no hot restart); reuse the agent form's "saved — picked up on the next run"
  banner copy.
- **Keep every commit green.** If a docs-completeness guard
  (`test_claude_md_module_map`) would fail between tasks, fold its one-line fix
  into the task that introduces the module.
