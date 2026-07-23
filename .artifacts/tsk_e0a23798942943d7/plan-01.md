# Plan — the `healer` workflow

## Summary

Today a task that lands in `failed/` just sits there until an operator notices
it on the board and either fixes the underlying harness bug by hand or clicks
`restart`. This feature closes that loop: a new `TaskSource` watches `failed/`
for failures of the ordinary (`default`) workflow, and for each one enqueues a
**deduped `healer` task** that runs a diagnostic agent. If the agent confirms
the failure was caused by a bug in the harness itself (not in the target
repository, not a transient external failure), the harness **automatically
files a GitHub issue** against its own repository through a new `Forge`
verb, `open_issue`. Everything else about a failure (it still ends up in
`failed/`, `restart` still works) is unchanged — healing is a parallel,
best-effort observer, not a replacement for operator control.

## Context

`TaskSource` (phase 4) already generalizes "the outside world of tasks" to
one port with `poll`/`report_progress`/`finish`, and `Forge` (phase 2)
generalizes "propose a change" to `open_pull_request`. Both were built with
the expectation of more drivers/verbs arriving later without touching the
core (`CLAUDE.md`: "swap a driver, never its surroundings"). This task is the
first consumer that needs a `TaskSource` whose input is the harness's *own*
queues rather than an external system, and a `Forge` verb that reports a
*process* problem (a bug in the harness) rather than proposing a *product*
change (a PR). It's also the first time a single running harness needs to
service two workflows (`default` for real work, `healer` for diagnosing its
failures) concurrently — see the architecture note below, which is the
central risk of this plan.

## Functional requirements

### FR-1 — `FailedQueueTaskSource` polls `failed/` for default-workflow failures

A new `TaskSource` driver (`kind = "failed-queue"`) lists the tasks currently
in the `failed` queue on every poll tick (reusing the same `SourcePoller` /
`source_interval` loop already used for GitHub) and, for each task whose
`workflow_template` equals the configured target workflow (default:
`"default"`), builds one `healer` task.

Acceptance criteria:
- A task with `workflow_template="default"` sitting in `failed/` produces
  exactly one `healer` task after one poll tick.
- A task with `workflow_template="healer"` (or any workflow other than the
  configured target) sitting in `failed/` produces **no** healer task — this
  is what stops the healer from healing its own failures and looping forever.
- A task that is *not yet* in `failed/` (still in flight) is never picked up.

### FR-2 — Healing is deduplicated per failure occurrence, survives restart

Exactly one healer task exists per *occurrence* of a default-workflow
failure. Re-polling an unchanged `failed/` must not create a second healer
task for the same failure, including after a harness restart.

This reuses the existing `Task.dedup_key` / `SourcePoller._seen` /
`Harness._seed_pollers` machinery verbatim (`CLAUDE.md` already documents
this as the mechanism that survives a restart) — no new dedup mechanism is
needed, provided the key is chosen correctly. **The key must not be just the
failed task's id.** `TaskControl.restart` (invariant #23) resets `status`
and `last_outcome` but does **not** clear `history` or issue a new task id —
so the same task id can fail a second time later, and a key of
`dedup_key("failed-queue", task.id)` would (wrongly) be considered
already-healed forever after the first failure. The key must include
something that changes on every distinct failure of the same task, e.g.
`dedup_key("failed-queue", task.id, len(task.history))` (`history` strictly
grows by one entry per failure and per restart, so its length at
poll-time is a correct, monotonic per-occurrence discriminator).

Acceptance criteria:
- Poll twice without the `failed/` task changing → one healer task total.
- Restart the harness (fresh `SourcePoller`, reseeded from disk) → still one
  healer task total for that failure.
- A task fails, is `restart`ed by an operator, and fails again later (same
  task id, longer `history`) → a **second**, distinct healer task is created
  for the second failure.

### FR-3 — The `healer` workflow: diagnose, then optionally file an issue

A new built-in workflow definition, `healer`, with two steps:

```
start: diagnose
diagnose --bug_confirmed--> file_issue
diagnose --not_a_bug--> end
file_issue --done--> end
```

