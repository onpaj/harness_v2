# Phase 2 — artifacts, worktree, and landing

Status: draft
Date: 2026-07-20

## Goal

A task stops being merely a token that travels between queues. In phase 2 every
phase **works in a worktree** named in the task, **produces artifacts** (plan,
design, review) into a harness-owned folder, and **commits its work** with its
own message. At the end of the workflow the **landing** step folds the artifacts
into the worktree and opens a **pull request**. The harness never touches
`main` — it only proposes.

Phase 2 is still a POC. The real agent, real GitHub, and production storage get
swapped in later phases — **by swapping the driver, never its surroundings**.

### What's new in phase 2

- `repository` and `worktree` stop being opaque cargo. The behavior reads them
  in order to attach to a working surface. **The router and dispatcher still
  don't read them.**
- Phases produce artifacts. The behavior writes them *live* into the harness
  folder; the UI watches them accumulate.
- The behavior returns `(outcome, summary)` instead of a bare `Outcome`.
  `summary` is a short description of "what I did".
- Work is committed per phase onto the task branch. The commit message comes
  from `summary`.
- The landing step folds the artifacts into the worktree and opens a PR.

### What's still out of scope

- **The real agent.** The behavior is a dummy — it writes prepared artifacts and
  returns a deterministic outcome + summary.
- **Real GitHub.** Landing goes through the `Forge` port; in phase 2 it has a
  test driver (records the PR / pushes into a local bare remote). The GitHub
  driver is a clean follow-up — a driver swap.
- **Multiple processes, retry policies, rate limiting, lease TTL.**

## Foundational thesis (ARD2): a task is a transaction

During a task, all work lives in an isolated working surface that the project's
real history never sees. At the end it's folded in as a whole — or not at all.

Three properties that follow from this, and that phase 2 protects:

1. **Atomicity.** A task that fails or is abandoned leaves the project
   untouched.
2. **Clean history.** The project doesn't see five plan revisions and a
   request_changes loop. It sees one proposal (the PR).
3. **Isolation.** Two tasks running concurrently never collide in the project's
   history, because neither is in it until it lands.

The price: the harness must hold potentially large working state (worktree +
artifacts folder) per task, **durably**, for the task's entire lifetime.
"Unversioned during the task" ≠ "non-persistent" — crash recovery applies here
too.

## Two working surfaces per task

At runtime a task has **two** separate surfaces. They are separate on purpose.

| Surface | What it holds | Versioning | Who reads it |
|---|---|---|---|
| **Worktree** (`repository`/`worktree`) | the code the phases edit | task git branch, commit per phase | behavior |
| **Artifacts folder** (harness) | plan, design, review | not versioned until landing | behavior + UI |

Why not a single surface (artifacts directly in the worktree):

- **`git status`/`diff` stays clean.** The code diff is just code, not
  scaffolding.
- **The UI reads without git.** One shared harness folder, not N worktrees
  across N repos.
- **Resilience to git operations.** `git clean -fdx`, switching branches, or a
  reset in the worktree can't delete artifacts — they lie outside git's domain.
- **"Unversioned during the task" is free.** In the worktree an unversioned
  artifact would have to be *untracked* → exactly the state `git clean` wipes.
  In a folder outside git, "unversioned" and "resilient" hold at the same time;
  in the worktree they're mutually exclusive.

The price is the landing step that folds the artifacts into the worktree — and
that's actually an advantage: it decides *where* in the repo the artifacts land.

### Artifact addressing — attempt-indexed

Artifacts live under `<artifacts_root>/<task-id>/<step>/<attempt>/<name>`.

`attempt` is the sequence number of a given step's run by a given task. The
back edge (`review --request_changes--> development`) means `development` and
`review` both run more than once. If the second run overwrote `review.md`, the
audit trail would lose the loop — so **each attempt gets its own subdirectory**.
The store allocates the next `attempt` slot on `begin(task_id, step)`.

## Behavior contract

```python
@dataclass(frozen=True)
class BehaviorResult:
    outcome: Outcome
    summary: str = ""

class ConsumerBehavior(ABC):
    async def run(self, task: Task) -> BehaviorResult: ...
```

