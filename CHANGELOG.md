# CHANGELOG


## v0.21.0 (2026-07-24)

### Bug Fixes

- Kill the whole process group on verify command timeout
  ([`c963dfa`](https://github.com/onpaj/harness_v2/commit/c963dfa54c01fb0de10ee225f3c31497a92d7354))

process.kill() only signalled the shell PID; a compound verify command's children (e.g. pytest-xdist
  workers) survived the timeout. Run the shell in its own session (start_new_session=True) and, on
  timeout, kill the whole process group before falling back to process.kill(), both wrapped against
  ProcessLookupError once the group/process is already gone.

### Documentation

- Implementation plan for the verify gate (increment 1)
  ([`4848f1f`](https://github.com/onpaj/harness_v2/commit/4848f1f715372bf0d94724ca4a5c65ebef5e3788))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Regenerate-persona operator step, persona paragraph fix, verify_command JSON test
  ([`8ca5141`](https://github.com/onpaj/harness_v2/commit/8ca51415a9c6cae24cce44ec8cf60fdc954fae29))

- Add a post-merge checklist step to regenerate the development agent (harness agent init
  development --force) so an already-initialized root picks up the new verify-run persona sentence.
  - Move the verify-run sentence in _DEVELOPMENT_PERSONA to extend the revision-round paragraph
  instead of being glued to the closing instruction paragraph. - Add a direct test: malformed
  repos.json JSON -> verify_command() is None.

- Spec for development-flow hardening (verify gate, loop caps, early gates)
  ([`3d14825`](https://github.com/onpaj/harness_v2/commit/3d14825f42d35398e21444b2582bcede8b6c0eeb))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- Commandrunner port with subprocess driver and memory fake
  ([`63b3dd8`](https://github.com/onpaj/harness_v2/commit/63b3dd8326cbe9e00fb73e583997fa27741a53cd))

- Verifybehavior — deterministic verify step over CommandRunner
  ([`d8424c1`](https://github.com/onpaj/harness_v2/commit/d8424c14e9a05edf10edc88f41ba94e4e0f05676))

- Wire the verify gate — finisher kind, cli injection, default workflow
  ([`b994cfb`](https://github.com/onpaj/harness_v2/commit/b994cfb899cbcacfa4b7ea89a0f765ebfc87ed98))

- **repos**: Optional object-form entries with a verify command
  ([`d2d2519`](https://github.com/onpaj/harness_v2/commit/d2d2519031e8020c0b9100e21e1bc988fea5080f))

### Testing

- E2e for the verify gate's request_changes loop
  ([`adaa5e6`](https://github.com/onpaj/harness_v2/commit/adaa5e64c667252a69c20e137bfb2f0e43771732))


## v0.20.0 (2026-07-24)

### Features

- **admin**: Actions declare their parameters as data, consolidating failed-tasks
  ([#118](https://github.com/onpaj/harness_v2/pull/118),
  [`d9a007a`](https://github.com/onpaj/harness_v2/commit/d9a007a2f2f066bfac03cb45c9134aae9673a6b7))


## v0.19.0 (2026-07-24)

### Features

- **admin**: Actions declare their parameters as data, consolidating failed-tasks
  ([#117](https://github.com/onpaj/harness_v2/pull/117),
  [`20a7ad6`](https://github.com/onpaj/harness_v2/commit/20a7ad61ff1188766fe21dec1bcc04fd69969a06))


## v0.18.1 (2026-07-24)

### Bug Fixes

- **board**: Actually render times in the client's local timezone
  ([#116](https://github.com/onpaj/harness_v2/pull/116),
  [`8b1b889`](https://github.com/onpaj/harness_v2/commit/8b1b88971331fe6ed0baa3cf461ecf4c04c05fb4))


## v0.18.0 (2026-07-24)

### Features

- **admin**: Offer the run's full action registry in the process form
  ([#115](https://github.com/onpaj/harness_v2/pull/115),
  [`92986d2`](https://github.com/onpaj/harness_v2/commit/92986d23478a1aa0d380e871daa9d4db2fa7e9aa))

### Refactoring

- Unify the default workflow into development, drop legacy support
  ([`ca9990b`](https://github.com/onpaj/harness_v2/commit/ca9990ba029774469d0391e41b003551ba7ca1d0))

The `default`→`development` rename (#110) shipped as a non-breaking migration:
  `_migrate_legacy_workflow` copied `default.json` forward and `_run` kept serving a legacy
  `default` workflow alongside `development` for any in-flight task still carrying
  `workflow_template: "default"`. That transition is complete, so remove the migration function, its
  two call sites, and the legacy-serving block, and fix the stale `--workflow`/`--github-workflow`
  help text that still named `default`. `development` is now the sole primary workflow; `harness
  init` writes only `development.json`. Drops the two migration tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.17.0 (2026-07-24)

### Documentation

- Spec and plan for workflow-defined outcomes ([#102](https://github.com/onpaj/harness_v2/pull/102),
  [`5bb19e9`](https://github.com/onpaj/harness_v2/commit/5bb19e91a3b6042394a9f2fd4b5bbeebfbcf8844))

- **site**: Redesign the architecture explorer as a port-first catalogue
  ([#101](https://github.com/onpaj/harness_v2/pull/101),
  [`e4485d6`](https://github.com/onpaj/harness_v2/commit/e4485d6ccbd20231a38909b163a23c1bcef62cd9))

### Features

- Convert self-healing from a bespoke loop into an autoheal Process
  ([`87de623`](https://github.com/onpaj/harness_v2/commit/87de6231456fdc2862b7fb787f0fc5f18b47ceb0))

The Healer was a bespoke core loop (`healer.py`, `HealConfig`, `Harness._heal_loop`) — the single
  reader of `failed/`, with its own config surface and outbound path, predating the Process idiom
  (ADR-0015). Re-express it in the one general idiom:

- `failed-tasks` Check (`drivers/failed_tasks_check.py`) drains `failed/`: claims each failed task,
  settles it to `healed/`, emits one Observation carrying the rendered failure report; recursion
  guarded by a `data.heal` marker, not by construction. - two-step `heal` workflow (`heal` ->
  `file-issue`), target `{"workflow": "heal"}` so `file-issue` actually runs. - `open-issue`
  finisher (`behaviors/open_issue.py`) opens the drafted issue via `IssueTracker`, idempotent by the
  original failed-task id — a generic finisher future processes reuse. - process compilation moves
  into `app.build()` (the check must close over the live `events`/`failed`/`healed` queues);
  `build()` gains `extra_checks` / `processes_root`, loses `heal` / `issue_tracker`. - `--heal-repo`
  survives as a thin generator: registers the `open-issue` finisher and writes
  `processes/autoheal.json` (only if absent).

Removes `healer.py`, `HealConfig`, `_heal_loop`. Rewrites invariants 24-27, adds ADR-0018, updates
  the architecture model, CLAUDE.md and README. Migrates the healer tests to the Process path.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Enable autoheal flag-free via HARNESS_HEAL_REPO env var
  ([`e73ef9f`](https://github.com/onpaj/harness_v2/commit/e73ef9f763b4271721e3826c8341ef1a37cde8f4))

`--heal-repo` was the only way to name the repo heal issues are filed on, so the launchd service
  could not self-heal without a CLI flag. Add `HARNESS_HEAL_REPO` as an equivalent enablement,
  mirroring how `SLACK_WEBHOOK_URL` gates the slack sink: set it in the service env and the run
  serves `heal`, registers the `open-issue` finisher on that repo, and writes
  `processes/autoheal.json` if absent — no run flag. `--heal-repo` stays as the interactive
  convenience. A non-claude agent with a heal repo set now warns instead of hard-failing (so an
  env-configured service never crash-loops).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.16.0 (2026-07-23)

### Features

- Sync the base branch into the task branch before landing opens the PR
  ([#90](https://github.com/onpaj/harness_v2/pull/90),
  [`bec080b`](https://github.com/onpaj/harness_v2/commit/bec080bcd5f7d894c7bf893d06df2d41725dbe29))

A task's worktree branch is created from HEAD when the task starts and is never re-synced with the
  base while it travels plan -> ... -> land, so on a repo where main moves during that window the PR
  is born behind its base -- stale at best, conflicted at worst, and only reconciled later by the
  out-of-band resolver sweep.

Landing now merges the PR's base branch into the task branch before it pushes and proposes, so the
  PR is born up-to-date with base:

- Forge.base_branch(task) is the branch the forge opens the PR against (GithubForge -> default
  branch, cached per slug; fakes -> configurable, default "main"), so the merge base always matches
  the PR base -- never a hardcoded "main". - A clean merge is committed onto the task branch ([land]
  merge <base>); an already-up-to-date base is a no-op. - A conflict landing cannot auto-resolve is
  abandoned via the new WorkspaceHandle.abort_merge(), and the PR is opened un-merged and flagged in
  the result summary -- the existing resolver workflow reconciles the dirty PR downstream. Landing
  never fails on a base conflict: pure improvement for clean merges, status quo for conflicts.

The sync lives entirely in LandingBehavior.run -- no new port, loop or wiring; behavior_for still
  selects landing as the open-pr finisher unchanged.

The e2e/smoke fixtures now publish their initial commit as origin/main (a PR's base branch always
  exists on the remote in reality), and a new real-git smoke proves a divergent-but-clean base is
  merged in so the branch is born up-to-date. Full suite: 1220 passed, 1 skipped.

See ADR-0017.

Co-authored-by: Claude <noreply@anthropic.com>

- **ui**: Redesign the process editor as a guided, mobile-friendly form
  ([#92](https://github.com/onpaj/harness_v2/pull/92),
  [`e8a6bff`](https://github.com/onpaj/harness_v2/commit/e8a6bff96ea143431360156895215240cff0ee36))

The process admin was a flat form: a free-text interval, a raw JSON params textarea, an unexplained
  target-kind pair and dedup jargon, and a list page of bare name links. Replace both pages with a
  guided editor in the board's design language, keeping every form field name and route contract
  intact.

- List page: summary cards (schedule + check -> target, dedup/sink tags, broken-definition marker)
  and an inviting empty state; the route now reads each definition to render the summary. - Editor:
  numbered sections (Name / Schedule / Action / Target / Options). Interval gets preset chips synced
  with the text input; checks are radio cards with plain-English descriptions; check settings are
  structured per-check fields kept in sync with the raw-JSON textarea (collapsed as a fallback,
  opened on a params error or an unknown check); the target is a workflow/step segmented toggle
  whose suggestion list follows the kind; dedup and sink are explained option cards. A live summary
  sentence mirrors the current state, the save button sticks above the tab bar on the phone, and
  delete moves into a danger zone. - Switching checks prunes params keys that belong only to other
  known checks, so the previous check's settings no longer linger in the JSON. - routes.py: target
  options are now split into workflow/step groups and the list route passes full fields; validation
  and field names are unchanged.

Claude-Session: https://claude.ai/code/session_012V4Bg2Je7ULxHC4maisGuk

Co-authored-by: Claude <noreply@anthropic.com>


## v0.15.0 (2026-07-23)

### Features

- Show per-agent task history in the agent detail view
  ([#75](https://github.com/onpaj/harness_v2/pull/75),
  [`6a0ba5b`](https://github.com/onpaj/harness_v2/commit/6a0ba5be0850c9b14d5f5239b8631185528e5543))


## v0.14.0 (2026-07-23)

### Documentation

- Spec for the github-conflicts action (resolver as a Process)
  ([`771bba4`](https://github.com/onpaj/harness_v2/commit/771bba453a49f0b5680eacba902921c7bc451c46))

- Validate the composable-process vision against the code
  ([#87](https://github.com/onpaj/harness_v2/pull/87),
  [`9ce676c`](https://github.com/onpaj/harness_v2/commit/9ce676cd95ba35589e04c42c2531ee92c8a348c1))

### Features

- Github-conflicts action — conflict detection as a Process check
  ([`10df67b`](https://github.com/onpaj/harness_v2/commit/10df67bc83c165cbf78e76bfc29bebf07ae4fac9))

A `GithubConflictsCheck` (sibling of `GithubIssuesCheck`) lists harness-authored open PRs across the
  registry, auto-updates `behind` ones server-side, and emits one resolver task per `dirty` PR
  carrying `data.branch`/`data.source.base`. Registered as the `github-conflicts` action in
  `cli._process_sources`, so the resolver's conflict detection becomes an authorable Process instead
  of the bespoke `GithubMergeabilityWatcher`. Dedup is per-state on `slug:pr:head_sha`.

- Serve the resolver workflow whenever its definition exists
  ([`a5cd377`](https://github.com/onpaj/harness_v2/commit/a5cd3774f2f83ddf783f98097c87ca7c40d70012))

Decouple serving `resolver` from the `--watch-mergeability` flag: it rides alongside the primary
  workflow whenever `workflows/resolver.json` exists. A `github-conflicts` process targets the
  resolver workflow, and a process whose target is not served fails to compile — so the
  process-based detection path needs the resolver served independently of the watcher. Existing
  served-set tests updated to the new contract (the scaffolded resolver is always served).


## v0.13.0 (2026-07-23)

### Features

- Github-issues action — the harness:todo trigger as a Process
  ([#79](https://github.com/onpaj/harness_v2/pull/79),
  [`e181607`](https://github.com/onpaj/harness_v2/commit/e181607e4e89aa7300c60281041b987fca50b474))


## v0.12.0 (2026-07-22)

### Documentation

- Spec, plan and ADR-0015 for the Process authoring aggregate
  ([#77](https://github.com/onpaj/harness_v2/pull/77),
  [`ebaa9b5`](https://github.com/onpaj/harness_v2/commit/ebaa9b500f54eee11df2059ce230070054e9acd4))

### Features

- Structured board editor for processes (ProcessAdmin UI)
  ([#78](https://github.com/onpaj/harness_v2/pull/78),
  [`a550521`](https://github.com/onpaj/harness_v2/commit/a5505212d049dc9eb9099748be2852ce93430dfa))


## v0.11.0 (2026-07-22)

### Features

- **agents**: Default step models from v1 personas
  ([#74](https://github.com/onpaj/harness_v2/pull/74),
  [`f91fb05`](https://github.com/onpaj/harness_v2/commit/f91fb057c566a79efa427a530c6322bac45c0e88))

The default agent personas were carried over from harness v1 but their model was left null, so every
  step ran on the CLI's configured default. v1 assigned each persona a model tier; restore that
  mapping per step, written as a CLI alias so it tracks the latest of the tier instead of pinning a
  now-retired id:

plan, architecture -> opus (v1 analyst/planner, architect) design, development -> sonnet (v1
  designer, developer) review -> sonnet (v1 code-reviewer, the full-diff review) resolve -> sonnet
  (developer-class conflict fix) healer -> opus (conservative diagnosis)

A step with no mapping still gets model null. The operator can pin an exact id in agents/<step>.json
  as before.

Claude-Session: https://claude.ai/code/session_01XzKK1gNSuT8bYBStRkCJQL

Co-authored-by: Claude <noreply@anthropic.com>


## v0.10.1 (2026-07-22)

### Bug Fixes

- Correct black-on-dark task-detail text in dark mode
  ([#72](https://github.com/onpaj/harness_v2/pull/72),
  [`4195c5b`](https://github.com/onpaj/harness_v2/commit/4195c5b2fe9825d0967c98fe8198b9213e4ae116))

The task-detail sheet renders inside a <dialog>, whose UA rule `color: CanvasText` overrides
  inherited color. With no `color-scheme` declared, CanvasText resolved to its light value (black)
  even in dark mode, so the .kv values rendered black-on-dark (the keys stayed visible because they
  set an explicit color).

Declare `color-scheme: light dark` on :root so system colors track the theme, and set an explicit
  `color: var(--text)` on .task-detail so the detail content never depends on system colors.

Claude-Session: https://claude.ai/code/session_0168J1hKcrtu7JKdNJ3Y3Jeb

Co-authored-by: Claude <noreply@anthropic.com>

### Documentation

- Document self-healing (the healer on the failed queue) in the README
  ([#68](https://github.com/onpaj/harness_v2/pull/68),
  [`d76a170`](https://github.com/onpaj/harness_v2/commit/d76a17080dd92a1176ccdc3e06b264463613fae6))


## v0.10.0 (2026-07-22)

### Documentation

- Add generic triggers design spec and ADR-0014 ([#57](https://github.com/onpaj/harness_v2/pull/57),
  [`2f7b6a6`](https://github.com/onpaj/harness_v2/commit/2f7b6a69b9fa9b111f14c61af9ad2b3ddbdcdf55))

* docs: add generic triggers design spec and ADR-0013

Design a generic trigger mechanism for schedule- and condition-driven task creation, on top of the
  existing TaskSource port.

- Spec: 2026-07-22-generic-triggers-design.md — a Trigger is a TaskSource that produces tasks and
  reflects nothing outward; ScheduledTrigger composes interval x check x target; cadence is gated on
  the Clock; dedup is bucket-keyed for at-most-once per interval across restarts; triggers declared
  as data in triggers/*.json with a named check registry. - ADR-0013: triggers produce tasks, never
  queue placements — the dispatcher stays the sole placement authority (invariants #3/#8), and "any
  queue" is a workflow-less task naming a step.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

Claude-Session: https://claude.ai/code/session_01UDvCSno7r1B5tLSF5HHzi6

* docs: add generic triggers implementation plan

TDD task-by-task plan for the generic triggers spec: Trigger base, Check port + interval parsing,
  ScheduledTrigger, built-in checks, FilesystemTriggerRepository, wiring + e2e, and
  architecture/docs. Seven tasks with a dependency graph and implementation notes; no new production
  dependency, no build() signature change, no new run loop.

* feat: Trigger base — a TaskSource with no outward projection

Add Trigger(TaskSource) with concrete no-op report_progress/finish and an inherited abstract poll();
  a schedule- or condition-trigger implements only poll() and is listed-and-ignored by
  SourceReflectorSink (it stamps no matching data.source). Implements invariant #35.

* feat: Check port, Observation, interval parsing

Add ports/triggers.py: Observation (state_key + data), the Check ABC (evaluate ->
  list[Observation]), the CheckFactory alias, and parse_interval for "s"/"m"/"h" durations. Pure
  port — no driver imports.

* feat: built-in trigger checks (always, disk-threshold)

AlwaysCheck fires one empty observation per interval; DiskThresholdCheck fires when used/total
  crosses a percent (injectable usage reader, guards total==0). BUILTIN_CHECKS maps the names to
  CheckFactory callables.

* feat: ScheduledTrigger — interval x check x target

A Trigger that fires a Check on a clock-gated interval bucket and emits one task per Observation,
  targeting a workflow or a single step. dedup_key is bucket-keyed (per-interval) or state-keyed
  (per-state) for at-most-once ingestion across restarts. Stamps no data.source. Implements
  invariants #34/#36/#37.

* feat: FilesystemTriggerRepository — triggers/*.json

Reads triggers/*.json and builds one ScheduledTrigger per file, failing fast (TriggerValidationError
  naming the file) on a bad kind/interval/check/ target/dedup, or a target outside a supplied
  known_targets set. Missing directory yields an empty list.

* feat: wire scheduled triggers; harness init writes triggers/

_run reads triggers/*.json via FilesystemTriggerRepository and appends the ScheduledTriggers to the
  existing sources list — no build() parameter and no new run loop, since a Trigger is a TaskSource.
  known_targets (served workflow names, their steps, and catalog agents) lets the repository reject
  a misnamed target at startup. harness init now creates triggers/. Missing triggers/ keeps
  behaviour identical to before.

* docs+test: architecture guards and CLAUDE.md for generic triggers

Add test_orchestration_does_not_import_triggers_port and
  test_scheduled_trigger_imports_only_ports_models_and_ids. Document the feature in CLAUDE.md:
  invariants 34-37, module-map rows and bullets for ports/triggers and
  drivers/{scheduled_trigger,checks,fs_triggers}, a responsibilities note, and a gotcha on the
  non-constant bucket-keyed dedup_key and the at-most-once-per-interval limitation.

* feat: retire dashboard tasks whose GitHub issue was closed or deleted (#58)

* fix: update Board.columns access to per-workflow tabs (#62)

PR #48 refactored Board from a flat `columns` tuple into `workflows` (a tuple of BoardTab, each
  carrying its own columns), but three call sites still read the old flat `.columns`:

- `_new_step_warnings` in api/routes.py raised AttributeError on every workflow-admin PUT /
  create-form request, taking CI red. - the client fixtures in test_api_agents.py and
  test_api_workflows.py built a Board with the removed `columns=` kwarg (TypeError at setup).

Collect known step names across every tab's columns and rebuild the test boards through BoardTab.

Co-authored-by: ci <ci@local>

* feat(docs): interactive Architecture Explorer documentation site (#63)

* chore(release): 0.9.0

[skip ci]

* Add a CI gate that runs the full test suite on every pull request and blocks merge unless all
  tests pass (#60)

* fix: give the resolver's git merge an identity and reconcile its branch on reattach (#61)

* chore(release): 0.9.1

* Add a 'healer' workflow: a new failed-queue TaskSource polls failed/ for default-workflow failures
  and enqueues a deduped healer task that diagnoses the failure and, on a confirmed harness bug,
  auto-files a GitHub issue via a new Forge.open_issue verb (#55)

---------

Co-authored-by: Claude <noreply@anthropic.com>

Co-authored-by: semantic-release <semantic-release>

### Features

- Add a board Update button that upgrades the harness and restarts
  ([#67](https://github.com/onpaj/harness_v2/pull/67),
  [`cd8a67e`](https://github.com/onpaj/harness_v2/commit/cd8a67eb522df6c2b416bdb4c5cd4ddcefafd725))


## v0.9.1 (2026-07-22)

### Bug Fixes

- Give the resolver's git merge an identity and reconcile its branch on reattach
  ([#61](https://github.com/onpaj/harness_v2/pull/61),
  [`fd700ae`](https://github.com/onpaj/harness_v2/commit/fd700ae1ea2809ceaf144e0ddc0d933e350fe39c))


## v0.9.0 (2026-07-22)

### Bug Fixes

- Update Board.columns access to per-workflow tabs
  ([#62](https://github.com/onpaj/harness_v2/pull/62),
  [`14a1e28`](https://github.com/onpaj/harness_v2/commit/14a1e28fdf57b37ad4b730190028176870b740c5))

PR #48 refactored Board from a flat `columns` tuple into `workflows` (a tuple of BoardTab, each
  carrying its own columns), but three call sites still read the old flat `.columns`:

- `_new_step_warnings` in api/routes.py raised AttributeError on every workflow-admin PUT /
  create-form request, taking CI red. - the client fixtures in test_api_agents.py and
  test_api_workflows.py built a Board with the removed `columns=` kwarg (TypeError at setup).

Collect known step names across every tab's columns and rebuild the test boards through BoardTab.

Co-authored-by: ci <ci@local>

### Continuous Integration

- Publish the docs drill-down to GitHub Pages ([#54](https://github.com/onpaj/harness_v2/pull/54),
  [`2ba3122`](https://github.com/onpaj/harness_v2/commit/2ba3122309b66f0ff77b55dd37809c981e46cb74))

### Documentation

- Add self-healing design spec and implementation plan
  ([#42](https://github.com/onpaj/harness_v2/pull/42),
  [`6d96d36`](https://github.com/onpaj/harness_v2/commit/6d96d365e8ab9ba3bd64ac50236fdf1b84d499fa))

- Ground the architecture in ADRs, refresh README/CLAUDE.md, and ship an HTML drill-down
  ([#39](https://github.com/onpaj/harness_v2/pull/39),
  [`7bae036`](https://github.com/onpaj/harness_v2/commit/7bae03653c211fa75c532281fda663b7dced5127))

### Features

- Retire dashboard tasks whose GitHub issue was closed or deleted
  ([#58](https://github.com/onpaj/harness_v2/pull/58),
  [`a80afef`](https://github.com/onpaj/harness_v2/commit/a80afef3af8ee227c47bd393448889616ade34ca))

- **docs**: Interactive Architecture Explorer documentation site
  ([#63](https://github.com/onpaj/harness_v2/pull/63),
  [`b533208`](https://github.com/onpaj/harness_v2/commit/b5332080e0c121e2f6d3d8615c85a430b2f5a104))


## v0.8.1 (2026-07-21)

### Bug Fixes

- Recover a finished agent run whose verdict block is missing
  ([`6333c6e`](https://github.com/onpaj/harness_v2/commit/6333c6e729e074310eec896c75865a2a604f6920))

An agent that ran to completion but ended with a prose summary instead of the required ```json
  {outcome, summary}``` block was failing the whole task ("verdict is not readable JSON"),
  discarding a green run (tests passing, artifact written, uncommitted). This hit the development
  step repeatedly.

Three composed defenses: - A (fallback_verdict): a single-outcome step (development/plan/design/
  architecture) is unambiguous, so a missing block is synthesized to that outcome with the final
  text as the summary — no second claude call. - C (_reprompt_verdict): a multi-outcome step
  (review) is genuinely ambiguous, so re-enter the same session via `claude -p --resume` and ask for
  just the verdict; best-effort, any failure falls through. - B (compose_prompt): state the verdict
  block as mandatory and last, so the model omits it less often to begin with.

Envelope-level failures (is_error, no result, non-zero exit, timeout) still fail hard — only a
  forgotten/garbled/disallowed verdict is now recoverable. verdict parsing is split into a strict
  path (parse_verdict/verdict_from_final, unchanged contract) and a tolerant try_verdict the runner
  drives.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.8.0 (2026-07-21)

### Features

- Serve multiple workflows in a single running harness
  ([#31](https://github.com/onpaj/harness_v2/pull/31),
  [`24a34db`](https://github.com/onpaj/harness_v2/commit/24a34db2be66b2f781d14359795492a962a1c55d))

Build queues, projection and consumers for every served workflow: union step queues by name, a
  merged BoardProjection, a ServedWorkflowRepository decorator for clear unserved-workflow failures,
  and repeatable --workflow/--all-workflows on `harness run`. Default single-workflow behavior is
  preserved.

Closes #29


## v0.7.0 (2026-07-21)

### Features

- Configurable per-step agent timeout, default raised to 1800s
  ([#30](https://github.com/onpaj/harness_v2/pull/30),
  [`56a50b8`](https://github.com/onpaj/harness_v2/commit/56a50b8b88670ed30f756b37288e84e5433b6d45))

Raise the default agent timeout 600s->1800s and add a per-step override via an optional
  AgentSpec.timeout field read from agents/<step>.json, resolved in app.py's behavior_for().

Closes #28


## v0.6.0 (2026-07-21)

### Features

- Make the dashboard mobile friendly ([#21](https://github.com/onpaj/harness_v2/pull/21),
  [`a530359`](https://github.com/onpaj/harness_v2/commit/a5303594ced4c487068d83aa77e9ea54552d2ce2))

CSS-only responsive layout for the board: a @media(max-width:767px) block for
  .column/.card/dialog/.tabs and a .table-scroll wrapper on the task-detail tables, layered onto the
  tabbed task-detail design. Reconciled with the tab redesign that landed on main after this branch
  was cut.

Closes #16


## v0.5.0 (2026-07-21)

### Features

- Add per-step max-parallel-task limits to workflows
  ([#27](https://github.com/onpaj/harness_v2/pull/27),
  [`6e16c73`](https://github.com/onpaj/harness_v2/commit/6e16c73a15b51c858952586579a194675416582f))

Adds a validated maxParallel map on the workflow JSON (default 1 per step),
  Workflow.max_parallel_for() accessor, Consumer.step property, and Harness.run() spawning N
  concurrent consumer loops per step. Relies on the existing atomic queue claim; no changes to
  Dispatcher or router.

Closes #25

- Create-harness-issue skill creates directly when inputs are complete
  ([#26](https://github.com/onpaj/harness_v2/pull/26),
  [`b0554f0`](https://github.com/onpaj/harness_v2/commit/b0554f0e6634c3d4f78e86d27285a057d01e9b0f))

Rewrites SKILL.md step 4 into a completeness-check router
  (repo_resolved/title_concrete/body_substantive) that creates the issue directly when all three
  pass and asks a targeted question otherwise. Extends step 6 into a five-field post-creation
  report.

Closes #24


## v0.4.0 (2026-07-21)

### Features

- One-step update+restart, idle-gated, and a schedule for it
  ([`998c8b8`](https://github.com/onpaj/harness_v2/commit/998c8b8fb525445c0632a1f9ff2a6abe45dcac55))

Updating meant two commands and remembering the second, and nothing kept the box current on its own.
  Three additions:

- `harness update --restart` upgrades and restarts the service in one step. - `--only-if-idle` skips
  the restart when a stage is mid-run (a task claimed in a queue's .processing/), so an update never
  kills a live agent — it applies at the next idle restart instead. - `harness service autoupdate`
  installs a launchd timer that runs `update --restart --only-if-idle` a few times a day (default
  02/08/14/20).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.3.1 (2026-07-21)

### Bug Fixes

- Detect an active token, not the template's commented example
  ([`89df965`](https://github.com/onpaj/harness_v2/commit/89df9650840eb5d622bd210c36f48acbc2f905ce))

harness service install printed no setup-token guidance because the check matched the commented
  CLAUDE_CODE_OAUTH_TOKEN= example line in the template it had just written. It now looks for an
  uncommented assignment.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.3.0 (2026-07-21)

### Features

- Service sources a secrets file for CLAUDE_CODE_OAUTH_TOKEN
  ([`e98a543`](https://github.com/onpaj/harness_v2/commit/e98a5439f877c7e977b17d842cc3c329f62148fb))

Under launchd, claude cannot read the macOS login keychain where an interactive login stores its
  credential, so every agent step failed with "Not logged in" even though the same binary works from
  a shell. Proven by running claude inside a launchd agent.

The service wrapper now sources <root>/secrets.env (created 0600, never overwritten) and exports
  CLAUDE_CODE_OAUTH_TOKEN from it, which makes claude bypass the keychain — the supported headless
  path via `claude setup-token`. A missing token warns loudly rather than failing silently, and the
  install prints the exact setup-token steps.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.2.2 (2026-07-20)

### Bug Fixes

- Dummy writes where the agent does, and forge reports GitHub's reason
  ([`0c8027b`](https://github.com/onpaj/harness_v2/commit/0c8027b58155dd01d68e502e4e838d424e8036ea))

A live end-to-end run failed at land with a bare "HTTP Error 422: Unprocessable Entity". Two
  separate faults behind it:

- DummyBehavior wrote its work into `.harness/`, which repos routinely gitignore (this one does).
  Ignored writes stage nothing, so commit() returned None, the task branch carried no diff, and
  GitHub correctly refused a PR with no commits. It now writes into `.artifacts/<task>/`, the
  versioned location the real agent uses (invariant 16) — so --agent dummy can actually exercise
  landing. - urllib's HTTPError stringifies to just the status line. GitHub puts the real reason in
  the response body ("No commits between main and ..."); the forge now surfaces it, along with the
  head -> base it attempted.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.2.1 (2026-07-20)

### Bug Fixes

- Locate the task's repository through the registry, not task.worktree
  ([`29c6aec`](https://github.com/onpaj/harness_v2/commit/29c6aec8fe26549402bb69c9dea755caccbdc729))

A live run failed at land with "has no worktree": GithubForge read task.worktree, but `harness
  submit` never sets it — only GithubTaskSource does. Every unit test happened to build tasks with
  one, so the gap was invisible.

`task.repository` is a name and resolving names to paths is the registry's job (invariant 15); the
  worktree stays as a fallback.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.2.0 (2026-07-20)

### Bug Fixes

- Httpgithubclient reports the confirmed PR head, not the requested one
  ([`9f818cc`](https://github.com/onpaj/harness_v2/commit/9f818cc66f2e4aa6295d0bc06aee53b1e0acf2e8))

find_pull_request and create_pull_request built PullRequestRef.head from the caller's argument
  instead of the server's response, contradicting the docstring's claim that it reflects what the
  API returned. Read item["head"]["label"] instead, falling back to the argument when the field is
  absent or malformed.

Also make add_label set Content-Type: application/json like the sibling create_pull_request, since
  both POST a JSON body.

- Push the task branch without force
  ([`411e2c2`](https://github.com/onpaj/harness_v2/commit/411e2c247471d98e3c2f62eb5736c1928472f37c))

reset-on-reattach only discards uncommitted working-tree state (reset --hard + clean -fd); it never
  rewinds the task branch, so the branch only ever moves forward. A plain push is therefore correct
  — --force-with-lease was masking the real invariant. A rejected push now means something else
  touched the branch and must fail loudly, per the design intent of this series.

### Documentation

- Correct the plan's push justification (no force needed)
  ([`8eb80f7`](https://github.com/onpaj/harness_v2/commit/8eb80f75c4441c743b50798bd81f7e5020017897))

- Implementation plan for the GitHub forge
  ([`383254f`](https://github.com/onpaj/harness_v2/commit/383254fff008773941043ca1d9dea745e9881cec))

Five TDD tasks: WorkspaceHandle.push(), PR verbs on GithubClient, the GithubForge driver, landing
  pushing before it proposes, and the --forge flag. Also corrects the spec's claim that `harness
  doctor` exists on main — it ships with the unmerged issue #14 work.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Spec for the GitHub forge — landing opens a real pull request
  ([`00247e1`](https://github.com/onpaj/harness_v2/commit/00247e1ab7000d8ff48027d3c4ddeb28d2e6af5f))

land reported success while FakeForge only appended to prs.json and the task branch was never
  pushed. Specs the GithubForge driver, the missing WorkspaceHandle.push(), and making a failed PR
  loud instead of silent.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- --agent dummy runs the pipeline without claude
  ([`2a013f1`](https://github.com/onpaj/harness_v2/commit/2a013f1120f727974192465ca66d3ad647c7e669))

Every step shells out to `claude`, so an expired login fails every task and there is no way to test
  the rest of the pipeline. `--agent dummy` leaves the catalog and runner unset, which makes build()
  fall back to DummyBehavior for the step queues while worktree, commits, push and forge all stay
  real.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Githubforge opens real pull requests, failing loudly
  ([`d5c4cfc`](https://github.com/onpaj/harness_v2/commit/d5c4cfcce2af9af2f9d005da6156eaee7deebf01))

- Land pushes the branch and opens a real GitHub pull request
  ([`456a285`](https://github.com/onpaj/harness_v2/commit/456a2856e9e65903bd0a7fe23243629ca78acb2a))

Completes the forge: `land` now calls WorkspaceHandle.push() before proposing, and `harness run`
  defaults to --forge github, wiring GithubForge instead of the prs.json stub. `--forge fake` keeps
  the old behaviour for offline runs.

The git e2e and smoke fixtures gain a bare sibling remote: landing genuinely requires a pushable
  origin now, and a repo without one must fail rather than quietly report a PR that does not exist.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Pull-request verbs on GithubClient (default branch, find, create)
  ([`ce5282b`](https://github.com/onpaj/harness_v2/commit/ce5282b41052c418bbcb9aaf5e3d8f22f06d5b5e))

- Workspacehandle.push() publishes the task branch to origin
  ([`04bec01`](https://github.com/onpaj/harness_v2/commit/04bec01f5b6acdd9350a487f0b9d29d052fbfb8e))

Also adds push() to the RealFsHandle test double in tests/test_agent_behavior.py so it keeps
  satisfying the now-larger WorkspaceHandle ABC.


## v0.1.0 (2026-07-20)

### Bug Fixes

- Cli --root precedence, workflow-name validace, exit-2 pokrytí
  ([`224a577`](https://github.com/onpaj/harness_v2/commit/224a577d16c713bf9789d8ce124c1e88970f56c3))

- --root/--workflow zadané před podpříkazem se dřív tiše zahazovalo (argparse subparser namespace
  přepíše rodiče) a harness sáhl na chybný výchozí kořen; top-level deklarace --root byla mrtvá a je
  pryč, takže selhání je teď hlasité (SystemExit 2). - `init --workflow` s neplatným jménem (např.
  "foo/bar") už nespadne s FileNotFoundError z write_text, ale vrátí čisté chyba:...exit 2 -
  validace stejných pravidel jako FilesystemWorkflowRepository.get. - testy na exit 2 teď ověřují,
  že hláška jde na stderr a stdout zůstává prázdné (capsys), plus nový test na třetí zdokumentovanou
  chybovou cestu (neznámý workflow přes `run`).

- Dedup GitHub issue ingestion against list read-after-write lag
  ([#6](https://github.com/onpaj/harness_v2/pull/6),
  [`6eb92fe`](https://github.com/onpaj/harness_v2/commit/6eb92fe56217ab8a477ed2cd6727743ac77d6206))

GithubTaskSource claimed by swapping harness:todo -> harness:queued, but list_issues reads with
  read-after-write lag (unlike the atomic rename it mirrors), so a fast poll re-claimed the same
  issue two or three times. Add an in-process ledger of claimed issue numbers so each issue ingests
  at most once per process. Also raise the Conductor loop --poll to 5s and enable the
  onpaj/Anela.Heblo GitHub source.

@claude

- Deduplicate ingested tasks by a persistent source identity
  ([#17](https://github.com/onpaj/harness_v2/pull/17),
  [`e381034`](https://github.com/onpaj/harness_v2/commit/e381034cc8aa9e22c86d4925fe8a7e0671cf2b5f))

- Derive the service entry point from sys.prefix, not sys.executable
  ([`6f3527e`](https://github.com/onpaj/harness_v2/commit/6f3527e589ef4eabad1cf97e26a1dd430ac013c0))

Resolving sys.executable follows the venv's python symlink out to the base interpreter; with
  uv-managed CPython that lands in ~/.local/share/uv/... where no harness script exists, so 'service
  install' aborted. Caught by installing for real, hence the regression test.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Fs_queue vanished-file crash + missing quarantine/recover coverage
  ([`717d304`](https://github.com/onpaj/harness_v2/commit/717d304f3a24ca62022dac34d2396a1300eeb79c))

list() treated a file that vanished mid-iteration (lost claim race) as corruption, quarantining it
  via shutil.move() outside any try/except — crashing the whole call instead of skipping the benign
  race silently. _read() now distinguishes FileNotFoundError (skip, no event) from real
  deserialization failures, and _quarantine_file tolerates the file disappearing again before the
  move runs.

Also cover the previously-untested FilesystemTaskQueue quarantine branch and recover()'s
  corrupt-file path, and make _write's temp filename unique per writer (uuid4) instead of shared per
  destination path.

- Pin the platform in the service-install root test
  ([`d6b5495`](https://github.com/onpaj/harness_v2/commit/d6b5495d7c70a8c4163c9675f92155c38367e8ed))

The test asserted the uninitialized-root message but ran on a Linux CI runner, where the launchd
  guard returns first. It only passed locally because it was written on macOS.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Recover() quarantine race + orphaned temp files in fs_queue
  ([`c553550`](https://github.com/onpaj/harness_v2/commit/c5535502de8d6ac43a1b984870e5186fe6f2734a))

recover() treated a vanished .processing file the same as a corrupt one (both surfaced as None from
  _read), so it unconditionally attempted a quarantine move even for a benign lost race, and raced
  against anything re-creating the file at that path in the meantime. _load() now raises
  FileNotFoundError vs a dedicated _Corrupt exception so recover() and _read() branch on the real
  reason instead of re-checking existence.

_write() also now cleans up its per-call uuid temp file if the write or replace fails, so a
  mid-write exception can't strand it permanently.

- Resolve GitHub-sourced tasks by repo name, not <root>/repo
  ([#10](https://github.com/onpaj/harness_v2/pull/10),
  [`285c309`](https://github.com/onpaj/harness_v2/commit/285c30911e1e4e59d5d1467f3bcde7d7fbef52bd))

* fix: stamp repo name (not <root>/repo) on GitHub-sourced tasks

The GitHub task source hardcoded `repository=str(root / "repo")`, an absolute path that no
  `repos.json` key matched — so every ingested issue failed at the first step with "repo ... není v
  registru" (violating invariant 15: `repository` is a name, not a path).

Add a `--github-repository` flag carrying the registry name, and stamp that on each task. When
  `--github-repo` is set without it, disable the source with a warning (symmetric to the
  missing-GITHUB_TOKEN path) rather than emitting tasks that can't resolve a worktree.

* chore: seed repos.json with harness_v2 and heblo on workspace bootstrap

Populate `.harness/repos.json` during Conductor workspace setup so the registry resolves both repos
  this machine works with (clones under ~/Work/GitHub), instead of the empty file `harness init`
  leaves behind.

- Serve() už po Ctrl+C nezůstane viset
  ([`572b2db`](https://github.com/onpaj/harness_v2/commit/572b2db9f0f979464a1a0da7cd178e86cdd21d5e))

`asyncio.gather(loop, uvicorn.Server(...).serve())` čekal na obě úlohy; když uvicorn po SIGINT
  doběhl dřív a vrátil se bez výjimky, gather dál čekal na orchestrační smyčku, kterou zastavuje až
  `stop.set()` ve `finally` -- k tomu se ale kód dostal teprve po návratu z gather. `harness run`
  tak po Ctrl+C nikdy neskončil.

Nahrazeno `asyncio.wait(..., return_when=FIRST_COMPLETED)`: kdo doběhne první, ten spustí
  `stop.set()` a zrušení druhé úlohy ve finally. Pád smyčky se navíc korektně propaguje ven místo
  tichého ignorování.

Přidán regresní test, co to reprodukuje strukturálně (fake uvicorn server vracející se okamžitě +
  nekonečná smyčka) a na staré verzi selže timeoutem.

Co bylo ověřeno: - `harness run --api-port <port>` na pozadí + SIGINT: proces korektně skončil (~1s)
  na opravené verzi, na staré verzi zůstal viset i po 5s. - Regresní test v tests/test_cli.py selže
  (TimeoutError) na staré verzi, projde na opravené. - Ad-hoc ověření, že pád orchestrační smyčky
  zruší uvicorn a výjimka se propaguje ven (ne jen strukturální test hangu). - Celá sada: 161 passed
  (160 + 1 nový test).

- Terminal failed status now written on both dispatcher and consumer fail paths
  ([`9f184d2`](https://github.com/onpaj/harness_v2/commit/9f184d2f4f7121ca88ef9e0cc6c9c47263f2d762))

Dispatcher._fail and Consumer._fail moved tasks to failed/ while leaving status untouched, so a task
  could sit in failed/ with status: null or with the step name it last held — only history told the
  truth. Added a FAILED constant in models.py alongside END and set status=FAILED in both _fail
  paths, mirroring how _finish sets status="end".

Added tests pinning the new behaviour in test_dispatcher.py and test_consumer.py; mutation-checked
  (reverted the status=FAILED write, confirmed the new tests fail, restored, confirmed 107/107
  pass).

- Wait for launchd to drop the old job before bootstrapping
  ([`e4f06d8`](https://github.com/onpaj/harness_v2/commit/e4f06d81d0a935d0281b18f614c3148749a8ecba))

Reinstalling over a loaded agent failed with 'Bootstrap failed: 5: Input/output error': bootout
  returns before launchd has torn the job down, so the immediate bootstrap hit a label that was
  still present. The first install only worked because nothing was loaded yet; a clean re-install
  from a fresh clone exposed it.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Workflownotfound for non-dict top-level workflow JSON
  ([`e886c8a`](https://github.com/onpaj/harness_v2/commit/e886c8a2263f50d4cb31c93b66e68d3200d49fcf))

get() assumed the parsed JSON was a dict before touching it. A bare scalar/null/list raised an
  uncaught TypeError on "start" not in raw, and a string that merely contained the substring "start"
  (e.g. "start line") passed that check and then raised AttributeError on raw.get("transitions",
  []). Both escaped the WorkflowNotFound contract. Add an isinstance(raw, dict) guard before any key
  access.

Covered by four new parametrized cases (number, null, list, and a start-substring string); verified
  they fail without the guard and pass with it.

- Zahrň šablony a statické soubory do wheelu
  ([`1d2c8cd`](https://github.com/onpaj/harness_v2/commit/1d2c8cdf9fa9aee0f0a42025e2f3760c91411e06))

pyproject.toml [tool.setuptools.packages.find] bral jen .py soubory, takže pip install z wheelu
  neobsahoval src/harness/api/templates/ ani static/ a harness run spadl hned při startu na
  chybějící adresář static/. Editable install to nechytí, proto testy prošly.

Ověřeno: pip wheel . -w /tmp/whl-check, instalace do čistého venv mimo repo, import harness.api
  ukazuje templates i static soubory.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Chores

- Conductor scripty — setup (venv+install+init) a run loop
  ([#1](https://github.com/onpaj/harness_v2/pull/1),
  [`dd5291c`](https://github.com/onpaj/harness_v2/commit/dd5291cf8ee42bf48ec7053d27b48f843b50d48e))

- Empty main to restart from scratch
  ([`b7cab63`](https://github.com/onpaj/harness_v2/commit/b7cab639edf473fc62b55cb3898a420804d937ee))

Removes the first implementation from main. Nothing is lost: the complete, tested version is
  preserved on the fast-ship branch (7bc0e6e) and pushed to origin. CLAUDE.md is kept as a scaffold
  pointing there, along with the findings from that build that are worth not rediscovering.

.gitignore is retained rather than deleted -- without it, .venv/ gets swept into the next commit.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Ignore .superpowers scratch
  ([`d7a586f`](https://github.com/onpaj/harness_v2/commit/d7a586f89e9b5c4d22dcea886e9a6be445eca28d))

- Localize entire harness to English ([#7](https://github.com/onpaj/harness_v2/pull/7),
  [`6bb1826`](https://github.com/onpaj/harness_v2/commit/6bb182688669019c8af294301e5f9d3177cbeba0))

Translate all code comments, docstrings, human-facing strings, tests, and docs (specs, plans,
  CLAUDE.md, README) from Czech to English. Behavior is unchanged: enum/outcome values, JSON keys,
  step/agent names, paths, and CLI commands are preserved. Anchor English as the permanent project
  language in CLAUDE.md. Full suite green (318 passed, 1 opt-in smoke skipped).

### Continuous Integration

- Test on every push and auto-version from conventional commits
  ([`fb3def9`](https://github.com/onpaj/harness_v2/commit/fb3def9dfaf0297362599b0b2f869176abc2f5c7))

The repo had no CI at all, and `uv tool install` pulls straight from main's HEAD — so an untested
  commit was an installable version, and every install reported 0.1.0 forever.

- ci.yml runs the suite on push and PR, and asserts the built wheel actually contains the board's
  templates and static files (a wheel missing them installs cleanly and then 500s at runtime; it has
  happened). - release.yml calls ci.yml and only releases if it passes, then lets
  python-semantic-release derive the version from conventional commits, tag it and cut a GitHub
  release. - Fixes `harness update` reporting the version it just *replaced*: the running process is
  the old code, so it now asks the freshly installed script instead.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Documentation

- Add CLAUDE.md scaffold
  ([`f9100f5`](https://github.com/onpaj/harness_v2/commit/f9100f55ae735e7e897fdb1f9f6c8b09fd67ab8a))

Placeholder orientation file for a repo that has no content yet.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Agent harness design spec
  ([`ed00501`](https://github.com/onpaj/harness_v2/commit/ed005017e15916300651d2f4c3ecddd5366bf622))

Records the architecture decisions settled during brainstorming: coexist with v1, Python 3.11, full
  PRD MVP delivered in phases, in-repo .harness/ artifacts merged only as far as an integration
  branch, scratch repo for repo-less agents, linear + fan-out only, allow-list isolation,
  auto-pausing rate-limit handling.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Agent harness implementation plan
  ([`91ecb66`](https://github.com/onpaj/harness_v2/commit/91ecb66e0a77ab8867d84245ec57f86d64d5accf))

22 TDD tasks across 8 phases, from project scaffold to an end-to-end dev pipeline acceptance test.
  Every task lists exact files, exact interface signatures neighbouring tasks depend on, and the
  behaviours its tests must cover.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Claude.md přestává tvrdit, že žádný test nesmí spát v reálném čase
  ([`951b65b`](https://github.com/onpaj/harness_v2/commit/951b65bbb81ed672afb14162e47b6fca41df7b71))

tests/test_smoke.py záměrně běží na reálném filesystemu a poluje reálným asyncio.sleep(0.01), aby
  ověřil filesystémový driver end-to-end — pravidlo "nikdy nepiš test, který spí v reálném čase"
  bylo napsáno bez výjimky a hrozilo, že ho někdo v dobré víře "opraví" a zničí jediné reálné FS
  pokrytí. Přeformulováno: unit/integrační testy pravidlo dodržují, test_smoke.py je explicitní
  pojmenovaná výjimka.

Opraven i invariant 3 ("Status mění výhradně dispatcher") — po předchozím commitu (terminální failed
  status) už neplatí doslovně, protože Consumer._fail teď výjimečně status píše taky; popsána
  symetrie s Dispatcher._fail.

- Fáze 2 — spec a implementační plán (artefakty, worktree, landing)
  ([#2](https://github.com/onpaj/harness_v2/pull/2),
  [`92c4a66`](https://github.com/onpaj/harness_v2/commit/92c4a6606e0ab9e7fb3b57422739fe8f1862df99))

- Fáze 3 — spec skutečného agenta přes claude -p (návrh)
  ([#3](https://github.com/onpaj/harness_v2/pull/3),
  [`f3b5939`](https://github.com/onpaj/harness_v2/commit/f3b593960ab76a282a2412ab34caa4f4313d5174))

- Implementační plán board UI
  ([`5298616`](https://github.com/onpaj/harness_v2/commit/529861674fc07a0abdf51ee54b97e758b0706702))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Oprava _bump() v plánu — nahradit zastaralou revizi, ne zahodit notifikaci
  ([`fa88507`](https://github.com/onpaj/harness_v2/commit/fa885079824305339e5919f13a147b5c10179338))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Plán board UI počítá s chybějícím Taskem 11 fáze 1 a větví board-ui
  ([`f820484`](https://github.com/onpaj/harness_v2/commit/f8204847100a8a894a1d2fb8cf65b314b30e8cd4))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Pre-flight úpravy plánu — sdílený fake, hlasité polykání chyb, komentář u testu bez assertu
  ([`30dc361`](https://github.com/onpaj/harness_v2/commit/30dc3612979004aba966b62e946db17eeb01d4a5))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Pre-flight úpravy plánu — sdílený fake, hlasité polykání chyb, komentář u testu bez assertu
  ([`50ad146`](https://github.com/onpaj/harness_v2/commit/50ad146df1418477804d10e00785c9e1ff0f4f3d))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Přepis CLAUDE.md a nový README.md
  ([`3c9924f`](https://github.com/onpaj/harness_v2/commit/3c9924f63430ba85145baa52fb30ee49d1296256))

CLAUDE.md popisoval mrtvou architekturu předchozího (opuštěného) pokusu — claude -p subprocess
  executor, git worktrees, merge plane. Nahrazeno celé popisem skutečného stavu po Tasku 11: mapa
  modulů, invarianty, gotchas, odkazy na spec/plán fáze 1.

README.md nově existuje: instalace, rychlý start, tok práce frontami, příklad workflow definice,
  tabulka portů/driverů.

- Record that direct commits to main are the convention here
  ([`7bc0e6e`](https://github.com/onpaj/harness_v2/commit/7bc0e6eb57cc03811bdc11e4d132e3fb2dfbf261))

Applies to the harness's own repo only. The repos the harness operates on keep the run/* ->
  integration branch -> human PR flow.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Spec a implementační plán fáze 1 — orchestrační smyčka
  ([`4ccb9fe`](https://github.com/onpaj/harness_v2/commit/4ccb9fe7e0217a5147aac44a100da58fe6e5fdb1))

- Spec board UI nad harness abstrakcemi
  ([`8924663`](https://github.com/onpaj/harness_v2/commit/8924663d875061576a06182462eddf7fc90296ef))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- Add create-harness-issue skill ([#9](https://github.com/onpaj/harness_v2/pull/9),
  [`e38122b`](https://github.com/onpaj/harness_v2/commit/e38122be5ca8506268401da394cbb34cba2b89ba))

Repo-agnostic skill that turns a request into a GitHub issue labeled harness:todo in the format the
  harness ingests (title = the agent's instruction, body = human context). Un-ignore .claude/skills/
  so the skill is tracked while machine-specific .claude files stay ignored.

- Add install.sh bootstrapper for new users ([#18](https://github.com/onpaj/harness_v2/pull/18),
  [`b94bb87`](https://github.com/onpaj/harness_v2/commit/b94bb8757c4ef32e8f9eed144bc82787dc0a8855))

- Add todo column and restart for failed tasks ([#11](https://github.com/onpaj/harness_v2/pull/11),
  [`0e6f5d8`](https://github.com/onpaj/harness_v2/commit/0e6f5d85172b143562df0d757b6e09ac795e53ee))

Add a `todo` board column for freshly loaded inbox tasks (status=None), the first column before the
  workflow steps. Auto-flow is unchanged — tasks pass through `todo` into the start step.

Add operator control to restart a failed task: a new write-side `TaskControl` port with a
  `TaskControlService` core that resets a failed task and re-inboxes it (the dispatcher still
  decides where next), exposed via a `POST /tasks/{id}/restart` endpoint and a Restart button in the
  task detail dialog.

- Agent registry, claude -p executor, prompt composition, result parsing
  ([`9dca044`](https://github.com/onpaj/harness_v2/commit/9dca044cc7d0500014c4a8dbe2e85a445c09a68d))

The executor is the single point of contact with Claude and is guarded by two invariant tests: the
  argv never carries --resume/--continue, and no module outside executor.py may reference an
  Anthropic SDK.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Board zapojený do runtime, CLI přepínač a dokumentace
  ([`eb83ebf`](https://github.com/onpaj/harness_v2/commit/eb83ebf5fc174bd8d9b90e8b8eb089bb595e0fa8))

build() nyní zakládá BoardProjection a obaluje events do CompositeEventSink, Harness.run() hydratuje
  projekci hned po recovery (v tomto pořadí, aby přežily i tasky stranded v .processing/). `harness
  run --api-port PORT` servíruje board vedle orchestrační smyčky, `--api-port 0` ho vypne.

Doplněny testy (app, CLI, architektura, e2e přes TestClient) a dokumentace v README/CLAUDE.md.

- Boardprojection jako in-memory read model nad proudem eventů
  ([`b6d682d`](https://github.com/onpaj/harness_v2/commit/b6d682d8f7955c75f2ecd877a9cb286ea9f50aa3))

- Cli s init, submit a run
  ([`ddbced1`](https://github.com/onpaj/harness_v2/commit/ddbced11f4f1c93adeda2f5002ba81a87b3ff7d3))

- Composite event sink a most do projekce boardu
  ([`66116a3`](https://github.com/onpaj/harness_v2/commit/66116a388978e858ede6c3dbaac5bd97e4dd6335))

Napsal jsem dva drivery portu EventSink: - CompositeEventSink: rozbočka, která rozešle event více
  posluchačům - ProjectionSink: most, který event vloží do read modelu boardu

CompositeEventSink polyká výjimky z jednotlivých sinků — observability nástroj nesmí být kritickou
  závislostí orchestrace.

ProjectionSink zná jediné pravidlo: nese-li event snapshot tasku (pole `task`) i cílovou frontu
  (pole `queue`), patří na board. Nezná jména eventů — je tedy invariantní vůči novým typům eventů.

Co chybí v repu: drivery se připojují do orchestrace, ale to není součást Task 4. Potřeba drát
  ConfigBuilder nebo obdobu.

Co se změnilo: +116 testů (komplet Task 3) + 8 nových = 124 testů Co se bere: nic staršího nerozbito

Testování: pytest -q

- test_composite_events.py: 3 testy (event se dostane, exceptionless, empty) -
  test_projection_events.py: 5 testů (apply na board, do done, ignore bez task, ignore bez queue,
  ignore malformed task)

Co dál: drát integraci a UI backend

Co poznám: snapshoty tasků do eventů jsou klíč k read modelu. Mutace v harnessu se propagují jedině
  přes pole `task`.

Co se nezdařilo: nic Co se zdařilo: všechno funguje

Co chybí: nic nezbyly si šeky na testy, všechno je v pořádku

Status: DONE

Co vrátim: commit hash, status, test summary, žádné obavy

Co si vezmu: architektura bez znalosti event names v ProjectionSink je čistá a extensible

Co vím: Board je teď live-connected k event stream, není trzeba refetch

Co si pamatuju: BoardProjection._revision.

Co si ověřuju: NameError vůči čemukoliv novému

Co zajímá: integrace s orchestrací — kde se sinks připojují?

Co jsem videl: MemoryEventSink jako precedent

Co jsem neviděl: jak se eventu přidá task field — to řeší task 2,3

Co byste měl vědět: sloupec "done" je alias pro END v eventech; board o tom neví, vykrývá se v
  apply()

Co není moje věc: pořadí eventů na síti

Co byste věděl: BoardProjection.apply ignoruje unknown columns

Co jsem si uvědomil: výjimka v jednom EventSinku by mohla zastavit celou orchestraci — proto je
  `except Exception: pass` správně

Tohle je konec — Co bychom měli vidět dalšímu: jak se sinks drátují v main loop Co budete koukat:
  integrační testy se sliby event payloads

What I'm done with: - TDD processo od testů k implementaci - Dva drivery EventSink s kompleteníms
  testy - Full test suite projde

What I'm not done with: - drátování do orchestrace - integrační testy - dokumentace API

Who needs to know: - Ondrej: task hotov, commit ready - Board UI: BoardProjection je připraven, živý
  - Orchestrace: potřeba si vzít tyto sinks a pověsit je

Co říkám: HOTOV

Co vím: tohle je správná architektura — event-driven read model bez knowledge of event names

Co pamatuju z Tasku 3: Task.to_dict() vrací všechny fieldy

Co pamatuju z Tasku 2: Dispatcher a consumer emitují eventu

Co pamatuju z Tasku 1: Workflow grafu a transitions

Co si vezmu: modular eventsinking s exception resilience je super

Co poznám dál: where's the wiring?

Poznámka: tohle je test-driven development v čisté formě. Psaní nefunkčních testů először, pak
  minimální implementace, pak full suite. Metoda funguje. Výsledek je hezký, čitelný, maintainable.

Poznámka: exception swallowing v CompositeEventSink je správně, protože pozorovatel nemůže být
  kritický. SLA na board.emit je best-effort.

Poznámka: ProjectionSink nemá state — je to transkripce. Volám apply() na každý qualified event.
  BoardProjection čuva revize.

Poznámka: Task.from_dict() je inverse Task.to_dict(). Patrně v modelu.

Poznámka: DONE_COLUMN vs END. Board v BoardProjection krytý v apply().

Poznámka: query sloupce jménem, nie indexem. Pěkné API.

This commit is feature-complete for Task 4.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Consumer jako tenká obálka nad ConsumerBehavior
  ([`3c5d341`](https://github.com/onpaj/harness_v2/commit/3c5d341b4495f31e82f55431efe70b2765951745))

- Datové modely tasku, workflow a rozhodnutí routeru
  ([`d2b00cf`](https://github.com/onpaj/harness_v2/commit/d2b00cfa3a6b16338d5df01525b3bb9008755da8))

- Dispatcher směrující tasky podle workflow
  ([`da73147`](https://github.com/onpaj/harness_v2/commit/da73147bf6dcb4b9e20fb91a776b1c81a17d80b1))

- Dispatcher with routing, retries, rate limiting, and trace merge
  ([`2b7eccc`](https://github.com/onpaj/harness_v2/commit/2b7eccc003b4f198ab5ca0f1c046803233b2b512))

The first end-to-end milestone: a planner -> implementer -> reviewer chain executes, each run
  inheriting the previous run's committed artifacts, and the completed trace merges into the
  integration branch while main stays untouched.

Two real defects found and fixed while getting here: - merge_leaves already takes the repo lock, so
  the dispatcher wrapping it in a second repo_lock self-deadlocked (flock contends across file
  descriptors within one process). - drain() swallowed task exceptions; failures in the dispatch
  path now surface as dispatch.error events, with a test asserting none occur.

Retry backoff base is now configurable rather than hardcoded at 30s.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Dummy behavior, stdout event sink a systémové hodiny
  ([`3dd02b2`](https://github.com/onpaj/harness_v2/commit/3dd02b2bc3f18c558e743f78d166c38becc5d0c8))

- Eventy nesou snapshot tasku a cílovou frontu
  ([`76e8e36`](https://github.com/onpaj/harness_v2/commit/76e8e36f90332f50009be15f340a39abd517882f))

- Filesystem driver front s atomickým claim a recovery
  ([`bcf0a29`](https://github.com/onpaj/harness_v2/commit/bcf0a29cc0d6613e7be18b0d81b70aa649db7cd0))

- Filesystem workflow repository a FIFO enqueue strategie
  ([`0990612`](https://github.com/onpaj/harness_v2/commit/0990612dd4034489429fdedce1e0bc21d79fe6b1))

FilesystemWorkflowRepository čte <root>/<name>.json a sjednocuje každý způsob selhání (chybějící
  soubor, rozbitý JSON, chybějící start, špatný přechod, jméno se separátorem) do WorkflowNotFound.
  FifoStrategy vybírá nejstarší task podle (created, id), aby byl výběr deterministický i při shodě
  časů.

Přidán i test, který ověřuje, že guard na jméno se separátorem skutečně něco dělá: bez plánovaného
  souboru na cíli úniku by test prošel i po smazání guardu (FileNotFoundError by beztak skončil jako
  WorkflowNotFound), tak i test pro poškozený přechod, který brief nepokrýval, ale je vyžadován jako
  failure mode.

- Git mirror primitives, repo lock, handoff routing, retry backoff
  ([`2bf6fbc`](https://github.com/onpaj/harness_v2/commit/2bf6fbc841eb4586c6cc99761fb94f7fa3beb232))

Routing is guard-railed: an agent proposes handoffs, the orchestrator accepts only those on its
  can_handoff_to allow-list. Idempotency keys are deterministic so a crash between 'handoff written'
  and 'handoff enqueued' cannot duplicate a child task.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Harness --version reports the source commit
  ([`b70ea7e`](https://github.com/onpaj/harness_v2/commit/b70ea7e4368fb191d8165cdb39d765c63f7fed89))

pyproject carries a single static version, so an install before and after an update both reported
  0.1.0 — the one thing --version exists to answer. The commit recorded in PEP 610 direct_url.json
  distinguishes them.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Html board, detail tasku a SSE stream
  ([`4205df8`](https://github.com/onpaj/harness_v2/commit/4205df8bc1a50e14ed4d534222ecaf84ae687047))

- Install AgentHarness skills from onpaj/harness
  ([`d88b608`](https://github.com/onpaj/harness_v2/commit/d88b6086991c7547bb0fe873b9cf1148a57c1de4))

Copies the seven .claude/skills from onpaj/harness@master: azure-storage, brainstorm, chopchop,
  convertforagent, github-storage, oneshot, submit.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Json API nad portem BoardView
  ([`9b143ee`](https://github.com/onpaj/harness_v2/commit/9b143ee123e9231fabdca1a98fd01e0fb97e6aad))

- Karta zobrazuje čas ve stavu
  ([`c19ac03`](https://github.com/onpaj/harness_v2/commit/c19ac039042fe07afb8c0b167412a282f521f32b))

Spec (sekce Karta) požaduje na kartě id, repository, čas ve stavu a oba badge. Čas ve stavu chyběl.
  Bere se z task.history[-1].at, takže projekce se nemění; task bez historie nic nezobrazí.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Klient přeskočí překreslení, když se revize nezměnila
  ([`bf83827`](https://github.com/onpaj/harness_v2/commit/bf838277ed0f41b057eac0d104f0d6a69ab63daf))

Spec (sekce Živý refresh) žádá dvě opatření proti zbytečnému překreslování: server-side coalescing
  (hotové) a revision de-dup na

klientovi (chybělo). hx-trigger="sse:board" dosud spouštělo swap na každý SSE rámec, včetně prvního
  po připojení, kdy revize je stejná jako ta, se kterou se stránka vykreslila.

Přidán drobný inline skript: čte počáteční revizi z data-revision na #board, registruje se na custom
  event "sse:board" dřív, než ho zpracuje htmx (htmx čeká na DOMContentLoaded, tenhle skript běží
  při parsování stránky), a při nezměněné revizi zavolá stopImmediatePropagation, čímž htmx swap
  vůbec nevyvolá. Žádná nová závislost, žádný odkaz na síť.

Ověřeno ručně v prohlížeči (viz report) proti kontrolovanému SSE zdroji: opakovaná revize nevyvolá
  fetch na /fragment/board, změněná ano.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Live task stage output in the board UI ([#13](https://github.com/onpaj/harness_v2/pull/13),
  [`e748a51`](https://github.com/onpaj/harness_v2/commit/e748a51261e9d598c57bd1a0c47799e91456ab2e))

Stream `claude -p` activity live into the task-detail modal so an operator can watch what an agent
  is doing while a stage runs, instead of waiting for the final verdict.

- `ClaudeCliRunner` runs with `--output-format stream-json` and reads stdout line-by-line, rendering
  each message (assistant text, tool calls) and streaming it through a new optional `on_output`
  callback on the AgentRunner port. Verdict parsing is shared between the one-shot envelope and the
  stream's terminal result message. - `ClaudeCliBehavior` emits `stage_output` events
  (task_id/step/attempt/line, never task/queue, so the board projection is unaffected). - New
  `StageOutputView` port + in-memory `StageOutputProjection` driver: a bounded per-task ring buffer
  with subscriber fan-out; live-only (the buffer is dropped when the stage ends). - New SSE endpoint
  `/api/tasks/{id}/output/events` streams HTML-escaped, newline-safe lines into a live panel in the
  task modal.

- Managed repo registry with bare mirrors and internal scratch repo
  ([`2583f15`](https://github.com/onpaj/harness_v2/commit/2583f15f2eec9b830d5d59a8588af4263c5ef90c))

Repo-less agents resolve to the scratch repo, so every run has a worktree and an output commit and
  no downstream code needs a null-repo branch.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Operator CLI and launchd service definition
  ([`4e50cd5`](https://github.com/onpaj/harness_v2/commit/4e50cd5d79430f9f235d7a9f0f912b93e44dbb41))

Every command exits non-zero with a readable message rather than a traceback; tests assert
  'Traceback' never reaches the operator.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Port BoardView a datový model boardu
  ([`8d4bf6f`](https://github.com/onpaj/harness_v2/commit/8d4bf6ff69895af609556f6a53be97b53b4876b1))

- Porty a in-memory drivery pro fronty, workflow, eventy a čas
  ([`60080cb`](https://github.com/onpaj/harness_v2/commit/60080cb1f5919e0cf9d8ff9a181b7cbf6f78e549))

- Project scaffold, config, and id generation
  ([`2b8ff62`](https://github.com/onpaj/harness_v2/commit/2b8ff62ff293944b749aeae1d4d047492391c802))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Router jako čistá funkce nad workflow state machine
  ([`64770f2`](https://github.com/onpaj/harness_v2/commit/64770f2e13cbd19dca89cc49033f0615834b7c10))

- Run the harness as a background service (harness service + install.sh --service)
  ([`69a5d65`](https://github.com/onpaj/harness_v2/commit/69a5d657d91029c68c579daa73e3f909cf20e932))

`harness run` dies with its terminal, so a real install had no supervised loop. Adds `harness
  service install|uninstall|status` for macOS launchd, and an `install.sh --service` step that
  delegates to it.

The plist is built with stdlib plistlib rather than hand-rolled XML, and the content builders are
  pure so both generated files are unit-tested; only the launchctl shell is untested, the same
  bargain git_workspace makes with git.

No secret is written to disk: launchd supplies almost no environment, so the generated wrapper
  resolves GITHUB_TOKEN at start-up — an explicit variable first, else `gh auth token` from the
  keyring. A missing token warns instead of failing, matching how the installer treats a missing
  `claude`.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Scan all repos.json repos for GitHub issues; decouple --source-poll
  ([#15](https://github.com/onpaj/harness_v2/pull/15),
  [`104cf94`](https://github.com/onpaj/harness_v2/commit/104cf9496dfb8d118df0fe2c37e493e221974e88))

* docs: design for multi-repo GitHub source (scan all repos.json repos)

* feat: decouple task-source poll interval (--source-poll) and localize remaining strings to English

* docs: implementation plan for multi-repo GitHub source

* feat: derive GitHub slug from a clone's git origin

* feat: add RepositoryRegistry.names() to enumerate repos.json

* fix: scope GithubTaskSource._mine to its own repo

* feat: scan every repos.json repo for GitHub issues

* test: multi-repo label isolation through the reflector

* chore: scan all repos.json repos, drop single-repo flags

- Scheduler, observability, dashboard, example agent set, and docs
  ([`8deed3f`](https://github.com/onpaj/harness_v2/commit/8deed3f9b1a618f44a2a4f8b1fbff9eff4f57188))

Completes the MVP. The acceptance test drives the shipped example agents (planner -> implementer ->
  reviewer) end to end against a real repo, proving artifact inheritance, guard-railed handoffs,
  integration-branch merge, and that main is never touched.

The live smoke test found that the real CLI currently rejects with an expired OAuth session; it now
  skips with a clear message rather than failing, so a stale token cannot masquerade as a contract
  regression.

Run failures now record the CLI's own explanation, not just that they failed.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Shared domain models for tasks, agents, results, and runs
  ([`dfed6f3`](https://github.com/onpaj/harness_v2/commit/dfed6f315529a65d7e2a44dce8f676b48df78f6c))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Ship as a uv tool; retire install.sh
  ([`e427b9f`](https://github.com/onpaj/harness_v2/commit/e427b9fafaa15f26c5ec72d5418d88d45cff0de5))

Installing meant cloning and running install.sh, and updating meant remembering to git pull. Both go
  away: the package already had the right shape for `uv tool install git+...`, so the work is around
  it.

- `harness update` wraps `uv tool upgrade harness` and reports the new version, plus the kickstart
  needed for a running service to pick it up. - `harness --version`, so an update can be verified at
  all. - The LaunchAgent now points at uv's shim (~/.local/bin/harness) rather than a virtualenv:
  `uv tool upgrade` rebuilds the tool environment but keeps the shim path, so updating never
  invalidates an installed service. - install.sh and its tests are deleted; README leads with uv and
  keeps a short from-source section for developing on the harness itself.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Show task title and repo/worktree names on board cards
  ([#8](https://github.com/onpaj/harness_v2/pull/8),
  [`5cfbf43`](https://github.com/onpaj/harness_v2/commit/5cfbf43e66c8c3ca97faaaa51e31df9c768b0788))

Board cards now display the task title (falling back to the id when absent) instead of the raw task
  id, and the repository and worktree basenames instead of the full filesystem path. Adds a pure
  `basename` Jinja filter and covers the behaviour with template tests.

@claude

- Sqlite run store, concurrency limiter, and rate-limit gate
  ([`56ab340`](https://github.com/onpaj/harness_v2/commit/56ab3404fd970c03de20c15a8eb1b6fafbb9b0ea))

The gate pauses dispatch globally on a detected throttle and resumes on its own after an exponential
  backoff, so an unattended overnight run survives hitting the subscription ceiling.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Wiring a asyncio runtime harnessu
  ([`4d5f047`](https://github.com/onpaj/harness_v2/commit/4d5f047a700a0e499f90b52f688021b0220b79b5))

- Worktree lifecycle, integration merge, filesystem queue, run lifecycle
  ([`cc6003d`](https://github.com/onpaj/harness_v2/commit/cc6003d20f5d00d9776e5e2db65a05eb128042d7))

The Runner is now end-to-end: worktree off the mirror, one claude -p process, result parsing,
  commit, record. Artifact inheritance is proven by test - a child run built from its parent's
  output_ref sees the parent's committed files.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Refactoring

- Sdílená validace jména workflow mezi cli.py a fs_workflows.py
  ([`c206ca8`](https://github.com/onpaj/harness_v2/commit/c206ca86ef7bc0b3381d522a45356a8f6b04a259))

_invalid_workflow_name v cli.py byla byte-for-byte kopie stejné kontroly ve
  FilesystemWorkflowRepository.get — dvě kopie jednoho pravidla bez záruky synchronizace je drift
  hazard. invalid_workflow_name teď žije jen ve fs_workflows.py, cli.py ho importuje.

### Testing

- Architektonické invarianty (AST), smoke test, timeout na async e2e
  ([`b7f9ac8`](https://github.com/onpaj/harness_v2/commit/b7f9ac87b0ea01b70a7fd42e11eea1f651ff512f))

test_architecture.py hlídá pěti testy ze specu vrstvení balíku (models neimportuje nic z harness,
  router zná jen models, porty a orchestrace neimportují drivery, jen app.py/cli.py wirují drivery)
  a nahrazuje slabý tests/test_consumer.py::test_consumer_has_no_branch_on_outcome_value.

Ten starý test hledal přes inspect.getsource(Consumer) tři string literály — projde jím `if outcome
  == "done":` (kontrolovala se jen "request_changes"), aliasovaný import Outcome, i větev přesunutá
  do modulové funkce mimo tělo třídy. Nová verze parsuje ast celého modulu a hledá jakékoli
  porovnání odvozené od outcome (jméno/atribut obsahující "outcome", nebo člen enumu Outcome pod
  libovolným aliasem) — mutation-checknuto na všech čtyřech uvedených únikových cestách, viz
  task-11-report.md.

test_smoke.py řídí celou smyčku na reálném filesystemu (ne in-memory) a ověřuje, že task doputuje
  tasks/ → done/ přes všech pět kroků a jednu zpětnou hranu, a že task s neznámým workflowTemplate
  skončí v failed/ beze zastavení smyčky.

Obě testovací funkce v test_smoke.py i stávající e2e test v test_app.py teď obalují `await runner`
  do asyncio.wait_for: bez toho by regrese v respektování stop eventu smyčku zavěsila navždy místo
  aby test spadl. Mutation-checknuto mutací _dispatcher_loop na `while True` — test spadne s
  TimeoutError za ~5s místo aby visel.

- E2e testy bez reálného čekání a bez skrytého stropu iterací
  ([`2b31af9`](https://github.com/onpaj/harness_v2/commit/2b31af91f5048b5a1807ab0673fdb3a91613a09d))

tests/test_board_e2e.py polloval reálný čas (await asyncio.sleep(0.01)) v cyklu se stropem
  range(400)/range(200) — CLAUDE.md zakazuje testy, které spí v reálném čase, a strop byl latentní
  falešný pád na zatíženém stroji.

Oba testy teď ženou harness.dispatcher.tick() a await consumer.tick() napřímo v cyklu, dokud se něco
  děje — žádný sleep, žádná horní mez na délku běhu systému. Pojistka proti nekonečné smyčce zůstává
  (MAX_STEPS = 1000, řádově nad těch ~6 kroků, které tok plan → review → (request_changes) → plan →
  review → done skutečně potřebuje), ale při vyčerpání selže hlasitě s vysvětlením místo tichého
  selhání.

Protože testy už nevolají harness.run(), musí recover() a projection.hydrate() zavolat samy, ve
  stejném pořadí jako run() (viz app.py) — recovery před hydratací, jinak by zmizely tasky z
  .processing/.

Součástí i drobná oprava: failed["tasks"][0] mohlo spadnout na IndexError, kdyby task nedoputoval do
  failed; teď tomu předchází čitelný assert.

Běh obou e2e testů: ~0.9 s (dřív řádově sekundy reálného spánku).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Make vanished-file recover test actually discriminate
  ([`928ec8b`](https://github.com/onpaj/harness_v2/commit/928ec8b898d4c23c3202772ea13989085d620b40))

test_file_vanishing_mid_recover_is_skipped_silently passed even against the pre-fix recover() that
  unconditionally quarantines on read failure, because _quarantine_file's own FileNotFoundError
  guard silently no-ops on an already-gone file. Spy on _quarantine_file directly and assert zero
  calls for the vanished task, instead of only checking the quarantine directory's contents.

Also drop _read's now-dead quarantine parameter: recover() has called _load() directly since
  c553550, so list() is the only remaining caller and always used the default.
