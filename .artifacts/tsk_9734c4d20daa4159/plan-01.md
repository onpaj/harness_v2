# Plan — mergeability watcher (auto-rebase "behind" PRs, queue conflicts to a resolver step)

## Summary

Harness-opened PRs can drift out of date once other work lands on the base
branch. Today nothing notices: a PR silently goes "behind" or, worse,
"dirty" (real merge conflicts) and sits there until a human happens to look.
This feature adds a periodic watcher that checks the mergeability state of
every open, harness-authored PR: a PR that is merely **behind** gets
auto-updated against the base with no human involved; a PR that is **dirty**
(actual conflicts) gets a fresh task queued into a new **resolver** workflow,
whose `resolve` step hands the conflicted files to an agent and whose `land`
step pushes the fix back onto the *same* PR.

## Context

Landing (`behaviors/landing.py`) opens a PR and the task then reaches `end` —
a clean terminal (invariant 12). Nothing in the harness watches that PR
afterwards. In practice, PRs pile up behind a moving `main`; some go stale
(fast-forwardable — "behind"), some genuinely conflict ("dirty"). Without
this feature that triage is entirely manual. The fix should reuse the
project's existing "outer world behind a port" shape (`TaskSource` /
`SourcePoller`, phase 4) rather than inventing a new orchestration primitive:
a conflicted PR is, from the harness's point of view, just another piece of
inbound work — the same shape as a GitHub issue.

## Functional requirements

**FR-1 — Discover harness-owned open PRs.**
For every repository in `repos.json` that has a GitHub origin, periodically
list open PRs whose head branch matches the harness's naming convention
(`harness/*`, per `GitWorkspace.attach`). PRs from outside the harness are
never touched.
- AC: a PR opened by a human (branch not prefixed `harness/`) is never listed,
  never rebased, never routed to the resolver.
