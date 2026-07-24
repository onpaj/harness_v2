# Architecture (rev 2): convert self-heal `Healer` into a Process

Grounded directly against this worktree's current tree (`HEAD = 9acd4e2`, unchanged
since `design-02.md` was written) — every file/line citation below was re-read live
off disk while writing this document, none carried over unverified from
`architecture-01.md`. `design-02.md` **supersedes** `design-01.md` and already folds
in every gap `architecture-01.md` found (the Slack-sink wiring-order break, the
invariant-#39 contradiction, the persona/artifact-path mismatch, the
`_heal_verdict_summary` history-lookup gap) as settled decisions. This review's job is
to re-verify `design-02.md` against the real, current source and close whatever it
still gets wrong before `development` starts.

**Verdict: not yet ready to implement as written.** `design-02.md` is correct and
thorough on almost everything — the repo-less-attach mechanism, the wiring-order fix,
the `open-issue` finisher, the recursion guard, the test-migration table. But tracing
the actual `route()`/`ScheduledTrigger`/`compile_process` code paths surfaces **two
blocking correctness bugs** that would silently break the feature at runtime with no
error message (§2.1, §2.2), plus a real regression in a "this is obviously safe"
generalization (§2.3) and a concrete test-suite hazard from the FR-6 relocation
(§2.4). None of the four were caught by `design-01.md`, `architecture-01.md`, or
`design-02.md` — all four are new findings from tracing the code this pass. Sections 3+
restate the rest of the design as affirmed, with the fixes folded in as the settled
architecture for `development` to build against.

## 1. Alignment with existing patterns — affirmed

`design-02.md §1–§8`'s framing is correct and needs no rework: `FailedTasksCheck`
mirrors `GithubIssuesCheck`'s "closed-over dependency, registered by a `cli.py`
factory, `BUILTIN_CHECKS` stays dependency-free" shape; `OpenIssueBehavior` mirrors
`LandingBehavior`'s "resolved by kind through the finisher registry, not a
`behavior_for` branch" shape; the repo-less `_attach_repo_less` reuses the exact
idempotent-reattach idiom `GitWorkspace.attach`'s override path already has. Verified
directly, not re-litigated here:

- `WorktreeArtifactView._dir(task_id)` (`drivers/worktree_artifacts.py:23-24`) derives
  the artifacts path as `self._worktrees_root / task_id`, and `GitWorkspace.attach`
  (both the ordinary and the new repo-less path) places the worktree at exactly
  `<worktrees_root>/<task_id>`. So `OpenIssueBehavior`, reading via
  `ArtifactView.list/read(task.id)`, correctly finds the artifact the `heal` step wrote
  two steps earlier **in the same task's own worktree** — no extra plumbing needed,
  confirmed by direct trace, not assumed.
- `MemoryWorkspace.attach` (`drivers/memory.py:212-213`) keys purely on `task.id` and
  already tolerates `task.repository is None` with zero code — confirmed, `design-02
  §1.1`'s claim holds exactly.
- `Task.repository: str | None = None` (`models.py:112`) already permits absence at
  the model layer — no model change needed, confirmed.
- `ProjectionSink.emit` (`drivers/projection_events.py:16-39`) dispatches on the
  presence of `task`+`queue` fields, not on the event *name* — so `FailedTasksCheck`
  reusing today's `"healing"`/`"healed"` event names (`design-02 §3.1` step 3/4) needs
  no consumer-side change, confirmed directly, not inferred from invariant #7 alone.
- `test_architecture.py`'s two healer-specific tests sit at exactly the lines
  `design-02.md §7` cites (`tests/test_architecture.py:225,240`) — confirmed byte-for-byte.
- `Workflow.finisher_for` exists at `models.py:196-198` exactly as `design-02 §3.3`
  step 3 assumes.

## 2. Four gaps found by tracing the real code — must be resolved before development

### 2.1 BLOCKING: `{"step": "heal"}` silently drops the task after one step — the issue is never filed

`design-02.md §4.5` picks `{"step": "heal"}` as the process target (a **workflow-less**
task: `Task(workflow_template=None, step="heal", ...)`), explicitly reasoning that
"either shape reaches the same dispatcher behavior once `heal` is the workflow's
`start` step." **This is false**, and the consequence is severe: the `file-issue`
step — and therefore `OpenIssueBehavior`/`IssueTracker.open_issue` — is **never
reached**. The healed task quietly lands in `done/` after only drafting an artifact
nobody reads.

Traced directly in `router.py:8-40`:

