# Development — per-process target repository selection (finishing PR #108)

Implements `plan-01.md`'s FR-1..FR-7 exactly as resolved by `design-01.md` and
endorsed by `architecture-01.md`: four additive extensions to existing seams,
zero changes to `ScheduledTrigger` itself, no new port/driver/invariant.

## What changed

**Port** — `src/harness/ports/process_admin.py`
- `ProcessFields` gains `repository: str = ""` ("" = all repositories).
- `ProcessAdmin` gains abstract `repository_names() -> tuple[str, ...]`,
  mirroring `check_names()`/`sink_kinds()`.

**Driver** — `src/harness/drivers/fs_processes.py`
- New `_parse_repository(where, repository, known_repositories)` validator
  (same shape as `_parse_dedup`/`_parse_sink`): `None` → no default; a
  non-empty string not in `known_repositories` (when supplied) → validation
  error; non-string/empty → validation error.
- `compile_process` gains `known_repositories: set[str] | None = None`, reads
  the file's own `"repository"` key, and passes
  `repository=file_repository or repository` into `ScheduledTrigger` — the
  `repository=` kwarg becomes a fallback default, the file wins when present.
  `ScheduledTrigger._task_for`'s existing `obs.repository or self._repository`
  is untouched, so the full precedence is
  `obs.repository or file_repository or repository_kwarg`.
- `ProcessValidationError`'s documented `field` enum extended with
  `repository`.
- `FilesystemProcessRepository.build`/`_build_one` thread `known_repositories`
  through to `compile_process`, same shape as `known_targets`.
- `FilesystemProcessAdmin` gains a `registry: RepositoryRegistry | None`
  constructor param; `write()` computes `known_repositories` from it (strict
  whenever a registry is wired, matching design's resolution of open question
  #2); `repository_names()` returns `tuple(sorted(registry.names()))` or `()`.
- `_raw_from_fields`/`_fields_from_raw` round-trip `repository` — omitted from
  the file when empty, so every pre-existing process file re-saves
  byte-identically.

**Wiring**
- `src/harness/app.py`: `build()` gains `repository_registry:
  RepositoryRegistry | None = None`; computes `known_repositories` once and
  passes it into the internal `FilesystemProcessRepository(...).build(...)`
  call.
- `src/harness/cli.py`: `serve()` gains `registry: RepositoryRegistry | None =
  None`, forwarded into `FilesystemProcessAdmin(...)`; `_run()` passes its
  already-constructed `registry` into both `build(repository_registry=registry)`
  and `serve(registry=registry)`.

**API** — `src/harness/api/routes.py`
- New `_repository_from_payload(payload)` helper: resolves "all vs. specific"
  server-side from an optional `repo_scope` key (HTML form only — a JSON body
  has no `repo_scope`, so it reads `repository` as-is).
- `_process_fields_dict`, `_process_fields_from`, `_process_fields_from_form`,
  `_process_form_context` all carry `repository`; `_process_form_context`
  gains `repository_options: tuple[str, ...]`; `_process_form_response` passes
  `repository_options=process_admin.repository_names()`.
- `src/harness/api/app.py`: `_EmptyProcessAdmin.repository_names()` added
  (`ProcessAdmin` ABC would otherwise break this and every other subclass —
  none other exists besides `FilesystemProcessAdmin`, both updated).

**Template** — `src/harness/api/templates/admin/process_form.html`
- New "Repository" section between Target and Options: a segmented "All
  repositories" / "Specific repository" toggle (`repo_scope` radio, not
  persisted) plus a `<select name="repository">` populated from
  `repository_options`, grafting in a stale/off-registry current value the
  same way `all_checks` already does. Options section headers renumbered
  (`{{5 if is_new else 4}}` → repository, `{{6 if is_new else 5}}` → options).
- Inline `<script>` extended (no separately-loaded JS): `syncRepoScope()`
  disables/enables the `<select>` on toggle; the live summary line appends
  `, targeting repo <NAME>` when specific + chosen.

**Cleanup**
- Deleted `src/harness/api/static/process_form.js` — the PR #108 stub,
  referencing `#repositories-field`/`scope`/`repositories`, none of which
  exist in the DOM under any design; loaded by no template; grep-confirmed
  zero references anywhere before deletion.

## Precedence (unchanged core logic, composed correctly)

```
effective repository on a produced task
    = obs.repository            (a check's own observation, e.g. github-issues)
      or file_repository        (the process's own "repository" key)
      or repository_kwarg       (compile_process/build()'s caller default)
      or None
```

