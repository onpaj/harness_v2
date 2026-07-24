# Design: Rename 'default' workflow to 'development'

No user interface is involved — this is a CLI/config/data-file change. The
UX/UI section is omitted.

## Component design

### `cli.py` — constants

- `DEFAULT_WORKFLOW: str = "development"` (was `"default"`).
- `DEFAULT_DEFINITION["name"] = "development"` (the `"start"`/`"transitions"`
  keys are untouched — byte-for-byte same topology).
- Comments that currently say "the default workflow" in a way that reads as
  the *name* `"default"` are reworded to say "the `development` workflow" or
  "the default value of `--workflow`" as appropriate, so "default" only ever
  means "the flag's default value," never a specific workflow's name.

### New helper: `_migrate_legacy_workflow(layout, workflow_name)`

Single new function in `cli.py`, next to `_write_default_agents` /
`_write_default_repos` (same style: small, filesystem-only, no return value).

```python
def _migrate_legacy_workflow(layout: HarnessLayout, workflow_name: str) -> None:
    """Copy workflows/default.json forward to workflows/development.json.

    Fires only when the caller is using the *default* workflow name (not an
    operator's explicit --workflow default), the new file doesn't exist yet,
    and the legacy file does. The legacy file is left in place, untouched —
    a task created before the upgrade still resolves it by name."""
    if workflow_name != DEFAULT_WORKFLOW:
        return
    legacy = layout.workflows / "default.json"
    current = layout.workflows / f"{DEFAULT_WORKFLOW}.json"
    if current.exists() or not legacy.exists():
        return
    current.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
```

**Call sites** — both before the definition file is read/written:

- `_init(args)`: call `_migrate_legacy_workflow(layout, args.workflow)`
  immediately after `layout.workflows.mkdir(...)` and before the
  `definition_path.exists()` check. This way, if the migration just created
  `development.json`, the subsequent "create it if missing" block is a no-op
  (preserves the operator's custom content instead of overwriting it with
  `DEFAULT_DEFINITION`), satisfying FR-2/AC1.
- `_run(args)`: call `_migrate_legacy_workflow(layout, args.workflow)` right
  after `layout = HarnessLayout(root)`, before `build(root, args.workflow,
  ...)` — so a service restart with no prior `harness init` still finds
  `development.json` (FR-2/AC3). `_run` never writes `DEFAULT_DEFINITION`
  itself, so this call is the only thing standing between it and
  `WorkflowNotFound`.

Because the guard is `workflow_name != DEFAULT_WORKFLOW: return`, an operator
who explicitly passes `--workflow default` never triggers the copy — the
function only fires when the caller is resolving *the* default workflow,
regardless of whether that argument came from argparse's own default or was
typed by the user (FR-2/AC5). This also means a brand-new root, where neither
file exists, is a no-op: `legacy.exists()` is false, so nothing is written and
no `default.json` is manufactured (FR-2/AC4).

Both call sites pass `layout` (already constructed) and `args.workflow` (or
`args.github_workflow`, see below) — no new parameters threaded through
`build()` or `HarnessLayout`.

### `_github_sources` / `GithubTaskSource`

`args.github_workflow` defaults to `DEFAULT_WORKFLOW` already (`cli.py:775`,
unchanged reference, now resolves to `"development"`). No migration call is
needed on this path: `_run` already migrates once per invocation using
`args.workflow` before `_github_sources` is built, and every `GithubTaskSource`
constructed from `args.github_workflow` reads the same `workflows/` directory
that `_run`'s migration just populated. If a future operator sets
`--github-workflow` to a name different from `--workflow`, that's an explicit
custom choice — same non-goal as `--workflow default` above, no migration
owed to it.

### `drivers/github_source.py`

`GithubTaskSource.__init__`'s `workflow: str = "default"` parameter default
becomes `workflow: str = "development"` (`github_source.py:33`). This is the
constructor's own fallback when a caller doesn't pass `workflow=...`
explicitly (`cli.py` always passes it explicitly via `args.github_workflow`,
so this only affects direct construction, e.g. in tests).

### `drivers/memory.py`

The fake/test double's matching parameter (`memory.py:212`,
`workflow: str = "default"`) becomes `workflow: str = "development"` — kept in
lockstep with the real driver's default so a test that omits `workflow=`
exercises the same default value in both.

## Data schemas

### Workflow definition file (unchanged shape, new filename + `name` value)

`workflows/development.json` (was `workflows/default.json`):

```json
{
  "name": "development",
  "start": "plan",
  "transitions": [
    {"from": "plan", "on": "done", "to": "design"},
    {"from": "design", "on": "done", "to": "architecture"},
    {"from": "architecture", "on": "done", "to": "development"},
    {"from": "development", "on": "done", "to": "review"},
    {"from": "review", "on": "done", "to": "land"},
    {"from": "land", "on": "done", "to": "end"},
    {"from": "review", "on": "request_changes", "to": "development"}
  ]
}
```

`Workflow.name` (`models.py`) is unaffected as a type — it's still a plain
string, read from the JSON's `"name"` key with the filename stem as fallback
(`fs_workflows.py:60-61`). No schema migration is needed for the file's
*shape*, only its location and the value of one field.

### `workflows/default.json` (legacy, post-migration)

Left on disk, byte-for-byte as the operator last edited it. Not read by any
new code path — only reachable if a `Task.workflow_template` (or an explicit
`--workflow default`) still names it, exactly as today. No schema change.

### `Task.workflow_template`

No type or shape change — still an opaque string matched against
`workflows/<name>.json`. Only the value that flows in when nothing else is
specified changes, from `"default"` to `"development"`:

- `harness submit` with no `--workflow`: `argparse` default → `"development"`.
- `GithubTaskSource`-originated tasks with no explicit workflow: same.

### README worked example

`README.md`'s `## Workflow` JSON block (`README.md:190-197`) updates
`"name": "default"` → `"name": "development"`, matching the shipped
`DEFAULT_DEFINITION`. Surrounding prose that refers to "the default workflow"
is reworded only where it names the workflow itself (not where it's talking
about `--workflow`'s default value, which remains correctly described as
"the default").

## Interfaces (no new surface)

- CLI flags unchanged in name and type (`--workflow`, `--github-workflow`,
  `--root`) — only their default *value* changes.
- No new CLI flags, no new HTTP routes, no new event payload fields. The
  migration is filesystem-only housekeeping invisible to `BoardView` /
  `ArtifactView`, which only ever echo whatever string is already stored on
  the task.