```python
def route(task: Task, workflow: Workflow | None) -> Decision:
    if task.status is None:
        if workflow is not None:
            return MoveTo(workflow.start)
        if task.step is None or task.step in (END, FAILED):
            return Failed(...)
        return MoveTo(task.step)          # <- first (only) hop for a workflow-less task

    if task.last_outcome is None:
        return Failed(...)

    if workflow is None:
        return Finished()                  # <- unconditional once status is set
    ...
```

For a workflow-less task (`workflow_template=None`), the **first** time `route()` is
called (`status is None`) it moves to `task.step` ("heal") exactly once. The **second**
time `route()` is called — after `heal` returns `done` and `Consumer._deliver`
(`consumer.py:90-106`) has set `last_outcome="done"` and put the task back in the
inbox — `task.status` is now `"heal"` (not `None`), so the function falls through to
`if workflow is None: return Finished()` **unconditionally**, with no further look at
`task.step` or any transition table. `Finished()` (`dispatcher.py`, confirmed via
`Decision`/`Finished` in `models.py`) sends the task straight to `done/`. This is
exactly the mechanism invariant #35/#22 rely on to make "target any queue" safe for a
**single**-step workflow-less task (a bare trigger firing directly into one step) — it
is not, and was never meant to be, a general two-step router. `heal`/`file-issue` is
a genuine two-step workflow (per `design-02 §3.2`'s own — correct — reasoning that a
step has exactly one bound behavior), so it needs an actual `Workflow` object in scope
on the second `route()` call, which only happens when `task.workflow_template` is set.

**Fix:** `processes/autoheal.json`'s target must be `{"workflow": "heal"}`, not
`{"step": "heal"}` (`design-02 §4.5`'s data model, the JSON in `design-02 §3.1`/plan's
data model). Traced through with this fix: `ScheduledTrigger._task_for`
(`drivers/scheduled_trigger.py:81-97`) sets `workflow_template="heal"`,
`step=None`. `Dispatcher.tick()` (`dispatcher.py:60-62`) resolves
`self._workflows.get("heal")` — succeeding once `"heal"` is in `served_names`
(`design-02 §1.2` step 1, unchanged, still required). First `route()` call: `workflow
is not None` → `MoveTo(workflow.start)` = `MoveTo("heal")`. Second call (after `heal`
→ `done`): `workflow` is the real `heal.json` `Workflow`, `task.status="heal"` is in
`workflow.steps()`, `workflow.target("heal", "done")` = `"file-issue"` per the
transitions in `design-02 §3.2` → `MoveTo("file-issue")`. Correct, verified against
the real `route()`/`Workflow.target()` contract, not assumed.

This is the single most important correction this review makes — it is not a
stylistic nit, it is the difference between the feature working and the feature
silently doing nothing (no error, no failed task, no log — just a `done/` task with an
unread artifact and no issue). Record it in the ADR as a corrected decision from
`design-02.md`, not as new scope.

### 2.2 BLOCKING: `app.build()`'s internal `known_targets` for process compilation must include served *workflow* names, not just step names

`design-02 §2.1`/`§3.4` moves process compilation (`FilesystemProcessRepository.build`)
inside `app.build()`, computing `known_targets=set(known_steps)` — and `known_steps`
(`app.py:423-428`, unchanged) is built **only** from `workflow.steps()` unioned with
catalog names, never from the *workflow names themselves*. Today, this validation
happens in `cli.py::_run` instead, where `known_targets` (`cli.py:1546-1554`) is
`set(served_names) | steps | catalog_names` — **`served_names` (workflow names) are in
there**. Confirmed directly: `test_process_sources_builds_a_github_issues_process`
and `test_process_sources_builds_a_resolve_conflicts_process`
(`tests/test_cli.py:939-1011`) both pass `known_targets={"default"}` /
`known_targets={"resolver"}` — i.e., a **workflow name**, not a step name — and this
is exactly what today's real `cli.py:1546` computation supplies in production, since
`{"target": "default"}`/`{"target": "resolver"}` are `{"workflow": ...}` targets
(`_parse_target`, `fs_processes.py:160-181`, checks `value in known_targets` where
`value` is the workflow name for a `{"workflow": ...}` target).

