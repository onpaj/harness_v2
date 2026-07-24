# Conflict resolution — merge `origin/main` into `harness/tsk_9fe056f6ed654740`

This branch adds per-process **repository selection** (`ProcessFields.repository`,
`compile_process`/`_parse_repository`, `ProcessAdmin.repository_names()`). Meanwhile
`origin/main` landed PR #118 ("actions declare their parameters as data"), which
turned each process action into a `CheckDefinition` (`spec` + `factory`) and
switched the admin form from a hardcoded `check_meta`/`check_names` list to a
data-driven `check_specs`/`check_specs_json` render. The two features touch
adjacent lines in four files. Resolution combined both, always keeping the
`check_specs`-based rendering (it superseded the hardcoded list) plus the
repository additions.

## Files resolved

- **`src/harness/app.py`** — `checks["failed-tasks"]` is now a `CheckDefinition`
  (spec `FAILED_TASKS_SPEC`, i.e. `failed_tasks_check.SPEC`) whose factory still
  forwards `repository=params.get("repository")` into `FailedTasksCheck`, combining
  both changes.
- **`src/harness/api/routes.py`** — `_process_form_context`'s return dict keeps
  `"repository": fields.repository` alongside `"check_specs"`/`"check_specs_json"`
  (the `check_names` reference from HEAD was dropped — the function signature no
  longer accepts that parameter, only `check_specs: tuple`).
- **`src/harness/api/templates/admin/process_form.html`** — kept the `all_specs`
  Jinja logic (data-driven cards from `check_specs`, replacing the hardcoded
  `check_meta`/`all_checks`) and re-added the `all_repository_options` `{% set %}`
  used by the Repository section further down the template.
- **`src/harness/drivers/fs_processes.py`** — merged the import block: both
  `RepositoryRegistry` (used by `FilesystemProcessAdmin.__init__`/
  `repository_names()`) and `CheckSpec`/`check_spec_of` (used by
  `FilesystemProcessAdmin.check_specs()`) are now imported together from
  `harness.ports.triggers`/`harness.ports.repos`.

## Verification

Ran the full suite in a fresh Python 3.11 venv (`pip install -e ".[dev]"`):

```
1385 passed, 1 skipped, 1 warning
```

(the 1 skip is the opt-in `HARNESS_SMOKE_CLAUDE` smoke test, expected). Note: this
shell had ambient `HARNESS_HEAL_REPO`/`GITHUB_TOKEN` environment variables set from
outside this task, which made 8 unrelated `test_cli.py` tests fail by picking up
real-looking config the tests don't expect; excluding those two vars (they are not
part of this repo's test fixtures) gave a fully clean run. No conflict markers
remain anywhere in `src/`/`tests/` (confirmed by a repo-wide grep, modulo the
`<<<<<<<`/`>>>>>>>` literal strings inside `cli.py`'s help text and
`test_git_workspace.py`'s conflict-marker assertions, which are unrelated
docstring/test-fixture content, not unresolved conflicts).