`run` returns a `BehaviorResult` instead of an `Outcome`. Why this doesn't break
the separation of roles from phase 1:

- **`outcome` already had to be a return value** — it's the control signal the
  dispatcher routes on. Bundling `summary` alongside it is symmetric: "this is
  what happened and this is what I did".
- **`summary` is a terminal statement about the run** — one sentence at the end
  of the work. That's different from the plan/review, which are *streamed* into
  the artifacts folder as the agent works. Both live side by side: large
  documents into the folder live, the summary on return.

A single `summary` serves **four** consumers:

1. **Commit message** — the behavior driver commits with it (`[development] added…`).
2. **History line** — the consumer writes it into the audit log.
3. **Board UI** — "what each step did".
4. **PR body** — landing aggregates the summaries from history into the PR
   description.

### Where the commit happens — the behavior driver, not the consumer

"The harness commits, not the agent" means precisely: the commit is done by the
**behavior driver** (the harness code wrapping the agent), not the LLM. The
agent edits files and *says* what it did; it never runs `git commit`. We don't
depend on it remembering to do that correctly, or on it staging the right paths.

The commit therefore lives in the behavior (which has the worktree and "what
changed" on hand), **not in the thin consumer** — which never acquires a git
dependency. The invariant "the consumer only delivers the outcome, never
branches on its value" still holds: the consumer writes both the outcome and the
summary, but decides nothing based on them.

## Landing

Landing is a **normal workflow step**, not harness magic. It's the last step
before `end`. Its behavior:

1. Attaches to the worktree.
2. Reads the task's artifacts folder and **folds** it into the worktree (e.g.
   under `docs/tasks/<id>/`), commits "[land] task artifacts".
3. Opens a PR through `Forge` — title from the task's original assignment, body
   from the aggregated summaries in history.
4. Returns `BehaviorResult(DONE, "opened PR …")`.

Because it's a step, it can fail (push rejected, API error) → `failed/`, the same
machinery as everywhere else. `end` stays a clean terminal with no side effects.
The user reviews the PR and merges it with their own strategy
(squash/rebase/merge) — the harness doesn't decide the merge strategy.

### Landing idempotence

Landing is multi-step (commit → push → open PR). A re-run after a crash must be
idempotent: "open the PR if it doesn't exist yet". Phase 2 solves this the
simplest way — the Forge driver returns the existing PR when one already exists
for the branch.

## New ports and drivers

| Port | Responsibility | Phase 2 driver | Swapped for |
|---|---|---|---|
| `Workspace` | `attach(task) -> WorkspaceHandle`; the handle has `path`, `branch`, `commit(msg) -> sha \| None` | git worktree | — |
| `ArtifactStore` | `begin(task_id, step) -> ArtifactSlot`; read: `list/read` | folder on disk | S3, DB |
| `ArtifactView` | read-only subset of `ArtifactStore` for the UI | the same fs driver | — |
| `Forge` | `open_pull_request(task, branch, title, body) -> PullRequest` | fake (records / local bare) | GitHub API |

Every port gets an **in-memory driver for tests**. The orchestration
(dispatcher, consumer) **doesn't know** the `Workspace`/`Forge`/`ArtifactStore`
ports — only the behavior touches them. The wiring is in `app.py`.

### WorkspaceHandle

- `path: Path` — the working directory where the behavior edits.
- `branch: str` — the task branch (`harness/<task-id>`) the commits sit on.
- `commit(message) -> str | None` — stages everything and commits; returns the
  sha, or `None` when there's nothing to commit (a phase with no code change —
  plan, review).

### GitWorkspace

- `attach(task)`: worktree under `task.worktree` for repo `task.repository`.
  If it doesn't exist, `git worktree add <worktree> -b harness/<task_id> <base>`;
  if it does, reuse it. Returns the handle.
- Two tasks **must not** share a worktree — otherwise they'd overwrite each
  other's work. In phase 2 the task's author guarantees this; the harness
  invariant just documents it.

## Changes to the workflow model

The default workflow gains a `land` step before `end`:

```json
{"from": "review", "on": "done", "to": "land"},
{"from": "land",   "on": "done", "to": "end"}
```

