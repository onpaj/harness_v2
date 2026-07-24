# Architecture: Rename 'default' workflow to 'development'

No UI is involved; this section is omitted (per house convention).

## Verdict

The plan and design are **approved as the implementation blueprint**, with one
addition below (a sharper articulation of *why* the migration must be a
byte-identical copy, not just a name change) and one small correction to a
call-site detail. Nothing here changes the shape of `_migrate_legacy_workflow`
or where it's called — it confirms the design against the actual dispatcher
code and tightens the risk section developers should carry into
implementation.

I re-derived the design independently against the current tree
(`src/harness/cli.py`, `app.py`, `dispatcher.py`, `fs_workflows.py`,
`drivers/github_source.py`, `drivers/memory.py`, `README.md`,
`tests/test_cli.py`) rather than trusting the prior artifacts at face value.
Every referenced line number and behavior checked out. One thing the design
under-states, traced below, makes this change slightly higher-stakes than "a
renamed constant plus a copy-forward."

## Alignment with existing patterns

- `_migrate_legacy_workflow` sitting beside `_write_default_agents` /
  `_write_default_repos` in `cli.py` is the right home: same signature shape
  (`layout`, no return value, filesystem-only), same "small, targeted
  function called from `_init`" pattern already established by its
  neighbors.
- The guard style (`if workflow_name != DEFAULT_WORKFLOW: return`) matches
  the codebase's preference for early-return guards over nested
  conditionals (`_init`'s own `invalid_workflow_name` check is the same
  shape).
- No port, no driver, no new abstraction — correctly scoped as a `cli.py`-
  and two-driver-defaults change. This is consistent with invariant-driven
  architecture in this repo: a naming change must not smuggle in a new
  seam. There is none here.

## Confirmed integration points (verified against source, not assumed)

- `cli.py:49` — `DEFAULT_WORKFLOW = "default"`; used at lines 744, 749, 757,
  775 as the `argparse` default for `--workflow` (init/submit/run) and
  `--github-workflow` (run). Renaming the constant alone correctly
  propagates to all four call sites — confirmed no other literal `"default"`
  hides in argparse setup.
- `cli.py:59-71` — `DEFAULT_DEFINITION["name"] == "default"`; written verbatim
  by `_init` at `definition_path = layout.workflows / f"{args.workflow}.json"`
  (line 90) only `if not definition_path.exists()` (line 91). This confirms
  the design's placement of the migration call: it must run *before* line 91
  so a just-copied `development.json` short-circuits this write and the
  operator's edits in the legacy file survive (FR-2/AC1).
- `dispatcher.py:60` — `workflow = self._workflows.get(task.workflow_template)`.
  This is the detail worth surfacing explicitly (see Risks): the dispatcher
  resolves a workflow **per task, by name, at every tick** — not once at
  harness startup. `self._workflows` is a live
  `FilesystemWorkflowRepository(layout.workflows)` (`app.py:233`), so a task
  whose `workflow_template` is the legacy `"default"` will, forever, cause a
  fresh disk read of `workflows/default.json` on every dispatch tick, for as
  long as that task is in flight — regardless of what workflow name the
  harness itself was started with.
- `app.py:233-234` and `app.py:257` — `step_queues` is built **once, at
  `build()` time**, from the steps of the single `workflow_name` passed in
  (i.e. whatever `harness run --workflow ...` resolves to — `development`
  after this change). `dispatcher.tick()` then does
  `self._step_queues.get(decision.step)` (dispatcher.py ~65) using the step
  name that `route()` computed from the **task's own** workflow (looked up
  by `workflow_template`, which may be the legacy `"default"` workflow).
  If those two workflow definitions ever diverge in their step set, a
  legacy task fails with `"step {step!r} has no queue"` — not
  `WorkflowNotFound`, a different and easily-missed failure mode.
- `fs_workflows.py:60-61` (`Workflow.name` falls back to the filename stem
  when absent) — confirmed; irrelevant here since the migration always
  copies file content as-is with its `"name"` field intact.
