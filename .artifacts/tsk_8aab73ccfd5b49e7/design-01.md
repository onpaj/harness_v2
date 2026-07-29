# Design: reject a `{"step": X}` Process/Trigger target with no dispatch queue

No UI/UX section — this is a validation-logic and message-text change; the
existing admin dashboard's Process form and board failure-reason column are
unchanged in shape, only in which inputs are now rejected and what one string
says.

## 1. Component design

### 1.1 `_parse_target` splits one merged set into two independent ones

Both `drivers/fs_processes.py::_parse_target` and
`drivers/fs_triggers.py::FilesystemTriggerRepository._parse_target` currently
take one `known_targets: set[str] | None` and check *either* key (`workflow`
or `step`) against it. They change to take two, checking each key against
only the set that actually governs it:

```python
def _parse_target(
    where: str,
    target: object,
    known_steps: set[str] | None,
    known_workflows: set[str] | None,
) -> tuple[str | None, str | None]:
    if not isinstance(target, dict) or set(target) not in ({"workflow"}, {"step"}):
        raise ProcessValidationError(
            f"process {where} must have a target of exactly one of "
            f"{{'workflow': ...}} / {{'step': ...}}",
            field="target",
        )

    workflow = target.get("workflow")
    step = target.get("step")
    if workflow is not None:
        if known_workflows is not None and workflow not in known_workflows:
            raise ProcessValidationError(
                f"process {where} targets workflow {workflow!r}, which is "
                f"not a served workflow",
                field="target",
            )
    else:
        if known_steps is not None and step not in known_steps:
            raise ProcessValidationError(
                f"process {where} targets step {step!r}, which has no "
                f"dispatch queue",
                field="target",
            )
    return workflow, step
```

(`fs_triggers.py`'s twin raises `TriggerValidationError` with the same two
messages, no `field=`, mirroring its existing style.)