`ScheduledTrigger._task_for`'s `obs.repository or self._repository` line was
never touched — the three-way precedence falls out purely from what
`compile_process` passes as `self._repository`'s source value.

## Tests added

- `tests/test_fs_processes.py`: `_parse_repository`/`compile_process` cases —
  absent, valid, unknown-with-registry, non-string, empty-string, file
  overriding the `repository=` kwarg, kwarg applying when the file has none,
  and the observation-wins precedence proof (a trivial in-file `Check`
  stamping its own `Observation.repository`).
- `tests/test_fs_process_admin.py`: round-trip (present/absent),
  `repository_names()` with/without a wired registry, `write()`
  success/rejection against a wired registry, lenient-when-no-registry.
- `tests/test_process_admin_api.py`: JSON PUT/GET carry `repository`; HTML
  form create with `repo_scope=specific`/`all`, a stale select value being
  ignored when scope is "all", redisplay after a params error preserving
  `repo_scope=specific`, and the "new process" page rendering registry-backed
  options. Also fixed the pre-existing
  `test_edit_process_page_shows_cron_value_and_no_disabled_inputs`, whose
  blanket `"disabled" not in body` assertion no longer holds now that the
  Repository section's `<select>` is legitimately `disabled` in the "all"
  state — narrowed to check only the cadence inputs it was actually about
  (its own comment already scoped the intent to those two fields).
- `tests/test_processes_e2e.py`: full `app.build()` → done-task pipeline
  proving a declared `repository` reaches the finished task, and a
  `github-issues`-backed process with both a process-level `repository` and
  a per-issue one proving the observation wins.
- `tests/test_app.py`: `build(repository_registry=...)` rejects an
  unregistered repository name; omitting the param stays lenient
  (backward-compatible default).
- `tests/test_cli.py`: `serve(registry=...)` reaches `FilesystemProcessAdmin`
  (`repository_names()` reflects it); `_run()` forwards the same constructed
  `FilesystemRepositoryRegistry` instance into both `build(...)` and
  `serve(...)`. Also updated ~21 pre-existing `fake_serve` stand-ins across
  the file to accept the new `registry=None` keyword `serve()` now has, so
  `_run()`'s new `serve(..., registry=registry)` call doesn't `TypeError`
  against them.

## How to verify

```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN .venv/bin/pytest -q
```

(`-u HARNESS_HEAL_REPO -u GITHUB_TOKEN` only matters on this particular
machine, which has both set in its real shell environment for its own live
harness service — unrelated to this change; 8 tests fail under those env
vars on a completely clean `main` checkout too.)

Full suite: **1378 passed, 1 skipped** (the skip is the opt-in
`HARNESS_SMOKE_CLAUDE=1` real-`claude` smoke). `tests/test_architecture.py`
(25 tests) passes unchanged — no new port/invariant, `RepositoryRegistry` only
reaches `fs_processes.py`/`app.py`/`cli.py`, never `api/`.

Manual smoke (also run during development, not just asserted in tests): built
a `FilesystemProcessAdmin` with a real `FilesystemRepositoryRegistry` over a
temp `repos.json`, hit `GET /admin/processes/new` (rendered "All
repositories" + the registered name), then `POST /admin/processes` with
`repo_scope=specific&repository=harness_v2` and confirmed the saved
`ProcessFields.repository == "harness_v2"`.

## Acceptance criteria — status

- [x] `processes/*.json` repository field, read by `compile_process`.
- [x] `ProcessFields` carries it; round-trips through
      `_raw_from_fields`/`_fields_from_raw`/API+form parsers.
- [x] Editor template renders the control; `process_form.js` deleted (not
      revived — its multi-repo checkbox shape was superseded by design).
- [x] `ScheduledTrigger` stamps it with observation-wins precedence preserved
      (zero changes to `scheduled_trigger.py`).
- [x] Validation via `compile_process` → `ProcessValidationError(field=
      "repository")`, mapped by `FilesystemProcessAdmin.write` to
      `ProcessAdminValidationError`.
- [x] `ProcessAdmin.repository_names()` — `api/` imports no driver.
- [x] Tests cover round-trip, compilation with/without a repository, and
      observation-wins precedence (plus registry-strict-vs-lenient validation
      and a full e2e pipeline proof).
- [x] PR #108's orphaned JS removed; template/routes are the single source of
      truth; no dangling references remain (grep-verified before deletion).