- `drivers/github_source.py:33`, `drivers/memory.py:212` — both
  `workflow: str = "default"`, confirmed as the only two non-`cli.py`
  literal defaults tied to this concept.
- `README.md:192` — `"name": "default"` in the worked example, confirmed as
  the only doc reference in scope.
- `tests/test_cli.py:8,26,29,38,40,46,72` — confirmed current tests
  hardcode `workflows/default.json` as a path string in three places and
  already import `DEFAULT_WORKFLOW` for one assertion (`workflow_template ==
  DEFAULT_WORKFLOW`, line 72) — the plan's preference for asserting via the
  constant rather than a literal is already the file's own idiom in that one
  spot, so FR-4/AC1 is bringing the other assertions into line with existing
  practice, not inventing a new one.

## The one thing to add to the design: why "copy," not "rename"

The design already forbids deleting or rewriting `default.json`, and the plan
already calls this out as the FR-2/AC2 acceptance criterion. Worth stating
the *mechanism* plainly for whoever implements this, because it's not just
an audit-trail nicety:

A pre-upgrade task in flight (say, sitting in the `review` queue) carries
`workflow_template == "default"`. After the upgrade, `harness run` starts
with `args.workflow` defaulting to `"development"`, so `step_queues` is built
from `workflows/development.json`'s steps. On the next dispatch tick for
that in-flight task, `dispatcher.py:60` resolves its workflow via
`self._workflows.get("default")` — reading `workflows/default.json` fresh
off disk. `route()` computes the next step from *that* definition. For the
`_step_queues.get(...)` lookup two lines later to succeed, `default.json`'s
step names must be a subset of `development.json`'s. The migration
guarantees this by construction (it's a byte-copy: both files start
identical), but it is a guarantee that quietly breaks the moment someone
"cleans up" `default.json` by hand-editing it independently of
`development.json` post-migration, or renames a step in one without the
other. This isn't something the migration code can enforce at runtime — it's
an operational invariant, not a type-checked one. Flag it in the migration
docstring (the design's draft docstring already gestures at this — "a task
created before the upgrade still resolves it by name" — but doesn't say
*why the topology must match*, which is the part a future editor of either
file needs to know).

**Guidance for implementation:** keep the docstring's existing prose, but add
one sentence: state that `default.json` and `development.json` must keep
matching step sets for as long as any legacy task might still be in flight,
because the dispatcher resolves per-task by `workflow_template` while the
step queues are fixed at harness startup from the CLI's own `--workflow`
value. That sentence costs nothing now and prevents a confusing
`"step {step!r} has no queue"` failure later that has nothing to do with
whoever is debugging it having touched this migration at all.

## Implementation guidance

Follow the plan/design as written; below is only what a developer needs
beyond those two documents to execute without re-deriving the above.

1. **`cli.py:49`**: `DEFAULT_WORKFLOW = "default"` → `"development"`.
2. **`cli.py:59-71`**: `DEFAULT_DEFINITION["name"]` → `"development"`. Leave
   `"start"`/`"transitions"` untouched — verified byte-identical is required
   (see above), don't "tidy" the JSON while touching this block.
3. **New function in `cli.py`**, placed directly above or below
   `_write_default_agents` (around line 284):
   ```python
   def _migrate_legacy_workflow(layout: HarnessLayout, workflow_name: str) -> None:
       ...
   ```
   Exactly as specified in `design-01.md`. Add the one-sentence topology
   invariant to the docstring per the section above.
4. **Call site 1 — `_init`** (`cli.py:80-108`): insert the call immediately
   after line 88 (`layout.workflows.mkdir(...)`) and before line 90
   (`definition_path = ...`). Ordering matters: the `mkdir` must run first so
   the migration's `write_text` has a directory to land in on a fresh root
   that only has a legacy file at some other location — though in practice a
   legacy `default.json` implies `workflows/` already exists, `mkdir(...,
   exist_ok=True)` is cheap insurance and keeps the two operations in
   the order the design specifies.
