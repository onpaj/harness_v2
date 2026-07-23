# Admin UI: agent catalog and workflow editor — architecture assessment

Reviews `.artifacts/tsk_92ca469924ef4323/plan-01.md` and `design-01.md` against
the actual state of the repository (not just the two documents). Verdict:
**the design is sound and ready to build as specified**, with the refinements
below folded in before or during implementation. I did not find a reason to
change the overall shape — two new read/write ports mirroring the existing
`BoardView`/`TaskControl` split, filesystem drivers beside the existing
read-only ones, raw-JSON for workflows, structured form for agents. What
follows tightens the contract at the few points where the design was
imprecise about exception plumbing, route ordering and create-vs-exists
semantics, and removes one step of planned work that turns out to be
unnecessary.

## Alignment with existing patterns and integration points

I read the actual source, not just the plan/design's paraphrase of it, to
confirm every claim they rest on:

- **`FilesystemAgentCatalog.get`** (`drivers/fs_agents.py:36-75`) and
  **`FilesystemWorkflowRepository.get`** (`drivers/fs_workflows.py:25-62`)
  match the design's description exactly: JSON load → not-a-dict check →
  required-field check → per-item parse (`Outcome(...)` / `Transition(...)`)
  → construct. The extraction points the design names
  (`_parse_agent_spec`, `_parse_workflow`) line up cleanly with this
  structure — the refactor is genuinely mechanical.
- **`api/routes.py`** and **`api/app.py`** already establish the exact
  extension pattern the design proposes to reuse: `build_json_router`/
  `build_html_router` take ports as plain parameters, `create_app` supplies
  null-object defaults (`_EmptyArtifactView`, `_EmptyStageOutputView`,
  `_NullTaskControl`) so every existing caller keeps working. Adding
  `_EmptyAgentAdmin`/`_EmptyWorkflowAdmin` alongside these is a copy of an
  established idiom, not a new one.
- **`BoardView`** (`ports/board.py`) and **`BoardProjection`**
  (`projection.py`) confirm the design's key inference: `Board.columns`
  really is built from the *same* `workflow.steps()` call that `app.py::build()`
  uses to construct `step_queues` (`projection.py:25-47`, `column_order`
  loops `workflow.steps()` into `order` unconditionally). So "board columns
  minus todo/done/failed" is not an approximation of the live `step_queues`
  keys — it **is** them, by construction. The design's plan to compute the
  restart warning from `view.snapshot().columns` rather than from `WorkflowAdmin`
  is correct, and more robust than it first appears (see below).
- **`Dispatcher.tick`** (`dispatcher.py:59-60`) resolves the workflow
  *per task* via `self._workflows.get(task.workflow_template)`, not from a
  single fixed workflow object. That means several workflow files can be
  in flight in one process at once, each task pinned to the one it was
  created with. This makes the design's warning computation *more* correct
  than a naive reading suggests: `step_queues` is the one thing that's
  really fixed at process start (`app.py::build()`, `step_queues = {step:
  ... for step in workflow.steps()}` for the single `--workflow` the process
  was launched with — `cli.py:669-681`), and that's exactly what
  `Dispatcher._move` fails against (`destination is None` →
  `"step {step!r} has no queue"`, `dispatcher.py:71-74`). Board columns
  mirror that fixed set precisely, regardless of which workflow file the
  operator is editing. Good — this is the one part of the design worth
  being confident about rather than merely trusting.
- **`test_architecture.py`** — I read every guard, not just the ones the
  design cites. This changes the plan's step 3 (see Implementation guidance
  §4).
- **Atomic writes**: `FilesystemTaskQueue._write` (`drivers/fs_queue.py:151-168`)
  already establishes the exact idiom the design calls for in prose
  ("temp file in the same directory, then `os.replace`") — unique temp name
  (`uuid4().hex`), `write_text`, `os.replace`, `except Exception: unlink;
  raise`. This is the pattern to copy verbatim for the two new drivers, not
  reinvent.
- **Templates**: `board.html` has no shared layout partial today — each page
  is a standalone Jinja template with its own inline `<style>` block
  (`api/templates/board.html:1-24`). The design's plan to add `_nav.html` as
  a small `{% include %}`, without introducing shared page chrome beyond
  that, matches the codebase's existing granularity rather than inventing a
  new convention.
- **Test layout**: `tests/test_fs_agents.py` exercises `FilesystemAgentCatalog.get`
  only at the `AgentNotFound` boundary (missing file, broken JSON, invalid
  name, bad outcome — all asserted via `pytest.raises(AgentNotFound)`). None
  of these tests reach into the parsing internals, which confirms the
  design's "pure refactor, existing tests pass unchanged" claim rather than
  just assuming it.