`diagnose` is an ordinary agent step (`ClaudeCliBehavior`, same as
`plan`/`design`/etc.) whose persona is instructed to determine whether a
task failure was caused by a defect in the harness's own code versus
something external (a bug in the target repo, a flaky agent run, a GitHub
outage, an operator error). `file_issue` is a small dedicated behavior
(parallel to `LandingBehavior`) that calls the new `Forge.open_issue` verb;
it performs no agent run and no code changes.

This requires extending the closed `Outcome` enum
(`src/harness/models.py`) with two new members, `BUG_CONFIRMED =
"bug_confirmed"` and `NOT_A_BUG = "not_a_bug"`. This is a safe extension of
an existing, designed-for-extension seam: the router/dispatcher only ever
compare outcome *strings* from workflow JSON (`Workflow.target`), the
consumer never branches on the value (guarded by
`test_consumer_has_no_branch_on_outcome_value`), and `AgentSpec.
allowed_outcomes` already exists precisely to bound which subset of `Outcome`
a given step's agent may return. No existing step's behavior changes.

Acceptance criteria:
- `diagnose`'s `AgentSpec.allowed_outcomes = (BUG_CONFIRMED, NOT_A_BUG)`; a
  verdict outside that set is rejected exactly like today's `VerdictError`
  path for `review`/`request_changes`.
- `not_a_bug` routes straight to `end` — no issue is filed, task lands in
  `done/`.
- `bug_confirmed` routes to `file_issue`, which on success also lands the
  task in `done/` (the healer task is never expected to open a PR or touch
  code — `end` is the only terminal, `failed/` only via the ordinary
  exception path if `open_issue` raises).

### FR-4 — `Forge.open_issue`: a new verb, symmetric with `open_pull_request`

`ports/forge.py` gains:

```python
@dataclass(frozen=True)
class FiledIssue:
    number: int
    url: str
    title: str

class Forge(ABC):
    ...
    @abstractmethod
    def open_issue(self, task: Task, *, title: str, body: str) -> FiledIssue: ...
```

Every existing `Forge` implementation must grow an `open_issue`:
`GithubForge` (real, via a new `GithubClient.create_issue`), `MemoryForge`
and `FakeForge` (record-only, mirroring how they already record
`open_pull_request`).

`GithubForge.open_issue` resolves the target repository **the same way
`open_pull_request` already does** — through `RepositoryRegistry`/
`task.repository`, falling back to `task.worktree` — so the issue lands on
whatever repo the healer task is attached to. Per FR-6 below, that repo is
configured to be the harness's own source, not the target repo of the task
that originally failed.

Acceptance criteria:
- `open_issue` raises `ForgeError` on the same failure classes as
  `open_pull_request` (no token, unresolvable repo, non-GitHub origin, API
  error), with GitHub's own error detail surfaced (`_explain`).
- Retrying `file_issue` for the *same* healer task attempt (e.g. after a
  crash between issue creation and the consumer recording the outcome) must
  not create two GitHub issues — `open_issue` needs an idempotent lookup
  (e.g. a stable marker in the issue body, `<!-- harness-healer:<task.id>
  -->`, checked via a new `GithubClient.find_issue`), the same shape as
  `find_pull_request`/`create_pull_request`. Exact lookup mechanism is an
  architecture-step decision; the *requirement* (idempotent by healer task
  id) is not optional — `file_issue` is a retryable at-least-once step like
  every other consumer step.

### FR-5 — Diagnostic context reaches the agent without new prompt plumbing

The `diagnose` agent needs enough about the original failure to have a
chance at a real diagnosis: which task, which workflow/step it failed at,
the failure `reason`, and (ideally) the failing history entries. Reuse the
existing, unmodified `compose_prompt`/`_request_of` (`behaviors/agent.py`),
which already surfaces `task.data["request"]` verbatim into the prompt for
every step — `FailedQueueTaskSource` should format the diagnostic report
(failed task id, workflow, step, reason, relevant history) as one string
into the healer task's `data["request"]`. No shared prompt-composition code
needs to change; this is deliberately the path of least resistance and
consistent with how every other source already populates `data`.

