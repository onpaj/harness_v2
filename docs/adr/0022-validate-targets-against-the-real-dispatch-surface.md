# ADR-0022: A target check validates against the exact set the runtime dispatches into, never a broader superset

Status: Accepted

## Context

Task `tsk_936d9920688e414f` (repo `personal_assistant`) failed with
`step 'nanoclaw-sweep' has no queue`, even though `agents/nanoclaw-sweep.json`
(a valid catalog agent) and `processes/nanoclaw-sweep.json` (a valid Process
targeting `{"step": "nanoclaw-sweep"}`) both existed and loaded successfully.
That instance reached the failing state via a live-config/restart-ordering
desync — the agent and process were added against an already-running
service, and `step_queues` stayed frozen at the set `app.build()` computed
before either file existed.

Investigating it surfaced a second, independent bug in the same area, latent
but reproducible without any restart at all: `_parse_target`
(`drivers/fs_processes.py`, mirrored in `drivers/fs_triggers.py`) validated a
`{"step": X}` **or** `{"workflow": X}` target against one merged set,
`known_targets = known_steps | served_workflow_names` (`app.py`). A
`{"step": "resolver"}` target — where `"resolver"` is a served workflow's
*name*, not a queued step — validated cleanly, because `"resolver"` sits in
the workflow-name half of the union. `step_queues`, though, is keyed only by
`known_steps`. `Dispatcher.tick` (`dispatcher.py`) has no queue to route
into and can only fail the task — identically to the `nanoclaw-sweep`
symptom, but reachable the moment such a file is written, with no restart
and no timing window required.

The two failures share a root cause: something the harness treats as
"fireable" (it passed target validation) has no receiving queue at dispatch
time. `Dispatcher.tick` can only dead-end such a task into `failed/`
(`dispatcher.py`), which then loops into self-healing without a real fix
available.

## Decision

**A compile-time target check must validate against the exact set the
runtime will dispatch into — never a broader superset that merges two
distinct namespaces.**

Concretely: `_parse_target` in both `drivers/fs_processes.py` and
`drivers/fs_triggers.py` now takes two independent parameters,
`known_steps: set[str] | None` and `known_workflows: set[str] | None`, in
place of one merged `known_targets: set[str] | None`.

- A `{"step": X}` target is checked only against `known_steps` — the exact
  set `step_queues` is keyed by (served workflow steps ∪ catalog agent
  names, `app.py`), never against served workflow *names*.
- A `{"workflow": X}` target is checked only against `known_workflows` — the
  served-workflow-name set, never against step/agent names.
- Each parameter independently defaults to `None`, meaning "skip this half
  of the check" — the same escape-hatch convention every other `known_*`
  parameter in this module already follows (`known_repositories=None`).

Every call site that used to compute one union now computes the two
underlying sets and passes them through unmerged: `app.py`'s `build()`,
`cli.py`'s `_scheduled_sources`/`_run`, and `FilesystemProcessAdmin`'s
`write()` (which previously passed `known_targets=None` unconditionally —
skipping target validation on every dashboard submission; it now receives a
live `known_steps`/`known_workflows` snapshot read straight off a built
`Harness`, via a new `Harness.known_steps` read-only property derived from
`_step_queues` rather than a second stored copy).

`Dispatcher.tick`'s residual `step 'X' has no queue` failure — reachable
only via the live-edit/restart-ordering path this validation tightening
cannot close (config compiled at one instant, dispatch happening against a
frozen queue set built at an earlier instant) — now says more than the bare
fact: it names the concrete remedy (restart the harness service to rebuild
its queue set). `dispatcher.py` gains no new dependency to do this; it is a
change to a string literal only.

## Consequences

- A `{"step": X}` target naming a served workflow's name (or vice versa)
  fails fast at `FilesystemProcessRepository.build()` /
  `FilesystemTriggerRepository.build()`, before any task is ever produced —
  the same fail-fast guarantee this repository already gives a genuinely
  unknown target.
- A dashboard-authored process naming an unreachable target is rejected at
  save time (`ProcessAdminValidationError`, `field="target"`) instead of
  being written to disk to fail — silently or noisily — only later.
- **This does not prevent the literal `nanoclaw-sweep` incident.** That
  task's files were valid and would pass both the old check and this
  tightened one; the failure came from being added against an
  already-running instance with no live-reload of `agents/`/`processes/`,
  not from a target naming the wrong namespace. Only the dispatcher's
  reworded failure text touches that path, and it does not prevent the
  failure — it only makes it legible, with "restart the service" as the
  concrete next step. Live-reload of config directories would close that
  residual gap fully; it is a materially larger feature and stays out of
  scope here.
- The underlying rule — validate a target against the set it will actually
  be dispatched into, never a broader superset formed by merging two
  namespaces that happen to share a lookup — is general enough to apply the
  next time a `{"kind": ...}`-shaped target or check validation is added to
  this codebase.