Nothing in the plan or design contradicts an existing invariant in
`CLAUDE.md`. The closest brush is invariant #5 / #11 ("`api/` touches only
`ArtifactView`" / "`Workspace`/`Forge`/`ArtifactStore` are unknown to the
dispatcher and consumer") — the two new ports are additional instances of
the same shape, not an exception to it.

## Proposed architecture

Endorsing the design's shape as-is:

```
                     ┌────────────────────────────┐
                     │  api/routes.py              │
                     │  build_json_router()        │
                     │  build_html_router()         │
                     │   — new: /admin/agents*      │
                     │   — new: /admin/workflows*   │
                     │   — new: /api/agents*        │
                     │   — new: /api/workflows*     │
                     └───────────┬─────────────────┘
                                 │ depends only on
                     ┌───────────▼─────────────────┐
                     │  ports/agent_admin.py        │   ports/workflow_admin.py
                     │  AgentAdmin (ABC)             │   WorkflowAdmin (ABC)
                     │  AgentFields, AgentValidat-   │   WorkflowValidationError
                     │  ionError                      │
                     └───────────┬─────────────────┘
                                 │ implemented by
                     ┌───────────▼─────────────────┐
                     │  drivers/fs_agents.py         │   drivers/fs_workflows.py
                     │  FilesystemAgentAdmin          │   FilesystemWorkflowAdmin
                     │  (+ shared _parse_agent_spec)  │   (+ shared _parse_workflow)
                     └────────────────────────────┘
                                 ▲ wired only in
                     ┌───────────┴─────────────────┐
                     │  cli.py :: serve()            │
                     └────────────────────────────┘
```

Key decisions, and why they're the right call rather than the only option:

1. **Two ports, not one.** A single `AdminPort` covering both agents and
   workflows was the alternative; rejected because it would force a
   structured-form path (agents) and a raw-text path (workflows) to share
   one interface for no reason — they have nothing in common except "CRUD
   over a JSON file on disk," and `AgentAdmin.write` needs typed fields
   where `WorkflowAdmin.write_raw` deliberately wants the opposite (opaque
   text). Matches the codebase's existing granularity: `BoardView` and
   `TaskControl` are two ports over the same underlying task state, not one.

2. **`AgentFields` (raw strings) in, `AgentSpec`/exception out — not
   `AgentSpec` in.** This is the design's one deviation from the plan, and
   it's the correct call: constructing an `AgentSpec` before validation
   would either duplicate the `Outcome(...)` parsing in the router (the
   thing this whole design exists to avoid) or force `AgentSpec` to grow an
   "unvalidated" variant, contaminating the type every other consumer
   (`ClaudeCliBehavior`, `AgentCatalog.get`) already trusts. Keeping the
   boundary as "plain strings in, validated model or exception out" is the
   shape both the JSON body and the HTML form data can be adapted into
   directly.

3. **Workflows stay raw text end to end**, never parsed-and-reserialized.
   The alternative (parse into `Workflow`, edit fields, `json.dumps` back
   out) was rejected by the plan for a real reason: it would silently
   reformat whatever the operator typed, which defeats the stated purpose of
   a raw editor. Correct to keep `write_raw(name, raw_json: str)` as the
   entire contract.

4. **The restart warning is computed at the route layer, not inside
   `WorkflowAdmin`.** Confirmed correct above — `WorkflowAdmin` is a pure
   filesystem port with no idea what the running process's `step_queues`
   contains; `BoardView`, already threaded into both router factories, is
   exactly the right existing seam. No new port needed for this.

5. **Reuse `build_json_router`/`build_html_router` by adding parameters,
   not four new router factories.** Consistent with how `artifacts`/
   `output`/`control` were added to these same two functions previously —
   evidenced by reading `api/app.py`'s existing null-object list.

## Implementation guidance

### 1. Extraction order (do this first, as its own commit)

Pull `_parse_agent_spec(name, raw: dict) -> AgentSpec` out of
`FilesystemAgentCatalog.get`, and `_parse_workflow(name, raw: dict) ->
Workflow` out of `FilesystemWorkflowRepository.get`. One concrete rule the
design states but doesn't spell out mechanically: **standardize on
`ValueError` as the one exception type both helpers raise for every
validation failure** (missing `prompt`/`start`, non-dict `raw`, bad
`allowed_outcomes`, malformed transition). Concretely:

