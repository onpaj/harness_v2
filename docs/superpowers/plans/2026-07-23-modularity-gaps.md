# Modularity gaps — implementation plan (2026-07-23)

Implements the punch list of `docs/design/2026-07-23-modularity-validation.md`.
Six items, grouped into three work packages (A/B/C) plus a docs pass; each
package lands as its own conventional commit.

## Package A — finisher as data (punch item 1, ADR-0016)

**Gap.** `app.build()`'s `behavior_for()` selects the finishing behavior by a
hardcoded step name (`LANDING_STEP = "land"` → `LandingBehavior` →
`Forge.open_pull_request`). There is exactly one finisher, chosen by naming
convention — the same name-keyed branching invariant #14 forbids for personas.

**Design.** Mirror ADR-0007 (persona as data):

- `Workflow` gains `finishers: dict[str, str]` (step name → finisher kind),
  parsed from an optional `"finishers"` key in the workflow JSON by
  `_parse_workflow` (`drivers/fs_workflows.py`). Validation at parse time:
  must be an object; every key must be a known step of that workflow; every
  value a non-empty string. `Workflow.finisher_for(step) -> str | None`.
- `app.build()` gains `finishers: dict[str, ConsumerBehavior] | None = None`
  — the **finisher registry**, kind → behavior. The default registry is
  `{"open-pr": landing}` (the `LandingBehavior` build already constructs);
  a caller-supplied dict is merged over it. `Forge` is thereby demoted to
  the driver behind the `open-pr` kind.
- `build()` computes a step → kind map by merging every *served* workflow's
  `finishers`; a conflict (two workflows binding the same step to different
  kinds) raises `ValueError` at build (fail fast). When no served workflow
  binds `landing_step` and that step exists, the map defaults
  `{landing_step: "open-pr"}` — full backward compatibility: a workflow file
  written before this feature behaves exactly as before.
- `behavior_for(step)` resolves through the map + registry; an unknown kind
  raises `ValueError` at build, not at consume time. The name-keyed
  `if step == landing_step` branch is deleted. (The `RESOLVE_STEP` branch
  stays — the resolver is not a finisher; noted in the ADR as a candidate
  follow-up.)

**Files.** `models.py`, `drivers/fs_workflows.py`, `app.py`,
`docs/adr/0016-finisher-as-data.md`, CLAUDE.md (new invariant + map), tests
(`test_fs_workflows`-style parse cases, build-time failures, e2e: a custom
step name bound to `open-pr` lands through the forge; legacy workflow without
the key still lands).

## Package B — sink registry + first real sink, and `trigger.kind` (punch items 2 + 3)

**Gap.** The Process `sink` field accepts only `{"kind": "none"}`; no
`data.sink` is stamped; nothing routes outbound by destination. And the
`trigger` object carries only `interval` — no `kind` slot reserved.

**Design (sink).** Realize the spec's §"sink seam" with a stateless first
driver:

- `ScheduledTrigger` gains `sink: dict | None`; when the kind isn't
  `none`/absent, `_task_for` stamps `data["sink"] = {"kind": ...}`. (The
  trigger still stamps no `data.source` — reflection routes on the
  *destination* identity, exactly as invariant #40 planned.)
- `fs_processes.py`: `_ACCEPTED_SINK_KINDS = {"none", "slack"}`;
  `_validate_sink` returns the parsed sink and `compile_process` passes it to
  the trigger. `FilesystemProcessAdmin.sink_kinds()` → `("none", "slack")`.
- New driver `drivers/slack_sink.py`: `SlackWebhookSink(TaskSource)`,
  `kind = "slack"` — the outbound-only mirror of `GithubLabelReflector`.
  `poll()` is `[]`; `_mine(task)` matches `task.data.sink.kind == "slack"`;
  `report_progress` posts a short step message, `finish` posts the ok/failed
  outcome with the summary. The HTTP POST is an injectable callable
  (default: stdlib `urllib` to the webhook URL — same posture as
  `HttpGithubClient`); an in-process `(task_id, step)` ledger suppresses
  duplicate progress posts (invariant #21's idempotency, per-run).
- Wiring (`cli._run`): register one `SlackWebhookSink` when
  `SLACK_WEBHOOK_URL` is set (the service holds no secret — the URL never
  enters a JSON file). If a process declares a slack sink and the variable is
  missing, print a warning; the sink is simply inert.

**Design (`trigger.kind`).** `compile_process` accepts an optional
`trigger.kind`, defaulting `"schedule"`; any other value is a
`ProcessValidationError` — the same zero-cost reservation `sink` got.
`_raw_from_fields` keeps writing files without the key (reads accept it).

**Files.** `drivers/scheduled_trigger.py`, `drivers/fs_processes.py`,
`drivers/slack_sink.py` (new), `cli.py`, CLAUDE.md (refine invariant #40 +
map), processes spec (mark the sink seam partially realized), tests
(compile/validation, sink stamping, SlackWebhookSink with a fake post,
foreign-task no-op, dedup of repeat progress, wiring gate).

## Package C — filesystem and command actions (punch items 4 + 5)

**Gap.** `BUILTIN_CHECKS` holds only `always` and `disk-threshold`; no
file-based action, and no way to author an action without Python.

**Design.**

- `FileGlobCheck` (`"fs-files"`): params `path` (directory) and `pattern`
  (glob, default `"*"`). One `Observation` per matching file:
  `state_key` = the file's path (naturally `per-state`-friendly),
  `data = {"title": ..., "file": ...}`. The lister is injectable
  (default: real `pathlib` glob), same idiom as `DiskThresholdCheck.usage`
  — unit tests stay off the disk.
- `CommandCheck` (`"command"`): params `command` (a shell string) and
  `timeout` (seconds, default 30). Runs the command (injectable runner,
  default `subprocess.run`); exit code 0 → one `Observation` per non-empty
  stdout line (`state_key` = the line); non-zero exit or timeout → `[]`.
  Blocking the poll tick briefly is the existing posture (`GithubTaskSource`
  polls over sync HTTP); the modest default timeout bounds it.

**Files.** `drivers/checks.py`, tests. Docs handled in the final pass.

## Final pass — vocabulary + reconciliation (punch item 6)

- Terminology note in the processes spec (and the CLAUDE.md gotchas): an
  action's outputs are **Observations** that become tasks; **artifact** stays
  reserved for `.artifacts/<id>/` work products.
- CLAUDE.md: new checks in the module map; full-suite `pytest -q` green.

## Execution

Packages A and C are file-disjoint and run in parallel (subagents); B follows
A (both touch `cli.py` and CLAUDE.md). Each package: implementation + tests +
its docs, verified with the full suite, committed conventionally (`feat:` per
package, `docs:` for the plan and final pass) on
`claude/modular-workflow-validation-98u56g`.