5. **Call site 2 — `_run`** (`cli.py:649-651`): insert the call right after
   line 651 (`layout = HarnessLayout(root)`) and before any of the driver
   construction that follows, definitely before the `build(...)` call
   at line 669. No `layout.workflows.mkdir()` call currently exists on this
   path — that's fine, because the migration only ever *writes* when
   `legacy.exists()` is true, which already implies the directory exists.
6. **`drivers/github_source.py:33`**: `workflow: str = "default"` →
   `workflow: str = "development"`.
7. **`drivers/memory.py:212`**: same change, for the fake.
8. **`README.md:192`** and its surrounding prose: `"default"` → `"development"`
   per FR-3/AC4. Leave prose that describes `--workflow`'s *default value*
   (a flag mechanic) worded as "default" — only the workflow's *name* changes.
9. **`tests/test_cli.py`**: update the three hardcoded
   `workflows/default.json` path assertions (lines ~29, 40, 46) to build the
   path from `DEFAULT_WORKFLOW` instead of a literal, matching the file's
   existing idiom at line 72. Add the two new tests from FR-4/AC2-AC3:
   - migration preserves custom `default.json` content into `development.json`
     without touching `default.json`, on `harness init`.
   - `harness run` (not `init`) against a root with only a legacy
     `default.json` present successfully picks up `development` without a
     prior `init` call — this is the test that actually exercises the
     dispatcher-level concern above, so make sure it drives a task through
     at least one dispatch tick, not just a successful `build()` call, or it
     won't catch a future topology-divergence regression.
10. Run `.venv/bin/pytest -q` in full — nothing in this list touches ports
    or the architecture-guard tests (`test_architecture.py`), so no fallout
    is expected there; if any of those tests fail, that's a signal something
    strayed outside the scoped files.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A legacy task in flight during the upgrade fails with an oblique `"step {step!r} has no queue"` if `default.json` and `development.json` ever diverge in step names | Migration copies byte-for-byte at upgrade time (already in design); document the topology invariant in the migration docstring (see above) so a future editor doesn't break it unknowingly |
| Operator runs `harness init` a second time after manually editing `development.json` | No new risk introduced: `_init`'s existing "create if missing" guard (line 91) already no-ops on a pre-existing file; the migration call sits before it and only fires when `development.json` is absent, so a second `init` run is unaffected regardless of whether the file came from migration or manual edit |
| `harness update` / `uv tool upgrade` bumps the code but the operator never restarts the service, so migration never runs | Out of scope for this change — pre-existing behavior for *any* code update, not specific to this rename. Not a regression this task introduces. |
| This machine's live `~/.harness/workflows/default.json` breaks the running launchd service on next restart if migration is missed | Directly covered by FR-2/AC3 (`_run` performs the same migration) — verify manually per plan step 8 before restarting `com.nanoclaw-v2-8d6e3afc`-adjacent or any harness-specific launchd service on this machine, once this change ships |
| Test coverage exercises `build()` success but not an actual dispatch tick for a migrated legacy task | Call out explicitly in test guidance (#9 above) — assert the migration test drives at least one `dispatcher.tick()` equivalent, not just successful construction |

## Prerequisites before implementation begins

None outstanding. This is a leaf-level, self-contained change with no
upstream dependency, no port or driver addition, and no data-shape change.
The plan and design are both grounded in the actual source (verified above)
and ready to implement as specified, with the one docstring addition and
the sharpened test guidance folded in.

```json
{"outcome": "done", "summary": "Verified plan/design against the live cli.py/app.py/dispatcher.py/fs_workflows.py/github_source.py/memory.py/README.md/test_cli.py; approved the _migrate_legacy_workflow design as-is. Added one finding the design under-stated: dispatcher.py resolves a task's workflow per-tick by workflow_template while step_queues is fixed at harness startup from the CLI's own --workflow, so default.json and development.json must stay topologically identical for in-flight legacy tasks to keep dispatching — documented as a docstring addition and a sharper test requirement (drive an actual dispatch tick, not just build()) rather than a design change."}
```