Acceptance criteria:
- The `diagnose` agent's prompt contains the original task id, its
  workflow/step, and the failure reason without any change to
  `compose_prompt`.
- `data["failed_task"]` also carries the same facts in structured form (task
  id, workflow, status, reason, repository) for the board/API to show
  without parsing prose.

### FR-6 — The healer task's own workspace is the harness's own repository

The `diagnose` agent needs to read the harness's own source to judge "is
this a harness bug", not the target repo the original task was working in
(which may be a different repo entirely, and whose worktree may already
have been reset/reclaimed). The healer task is therefore attached to a
dedicated, operator-configured repository — the harness's own git checkout,
registered in `repos.json` like any other repo — via a new
`--healer-repo <name>` CLI flag. `file_issue` then files the issue against
that same repo (FR-4), which is exactly the intent: "a confirmed harness bug
becomes an issue on the harness's repo."

Acceptance criteria:
- `FailedQueueTaskSource` builds each healer task with
  `repository=<healer_repo_name>`, resolved through the same
  `RepositoryRegistry` convention as every other task (invariant #15 —
  `task.repository` is a name, not a path).
- Reading the *original* failed task's own worktree/artifacts is explicitly
  **out of scope** for v1 (see Open questions) — diagnosis works from the
  failure reason/history captured in FR-5, plus whatever the harness's own
  source shows.

### FR-7 — One running harness now serves two workflows at once