- AC: a PR that already exists for a repo not in `repos.json`, or for a repo
  in `repos.json` with no GitHub origin, is not watched (mirrors
  `_github_sources`' existing skip-with-warning behavior).

**FR-2 — Auto-update a "behind" PR.**
When a watched PR's `mergeable_state` is `behind`, call GitHub's "update
branch" API (`PUT /pulls/{n}/update-branch`) directly — no local git, no
worktree, no new task. This is the same action as the "Update branch" button
in GitHub's UI: a merge of the base into the head, performed server-side.
- AC: a `behind` PR is updated without creating any harness task or event
  other than a log/audit event.
- AC: calling update on a PR that is already up to date (or where the API
  reports it needs no update) is a no-op, not a failure — idempotent, safe to
  call every tick.
- AC: an update call that itself produces a conflict (base and head diverged
  in a conflicting way) is treated as `dirty` on the *next* poll, not as an
  error on this one — no special-casing of the update call's own outcome
  beyond "don't crash the loop".

**FR-3 — Queue a "dirty" (conflicted) PR to a resolver task.**
When a watched PR's `mergeable_state` is `dirty`, create exactly one new
task, on the **resolver** workflow (`resolve → land → end`), targeting the
same repository and the *same branch* as the conflicted PR (not a fresh
branch). The task is put in the inbox like any other task.
- AC: one dirty PR produces exactly one resolver task per distinct
  "occurrence" of the conflict (see dedup rule below) — never a duplicate
  while the previous resolver task for the same occurrence is still in
  flight, and this holds across a harness restart (dedup key persists on the
  task, exactly as `TaskSource` dedup already works, see `source_poller.py`).
- AC: if the PR is fixed and later becomes conflicted *again* (a new commit
  landed on `main` reintroduces conflicts), a new resolver task is created —
  the dedup key must vary with the occurrence, not just with the PR number
  (proposed: include the PR's current head commit sha in the key).
- AC: the resolver task's `data.source` records `{kind: "mergeability", repo,
  pr, url, base}`, following the existing `data.source` convention
  (invariant 19), so the board's existing task-detail view shows it with no
  UI change.

**FR-4 — The `resolve` step produces a real merge-conflict working tree.**
Before the step's agent is invoked, the attached worktree must already
contain the actual conflict (base merged into the branch, with conflict
markers) — otherwise there is nothing to resolve. If, by the time the
resolver task reaches this step, the conflict has disappeared on its own
(race: someone else updated the branch), the step completes without invoking
the agent at all.
- AC: given a real conflict, the agent sees standard `<<<<<<<`/`=======`/
  `>>>>>>>` markers in the working tree and a persona instructing it to
  resolve them (no commit — the worker commits, invariant 9).
- AC: given no actual conflict at attach time, the step commits the
  (fast-forward or clean-merge) result and returns `done` without spending an
  agent call.

**FR-5 — Land the fix onto the existing PR, not a new one.**
The resolver task's `land` step reuses `LandingBehavior`/`GithubForge`
unchanged: it pushes the branch (plain push, no force — the merge commit
moves the branch forward, it does not rewrite history) and opens a PR, which
`GithubForge` resolves idempotently to the *already-open* PR for that head
branch. No duplicate PR is created.
- AC: after a resolver task finishes, the original PR (same number, same
  URL) carries the new merge-resolution commit; no second PR was opened.

**FR-6 — Isolation and resilience.**
A GitHub outage or a single bad PR must not stop the watcher loop or the rest
of the harness, mirroring `SourcePoller.tick`'s existing exception handling.
- AC: an exception raised while checking one PR is caught, logged as an
  event, and the loop proceeds to the next PR / next tick.
- AC: `harness run` without `GITHUB_TOKEN` runs exactly as it does today —
  the watcher is simply absent, like the existing GitHub `TaskSource` is
  absent without a token.

**FR-7 — Operator visibility.**
No new UI is required. Resolver tasks appear on the existing board like any
other task (new workflow name, same `BoardView`); their `data.source` is
visible in the task-detail JSON the same way an issue-sourced task's is
today.

## Non-functional requirements

- **Rate limits.** Listing PRs and checking mergeability happens on the same
  slow cadence as the existing `TaskSource` polling (`--source-poll`,
  default 30s) — this feature adds no new poll cadence, it rides the
  existing `SourcePoller` loop.
- **Idempotency.** Every outward call (`update-branch`, label changes) must
  be safe to repeat every tick — mirrors invariant 21 (the outward projection
  is idempotent and doesn't block decision-making).
- **Testability.** No real waiting or real network in unit/e2e tests — a
  fake GitHub client (extending the existing `FakeGithubClient`) drives the
  new behavior, following the project's `FakeClock`/fake-driver convention.
  `tests/test_smoke_github.py`-style opt-in coverage may exercise the real
  API separately.
- **No new production dependency.** The GitHub calls this needs
  (`GET .../pulls`, `PUT .../update-branch`) are added to the existing
  stdlib-`urllib`-based `HttpGithubClient` — no new package.
- **Security.** Reuses the existing `GITHUB_TOKEN` credential path (OneCLI /
  env var already wired into `cli.py` and the launchd service); no new
  secret is introduced. The update-branch call requires the same repo-write
  scope the forge already needs to open PRs.

## Data model

- **`PullRequestInfo`** *(new, driver-level, alongside `Issue` in
  `drivers/github_client.py`)* — `number`, `url`, `head_branch`, `head_sha`,
  `base_branch`, `mergeable_state` (`behind` | `dirty` | `clean` | `blocked` |
  `unstable` | `unknown`, GitHub's own vocabulary).
- **`Task.data["branch"]`** *(new, optional key in the existing free-form
  `data` dict)* — when present, `GitWorkspace.attach` checks out this
  *existing* branch (fetch + track) instead of creating `harness/<task.id>`
  from the repo's HEAD. Absent (the default, every non-resolver task) →
  today's behavior is unchanged.
- **`Task.data["source"]`** *(existing convention, invariant 19)* — resolver
  tasks populate it with `{kind: "mergeability", repo, pr, url, base}`.
- **`Task.dedup_key`** *(existing field)* — `mergeability:<repo>:<pr-number>:<head-sha>`,
  so the same conflict occurrence is never queued twice, but a later,
  different occurrence on the same PR is.
- **Workflow `resolver`** *(new, `workflows/resolver.json`, same shape as the
  existing `default.json`)* — `start: "resolve"`, transitions `resolve
  --done--> land`, `land --done--> end`.

No new persisted store is introduced — no PR registry, no side ledger. "Which
PRs are ours" is answered by listing GitHub directly and filtering by branch
prefix, the same way `GithubTaskSource.poll()` answers "which issues are
ours" by filtering on a label; "have we already queued this occurrence" is
answered by the existing `dedup_key` + `SourcePoller._seen`/`seed` machinery.

## Interfaces

- **`GithubClient`** *(extend, `drivers/github_client.py`)*:
  - `list_pull_requests(repo: str, *, head_prefix: str | None = None) -> list[PullRequestInfo]`
  - `update_branch(repo: str, number: int) -> None` — idempotent; a 422
    ("already up to date" / "not behind") is swallowed, not raised.
  - Implemented on both `FakeGithubClient` (tests) and `HttpGithubClient`
    (real, stdlib `urllib`).
- **`GithubMergeabilityWatcher(TaskSource)`** *(new driver,
  `drivers/mergeability_watcher.py`)* — `kind = "mergeability"`:
  - `poll()`: for each open PR under `head_prefix="harness/"`: `behind` →
    call `update_branch` (side effect, no task returned); `dirty` → return a
    new resolver `Task` (per FR-3), skipped by `SourcePoller` if its
    `dedup_key` was already seen; anything else → ignored. This is a
    deliberate, documented widening of `TaskSource.poll()`'s contract (it
    both returns tasks *and* performs one outward side effect per tick) —
    the alternative (a second, bespoke core loop) was rejected because it
    would duplicate `SourcePoller`'s dedup/seed/isolation logic for no
    benefit.
  - `report_progress`/`finish`: optional label projection onto the PR
    (`harness:resolving` while in `resolve`/`land`, cleared on `finish`),
    mirroring `GithubTaskSource`'s `_set_state` pattern. PRs are GitHub
    "issues" under the hood, so the existing `add_label`/`remove_label`
    calls apply unchanged.
  - Wired exactly like `GithubTaskSource` today: one instance per repo with a
    GitHub origin, added to the same `sources: list[TaskSource]` passed into
    `build()` — it gets a `SourcePoller` "for free", no new loop in
    `Harness.run()`.
- **`GitWorkspace.attach`** *(extend, `drivers/git_workspace.py`)* — reads
  `task.data.get("branch")`; when set, `git fetch origin <branch>` +
  `git worktree add <path> <branch>` (tracking) instead of `-b harness/<id>`
  from HEAD. Reattach (existing worktree) is unchanged (`reset --hard HEAD` +
  `clean -fd`, now against whatever branch is checked out there).
- **`resolve` step behavior** *(new, `behaviors/resolve_conflict.py` or a
  variant of `ClaudeCliBehavior` — left to the design step)* — on `run()`:
  attach, `git fetch origin <base>` + attempt a merge; if clean, commit and
  return `done` without an agent call (FR-4); if conflicted, run the agent
  (same `AgentRunner`/`AgentCatalog`/`AgentSpec` machinery as every other
  step, new persona `resolve`) then commit exactly as `ClaudeCliBehavior`
  does today (worker commits, invariant 9).
- **CLI (`cli.py`)**: a new helper `_mergeability_sources(args, root,
  registry, ...)`, mirroring `_github_sources`, feeding the same `sources`
  list already passed into `build()`. New flags: `--watch-mergeability`
  (default: on when a GitHub token/registry entry is present, symmetric with
  how `--github-repo` sources already default off without a token),
  `--resolver-workflow` (default `resolver`).
- **`build()` / `app.py`**: step queues must be created for every workflow
  template actually in use, not just the one named at `build()` time — today
  `step_queues` is derived from a single `workflow_name`. Extend it to union
  `steps()` across the primary workflow and the resolver workflow whenever
  mergeability sources are configured.
- **`harness init`**: also writes `workflows/resolver.json` (new fixed
  definition, alongside the existing `default.json`) and a default
  `agents/resolve.json` persona (alongside `plan`/`design`/.../`review`).

## Dependencies and scope

Depends on (all already shipped): the phase-4 `TaskSource`/`SourcePoller`
machinery, `RepositoryRegistry` + `GitWorkspace` (phase 3), `GithubForge`'s
idempotent-by-branch PR opening (phase 2 design), `GithubClient`/
`HttpGithubClient` (phase 4).

Out of scope for this task:
- Any new UI/board surface beyond what already renders `data.source` and a
  new workflow's tasks — no PR-mergeability dashboard.
- Rebasing (history rewrite) as the conflict-resolution strategy — the
  resolver produces a **merge** commit specifically so the existing
  no-force-push invariant in `GitWorkspaceHandle.push()` keeps holding. If a
  linear-history rebase strategy is wanted later, it needs its own review of
  that invariant.
- Handling non-harness PRs, non-GitHub forges, or repos with no registered
  GitHub origin.
- Webhooks / real-time triggering — this rides the existing poll cadence.
- Automatic merging of PRs once mergeable again — merging remains a human's
  call, exactly as today (the harness only ever proposes, never merges).

## Rough plan

1. **`GithubClient` additions** — `PullRequestInfo`, `list_pull_requests`,
   `update_branch` on `FakeGithubClient` + `HttpGithubClient`; unit tests.
2. **`GitWorkspace.attach` branch override** — `task.data["branch"]` support,
   with tests covering: fresh checkout of an existing branch, reattach after
   a crash, and that the default (no `branch` key) path is byte-for-byte
   unchanged.
3. **`resolver` workflow + resolve-step behavior** — `workflows/resolver.json`,
   the new behavior (merge-then-agent-if-needed), a default `resolve`
   persona in `cli.py`; unit tests with `FakeAgentRunner` for both the
   "already clean" and "real conflict" paths.
4. **`GithubMergeabilityWatcher`** — the new `TaskSource` driver: discovery
   by branch prefix, `behind` → `update_branch`, `dirty` → resolver task with
   the sha-scoped dedup key; unit tests against `FakeGithubClient`.
5. **Wiring** — `build()`/`app.py` union step queues across workflow
   templates in use; `cli.py` `_mergeability_sources` + new flags;
   `harness init` writes `resolver.json` + `agents/resolve.json`.
6. **End-to-end** — an in-memory e2e (mirroring `test_phase4_e2e.py`): a
   fake conflicted PR → resolver task flows through `resolve`→`land`→`end` →
   same PR updated, no duplicate; a fake `behind` PR → `update_branch`
   called, no task created; a restart mid-flight doesn't duplicate the
   resolver task (dedup survives via `seed`).
7. **Docs** — extend `CLAUDE.md`'s invariants list with the new ones this
   feature introduces (see Open questions, first item) and note the new
   driver/behavior in the module map.

## Open questions

- **New invariants to add to `CLAUDE.md`.** This feature introduces real new
  rules that later phases must not accidentally break — e.g. "a task's
  workspace branch is `harness/<task.id>` unless `data.branch` overrides it"
  and "conflict resolution is always a merge, never a rebase, to preserve
  the no-force-push invariant." Default: add them during step 7 above, worded
  to extend (not replace) the existing numbered list, as every prior phase
  did.
- **Where the merge-then-maybe-agent logic lives.** A new dedicated
  behavior class vs. a flag/hook on `ClaudeCliBehavior`. Default: a small new
  behavior (`ResolveConflictBehavior`) that wraps the same
  `AgentRunner`/`AgentSpec` call `ClaudeCliBehavior` uses, so the generic
  behavior stays free of git-merge knowledge and the blast radius of this
  feature stays contained to one new file. Left for the architecture step to
  confirm.
- **Multiple resolver attempts.** If the agent's conflict resolution itself
  turns out wrong (e.g. review would have caught it, but the resolver
  workflow has no review step), is one `resolve → land` pass enough, or
  should `resolver` include a review step too? Default: keep `resolver`
  minimal (`resolve → land`) for v1 — conflict resolution is mechanical
  enough that a review step can be added later without changing the shape of
  this feature.
- **`update_branch` on a PR with failing/pending checks.** GitHub may refuse
  or the state may be `unstable`/`blocked` rather than cleanly `behind`.
  Default: treat every state other than `behind`/`dirty` as "leave alone" —
  no action, no task — for v1; branch protection / required-checks handling
  is future work.
- **Per-repo opt-out.** Should watching be configurable per entry in
  `repos.json`, or is the CLI-level `--watch-mergeability` flag (all-or-
  nothing across the registry) enough for v1? Default: all-or-nothing flag,
  consistent with how `--github-repo`/`GITHUB_TOKEN` today enable GitHub
  ingestion for every registered repo uniformly.