Once compilation relocates inside `build()` per `design-02 §3.4`, with
`known_targets=set(known_steps)` (step names only), **every existing
`{"workflow": ...}` process or trigger target silently starts failing validation** —
not just the new `{"workflow": "heal"}` target from §2.1's fix, but the
already-shipped `github-issues` process targeting `{"workflow": "default"}` and the
`github-conflicts` process targeting `{"workflow": "resolver"}`
(`docs`/production configs, and mirrored by `test_processes_e2e.py`'s
`{"target": {"workflow": "default"}}` fixtures). This is a real regression the
relocation introduces, not specific to healing — §2.1's fix would trade one silent
failure (issue never filed) for a loud one (`ProcessValidationError: process autoheal
targets 'heal', which is not a known workflow or step`) unless this is fixed too.

**Fix:** inside `app.build()`, compute the `known_targets` passed into
`FilesystemProcessRepository.build()` as the union of step names **and** served
workflow names:

```python
known_targets = set(known_steps) | set(resolved)
```

`resolved` (`app.py:402`, `dict[name, Workflow]` of the served set) already exists at
exactly the point `design-02 §3.4`'s new block sits (right before the `pollers = [...]`
construction) — no new state needed, just union in the one collection `known_steps`
was missing. This restores parity with `cli.py`'s current (pre-relocation)
`known_targets` computation rather than silently narrowing it. Fold this into FR-6;
it belongs in the same commit as the `checks`/`process_sources` wiring block, not as a
follow-up.

### 2.3 `_write_default_agents`'s proposed generalization breaks the shipped `land` default

`design-02 §3.5` proposes replacing the hardcoded `if step == LANDING_STEP: continue`
(`cli.py:476-477`) with `if workflow.finisher_for(step) is not None: continue`,
claiming it "is strictly less code than a second hardcoded carve-out" and covers
`file-issue` "the same way it covers `land`." Traced directly, this equivalence does
**not** hold: `DEFAULT_DEFINITION` (`cli.py:97-111`) and `RESOLVER_DEFINITION`
(`cli.py:115-122`) — the two workflow files `harness init` actually ships — **neither
declares a `"finishers"` key**. `land`'s binding to the `"open-pr"` kind for both of
them comes entirely from `app.build()`'s own fallback (`app.py:544-545`:
`if landing_step not in step_finishers: step_finishers[landing_step] = "open-pr"`),
which is a `build()`-time computation over the *union of served workflows'*
`finishers` maps — **not** a property the standalone `Workflow` object returned by
`FilesystemWorkflowRepository.get(...)` carries. So `workflow.finisher_for("land")` on
either shipped workflow returns `None` (confirmed: `_parse_workflow`,
`fs_workflows.py:81-102`, builds `finishers` strictly from the raw JSON's own
`"finishers"` key — no cross-referencing of `app.build()`'s implicit default).

Swapping the check as proposed would make the generalized skip condition **false**
for `land` on both `default` and `resolver`, so `_write_default_agents` would start
writing a spurious `agents/land.json` on every fresh `harness init` — a real behavior
regression `design-02.md` did not catch (it reasoned from `heal.json`, which *does*
declare `finishers` explicitly, and didn't re-check the two pre-existing shipped
workflows that don't).

**Fix:** keep both conditions — the generalization is additive, not a replacement:

```python
if step == LANDING_STEP or workflow.finisher_for(step) is not None:
    continue
```

This still covers `file-issue` (bound to `"open-issue"` in `heal.json`'s own
`finishers`, so `finisher_for` returns non-`None` there) while preserving the existing
implicit-default behavior for `land` on workflows that rely on `app.build()`'s
fallback rather than declaring it themselves. (An alternative fix — have `_init` write
`"finishers": {"land": "open-pr"}` explicitly into the two shipped templates — is
rejected: it's unrelated scope creep on files this task has no other reason to touch,
and it wouldn't help already-installed harnesses whose on-disk `workflows/*.json`
predate this task anyway.)

### 2.4 Test-suite hazard: `build()`'s auto-compile of `processes/*.json` will double-fire against tests that also hand-build sources from the same directory

Not a production bug, but a concrete trap for whoever implements FR-6, worth flagging
explicitly rather than leaving to be discovered as a mysterious test failure.
`test_processes_e2e.py::build_harness` (`tests/test_processes_e2e.py:70-84`) writes
`processes/*.json` under `tmp_path` **and separately** calls
`FilesystemProcessRepository(tmp_path / "processes").build(clock=clock)` itself,
passing the result through `build(..., sources=sources or None)`. Once process
compilation moves inside `build()` (§2.1/`design-02 §2.1`), `build()` — given
`processes_root` defaults to `layout.processes`, i.e. `tmp_path/"processes"`, the
exact same directory — will **also** compile that file internally. The same
`ScheduledTrigger` would then exist twice in the harness's effective source list (once
from the test's manual `sources=`, once from `build()`'s own internal
`all_sources`/`pollers` construction), double-firing every task the process produces —
`test_process_fires_a_task_that_reaches_done_and_reflects_nothing` would see **two**
files under `done/`, not one, and fail confusingly rather than the fix being obvious
from the diff.

**Fix, to fold into FR-8's test migration alongside the healer-test table:** simplify
`build_harness` to stop manually compiling — delete the
`FilesystemProcessRepository(...).build(clock=clock)` line and the `sources=sources or
None` passthrough for the process case entirely, relying on `build()`'s own internal
auto-discovery of `tmp_path/"processes"` (this is in fact a nice simplification, not
added complexity — the test's whole point was exercising the auto-discovery path,
which after FR-6 lives one layer closer to where the test already points). Audit
`tests/test_cli.py`'s slack-sink tests (`_slack_process_sources`,
`test_slack_sinks_*`, `cli.py:1039-1096`) too — these call the narrowed
`_process_sources`/`_process_check_factories` directly as a unit test (not through
`build()`), so they are unaffected by the double-compile risk, but do need the
signature-narrowing migration `design-02 §2.2` step 2 already calls for
(`_slack_sinks(declared_kinds: set[str])`). Same audit should sweep any other test
that both seeds `processes/*.json` under a `build()`-visible root **and** separately
threads compiled sources through `sources=`.