- `_parse_agent_spec` raises `ValueError` for: missing `prompt`, invalid
  `allowed_outcomes` (catch `Outcome`'s own `ValueError` and re-raise with
  context, or let it propagate — either way it's a `ValueError`).
- `_parse_workflow` raises `ValueError` for: `raw` not a dict, missing
  `start`, and — this is the one place the current code raises something
  else (`KeyError`/`TypeError` from the `Transition(...)` comprehension,
  `fs_workflows.py:55`) — catch those and re-raise as `ValueError` with the
  same message, so `_parse_workflow` has a single exception contract for
  both call sites to catch.
- The **existing** `.get()` methods then wrap `ValueError` into
  `AgentNotFound`/`WorkflowNotFound` exactly as before — this is what makes
  `tests/test_fs_agents.py`/`test_fs_workflows.py` pass unchanged, since
  they assert on the outer exception type, never the inner one.
- The **new** `write`/`write_raw` wrap the same `ValueError` into
  `AgentValidationError`/`WorkflowValidationError`.

Get this contract explicit before writing the admin drivers — it's the one
place a sloppy refactor could leave `write`/`write_raw` catching too narrow
an exception type and letting a malformed submission through to disk (which
would violate the plan's explicit "never partially write" requirement).

### 2. New ports (`ports/agent_admin.py`, `ports/workflow_admin.py`)

Build exactly as sketched in `design-01.md`. Two additions to nail down that
the design left implicit:

- **Name validation belongs to the write path too, symmetrically.**
  `_invalid_agent_name`/`invalid_workflow_name` gate the *read* path today;
  `FilesystemAgentAdmin.write`/`FilesystemWorkflowAdmin.write_raw` must run
  the same check on `name` before writing (not just delegate to the OS and
  hope). The design says this for agents (FR-3's acceptance criterion) but
  doesn't restate it for workflow create — apply it there too, same
  function (`invalid_workflow_name`), same place in the sequence (before
  any file I/O).
- **`AgentAdmin.write`/`WorkflowAdmin.write_raw` don't distinguish create
  from update — the router does.** `FR-7` ("submitting creates the file if
  the name doesn't already exist") implies the collection-level create route
  (`POST /admin/agents`, `POST /admin/workflows`) must reject a name that
  already exists as a validation error, *before* calling `write`/`write_raw`
  — otherwise "new agent" silently overwrites an existing one with the same
  name, which is a real foot-gun for an operator who fat-fingers a name
  that happens to collide. Concretely: the create handler calls
  `agent_admin.list()` (or a `read`/`AgentNotFound` probe) first; if the
  name is already taken, respond with the same inline-error shape FR-3 uses
  for a bad prompt (`"name": "an agent named 'x' already exists"`), file
  untouched. The member-level update route (`POST /admin/agents/{name}`)
  has no such check — it's explicitly create-or-update by design, matching
  hand-editing the file.

### 3. Drivers (`drivers/fs_agents.py`, `drivers/fs_workflows.py`)

Copy `FilesystemTaskQueue._write`'s temp-file idiom
(`drivers/fs_queue.py:151-168`) into each new driver's `write`/`write_raw` —
unique temp name via `uuid4().hex`, `write_text`, `os.replace`, cleanup on
exception. Don't refactor `fs_queue.py` to share a helper with these two —
that file is stable, tested, working code with no other reason to change,
and the four extra lines of duplication across two *new* files is cheaper
than the risk/diff-noise of touching a third, unrelated one. (If a third
*new* site needs the same idiom later, that's the point to extract a shared
`drivers/atomic_write.py` — not before.)

### 4. `test_architecture.py` — one less step than the plan assumes

I traced every guard the plan's step 3 asks to "extend" and none of them
need new code:

- `test_ports_do_not_import_drivers` globs `(SOURCE / "ports").glob("*.py")`
  — automatically covers `ports/agent_admin.py`/`ports/workflow_admin.py`
  the moment they exist.
- `test_api_does_not_import_drivers` uses `.rglob("*.py")` over `api/` —
  automatically covers any new file under `api/`, including a subdirectory.
- `test_only_app_and_cli_wire_drivers` iterates `SOURCE.glob("*.py")`
  (non-recursive — only the top-level orchestration modules:
  `dispatcher.py`, `consumer.py`, `projection.py`, `router.py`, etc.) and
  separately `test_orchestration_does_not_import_work_ports`/
  `test_orchestration_does_not_import_control` check `dispatcher.py`/
  `consumer.py` by name. Since the design adds no import of the new
  ports/drivers anywhere near `dispatcher.py`/`consumer.py`, these already
  pass with zero changes.
- The extensions to `fs_agents.py`/`fs_workflows.py` stay inside
  `drivers/`, already fully exempt-by-being-drivers for every one of the
  above.

**Skip plan step 3 as written.** Nothing in `test_architecture.py` needs
editing for this feature — that's a feature of how those guards were
written (glob-based, not an enumerated allowlist), not a gap to close. Spend
that time on the driver/router unit tests instead (plan steps 2, 4, 7,
unchanged).

### 5. Routing — one concrete gotcha the design doesn't mention

FastAPI/Starlette matches routes in registration order. `GET
/admin/agents/new` **must** be registered before `GET /admin/agents/{name}`
in `build_html_router`, or the dynamic route swallows the literal `new`
path segment and the "new agent" form 404s (or worse, silently tries to
`AgentAdmin.read("new")`). Same for `/admin/workflows/new`. This applies
only to the HTML router — the JSON API has no `/new` route (creation there
is `PUT` on the target name, which is naturally idempotent and needs no
static path), so it's unaffected.

### 6. Wiring (`cli.py::serve`, `cli.py::_run`)

Confirmed exact location: `create_app(...)` is called once, at
`cli.py:708-714`, inside `serve()`. Add
`agent_admin=FilesystemAgentAdmin(harness.layout.agents)` and
`workflow_admin=FilesystemWorkflowAdmin(harness.layout.workflows)` there.
`HarnessLayout.agents`/`.workflows` already exist (`app.py:76-78`,
`app.py:52-54`) — no change needed to `HarnessLayout` itself, matching the
design's claim.

### 7. Update `CLAUDE.md` with a new numbered invariant, not just prose

The plan's step 8 says "update the module map." Go one step further and add
a **numbered invariant**, matching how every other port/driver boundary in
this file is codified (#5, #11, #17, #20, #23 are all the same shape: "port
X is touched only by Y, drivers wired in app.py/cli.py — guarded by
test_architecture.py"). Suggested addition after #23:

> **24. `AgentAdmin`/`WorkflowAdmin` are unknown to the dispatcher and
> consumer.** They are UI-facing admin ports, not orchestration ports — like
> `BoardView`/`TaskControl`, not like `AgentCatalog`/`WorkflowRepository`.
> `api/` touches only the two admin ports; the filesystem drivers are wired
> exclusively in `cli.py`'s `serve()`. Guarded by `test_architecture.py`'s
> existing glob-based checks (no dedicated test needed — see architecture
> assessment).

This keeps the invariant list a complete map of every such boundary in the
system, which is the whole point of that section existing.

## Risks and mitigations

- **Exception-type drift during the `_parse_*` extraction.** If the
  extracted helper keeps raising `AgentNotFound`/`WorkflowNotFound`
  directly (instead of a neutral `ValueError`) for some but not all
  validation failures, `write`/`write_raw` will either crash with an
  unhandled exception (500, not the documented 422) or — worse — catch too
  broadly and accidentally swallow an unrelated bug. Mitigation: the
  standardized-`ValueError` contract in §1 above, plus a unit test per
  helper asserting the *inner* exception type directly (not just the outer
  one `test_fs_agents.py`/`test_fs_workflows.py` already cover).
- **Silent overwrite on "create."** Addressed in §2 above — without an
  explicit existing-name check in the create route, FR-7 ("creates... if
  the name doesn't already exist") is unenforced. Low likelihood of being
  caught by testing unless someone writes the specific "create over an
  existing name" case — call it out explicitly in the test plan for
  FR-3/FR-7.
- **Route-ordering 404 on `/admin/{agents,workflows}/new`.** Addressed in
  §5. Easy to get right if flagged before writing the router; easy to miss
  and ship a broken "new" button otherwise, since `/admin/agents/{name}`
  for a genuinely unknown name already 404s today via `AgentNotFound`,
  which would look like the same failure mode and could be misdiagnosed.
- **Concurrent-write races** (two operators, or the operator and a
  currently-running agent step, touching the same file) are explicitly
  out of scope per the plan's non-functional requirements ("last write
  wins, no locking") — consistent with the existing hand-edit workflow, not
  a regression this feature introduces. No mitigation needed beyond what
  the plan already states; flagging here only so it isn't rediscovered as a
  surprise during review.
- **New-step restart warning depends on which workflow the harness was
  launched with**, not on the workflow file being edited. An operator
  editing a workflow that isn't the one currently running will still get an
  accurate warning (per the analysis above, `step_queues` is what matters,
  not "which workflow is this"), but the UI copy should say "the *running*
  harness" (as the design's own wireframe already does — `design-01.md`
  line 126) rather than implying anything about the specific workflow file
  being edited, to avoid operator confusion in the multi-workflow case.

## Prerequisites before implementation begins

None blocking. Everything the plan and design rest on — `HarnessLayout`,
`AgentSpec`/`AgentNotFound`, `WorkflowNotFound`, the router-factory pattern,
the null-object pattern in `create_app`, the atomic-write idiom — already
exists in the repository exactly as both documents describe. Implementation
can start directly from `plan-01.md`'s rough-plan steps 1-2, folding in the
exception-contract standardization from §1 above, then proceed through the
remaining steps with plan step 3 (`test_architecture.py` extension) dropped
per §4 and a numbered-invariant addition folded into step 8 per §7.
