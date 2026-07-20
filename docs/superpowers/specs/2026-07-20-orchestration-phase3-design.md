# Phase 3 — a real agent via `claude -p`

Status: draft
Date: 2026-07-20

## Goal

Replace `DummyBehavior` with a driver that hands the real work of a step to an
**agent launched through the `claude` CLI** (`claude -p`, headless), **not through
the API**. Each queue has its own agent (`architect`, `planner`, `reviewer`, …) —
a different persona, a different model, a different tool set. The CLI call is one
shared wrapper; what differs from queue to queue is the **agent's configuration
as data**.

Phase 3 changes only *how a `BehaviorResult` comes to be*. The loop, dispatcher,
router, queues, projection, and ports from phases 1–2 do not change — invariant 1
holds: **you swap the driver, never its surroundings.** The real agent is a driver
behind a port.

## What's new in phase 3

- **`AgentRunner` (port).** A shared wrapper around `claude -p`. It receives an
  invocation `(prompt, agent_spec, cwd, timeout)`, launches the subprocess, and
  returns a structured result. The real driver assembles the CLI flags; the fake
  driver returns canned output for tests — **no subprocess, no network, no money
  in the test suite.**
- **`AgentCatalog` (port) + `AgentSpec` (data).** Named agent definitions.
  `AgentSpec` carries the persona, model, tools, and allowed outcomes. The
  queue→agent binding defaults to name identity.
- **`ClaudeCliBehavior`.** A generic behavior constructed from `(agent_spec, runner,
  workspace)`. It replaces `DummyBehavior`: attach the worktree, run the agent,
  map its verdict onto a `BehaviorResult`, and the worker commits.
- **`RepositoryRegistry` (port).** A map from repo name → path on disk,
  **machine-specific**. The task carries only the repo name; the harness derives
  the worktree path itself.
- **Artifacts move into the worktree.** The phase asks the agent to write artifacts
  (plan, ADR, review) into `.artifacts/<task-id>/` inside the worktree. They are
  **versioned** — the worker commits them alongside the code. Earlier steps
  therefore see them as ordinary files in their cwd.

## What's still out of scope

- **Real GitHub.** Landing still goes through `Forge`; a live run uses a fake /
  local driver. The GitHub driver is a clean follow-up — a swap of the forge
  driver.
- **Multiple processes, lease TTL, distributed execution.** One process, recovery
  at startup, as before.
- **Retry policies beyond `fallback_model`.** Transient errors (rate-limit,
  timeout) fall into `failed/`; more sophisticated retry is deliberately deferred
  (see Open questions).

## Load-bearing thesis (ARD3): the agent is a driver, the persona is data

The decision about "what happened" (`Outcome`) and "what was done" (`summary`)
still arises in one place — the behavior. Phase 1 handled it with `sleep`, phase 2
with the dummy, phase 3 with a real agent. From the consumer's, dispatcher's, and
router's point of view **nothing changes**; they still receive a `BehaviorResult`
and route on `(status, lastOutcome)`.

Two things follow from this that phase 3 protects:

1. **The agent lives behind `AgentRunner`.** `ClaudeCliBehavior` knows nothing of
   the subprocess or the flags; it knows only the port. A test drives it with a
   `FakeAgentRunner` — just as phase 1 drives time with a `FakeClock`. Without
   this seam the behavior is untestable.
2. **The persona is configuration, not code.** There is no branch on the agent's
   name inside `ClaudeCliBehavior`. The difference between `architect` and
   `reviewer` is the content of the `AgentSpec` it was constructed with. Adding an
   agent = a new file in the catalog, not a new class.

## Repository registry — where repos live on this machine

Phase 2 treats both `task.repository` and `task.worktree` as **bare filesystem
paths** (`GitWorkspace.attach`: `repo = Path(task.repository)`). This leaks a
particular machine's layout into the task, and the task stops being portable.

Phase 3 splits this apart:

- **`task.repository` is a logical name** (`"harness_v2"`), not a path.
- **`RepositoryRegistry.resolve(name) -> Path`** — a map from name → the repo root
  on disk. Machine-specific config (`~/.harness/repos.json` or env), **outside the
  task, uncommitted.**
- **The harness derives the worktree path**, not the submitter:
  `<worktrees_root>/<task_id>`. `task.worktree` stops being a required input — it's
  derived.