This is the load-bearing structural change and the main risk of this plan.
Today `Harness`/`build()` are built around exactly **one** `Workflow`
(`step_queues` is `{step: queue for step in workflow.steps()}`,
`BoardProjection(workflow)` computes one `column_order`). `Dispatcher`
already resolves a task's workflow dynamically per-task
(`self._workflows.get(task.workflow_template)`), so nothing in routing
itself is single-workflow — the deficiency is only that `step_queues` (and
therefore the consumers, and the board's columns) are populated from just
one workflow's step list. A `healer` task routed to step `diagnose` would
hit `Dispatcher._fail(task, "step 'diagnose' has no queue")` today, because
no such queue/consumer exists.

`build()` must therefore accept an *additional* set of workflow names (in
practice: `{args.workflow, "healer"}` when healing is enabled) and union
their `steps()` when building `step_queues`/`consumers`, and
`BoardProjection`'s `column_order` must do the same union so healer tasks
are visible on the board instead of silently dropped by `_store`'s
`if column not in self._order: return` guard. Step names must stay unique
across all concurrently-active workflows on one harness instance (`default`
and `healer` don't collide today: `plan/design/architecture/development/
review/land` vs. `diagnose/file_issue`) — this plan does not attempt to
support colliding step names across workflows.

Acceptance criteria:
- With healing enabled, a `healer` task flows `diagnose → (file_issue|end)`
  to completion in the same running process that is also driving `default`
  tasks through `plan → … → land`.
- The board shows `diagnose`/`file_issue` as columns (or otherwise makes
  healer tasks visible) alongside the default workflow's columns, `todo`,
  `done`, `failed`.
- With healing disabled (the default), nothing changes: one workflow, one
  set of queues, one set of columns — byte-for-byte the behavior of today.

### FR-8 — Wiring: opt-in, off by default

`harness run` gains `--heal` (off by default) and `--healer-repo <name>`
(required when `--heal` is set). `harness init` always seeds
`workflows/healer.json` and default agent personas for `diagnose` (skipping
`file_issue`, the same way `land` is skipped today) — harmless when unused,
symmetric with how `default.json` is always seeded. `build()` — which
already special-cases the landing step by name (`LANDING_STEP`) — similarly
special-cases `file_issue` and constructs+registers the
`FailedQueueTaskSource` itself (it needs the freshly-built `failed`
`TaskQueue`, which does not exist yet when `cli.py` currently assembles
`sources` for GitHub).

Acceptance criteria:
- `harness run` without `--heal` builds no extra queues, no extra source, no
  extra columns — existing e2e/smoke behavior is unaffected.
- `--heal` without `--healer-repo` is a usage error caught before the loop
  starts (fail fast, matching how e.g. an invalid workflow name is rejected
  in `_init`).

## Non-functional requirements

- **No new production dependency.** `GithubClient.create_issue`/`find_issue`
  are stdlib `urllib`, exactly like every other `HttpGithubClient` method.
- **A source failure must not stall the loop.** `FailedQueueTaskSource.poll`
  runs through the same `SourcePoller.tick` try/except as GitHub — an
  exception here (e.g. a corrupt file in `failed/`, though `fs_queue.list()`
  already quarantines those) must not crash the dispatcher/consumer loops.
- **A healer failure must not mask the original failure.** The original
  task stays in `failed/` untouched; if the healer task itself fails (bad
  agent output, `ForgeError` from `open_issue`), it lands in `failed/` too,
  as its own, independent, ordinary task — `TaskControl.restart` already
  works on it for free.
- **Bounded blast radius on rollout.** The first poll after `--heal` is
  turned on will enqueue one healer task per *existing* default-workflow
  failure already sitting in `failed/` (see open questions) — this should
  be a conscious, visible event (an `ingested` event per healer task, same
  as today), not a silent burst.

## Data model

- `Task.data["failed_task"]` (new, healer tasks only): `{id, workflow,
  status, reason, repository}` — structured facts about the original
  failure, sourced from the failed task's last `FAILED`-labelled
  `HistoryEntry`.
- `Task.data["source"]` (existing convention, reused): `{"kind":
  "failed-queue", "task": <original task id>}` — lets `SourceReflectorSink`
  route `report_progress`/`finish` calls only to `FailedQueueTaskSource`
  (which implements them as no-ops: there is no external system to project
  healer-task progress into).
- `Task.dedup_key` (existing field, reused): `dedup_key("failed-queue",
  <original task id>, <len(original task.history) at poll time>)` (FR-2).
- No changes to `Task`, `HistoryEntry`, `Workflow`, or any persisted JSON
  shape beyond the above (all within the existing free-form `data` dict).

## Interfaces

- **New driver**: `drivers/failed_queue_source.py` —
  `FailedQueueTaskSource(TaskSource)`, `kind = "failed-queue"`. Reads the
  `failed` `TaskQueue` port only (no new port needed — `TaskQueue.list()`
  already exposes exactly what's needed).
- **New port additions**: `ports/forge.py` — `FiledIssue`, `Forge.
  open_issue`. `ports/source.py`/`models.py` — no changes beyond the
  `Outcome` enum extension.
- **New behavior**: `behaviors/file_issue.py` —
  `FileIssueBehavior(ConsumerBehavior)`, parallel in shape to
  `LandingBehavior` but simpler (no workspace/artifact copying, no push —
  just `forge.open_issue(...)`).
- **CLI**: `harness run --heal --healer-repo <name>` (FR-8). No change to
  `harness submit`.
- **Board/API**: no new endpoints; existing `BoardView`/`ArtifactView`
  surfaces healer tasks like any other task once FR-7's column-set fix
  lands. `task.data.failed_task`/`source` show in the existing task-detail
  `data` blob, the same way `data.source` already does for GitHub-origin
  tasks.

## Dependencies and scope

Depends on: `TaskSource`/`SourcePoller` (phase 4), `Forge`/`GithubForge`
(the GitHub forge work), `RepositoryRegistry` (phase 3), the existing
`ClaudeCliBehavior`/`AgentSpec` machinery for the `diagnose` step, and the
board projection (`docs/superpowers/specs/2026-07-19-board-ui-design.md`).

Explicitly out of scope for this task:
- Reading the *original* failed task's own worktree/code/artifacts during
  diagnosis (FR-6) — diagnosis works from the captured reason/history only.
- Healing failures of workflows other than the configured target (no
  meta-healing of `healer` task failures, no healing of arbitrary
  non-`default` workflows without explicit future configuration).
- Cross-task deduplication of GitHub issues (if the same harness bug causes
  five different task failures before it's fixed, this ships five issues,
  each idempotent only against *its own* healer task per FR-4 — not against
  each other).
- Any UI/board work beyond making healer tasks visible (FR-7) — no new
  dedicated "healer" view, filters, or issue links.
- A generalized N-workflow scheduler; FR-7 solves exactly the two-workflow
  case this feature needs (`default` + `healer`), not arbitrary workflow
  composition.

## Rough plan

1. **`Outcome` + `healer` workflow definition** — extend `Outcome` with
   `BUG_CONFIRMED`/`NOT_A_BUG`; add the `healer` workflow JSON (seeded by
   `harness init`) and its `diagnose` persona/`AgentSpec`.
2. **`Forge.open_issue`** — port addition, `MemoryForge`/`FakeForge` (trivial
   record-only implementations), `GithubClient.create_issue`/`find_issue` +
   `GithubForge.open_issue` (idempotent by healer-task marker), all with
   unit tests mirroring the existing `open_pull_request` test suites.
3. **`FileIssueBehavior`** — the `file_issue` step's behavior, unit-tested
   like `LandingBehavior` (success path, `ForgeError` → task fails).
4. **`FailedQueueTaskSource`** — polls `failed/`, filters by target
   workflow, builds the healer task per FR-2/FR-5/FR-6; unit tests for
   dedup-across-restart and dedup-across-repeat-failure (the two cases in
   FR-2's acceptance criteria) mirroring `test_source_poller.py`/
   `test_github_source.py`.
5. **Multi-workflow wiring (FR-7)** — extend `build()`/`Harness`/
   `BoardProjection.column_order` to union steps/columns across the active
   workflow set; architecture-test coverage that this doesn't reopen any of
   the guarded invariants (dispatcher/consumer still import only ports,
   `ports/source`/`ports/forge` still import no drivers).
6. **CLI wiring (FR-8)** — `--heal`/`--healer-repo`, `build()` constructing
   `FailedQueueTaskSource` from its own freshly-built `failed` queue,
   `harness init` seeding `healer.json` + `diagnose.json`.
7. **End-to-end coverage** — an e2e test in the shape of
   `test_phase4_e2e.py`: a `default` task fails → poll → healer task runs
   `diagnose` → `bug_confirmed` → `file_issue` opens an issue (fake forge) →
   healer task in `done/`; and the `not_a_bug` branch straight to `done/`
   with no issue filed. Extend `test_smoke_git.py`/`test_smoke_github.py`
   coverage only if the architecture step decides real-git/real-GitHub
   coverage is warranted beyond the in-memory e2e.

## Open questions

- **Backlog burst on rollout.** Turning on `--heal` for the first time will
  enqueue a healer task for every default-workflow failure already sitting
  in `failed/`, however old. Default for v1: no time filtering — heal
  everything currently failed. If the backlog is large, the operator can
  clear/archive `failed/` before opting in. Revisit if this proves
  disruptive in practice.
- **Cross-failure issue dedup.** Left out of scope (see above); a follow-up
  could have the `diagnose` persona search existing open issues on the
  healer repo for a matching one before recommending `bug_confirmed`, but
  that's a persona-prompt refinement, not a harness mechanism, and is
  deferred.
- **Exact `open_issue` idempotency mechanism** (FR-4): a body marker
  (`<!-- harness-healer:<task.id> -->`) checked via a new
  `GithubClient.find_issue`, versus a dedicated label plus title match.
  Recommend the marker approach (mirrors `_body`'s existing `Closes #n`
  convention in `GithubForge`) — left for the architecture step to confirm.
- **Should `report_progress`/`finish` for `FailedQueueTaskSource` do
  anything at all** (e.g. leave a breadcrumb on the *original* failed
  task's JSON that it's "under investigation")? Default for v1: no-op, kept
  symmetric with the trivial `MemoryTaskSource` shape — revisit if operators
  want that visibility on the board.
- **Step-name collision guard.** FR-7 assumes step names stay unique across
  concurrently-active workflows but does not propose enforcing it in code
  (e.g. a `build()`-time assertion). Recommend adding one cheaply during
  the architecture step rather than leaving it as a silent footgun.