This directly satisfies FR-1 and FR-2 in one function: a `{"step": "resolver"}`
target where `"resolver"` is a served workflow name but not a queued step now
fails (`known_steps` doesn't contain it), and symmetrically a
`{"workflow": "plan"}` target where `"plan"` is a step but not a served
workflow name now fails too. `known_steps=None` and `known_workflows=None`
remain **independent** escape hatches — each disables only its own half,
preserving the existing "`None` skips this check" convention used everywhere
else in this module (`known_repositories=None` is the precedent).

Every call site threading `known_targets` through both modules changes to
thread `known_steps`/`known_workflows` instead — no other logic in
`compile_process`, `FilesystemProcessRepository.build`/`_build_one`, or
`FilesystemTriggerRepository.build`/`_build_one` changes; it's a pure
parameter rename-and-split down the call chain.

### 1.2 `app.py`: stop merging, pass the two sets that already exist

`app.py:420-426` already computes exactly the real dispatch-surface set:

```python
known_steps: set[str] = set()
for workflow in resolved.values():
    known_steps |= set(workflow.steps())
if catalog is not None:
    known_steps |= set(catalog.names())
```

`resolved` (the served-workflow dict, keyed by name) is the served-workflow
*name* set. `app.py:694-711` currently does:

```python
known_targets = set(known_steps) | set(resolved)
...
process_sources = process_repo.build(..., known_targets=known_targets, ...)
```

This becomes:

```python
process_sources = process_repo.build(
    clock=clock,
    checks=checks,
    repository=None,
    worktree_root=str(layout.worktrees),
    known_steps=known_steps,
    known_workflows=set(resolved),
    known_repositories=known_repositories,
)
```

No new computation — `known_steps` was already sitting in scope; `set(resolved)`
replaces the union. The stale comment above the old line ("`known_targets`
must include served workflow names too...") is deleted along with the union;
its explanation is now redundant, `_parse_target`'s own docstring says why the
two sets are separate.

### 1.3 `Harness` exposes `known_steps` as a live property, not a duplicated field

FR-4's admin-write-time validation needs the exact same "does a live queue
exist for this step" answer `Dispatcher.tick` uses. Rather than threading a
second copy of the set through `build()` → `Harness.__init__` → storage (which
*could* drift from `step_queues` if one code path updated without the other),
`Harness` derives it from the queues it already stores:

```python
class Harness:
    ...
    @property
    def known_steps(self) -> frozenset[str]:
        """Step names with a live dispatch queue — exactly what `step_queues`
        is keyed by. The real-time twin of the `known_steps` set used at
        Process/Trigger build validation; the two must never diverge, since
        this one *is* what `Dispatcher.tick` routes into."""
        return frozenset(self._step_queues)
```

`self.workflows` (already public, `= resolved`) is reused directly as the
served-workflow-name source (`set(harness.workflows)`) — no new attribute
needed for that half.

No constructor signature change on `Harness`, no new `build()` parameter:
this is a read-only derived property over state that already exists.

### 1.4 `cli.py`: split the equivalent computation for triggers

`cli.py:1876-1884` builds one `known_targets` set for
`_scheduled_sources`/`FilesystemTriggerRepository.build`:

```python
known_targets: set[str] = set(served_names)
wf_repo = FilesystemWorkflowRepository(layout.workflows)
for name in served_names:
    try:
        known_targets |= set(wf_repo.get(name).steps())
    except WorkflowNotFound:
        continue
if catalog is not None:
    known_targets |= set(catalog.names())
sources = sources + _scheduled_sources(
    args, root, registry, clock=SystemClock(), known_targets=known_targets
)
```

This splits into two independently-built sets — `known_workflows` is simply
`set(served_names)` (no loop needed), `known_steps` is the loop's
accumulation without the `served_names` seed:

```python
known_steps: set[str] = set()
wf_repo = FilesystemWorkflowRepository(layout.workflows)
for name in served_names:
    try:
        known_steps |= set(wf_repo.get(name).steps())
    except WorkflowNotFound:
        continue
if catalog is not None:
    known_steps |= set(catalog.names())
known_workflows = set(served_names)
sources = sources + _scheduled_sources(
    args, root, registry, clock=SystemClock(),
    known_steps=known_steps, known_workflows=known_workflows,
)
```

`_scheduled_sources`'s own signature changes the same way (`known_targets` →
`known_steps`/`known_workflows`, both threaded straight into
`FilesystemTriggerRepository.build`).

Note this cli.py computation and `app.py`'s `known_steps` computation
(§1.2) are structurally the same loop, duplicated across the two modules
already today (one over `resolved.values()`, one over `wf_repo.get(name) for
name in served_names` — the same workflows, reached two different ways
because `cli.py`'s `_run` computes triggers *before* calling `app.build()`).
This design does not merge them — out of scope; the plan's dependency list
already scopes this to a parameter split, not a wiring refactor.

### 1.5 `FilesystemProcessAdmin` gains `known_steps`/`known_workflows` (FR-4)

```python
def __init__(
    self,
    root: Path,
    *,
    checks: dict[str, CheckFactory] | None = None,
    registry: RepositoryRegistry | None = None,
    known_steps: set[str] | None = None,
    known_workflows: set[str] | None = None,
) -> None:
    self._root = Path(root)
    self._root.mkdir(parents=True, exist_ok=True)
    self._checks = checks if checks is not None else BUILTIN_CHECKS
    self._registry = registry
    self._known_steps = known_steps
    self._known_workflows = known_workflows
```

`write()` passes them straight through instead of `known_targets=None`:

```python
compile_process(
    name,
    raw,
    clock=_LocalClock(),
    checks=self._checks,
    known_steps=self._known_steps,
    known_workflows=self._known_workflows,
    known_repositories=known_repositories,
)
```

Both default to `None` (validation skipped), so every existing
`FilesystemProcessAdmin(tmp_path)` construction across
`tests/test_fs_process_admin.py`/`tests/test_process_admin_api.py` keeps
compiling and behaving exactly as before — this is additive, not breaking.

Stored at construction (not recomputed per `write()` call, unlike
`known_repositories` which re-reads a live `RepositoryRegistry`), because
unlike the repository registry these two sets are frozen for the lifetime of
one `harness run` process — a `FilesystemProcessAdmin` only exists inside a
`serve()` call bound to one already-built `Harness`.

**Wiring** (`cli.py::serve`, where the admin is actually constructed):

```python
process_admin=FilesystemProcessAdmin(
    harness.layout.processes,
    checks=harness.process_checks,
    registry=registry,
    known_steps=set(harness.known_steps),
    known_workflows=set(harness.workflows),
),
```

This closes the plan's open question 1: no new snapshot mechanism, no
recomputation duplicated a third time — `serve()` already holds the fully
built `harness`, and `known_steps`/`workflows` are read straight off it.

### 1.6 `dispatcher.py`: actionable failure text (FR-5)

The single line at `dispatcher.py:78` changes from:

```python
self._fail(task, f"step {decision.step!r} has no queue")
```

to:

```python
self._fail(
    task,
    f"step {decision.step!r} has no queue (if this step, agent, or its "
    "process/trigger was added or changed recently, restart the harness "
    "service to rebuild its queue set)",
)
```

No new dependency, no new method, no branch on *why* the queue is missing —
`Dispatcher` still cannot distinguish "genuinely unknown step" from "known
step added after this process started" (it has no visibility into
`AgentCatalog`/`RepositoryRegistry`, and per the plan's constraint it must not
gain one). The message is deliberately generic and always-correct: a restart
is always a harmless, always-available remedy for this dead end, whether the
step was truly never valid or only recently became so. This keeps `dispatcher.py`
within the ports-only layer (module map, "Orchestration" row) — a wording
change to a string literal, not a new capability.

The existing test `test_step_without_queue_lands_in_failed`
(`tests/test_dispatcher.py:172`) asserts `"missing" in
failed.list()[0].history[-1].reason` — the step name stays in the message, so
that assertion is unaffected. A new test asserts the added remedy text is
present (FR-6, dev-phase task).

## 2. Data / schema

No persisted schema changes anywhere in this change:

- `Task`, `Process`/`ProcessFields`, `Trigger` file JSON shape: unchanged.
- The only "shapes" touched are two **derived, in-memory, per-process-lifetime
  sets** (`known_steps: set[str]`, `known_workflows: set[str]`) that replace
  one previously-merged set (`known_targets: set[str]`) at every call site
  listed in §1. Both are transient — recomputed on every `app.build()` /
  `cli._run` invocation from `catalog.names()` / served `Workflow.steps()` /
  `resolved`'s keys, never written to disk.
- No event payload changes. `Dispatcher._fail`'s `"failed"` event still emits
  the same fields (`task_id`, `reason`, `queue`, `task`) — only the `reason`
  string's *content* grows a trailing parenthetical, its shape (a `str`) is
  identical.
- No API/route request or response shape changes: `ProcessAdmin.write`'s
  signature (the port, `ports/process_admin.py`) is untouched — only the
  `FilesystemProcessAdmin` driver's constructor gains two optional keyword
  arguments, invisible to `api/routes.py`, which only ever calls
  `admin.write(name, fields)`.

## 3. Interfaces (final signatures)

- `drivers/fs_processes.py`
  - `_parse_target(where, target, known_steps, known_workflows) -> (workflow, step)`
  - `compile_process(..., known_steps=None, known_workflows=None, ...)` (was `known_targets=None`)
  - `FilesystemProcessRepository.build(..., known_steps=None, known_workflows=None, ...)`
  - `FilesystemProcessRepository._build_one(..., known_steps, known_workflows, ...)`
  - `FilesystemProcessAdmin.__init__(..., known_steps=None, known_workflows=None)`
  - `FilesystemProcessAdmin.write(...)` — internal call to `compile_process` updated, no signature change to `write` itself
- `drivers/fs_triggers.py`
  - `FilesystemTriggerRepository.build(..., known_steps=None, known_workflows=None)` (was `known_targets=None`)
  - `FilesystemTriggerRepository._build_one(..., known_steps, known_workflows)`
  - `FilesystemTriggerRepository._parse_target(self, path, target, known_steps, known_workflows)`
- `app.py`
  - `build()`'s own signature is unchanged (no new parameter) — only the body's local wiring at the old `known_targets` line changes.
  - `Harness` gains one new read-only property: `known_steps -> frozenset[str]`.
- `cli.py`
  - `_scheduled_sources(..., known_steps: set[str] | None, known_workflows: set[str] | None)` (was `known_targets`)
  - `_run`'s local `known_targets` computation splits into `known_steps`/`known_workflows` (§1.4)
  - `serve()`'s `FilesystemProcessAdmin(...)` construction gains `known_steps=set(harness.known_steps), known_workflows=set(harness.workflows)`
- `dispatcher.py::Dispatcher.tick` — no signature change; only the literal `reason` string at the "no queue" branch changes.

No port (`ports/*.py`) changes at all — this is entirely a driver- and
wiring-layer fix, consistent with the plan's framing of this as a validation
bug, not a new architectural capability.

## 4. Resolved open questions (from the plan)

1. **Admin's access to a live snapshot**: resolved by exposing
   `Harness.known_steps` as a property over the already-stored
   `_step_queues` (§1.3), reusing the already-public `Harness.workflows` for
   the served-workflow-name half. No new computation, no new constructor
   parameter on `Harness`/`build()` — the two values `serve()` needs already
   exist on the object it holds.
2. **FR-5 wording**: finalized above (§1.6) —
   `"step {step!r} has no queue (if this step, agent, or its process/trigger
   was added or changed recently, restart the harness service to rebuild its
   queue set)"`.
3. **`known_workflows=None` as an escape hatch**: yes, confirmed — symmetric
   with `known_steps=None`, each independently disables only its own half of
   `_parse_target`'s check (§1.1).
4. **ADR or plain fix?**: add a short ADR, `docs/adr/0022-...md` (next
   available number after `0021`). It records one reusable principle this bug
   revealed — *"a compile-time target check must validate against the exact
   set the runtime will dispatch into, never a broader superset merging two
   distinct namespaces"* — general enough to matter the next time a
   `{"kind": ...}`-shaped target/check validation is added (the plan's own
   framing already treats this as a class of bug, not a one-off). Required
   sections per `test_adr_docs.py`: `# ADR-0022: <title>`, a `Status:` line,
   `## Context`, `## Decision`, `## Consequences`. Content: the
   `known_steps`/`known_workflows` split (§1.1) as the decision; context is
   the `nanoclaw-sweep` incident plus the `{"step": "resolver"}` latent
   sibling bug found during investigation; consequences note FR-5's
   dispatcher message as the residual defence-in-depth for the
   live-edit/restart-ordering path this ADR's validation tightening cannot
   close (config compiled at one instant, dispatch happening against a
   frozen queue set built at an earlier instant).

## 5. Test surface (naming only — implementation is the dev step's job)

- `tests/test_fs_processes.py::test_target_outside_known_targets_raises_naming_the_file`
  — existing test's `_build(tmp_path, known_targets={"wf"})` call updates to
  `known_workflows={"wf"}` (the target under test is `{"workflow": "other"}`).
  New tests alongside it: a `{"step": "wf"}` target where `"wf"` is a served
  workflow name but not a step (rejected — FR-1/AC1), and a
  `{"workflow": "plan"}` target where `"plan"` is a step/agent name but not a
  served workflow (rejected — FR-2/AC).
- `tests/test_fs_triggers.py` — the mirror of the above three cases.
- `tests/test_app.py` (or wherever `build()`-level Process compilation is
  covered) — one end-to-end case: `processes/*.json` targeting
  `{"step": "<served-workflow-name>"}` fails `app.build()` fast, never reaches
  a live `Dispatcher.tick`.
- `tests/test_fs_process_admin.py` — a `FilesystemProcessAdmin` constructed
  with `known_steps`/`known_workflows` rejects a write naming an unreachable
  target with `field="target"`; a write with no such kwargs (defaults) keeps
  today's lenient behavior.
- `tests/test_dispatcher.py` — extend (or add alongside)
  `test_step_without_queue_lands_in_failed` to assert the new remedy text
  substring is present, keeping the existing `"missing" in ... reason`
  assertion.

```json
{"outcome": "done", "summary": "Wrote design-01.md: split known_targets into known_steps/known_workflows in _parse_target (both fs_processes.py and fs_triggers.py) and every call site (app.py, cli.py's _scheduled_sources and _run), exposed Harness.known_steps as a property over the existing _step_queues to feed FilesystemProcessAdmin's new known_steps/known_workflows kwargs at serve()-time (closing the FR-4 admin gap with zero new computation), finalized the FR-5 dispatcher message text, and scoped a new ADR-0022 plus the concrete test additions."}
```