`Workspace.attach(task)` then does: `base = registry.resolve(task.repository)` →
`git worktree add <worktrees_root>/<task_id> -b harness/<task_id>` from `base`.

The phase-3 driver: `FilesystemRepositoryRegistry` (reads JSON). An in-memory
driver for tests. Neither the dispatcher nor the consumer knows the registry —
only `Workspace` touches it, through wiring.

## Artifacts in the worktree — versioned, flat, attempt-suffixed

Phase 2 wrote artifacts into a harness-owned folder *outside* the worktree. But a
real subprocess agent sees **only its own cwd** — the architect's plan in an
external folder would be invisible to the developer. So the artifacts move **into
the worktree**, where every subsequent step sees them as ordinary files.

### Layout

```
.artifacts/<task-id>/
  plan.md
  architecture-decisions.md
  development-01.md
  review-01.md
  development-02.md
  review-02.md
```

- **The `.artifacts/` root** — the dot-prefix signals "harness metadata, not source
  code"; most tools (pytest, linters, coverage) skip dot-directories, so the
  artifacts don't pollute the target repo's tooling.
- **Flat files, no hierarchy.** The attempt is in the filename suffix, not a
  subdirectory — the listing sorts lexically by step and then by attempt, so the
  loop is legible at a glance. Should a step need several files per attempt, the
  `development-02` prefix groups them anyway; a hierarchy would needlessly lock in
  the shape.
- **Task-level = bare name** (`plan.md`), **step-attempt = `<step>-NN`**
  (two-digit zero-pad, per-step counter). Steps the workflow returns to via the
  loop get a number; run-once steps get the bare name.

### Who counts `NN`

Before launching the agent, the behavior driver scans `.artifacts/<task-id>/`,
counts the existing `<step>-*.md`, and allocates the next number. It's the remnant
of phase 2's `ArtifactStore.begin()` shrunk to a small helper over the worktree
filesystem — the standalone store disappears on the write side.

### Versioning and commit

The agent **writes** the artifacts; it does not run `git add`/`commit` (invariant
9 still holds). Once the agent finishes, the worker commits everything — code and
`.artifacts/**` — with `summary` as the message. `GitWorkspace.commit` already
does `git add -A` today, so it picks up the artifacts unchanged; the only real
change is *where the agent writes*, not *how the commit happens*.

