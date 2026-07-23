# Process admin UI — a structured editor for `processes/*.json`

Status: draft
Date: 2026-07-22

## Goal

Processes (ADR-0015) are declared as `processes/*.json` but have no UI: an
operator must hand-edit JSON on disk. Agents and Workflows already have admin
editors on the board (`AgentAdmin`/`WorkflowAdmin`, invariant #33); this spec
gives Processes the **same treatment as a structured form** — the "user
assembles a process in the dashboard" surface the processes brainstorm asked
for.

The thesis, exactly as for those two: **a write-side admin port beside the
read-side, wired only in `cli.serve()`, touched by `api/` and nothing else.**
The runtime still reads processes by compiling them into `ScheduledTrigger`s at
startup (`FilesystemProcessRepository`); the operator edits the same files
through `ProcessAdmin`. No orchestration module learns anything new.

Unlike a workflow (kept as raw text, edited in a `<textarea>`), a Process is a
small structured aggregate, so its editor is a **structured form** (mirroring the
Agent form): interval field, a **check dropdown**, a params box, a **target
dropdown**, a sink dropdown, a dedup dropdown.

## What's new

- **`ports/process_admin.py`** — `ProcessAdmin` (ABC), `ProcessFields` (the
  typed, form-editable shape), `ProcessNotFound`, `ProcessAdminValidationError`
  (field → message, mirroring `AgentValidationError`). Two option-list verbs —
  `check_names()` and `sink_kinds()` — so the form's dropdowns are populated
  **through the port**, keeping `api/` free of any driver import (invariant #5).
- **`drivers/fs_processes.py`** — a refactor plus `FilesystemProcessAdmin`:
  - extract a module-level **`compile_process(name, raw, *, clock, checks,
    repository, worktree_root, known_targets, where)`** from `_build_one`, so the
    *identical* validation serves both the repository (compile every file at
    startup) and the admin (validate one submission before writing). This also
    fixes a latent gap: a check factory that raises (e.g. `disk-threshold` with
    missing `params`) is wrapped into a `ProcessValidationError` instead of
    surfacing a raw `KeyError`.
  - `ProcessValidationError` gains an optional **`field`** attribute so the admin
    can map a compile failure to the right form field; its `str()` message is
    unchanged (the repository still "names the offending file").
  - `FilesystemProcessAdmin(root)`: `list`/`read`/`write`/`delete` over the same
    `<root>/<name>.json` files the repository reads, plus `check_names()`
    (the `BUILTIN_CHECKS` keys) and `sink_kinds()` (`("none",)`).
- **`api/routes.py`** — process routes symmetric to agents: JSON (`GET
  /api/processes`, `GET/PUT/DELETE /api/processes/{name}`) and HTML (`/admin/
  processes`, `/admin/processes/new`, `/admin/processes/{name}`, the two POST
  handlers, and `POST /admin/processes/{name}/delete`). The target dropdown's
  options are the running board's workflows + steps (read from `BoardView`, the
  same source `_new_step_warnings` already uses); check/sink options come from
  the port.
- **Templates** — `admin/processes_list.html`, `admin/process_form.html`
  (mirroring the agent pair), and a **Processes** nav entry in `_nav.html`.
- **`api/app.py`** — `_EmptyProcessAdmin` no-op default + a `process_admin`
  parameter on `create_app`, passed into both routers.
- **`cli.serve()`** — wires `FilesystemProcessAdmin(layout.processes)`; a
  `processes` property on `HarnessLayout`.

## `ProcessFields` — the editable shape

```python
@dataclass(frozen=True)
class ProcessFields:
    interval: str            # "1h"
    check: str               # "always" | "disk-threshold" | ...
    target_kind: str         # "workflow" | "step"
    target: str              # the named workflow or step
    params: dict = {}        # the check's params (parsed from the form's JSON box)
    sink_kind: str = "none"
    dedup: str = "per-interval"
```

`read` reconstructs it from the file (`trigger.interval`, `action.check`,
`action.params`, `target.{workflow|step}`, `sink.kind`, `dedup`); `write`
assembles the nested `processes/*.json` back from it. `name` is always the URL
path, never a form field, so a body cannot rename a process behind the path.

## Validation: one compiler, two callers

`FilesystemProcessAdmin.write` builds the nested dict from `ProcessFields` and
runs it through `compile_process(..., known_targets=None)` — the *same* code the
repository runs at startup. A `ProcessValidationError` (with its `field`) is
mapped into `ProcessAdminValidationError({field: message})`; nothing is written
unless it compiles. `known_targets` is `None` for the admin (a filesystem driver
doesn't know the running harness's served workflows/queues — same reason
`WorkflowAdmin` doesn't, and why "unknown target" is surfaced as a soft form
hint from `BoardView`, not a hard driver error).

## Invariants — refined (not new)

Invariant #33 already governs admin ports; it is **extended** to name the third:

> 33. **`AgentAdmin`/`WorkflowAdmin`/`ProcessAdmin` are unknown to the dispatcher
>     and consumer.** They are UI-facing admin ports … `api/` touches only these
>     admin ports; the filesystem drivers (`FilesystemAgentAdmin`,
>     `FilesystemWorkflowAdmin`, `FilesystemProcessAdmin`) are wired exclusively
>     in `cli.py`'s `serve()`. Guarded by `test_architecture.py`'s glob checks.

No other invariant changes. The Process runtime story (ADR-0015, invariants
#39–#40) is untouched — this is purely the write-side editor for the same files.

## Diagram

Add a **Process** part to the hand-curated architecture explorer
(`src/harness_docs_site/architecture.py`), grounded in ADR-0015, connected by an
edge so it is not an orphan (a Process compiles to a scheduled `TaskSource` that
feeds the inbox via `SourcePoller`). `validate()` (run in the test suite) must
stay green.

## Out of scope

- A real sink reflector / the `github-issues` action (still the ADR-0015 seams).
- A raw-JSON fallback editor (the structured form is the chosen shape).
- Live-reload semantics beyond what agents/workflows already have (a saved
  process is picked up on the next run, like a saved agent — no hot restart).

## Completion check

1. `FilesystemProcessAdmin` round-trips a process: `write(name, fields)` then
   `read(name)` returns equal fields; the file on disk is the nested
   `processes/*.json` the repository compiles without error.
2. `write` raises `ProcessAdminValidationError` (right field key) on a bad
   interval, unknown check, ill-formed target, unknown dedup, non-`none` sink,
   and a check whose factory rejects the params (e.g. `disk-threshold` missing
   `path`).
3. `compile_process` is the single validator: the repository's existing
   `test_fs_processes.py` cases still pass, and a `disk-threshold` file missing
   `params` now fails as a `ProcessValidationError`, not a `KeyError`.
4. API: `/admin/processes` lists; `/admin/processes/new` + POST creates;
   `/admin/processes/{name}` edits + POST updates; delete removes; a bad
   submission re-renders the form with the field error and writes nothing. The
   check/target/sink/dedup dropdowns are populated.
5. `create_app` with no `process_admin` still boots (the `_EmptyProcessAdmin`
   lists nothing, refuses writes); `cli.serve()` wires the real one.
6. `api/` imports no driver; dispatcher/consumer import neither
   `ports.process_admin` nor `drivers.fs_processes`; `test_claude_md_module_map`
   passes (CLAUDE.md names `process_admin`); the architecture-model `validate()`
   passes with the new Process part.
