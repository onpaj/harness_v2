# Resolve merge conflict on PR #108

## Conflicted files (git markers)

`CLAUDE.md`, `src/harness/api/app.py`, `src/harness/cli.py`,
`src/harness/drivers/fs_processes.py`, `tests/test_cli.py`,
`tests/test_fs_processes.py`.

## What the conflict actually was

This branch's own earlier work (commits `dc783db`..`94f6b8e`) built an
additive "Process" concept from scratch: `models.Process`/`ProcessSummary`,
`ports/processes.py` (`ProcessRepository`/`ProcessAdmin`), `drivers/
fs_processes.py`, and an editor at `api/process_routes.py` (`GET /processes`,
`GET`/`POST /processes/{name}/edit`). Its design: a Process is a `Workflow`
plus a `repositories` scope, stored as an extra key in the same workflow
JSON file.

Independently, `origin/main` had since grown a completely different,
already-shipped "Process" feature (ADR-0015, generic-triggers spec): a
Process is a top-level authoring aggregate of `trigger`/`action`/`target`/
`sink` that compiles into a `ScheduledTrigger`. It reuses the *same* class
names (`FilesystemProcessRepository`, `FilesystemProcessAdmin`) and the same
module path (`drivers/fs_processes.py`), but with an incompatible shape
(`ports/process_admin.py`, `ProcessFields`, `compile_process`), and it is
already fully wired into `api/routes.py` (`/admin/processes`) and `cli.py`
(`serve()`, `_process_sources`), with its own extensive test suite
(`tests/test_fs_processes.py`, `tests/test_fs_process_admin.py`,
`tests/test_process_admin_api.py`, `tests/test_processes_e2e.py`, ...).

So this was a genuine feature collision, not just a textual one: two
unrelated designs sharing one class name. `origin/main`'s version is the one
actually integrated everywhere else in the merged tree (nav bar links only to
`/admin/processes`, `routes.py`'s admin router already expects the new
`ProcessAdmin` shape), so it is the surviving design.

## Resolution

- Every marked conflict resolved in favor of `origin/main`'s Process design
  (`ports/process_admin.py`-shaped `ProcessAdmin`, trigger/action/target/sink
  `compile_process`, `FilesystemProcessRepository`/`FilesystemProcessAdmin`
  as in `origin/main`).
- Followed through on the parts of this branch's superseded design that
  git's line-based merge auto-combined *without* flagging a conflict (because
  the two additions landed on non-overlapping lines), which would otherwise
  have left a broken tree:
  - `src/harness/api/app.py`: dropped the leftover `processes`/`repos`
    params, `_NullProcessAdmin`, `_EmptyRepositoryRegistry`, and the
    `build_process_router` import/registration.
  - `src/harness/cli.py`: dropped the stray
    `FilesystemProcessAdmin(layout.workflows, registry)` line and the
    `processes=processes, repos=registry` kwargs passed into `create_app`
    inside `serve()` (undefined names once the old `serve()` params were
    gone).
  - Deleted the now-dead old-design-only files: `src/harness/ports/
    processes.py`, `src/harness/api/process_routes.py`,
    `src/harness/api/templates/process_edit.html`, `src/harness/api/
    templates/processes_list.html` (the root-level one; `admin/
    processes_list.html` is the surviving one), `tests/
    test_process_repository_scoping_e2e.py`, `tests/
    test_api_process_routes.py`.
  - Removed `models.Process`/`ProcessSummary`/`RepositoryScope` and their
    tests in `tests/test_models.py` (only consumers were the deleted files).
  - Removed the stale `CLAUDE.md` bullet describing the old
    `ports/processes.py` design.
- Verified: `src/harness/models.py`, `src/harness/api/app.py`,
  `src/harness/cli.py` are now byte-identical to `origin/main`;
  `drivers/fs_processes.py` and `tests/test_fs_processes.py` are
  `origin/main`'s versions in full.

## Verification

- No `<<<<<<<`/`=======`/`>>>>>>>` markers remain anywhere in the tree.
- `.venv/bin/pytest -q`: **1221 passed, 1 skipped** (the skip is the opt-in
  `HARNESS_SMOKE_CLAUDE` real-`claude` smoke test).
- `tests/test_architecture.py` and `tests/test_claude_md_module_map.py` pass,
  confirming the architecture invariants and module-map documentation stay
  consistent after the deletions.