Consequence: the artifacts ride along in git history and land in the PR as design
documentation. They survive the worktree being torn down (they're in the branch),
so the board and the audit log see them even after the task is done. Landing
thereby loses its copy step — the artifacts are already in the worktree; landing
just opens the PR.

### Recovery — gapless numbering for free

An in-progress attempt (`development-02.md` that the agent is writing) is
**uncommitted** until the worker's commit. When the agent crashes halfway,
recovery does `reset --hard HEAD` (see below) → the uncommitted `development-02.md`
vanishes → the re-run counts the committed `development-*` = `01` → and allocates
`02` again. Same number, no gap, no half-written artifact. Three decisions
(indexing + commit + reset) fit together.

## Agent — catalog, spec, binding to the queue

### `AgentSpec` (data)

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str                      # = queue name (default binding)
    prompt: str                    # persona
    model: str | None = None       # None → harness-level default
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[Outcome, ...] = (Outcome.DONE,)
```

- `allowed_outcomes` is **our** concept, not a CLI flag. `architect`/`planner` may
  only return `DONE`; `reviewer` may return `DONE`/`REQUEST_CHANGES`. A verdict
  outside the set → exception → `failed/`. This keeps the contract next to the
  agent, not scattered across the workflow.

### `AgentCatalog` (port)

`get(name) -> AgentSpec`. The phase-3 driver `FilesystemAgentCatalog` reads
`agents/<name>.json` (**our** format, so the catalog is the single source of
truth). An in-memory driver for tests. An invalid/missing name → `AgentNotFound`,
symmetric to `WorkflowNotFound`.

### Queue → agent binding

By default **identity**: step name == agent name (`architect` queue → `architect`
spec). Indirection (two queues share an agent) is handled by an optional
`step → agent` map — either an `"agent"` field on the step in the workflow JSON,
or kept separately. The phase-2 wiring already has a `behavior_for(step)` hook; in
phase 3 it becomes `ClaudeCliBehavior(spec=catalog.get(agent_of(step)),
runner=shared, …)`.

## `AgentRunner` — a wrapper around `claude -p`

Port:

```python
class AgentRunner(ABC):
    async def run(self, *, prompt: str, spec: AgentSpec, cwd: Path,
                  timeout: float) -> AgentRun: ...

@dataclass(frozen=True)
class AgentRun:
    outcome: Outcome
    summary: str
    raw: str          # raw output for audit / event stream
```

### Driver `ClaudeCliRunner` — mapping onto flags

Verified against `claude 2.1.211`:

| `AgentSpec` / context | Flag |
|---|---|
| `prompt` (persona) | `--append-system-prompt` *or* `--agents '<json>' --agent <name>` |
| `model` | `--model` (alias or full ID) |
| `fallback_model` | `--fallback-model` |
| `allowed_tools` | `--allowedTools` |
| work prompt (task + step) | positional `-p "<prompt>"` |
| cwd | worktree path from `RepositoryRegistry` |
| — | `--output-format json` (machine-readable result) |
| — | `--permission-mode bypassPermissions` (headless, no human) |
| — | `--setting-sources project` (determinism, see below) |

Verdict: the agent, in its persona, is instructed to finish with a machine-readable
`{outcome, summary}`. The runner extracts it from the JSON envelope. A
missing/unreadable verdict, or one outside `allowed_outcomes` → exception →
`failed/`. Phase 2's `BehaviorResult(outcome, summary)` is almost 1:1 for this
verdict — the model doesn't change on account of phase 3.

### Timeout

`claude -p` runs for minutes, not milliseconds. The runner owns the timeout →
kills the subprocess → exception → `failed/`. No port from phases 1–2 knows about
a timeout; it's added here, inside the runner.

## `ClaudeCliBehavior` — the flow

```
attach worktree (Workspace, cwd from RepositoryRegistry)
  → allocate attempt number in .artifacts/<id>/
  → prompt = compose(task, step, pointers to .artifacts/ of earlier steps)
  → run = await runner.run(prompt, spec, cwd, timeout)     # agent writes code + artifacts
  → worker: handle.commit(run.summary)                     # the driver commits, not the agent
  → BehaviorResult(run.outcome, run.summary)
```

The behavior does not branch on the outcome value (invariant 2 still holds). The
driver commits, not the agent (invariant 9). The `attach`/`commit`/`failed` paths
are unchanged from phase 2.

## Environment determinism

From its cwd, `claude -p` **automatically pulls in the target repo's `CLAUDE.md`,
its skills, plugins, and MCP**. That can be desirable (the agent honors the repo's
conventions), but it's also a source of nondeterminism and of the operator's global
config leaking in. Phase 3 chooses `--setting-sources project` — it pulls the
repo's project config, not the user's. A per-agent override (`setting_sources` in
the spec) is an open question.

## Recovery

Phase 2's `Workspace.attach` merely reuses the worktree. But a real agent that
crashed halfway leaves a **dirty worktree**; a re-run would layer on top of it.
Phase 3 therefore does `git reset --hard HEAD` + `git clean -fd` (without `-x`, so
any ignored files survive) on re-attach — it returns the worktree to the last
per-step commit and throws away work-in-progress. This also keeps artifact
numbering gapless (see above). Committed artifacts and code survive; only the
in-progress run is replayed.

## Error states (added to phases 1–2)

| Situation | Detection | Where |
|---|---|---|
| `RepositoryRegistry.resolve` fails (unknown repo) | exception from the behavior | `failed/` |
| `claude` exits nonzero / crashes | runner raises | `failed/` |
| agent timeout | runner kill → exception | `failed/` |
| verdict missing / unreadable JSON | runner raises | `failed/` |
| verdict outside `allowed_outcomes` | behavior validates | `failed/` |
| `AgentCatalog.get` fails | exception during wiring/behavior | `failed/` |

All through the existing `_fail` path — one bad task does not stop the loop.

## New ports and drivers

| Port | Responsibility | Phase-3 driver | Swapped out for |
|---|---|---|---|
| `AgentRunner` | `run(prompt, spec, cwd, timeout) -> AgentRun` | `ClaudeCliRunner` (`claude -p`) | another agent CLI / API |
| `AgentCatalog` | `get(name) -> AgentSpec` | `FilesystemAgentCatalog` | DB, remote |
| `RepositoryRegistry` | `resolve(name) -> Path` | `FilesystemRepositoryRegistry` | — |

Each with an in-memory driver for tests. Orchestration (dispatcher, consumer)
knows none of them — only the behavior / wiring touches them. `api/` is unchanged.

## What changes from phase 2

- **`ArtifactStore` (the write side) retires.** The agent writes artifacts into
  the worktree; a path convention + attempt helper replaces `begin/put`.
  `ArtifactView` (reading for the board) **stays**, only its driver reads
  `.artifacts/` in the worktree instead of a separate folder (invariant 11 — `api/`
  touches only `ArtifactView`).
- **Landing** loses its copy step (the artifacts are already in the worktree); it
  just opens the PR.
- **`GitWorkspace.attach`** resolves the repo name through `RepositoryRegistry` and
  resets the worktree on re-attach.

## Testing story

- **`FakeAgentRunner`** returns a canned `AgentRun` — `ClaudeCliBehavior` is
  testable without a subprocess, without a network, without `claude`. Unit and
  integration tests run in-memory and on `FakeClock`, like the whole suite.
- **In-memory `AgentCatalog` / `RepositoryRegistry`** for tests.
- **No real `claude` in the test suite** — nondeterministic, expensive, and it
  requires auth. An optional smoke test with a real `claude` sits behind an env
  flag, outside `pytest -q` (see Open questions).
- `tests/test_smoke_git.py` from phase 2 (real git) stays; the artifacts move into
  the worktree in it.

## Invariants — new/refined

These extend the list in `CLAUDE.md` (1–12), they don't cancel it.

13. **The agent lives behind `AgentRunner`.** `ClaudeCliBehavior` knows nothing of
    the subprocess or the CLI flags; a test drives it with a fake runner.
14. **The persona is data, not code.** There is no branch on the agent's name in
    the behavior.
15. **`task.repository` is a name, not a path.** Paths are handled by
    `RepositoryRegistry`, machine-specific, outside the task.
16. **Artifacts live in the worktree under `.artifacts/<id>/`, versioned.** The
    agent writes them, the worker commits them. Attempt numbering is gapless
    thanks to reset-on-reattach.
17. **`AgentRunner`/`AgentCatalog`/`RepositoryRegistry` know neither dispatcher nor
    consumer.** Only the behavior / wiring touches them.

## Open questions

- **Retry of transient errors.** Phase 3 default: none, everything unfortunate →
  `failed/`, with `fallback_model` as a partial hedge against model overload.
  Introduce a transient/permanent distinction and backoff, or wait for the
  multi-process phase?
- **A real smoke test.** Do we want an opt-in test with a live `claude` (behind an
  env flag), or does a fake runner + manual verification of the live run suffice?
- **Per-agent `permission_mode` / `setting_sources`.** Global
  (`bypassPermissions`, `project`), or a field in `AgentSpec`?
- **Persona via `--append-system-prompt` vs `--agents`+`--agent`.** Both
  deterministic; the first is simpler, the second also carries model/tools in a
  single definition.

## Done criteria

Phase 3 is done when:

1. A task with a repo name flows through a workflow where every step is served by a
   **real `claude -p` agent** of the given persona and model (verified by an
   opt-in smoke test or manually).
2. `RepositoryRegistry` translates the repo name into a path; the worktree is
   created at the derived path, and the task carries no absolute paths.
3. Every phase wrote an artifact into `.artifacts/<id>/` in the worktree; earlier
   steps read it as a file in cwd; the worker committed it with `summary`.
4. The back edge (`request_changes`) creates `development-02` / `review-02`
   alongside `-01`; all attempts are in the worktree and in git history.
5. `reviewer` can return `REQUEST_CHANGES`; `architect`/`planner` only `DONE`; a
   verdict outside `allowed_outcomes` ends up in `failed/`.
6. An agent crash / timeout / unreadable verdict → `failed/`, and the loop keeps
   running.
7. Killing the process midway and restarting leads to the task completing
   (reset-on-reattach, gapless attempt).
8. `ClaudeCliBehavior` is green with `FakeAgentRunner`, without a real `claude`.
9. Architecture tests: dispatcher/consumer don't import the new ports or drivers;
   `api/` touches only `ArtifactView`; the behavior doesn't branch on the outcome.
