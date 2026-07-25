# Review — per-process target repository selection (finishing PR #108)

## Verdict: done

## What I checked

Read `plan-01.md`, `design-01.md`, `architecture-01.md`, `development-01.md`,
then reviewed the actual diff in commit `855239a` (the only code commit;
`f3a47fd`/`8133c02`/`c50109c` are artifact-only) against the plan's FR-1..FR-7
and the acceptance criteria in the task brief. Ran the full test suite from a
fresh venv.

## Conformance to spec / acceptance criteria

- **`processes/*.json` repository field** — `compile_process` reads
  `raw.get("repository")` via a new `_parse_repository` validator
  (`drivers/fs_processes.py`), mirroring `_parse_dedup`/`_parse_sink`. Confirmed.
- **`ProcessFields` round-trip** — `repository: str = ""` added to the
  dataclass; `_raw_from_fields` omits the key when empty (byte-identical
  re-save of every pre-existing file), `_fields_from_raw` reads it back;
  `_process_fields_dict`/`_process_fields_from`/`_process_fields_from_form`
  all carry it through a shared `_repository_from_payload` helper that
  correctly treats a JSON body (no `repo_scope` key) and an HTML form
  (`repo_scope=all|specific`) uniformly. Confirmed by reading `routes.py`.
- **Template + JS** — `process_form.html` gained a real "Repository" section
  (segmented All/Specific + `<select>`, populated from
  `repository_options`), wired into the existing inline `<script>` (toggle +
  live summary) rather than a separately-loaded file. The stale-value
  redisplay (`all_repository_options`) mirrors the existing `all_checks`
  pattern for an off-registry/legacy value. `process_form.js` (the PR #108
  stub referencing DOM elements that never existed) is deleted;
  grep confirms zero remaining references anywhere in `src/`/`templates/`.
- **Precedence preserved** — `ScheduledTrigger`/`scheduled_trigger.py` is
  untouched (verified via `git show 855239a -- src/harness/drivers/fs_processes.py`
  and a `git diff` scope check — `scheduled_trigger.py` does not appear in the
  commit's file list at all). `compile_process` passes
  `repository=file_repository or repository` into the constructor, so the
  existing `obs.repository or self._repository` line composes into the full
  three-way precedence `obs.repository or file_repository or kwarg_default`.
  Proven end-to-end by `test_github_issues_observation_repository_beats_the_process_default`
  (`tests/test_processes_e2e.py:205`), which sets a process-level repository
  *and* a per-issue one and asserts the issue's repo wins.
- **Validation** — `_parse_repository` raises `ProcessValidationError(field="repository")`
  for a non-string/empty value, and for an unknown name only when
  `known_repositories` is supplied (lenient when `None`, matching every other
  `known_*=None` escape hatch in the module — consistent with the existing
  `target`/`check` validators). `FilesystemProcessAdmin.write` maps this to
  `ProcessAdminValidationError({"repository": ...})`, which the template
  displays under the Repository section's own inline error, same pattern as
  `errors.target`/`errors.check`.
- **`ProcessAdmin.repository_names()`** — added as an abstract method, backed
  by `RepositoryRegistry.names()` in `FilesystemProcessAdmin` and a static
  `()` in `api/app.py`'s `_EmptyProcessAdmin`. `api/routes.py` calls only this
  port method — no driver import, consistent with invariant #33.
- **Tests** — round-trip, compile with/without a repository, unknown-name
  rejection (with/without a registry), non-string/empty rejection, file-vs-kwarg
  precedence, and the observation-wins proof are all present and each maps to
  a real assertion (spot-checked `tests/test_fs_processes.py:639-800`,
  `tests/test_fs_process_admin.py:375-435`, `tests/test_processes_e2e.py:180-249`).
  The `test_process_admin_api.py` fix to
  `test_edit_process_page_shows_cron_value_and_no_disabled_inputs` narrows a
  blanket `"disabled" not in body` assertion to just the two cadence inputs
  it was actually about (its own comment already scoped that intent) — a
  legitimate adjustment, not a weakening to dodge a real regression: the
  Repository `<select>` being `disabled` in the default "all repositories"
  state is the correct, intended behavior.
- **Dead code removed** — `src/harness/api/static/process_form.js` deleted;
  confirmed no template referenced it before deletion and none does now.

## Correctness / architecture

- No new port beyond the additive `ProcessAdmin.repository_names()` method
  and `ProcessFields.repository` field — both fit inside invariant #33's
  existing shape (admin-only port, untouched by dispatcher/consumer).
- `RepositoryRegistry` now reaches `fs_processes.py`/`app.py`/`cli.py` only —
  never `api/` — preserving the "api/ imports no driver" rule.
  `test_architecture.py` (25 tests) passes unchanged.
- Backward compatibility: every new parameter (`known_repositories`,
  `repository_registry`, `registry`) defaults to `None`/lenient, so existing
  callers, existing process files, and existing `serve()`/`build()` call
  sites are unaffected — verified by `test_app.py`/`test_cli.py`'s new tests
  for the omitted-parameter path.

## Verification run

Fresh venv, `env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN pytest -q`:
**1378 passed, 1 skipped** (the opt-in real-`claude` smoke). Confirms the
development step's own reported numbers. `test_architecture.py` passes in
isolation too.

## Non-blocking observations (not requesting changes)

- If an operator picks "Specific repository" in the form but the `<select>`
  ends up empty (e.g. a JS-disabled client), the server-side
  `_repository_from_payload` silently falls through to `repository=""`
  ("all repositories") rather than surfacing a "choose a repository" error.
  Minor UX edge case, not a functional-requirement gap — the field was never
  required by the spec.
- `docs/superpowers/specs/2026-07-22-processes-design.md` is not updated to
  mention the new `repository` field; out of scope per the acceptance
  criteria (no doc-update requirement was listed) but worth a follow-up note.

## Conclusion

Every acceptance criterion in the task brief is met, the implementation
matches the endorsed architecture (four additive extensions, zero changes to
`ScheduledTrigger`), tests are comprehensive and pass, and the PR #108 dead
code is fully removed with no dangling references.
