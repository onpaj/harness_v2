# Plan — per-process target repository selection (finishing PR #108)

## Summary

An operator authoring a Process today has no way to say which repository the
tasks it produces should attach a worktree in — the field doesn't exist in
the schema, the form, or the compiled `ScheduledTrigger`. PR #108 landed a
UI stub for this (a JS file manipulating form elements that were never added)
but no backend. This plan adds a real, optional `repository` field to the
Process schema/admin/form, threads it through `compile_process` into
`ScheduledTrigger`, and retires the dead stub.

## Context

`ScheduledTrigger` has always accepted a `repository: str | None` constructor
parameter and already applies it correctly (`obs.repository or
self._repository`, `drivers/scheduled_trigger.py:111`) — it is simply never
fed anything but a hardcoded `None` today (`app.py:647`,
`process_repo.build(..., repository=None, ...)`). Nothing reads a
`"repository"` key out of a process's JSON. The only way a produced task
today gets a repository is a check's own observation supplying one (e.g.
`github-issues` stamping each issue's own repo per invariant handled inside
the check itself) — a process built on `always`/`command`/`disk-threshold`/
`fs-files` can never target a repository at all, which blocks e.g. "run a
shell command against repo X every 30 minutes and open a task in its
workflow."

`task.repository` is a *name*, resolved by `RepositoryRegistry`
(`repos.json`), never a path (invariant #15) — so the new field must be
validated as a name against that registry, not free text, and the
`ScheduledTrigger`/`Task` shape needs no change at all: it already carries a
single optional repository name per fired task.

PR #108's leftover `src/harness/api/static/process_form.js` assumes a
multi-repo checkbox list (`name="repositories"`, an "all" vs. selection
`scope` radio) that doesn't match `ScheduledTrigger`'s single-`repository`
cardinality — fan-out to N repositories per process would require the
trigger to emit N observations per occurrence, a materially bigger feature
not asked for here (see Open Questions). This plan treats PR #108's shape as
superseded, not as a spec to satisfy literally.

## Functional requirements

**FR-1 — `processes/*.json` schema gains an optional `repository` field.**
- AC: a process file with no `"repository"` key compiles exactly as today
  (no repository stamped by the process; an observation's own `repository`,
  if any, is unaffected).
- AC: a process file with `"repository": "<name>"` produces tasks carrying
  that name whenever the check's own observation doesn't supply one.
- AC: `compile_process` reads this from the process definition itself, not
  only from the `repository=` kwarg callers already pass in (that kwarg
  remains a fallback default, used only when the file omits the field —
  precedence: `obs.repository or file's "repository" or the caller's
  repository= default`).

**FR-2 — Validation against the repository registry.**
- AC: `compile_process` accepts an optional `known_repositories: set[str] |
  None` parameter, mirroring `known_targets`. When provided and the file
  names a repository not in that set, compilation raises
  `ProcessValidationError(msg, field="repository")`.
- AC: when `known_repositories` is `None` (registry unavailable to the
  caller), an unrecognized name is accepted — lenient, matching every other
  `known_*=None` escape hatch in this module.
- AC: a non-string / empty-string `"repository"` value is also a
  `field="repository"` error, distinct from the "not in registry" message.
- AC: both real compile paths — `FilesystemProcessRepository.build()` (real
  `harness run` startup) and `FilesystemProcessAdmin.write()` (the dashboard
  save) — supply `known_repositories` from the same `RepositoryRegistry`
  (`repos.json` is static, machine-local config available at both sites,
  unlike the served-workflow set `known_targets` depends on — that's why
  `target` validation stays lenient at admin-save time but `repository`
  validation does not need to).

**FR-3 — `ProcessFields`/round-trip.**
- AC: `ProcessFields` gains `repository: str = ""` (empty string means "all
  repositories" / no default).
- AC: `_raw_from_fields` omits the `"repository"` key entirely when empty,
  and writes it verbatim when set; `_fields_from_raw` reads it back
  (`raw.get("repository") or ""`) — write → read round-trips exactly.
- AC: `_process_fields_dict` (JSON API read), `_process_fields_from` (JSON API
  write), `_process_fields_from_form` (HTML form submit) all carry the field
  through with the same `.strip()` convention as the other string fields.

**FR-4 — `ProcessAdmin` surfaces repository options.**
- AC: `ProcessAdmin` gains an abstract `repository_names() -> tuple[str,
  ...]`, mirroring `check_names()`/`sink_kinds()`, so `api/` never imports
  `RepositoryRegistry` or a driver to populate the dropdown.
- AC: `FilesystemProcessAdmin` takes an optional `registry: RepositoryRegistry
  | None = None` constructor argument; `repository_names()` returns
  `tuple(sorted(registry.names()))` or `()` when no registry was supplied.
  The same registry backs `known_repositories` in `write()`'s validation
  call.

**FR-5 — The Process editor renders and submits the field.**
- AC: `templates/admin/process_form.html` renders a control for "All
  repositories" vs. a specific repository chosen from `repository_options`
  (populated via `process_admin.repository_names()`), using the same
  segmented-control idiom the form already uses for cadence/target
  kind/dedup/sink — not the orphaned checkbox-list shape from #108.
  Submitting "all" round-trips to an empty `repository` field; submitting a
  specific choice round-trips to that name.
- AC: the interaction (toggle disables/enables the `<select>`, and the
  existing live summary line mentions the chosen repository) is implemented
  in the form's existing inline `<script>` block, consistent with how every
  other field on this form already behaves — not via a separately loaded
  file.
- AC: `src/harness/api/static/process_form.js` (dead: references
  `#repositories-field`/`scope`/`repositories` that exist nowhere in the
  DOM, and is loaded by no template) is deleted, not left dangling.
- AC: an unknown-repository submission surfaces under the repository field's
  own inline error, the same pattern `errors.target`/`errors.check` already
  use.

**FR-6 — Wiring threads the registry into both compile paths.**
- AC: `app.build()` gains an optional `repository_registry: RepositoryRegistry
  | None = None` parameter, used only to compute `known_repositories` for its
  internal `FilesystemProcessRepository(...).build(...)` call — no other
  behavior changes.
- AC: `cli.serve()` gains an optional `registry: RepositoryRegistry | None =
  None` parameter (existing callers/tests that omit it are unaffected),
  passed straight into `FilesystemProcessAdmin(..., registry=registry)`
  alongside the existing `checks=` wiring.
- AC: `cli._run()` passes its already-constructed `registry` into both the
  `build(...)` call (`repository_registry=registry`) and the `serve(...)`
  call (`registry=registry`) — the same `FilesystemRepositoryRegistry`
  instance backs validation everywhere in a real run.

**FR-7 — Precedence is preserved end to end.**
- AC: a `github-issues`-backed process that also declares a process-level
  `"repository"` still produces tasks carrying *each issue's own* repo (the
  observation wins), never the process default — proven by a test that sets
  both and asserts the observation's value comes through.
- AC: an `always`/`command`/`fs-files`/`disk-threshold`-backed process with a
  declared `"repository"` and no observation-level repo produces tasks
  carrying the process's repository.
- AC: a process with no `"repository"` and a check whose observations never
  set one produces tasks with `repository=None` exactly as today (no
  behavior change for every existing process file in the repo/tests).

## Non-functional requirements

- No new I/O on the hot poll path: `known_repositories`/`repository_names()`
  read `repos.json` only at compile/admin time, never inside
  `ScheduledTrigger.poll()`.
- Backward compatible: every existing `processes/*.json` file, every existing
  `compile_process`/`FilesystemProcessRepository.build`/`app.build`/
  `cli.serve` call site (tests included) keeps working unchanged — the new
  parameters are all optional with defaults that reproduce today's behavior.
- Security: the repository name is validated against a fixed, machine-local
  allowlist (`repos.json`) before it can influence which worktree a task
  attaches to — no path or shell content ever flows from the form into the
  registry lookup (unchanged: `RepositoryRegistry.resolve` already owns that
  boundary).

## Data model

- **Process file** (`processes/<name>.json`): existing `trigger`/`action`/
  `target`/`dedup`/`sink` keys, plus a new optional top-level
  `repository: string` key (a name, not a path).
- **`ProcessFields`** (`ports/process_admin.py`): existing fields plus
  `repository: str = ""`.
- **`ScheduledTrigger`**: unchanged — already has `repository: str | None`.
- **`Task`**: unchanged — already has `repository: str | None`
  (`obs.repository or self._repository`, unaffected by this change's
  mechanics, only by what value now reaches `self._repository`).

## Interfaces

- File schema: `processes/*.json` optional `"repository": "<name>"`.
- Port: `ProcessAdmin.repository_names() -> tuple[str, ...]` (new abstract
  method).
- Port: `ProcessFields.repository: str = ""` (new field).
- REST: `GET/PUT /processes/{name}` request/response bodies gain
  `"repository"`.
- HTML: `GET /admin/processes/new`, `GET/POST /admin/processes/{name}` render
  and accept a `repository` form field; page context gains
  `repository_options`.
- Function signatures gaining an optional parameter (all additive, no
  breaking change):
  - `compile_process(..., known_repositories: set[str] | None = None)`
  - `FilesystemProcessRepository.build(..., known_repositories: set[str] |
    None = None)`
  - `FilesystemProcessAdmin.__init__(..., registry: RepositoryRegistry |
    None = None)`
  - `app.build(..., repository_registry: RepositoryRegistry | None = None)`
  - `cli.serve(..., registry: RepositoryRegistry | None = None)`

## Dependencies and scope

Depends on the existing, unchanged `RepositoryRegistry`/
`FilesystemRepositoryRegistry` port+driver (`ports/repos.py`,
`drivers/fs_repos.py`, including its already-present `names()` method) and
the existing `compile_process`/`ScheduledTrigger` machinery — no new port,
no new driver.

**Out of scope:**
- Multi-repository fan-out from a single process (one process firing tasks
  across several repositories per occurrence) — `ScheduledTrigger`/`Task`
  only ever carry one repository name; that would need each `Observation` to
  carry its own repository (already how `github-issues` does it) rather than
  a process-wide default, or a new "one trigger per repo" expansion at
  compile time. Flagged as a real follow-up, not built here.
- Retrofitting bare `triggers/*.json` (not Processes) with the same field —
  the task and #108 both scope this to Processes only.
- Any change to `github-issues`/`github-conflicts`, which already resolve
  their own per-repo behavior from the registry independent of this field.
- Timezone or other unrelated process-form fields.

## Rough plan

1. **Port** (`ports/process_admin.py`): add `ProcessFields.repository: str =
   ""`; add abstract `ProcessAdmin.repository_names()`.
2. **Driver** (`drivers/fs_processes.py`): add a `_parse_repository` validator
   (mirrors `_parse_dedup`/`_parse_sink`); extend `ProcessValidationError`'s
   documented `field` enum with `repository`; thread `known_repositories`
   through `compile_process` → `FilesystemProcessRepository.build` →
   `_build_one`; update `_raw_from_fields`/`_fields_from_raw` to round-trip
   `repository`; give `FilesystemProcessAdmin` a `registry` constructor arg,
   implement `repository_names()`, and pass `known_repositories` into its
   `write()`'s `compile_process` call.
3. **Wiring — `app.py`**: add `repository_registry` parameter to `build()`;
   compute and pass `known_repositories` into the internal
   `FilesystemProcessRepository(...).build(...)` call.
4. **Wiring — `cli.py`**: add `registry` parameter to `serve()`; pass it into
   `FilesystemProcessAdmin(...)`; pass `repository_registry=registry` into
   the `build(...)` call and `registry=registry` into the `serve(...)` call
   inside `_run()`.
5. **API** (`api/routes.py`): extend `_process_fields_dict`,
   `_process_fields_from`, `_process_fields_from_form`,
   `_process_form_context`, `_NEW_PROCESS_FIELDS` with `repository`; add
   `repository_options=process_admin.repository_names()` where
   `_process_form_response` is built.
6. **Template** (`templates/admin/process_form.html`): add the repository
   control (segmented "All repositories" / "Specific repository" +
   conditional `<select>` from `repository_options`), following the file's
   existing pattern; extend the inline `<script>` block's toggle/summary
   logic to cover it.
7. **Cleanup**: delete `src/harness/api/static/process_form.js` (superseded,
   never loaded by any template, incompatible multi-repo shape).
8. **Tests**: extend/add coverage in `test_fs_processes.py` (parser: valid /
   absent / unknown / non-string `repository`; `FilesystemProcessRepository
   .build` with/without `known_repositories`), `test_fs_process_admin.py`
   (`repository_names()`, `write` success/validation-failure, read
   round-trip), `test_process_admin_api.py`/routes tests (JSON + form
   payloads carry `repository`), `test_processes_e2e.py` or
   `test_triage_process_e2e.py` (precedence: observation-supplied repository
   beats the process default; process default applies when the check has
   none; no field at all behaves exactly as before), and `test_cli.py`
   /`test_app.py` wiring tests for the new optional `registry`/
   `repository_registry` parameters.
9. **Docs**: one-line addition to `ports/process_admin.py`'s
   `repository_names()` docstring and the `ProcessFields` docstring, mirroring
   how `sink_kinds()`/`check_names()` are already documented — no CLAUDE.md
   invariant changes expected (this doesn't touch dispatcher/consumer/router
   or add a new port, so no new numbered invariant is needed; a design-review
   pass should confirm that read).

## Open questions

- **Single repository vs. multi-repo fan-out per process.** Resolved for this
  plan as *single*, matching `ScheduledTrigger`/`Task`'s existing
  one-repository-per-task shape, and explicitly *not* the checkbox-list
  ("select several repos") shape PR #108's dead JS implied. If multi-repo
  fan-out is actually wanted, it's a bigger increment (observations
  multiplying per repo) and should be scoped separately — flagging for
  confirmation before design.
- **Strictness of repository validation at admin-save time.** This plan
  makes `repository` validation strict wherever a registry is available
  (unlike `target`, which stays lenient at admin-save time because the
  served-workflow set genuinely isn't known there). Since `repos.json` is
  static and available in both places, this asymmetry seems justified, but
  it's a deliberate deviation from the `target` precedent worth the design
  step double-checking.
- **UI control shape.** Defaulted to a segmented "All / Specific" toggle plus
  a `<select>`, reusing the form's existing idiom, rather than reviving
  `process_form.js`'s checkbox list or a bare unstyled `<select>`. Confirm in
  design.