`land` is an ordinary step with its own queue. The wiring assigns it
`LandingBehavior`; the other steps get `DummyBehavior`. Which step is the landing
step is configuration (`landing_step`, default `"land"`), not a magic name in the
core.

## HistoryEntry — new `summary` field

`HistoryEntry` gains an optional `summary: str | None`. The consumer fills it
with the value from `BehaviorResult`. It's serialized only when present. The
audit log thus carries not only *what happened* (outcome) but also *what was
done* (summary).

## Recovery stops being free

Phase 1 leaned on "work is idempotent, recovery just re-runs it". A real agent
that half-edited the worktree breaks that assumption. In phase 2 with the dummy
behavior a re-run is still safe, but the seam is real:

- The per-phase commit is a clean recovery point — the last commit holds the
  finished work, the phase replays from there.
- On a re-run the store's `begin()` allocates a **new** attempt, so half-written
  artifacts don't mix with the new run.

The lease TTL still isn't implemented in phase 2.

## Error states (added to phase 1)

| Situation | Detection | Where |
|---|---|---|
| Behavior doesn't return a `BehaviorResult` | validation in the consumer | `failed/` |
| `attach` fails (repo/worktree missing) | exception from the behavior | `failed/` |
| `commit`/landing fails | exception from the behavior | `failed/` |
| Forge rejects the PR | exception from the behavior | `failed/` |

All via the existing `_fail` path — one bad task doesn't stop the loop.

## UI

The board from phase 1 gets a second thing to render: **artifacts per task**,
live. The API touches `ArtifactView` (a read-only port), never the driver. The
task detail shows a list of artifacts (step, attempt, name) and their contents.

## Code structure (additions)

```
src/harness/
  models.py            # + BehaviorResult, HistoryEntry.summary
  consumer.py          # run() returns BehaviorResult; writes summary
  ports/
    workspace.py       # Workspace, WorkspaceHandle
    artifacts.py       # ArtifactStore, ArtifactView, ArtifactSlot, ArtifactRef
    forge.py           # Forge, PullRequest
  drivers/
    memory.py          # + MemoryWorkspace, MemoryArtifactStore, MemoryForge
    git_workspace.py   # GitWorkspace
    fs_artifacts.py    # FilesystemArtifactStore
    fake_forge.py      # FakeForge
    dummy_behavior.py  # writes artifacts, commits, returns (outcome, summary)
  behaviors/           # (new) landing.py — LandingBehavior
  app.py               # wiring of the new ports, per-step behaviors
```

## Invariants — new/refined

These extend the list in `CLAUDE.md`, they don't replace it.

8. **Only the behavior reads `repository`/`worktree`.** The router and
   dispatcher still decide solely from `(status, lastOutcome)`.
9. **The commit is done by the behavior driver, not the consumer and not the
   LLM.** The consumer doesn't know git.
10. **Artifacts are attempt-indexed.** A step re-run never overwrites a previous
    attempt.
11. **`Workspace`/`Forge`/`ArtifactStore` are unknown to the dispatcher and
    consumer.** Only the behavior touches them; wiring in `app.py`. `api/`
    touches only `ArtifactView`.
12. **Landing is a step, not magic.** `end` stays a clean terminal.

## Done-ness verification

Phase 2 is done when:

1. A task with `repository`+`worktree` flows through
   `plan → … → review → land → end`.
2. Every phase wrote an artifact into `<task>/<step>/<attempt>/` and the UI sees
   it.
3. The back edge (`request_changes`) creates a second attempt of both
   `development` and `review` — both attempts are visible in the folder.
4. The worktree carries a per-phase commit with a meaningful message (from the
   summary), not "development stage".
5. Landing folded the artifacts into the worktree and opened a PR (the fake
   Forge recorded the branch, title, and body from the aggregated summaries).
6. The `history` of a landed task carries a `summary` on every consumer line.
7. Killing the process midway and restarting leads to the task completing
   (recovery + a new attempt).
8. Architecture tests: the dispatcher/consumer don't import the new ports or
   drivers; `api/` touches only `ArtifactView`; the consumer doesn't branch on
   the outcome.
```