## 3. Everything else in `design-02.md` — affirmed, folded in as settled architecture

The remainder of `design-02.md` is verified sound and needs no rework:

- **§1.1 Repo-less attach.** `_attach_repo_less`'s two-branch shape (exists → reset,
  else → init+commit) is the right, idempotent-safe pattern, confirmed against
  `GitWorkspace.attach`'s existing override-reattach idiom (`git_workspace.py:297-319`)
  it mirrors. `push()` genuinely never gets called for this task, confirmed: nothing
  in the `heal`/`file-issue` workflow reaches a step bound to `LandingBehavior`.
- **§1.2 `--heal-repo` as a thin generator.** Confirmed against the real
  `cli.py:1568-1587`/`app.py:373` (`finishers=` override, exercised today by
  `test_caller_supplied_finisher_registry_entry_is_used`) — no `build()` change needed
  for `OpenIssueBehavior`'s own wiring, only for `extra_checks`/`processes_root`
  (§3.4, unchanged by this review beyond §2.2's `known_targets` fix).
- **§2 Slack-sink wiring-order fix.** Confirmed against `app.py:441-446`
  (`SourceReflectorSink(sources)` constructed before any process-compilation code
  could exist) — the `_declared_sink_kinds` pre-scan decoupling is the correct, and
  only structurally sound, fix. No changes needed to this section.
- **§3.1 `FailedTasksCheck` contract**, including the rendered-`data["body"]`
  requirement (confirmed against `compose_prompt`, `behaviors/agent.py:83-121`, which
  really does only ever read `task.data["request"]`/`["body"]` — no structured
  fallback exists, so this fix is load-bearing exactly as described) and the
  recursion-guard-first ordering. No changes.
- **§3.2 the one-sentence persona edit.** Confirmed against the live
  `_HEALER_PERSONA` text (`cli.py:372-373`, "write ... to the file `issue.md` in your
  working directory") — genuinely contradicts `compose_prompt`'s own artifact-path
  line (`behaviors/agent.py:110`) today; the fix is correct and narrowly scoped.
- **§3.3 `OpenIssueBehavior`**, including the `_heal_verdict_summary` history lookup
  (confirmed against the exact `HistoryEntry` shape `Consumer._deliver` writes,
  `consumer.py:90-106`) and the no-try/except error posture (confirmed against
  `Consumer.tick`'s blanket `except Exception` at `consumer.py:75-79`). No changes.
- **§6.1's correction to `design-01`** (that `FilesystemProcessAdmin.check_names()`
  structurally cannot reflect the new check without a `ProcessAdmin` port change) —
  confirmed directly against `fs_processes.py:357-358`
  (`tuple(sorted(BUILTIN_CHECKS))`, no parameter at all). Correct as stated, out of
  scope, no action needed.
- **§7/§8 invariant rewrites, ADR shape, and FR-8's test-migration table** — sound;
  §2.4 above is an addition to, not a replacement of, this table.

## 4. Implementation guidance (sequencing)

1. Implement `FailedTasksCheck` (`drivers/failed_tasks_check.py`) per `design-02 §3.1`
   unchanged — this piece has no dependency on §2's fixes.
2. Implement the repo-less `GitWorkspace.attach` branch and the one-sentence persona
   edit per `design-02 §1.1`/`§3.2` unchanged.
3. Implement `OpenIssueBehavior` per `design-02 §3.3` unchanged.
4. Implement `app.build()`'s wiring (`design-02 §3.4`) **with §2.2's fix applied
   inline**: `known_targets = set(known_steps) | set(resolved)`, not
   `set(known_steps)` alone. Get this right in the same commit — don't land the
   narrower version and "fix it later," since it would pass every test that doesn't
   specifically target a workflow (most of the new FR-1/FR-3 unit tests won't touch
   this path at all) while quietly breaking `github-issues`/`github-conflicts` in any
   real deployment.
5. Write `workflows/heal.json` exactly per `design-02 §3.2`'s JSON (unchanged —
   `{"from": "heal", "on": "done", "to": "file-issue"}` etc. is correct), and write
   `processes/autoheal.json` **with `"target": {"workflow": "heal"}`**, per §2.1's
   fix — not `{"step": "heal"}` as `design-02 §4.5` has it. This is the one line in
   the whole design document that must change before anyone copies its JSON verbatim.
6. Apply `cli.py` changes per `design-02 §3.5`, with §2.3's fix applied to
   `_write_default_agents`'s skip condition (`step == LANDING_STEP or
   workflow.finisher_for(step) is not None`, not the bare replacement).
7. Migrate tests per `design-02 §8` (`FailedTasksCheck`/`OpenIssueBehavior` unit
   tests, the two-hop e2e test, `tests/test_self_heal_e2e.py`'s rewrite), **plus**
   §2.4's `test_processes_e2e.py::build_harness` simplification and the
   `_slack_sinks` signature migration — both belong in the same FR-8 pass, not a
   follow-up.
8. Add one new regression test alongside the FR-1 unit tests: a process (or the
   autoheal process itself) targeting `{"workflow": "heal"}` compiles successfully
   through `app.build()`'s internal `known_targets` — this is the test that would
   have caught §2.2 before it ever reached a real deployment, and none of the
   existing FR-1/FR-3 unit tests (which drive `FailedTasksCheck`/`OpenIssueBehavior`
   directly, not through `build()`) exercise this path at all.
9. Rewrite `CLAUDE.md` invariants 24–27 (+ append 35, 39) and write
   `docs/adr/0018-healing-as-a-process.md` per `design-02 §7`, additionally recording
   §2.1's `{"workflow": ...}` vs `{"step": ...}` distinction as a documented decision
   (a future operator hand-authoring a similar two-step process needs this spelled
   out — it is exactly the kind of mistake this review had to trace through the
   router to catch, and it will recur for the next multi-step process unless it's
   written down).

## 5. Risks and prerequisites

- **Prerequisite:** none beyond what `plan-02.md`/`design-02.md` already established
  (FR-0 done, `origin/main` at the same commit). No new dependency introduced by this
  review's fixes.
- **Risk, mitigated by §2.1/§2.2:** without both fixes applied together, the feature
  would ship in a state where `pytest -q` likely still passes (the FR-1/FR-3 unit
  tests drive the two new components directly, not through the full
  `build()`→dispatch→route chain) while the real, wired-up autoheal process silently
  never files an issue. This is the most dangerous kind of gap — quiet in tests, quiet
  in production (no exception, no failed task, just a `done/` entry) — so §4 step 8's
  new regression test is not optional polish; it is the one piece of coverage that
  would have caught this before a human noticed heal issues stopped appearing.
- **Risk, mitigated by §2.3:** shipping the naive `_write_default_agents`
  generalization would silently start writing `agents/land.json` on every fresh
  `harness init` — caught immediately by any existing test that asserts the shipped
  agent file set (if none currently does, add one alongside the FR-6 work; it is a
  one-line assertion and cheap insurance against this exact regression recurring).
- **Risk, mitigated by §2.4:** `test_processes_e2e.py`'s double-compile trap would
  surface as a confusing test failure ("2 files in done/, expected 1") disconnected
  from its actual cause (the FR-6 relocation) unless the test is simplified in the
  same change that introduces the relocation.
- No new risk to the two invariants this task most directly touches (#2/#3 — the
  dispatcher alone decides *where*, the consumer never branches on outcome): both
  fixes in this review operate entirely within existing mechanisms (`route()`'s
  existing workflow-vs-workflow-less branch, `compile_process`'s existing
  `known_targets` parameter, `_write_default_agents`'s existing skip condition) — no
  new branch is added anywhere that inspects an outcome value or a task's
  repository/data.
