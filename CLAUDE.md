# harness_v2 — orientation for Claude

An orchestration harness for multiple agents. The unit of work is a **task**, which
travels between queues according to a **workflow** — a small state machine with
explicit edges.

> **Project language is English — always.** All code, comments, docstrings,
> string literals, tests, docs, commit messages and PRs are written in English.
> Do not introduce any other language, even when the prompt or an issue is in one.

Phase 1 spec: `docs/superpowers/specs/2026-07-19-orchestration-phase1-design.md`
Phase 1 plan: `docs/superpowers/plans/2026-07-19-orchestration-phase1.md`
Phase 2 spec: `docs/superpowers/specs/2026-07-20-orchestration-phase2-design.md`
Phase 2 plan: `docs/superpowers/plans/2026-07-20-orchestration-phase2.md`
Phase 3 spec: `docs/superpowers/specs/2026-07-20-orchestration-phase3-design.md`
Phase 3 plan: `docs/superpowers/plans/2026-07-20-orchestration-phase3.md`

The project is built **phase by phase**. Phase 1 is a POC of the orchestration loop.
Phase 2 adds **worktrees, artifacts and landing**: each phase works in a worktree,
writes artifacts, commits per phase and opens a PR at the end. Phase 3 swaps
`DummyBehavior` for a **real agent via `claude -p`**: a step's persona (`AgentSpec`)
is run by the shared `AgentRunner`, `task.repository` is a name (the path is
resolved by `RepositoryRegistry`), and artifacts move **into the worktree** under
`.artifacts/<id>/`, versioned. Real GitHub is still just a driver (forge) that gets
swapped out later.

## Invariants — do not break

1. **You may swap a driver, never its surroundings.** Every moving part sits behind
   a port in `ports/`. Neither `dispatcher.py` nor `consumer.py` may import anything
   from `drivers/` — wiring belongs exclusively in `app.py`. This is guarded by
   `tests/test_architecture.py`.
2. **Decision-making has three separate roles.** `ConsumerBehavior` says *what
   happened*, the dispatcher *where it goes next*, the consumer decides nothing.
   `consumer.py` must not contain a branch that depends on the outcome value; a test
   checks this by reading the source.
3. **The dispatcher changes status — with one exception.** The decision *where the
   task goes next* (step, `end`) belongs exclusively to the dispatcher. The one
   exception: when the consumer itself cannot deliver the task (the behavior raises
   an exception, or returns an invalid outcome), it writes the terminal status
   `failed` itself — symmetrically to how `Dispatcher._fail` does the same when
   routing fails. `lastOutcome` is written exclusively by the consumer.
4. **The router is a pure function.** `route()` must not touch I/O, time or state.
5. **Neither `api/` nor `projection.py` imports `drivers/`.** The UI must not know what the harness runs on.
6. **In `Harness.run()`, `recover()` comes before `hydrate()`.** The other way round loses tasks from `.processing/`.
7. **A task-movement event carries both `task` and `queue`.** Without it the projection won't see tasks created after startup.
8. **`repository`/`worktree` is read only by the behavior.** The router and dispatcher still decide solely on `(status, lastOutcome)`.
9. **The commit is done by the behavior driver, not the consumer and not the LLM.** The consumer knows no git.
10. **Artifacts are attempt-indexed** (`<task>/<step>/<attempt>/`). A step re-run never overwrites the previous attempt — otherwise the `request_changes` loop would vanish from the audit trail.
11. **`Workspace`/`Forge`/`ArtifactStore` are unknown to the dispatcher and consumer.** Only the behavior touches them; wiring in `app.py`. `api/` touches only `ArtifactView`. Guarded by `test_architecture.py`.
12. **Landing is a step, not magic.** It lands the artifacts into the worktree and opens a PR; it may fail into `failed/`. `end` stays a clean terminal. Phase 3: the artifacts are already in the worktree, landing doesn't copy them — it just opens a PR.
13. **The agent lives behind `AgentRunner`.** `ClaudeCliBehavior` knows nothing of subprocesses or CLI flags; a test drives it with `FakeAgentRunner`, the way phase 1 drove time with `FakeClock`.
14. **The persona is data, not code.** `behaviors/agent.py` has no branch on the agent's name — the difference between personas is the content of the `AgentSpec` supplied at construction.
15. **`task.repository` is a name, not a path.** Paths are resolved by `RepositoryRegistry` — machine-specific config (`repos.json`), outside the task. The worktree path is derived by the harness (`<worktrees_root>/<task_id>`).
16. **Artifacts live in the worktree under `.artifacts/<id>/`, versioned.** The agent writes them, the worker commits them. Attempt numbering (`<step>-NN`) is gapless across reset-on-reattach.
17. **`AgentRunner`/`AgentCatalog`/`RepositoryRegistry` are unknown to the dispatcher and consumer.** Only the behavior / wiring touches them. Guarded by `test_architecture.py`.
18. **The outside world of tasks is a single port `TaskSource` (`poll`/`report_progress`/`finish`).** GitHub is a driver; how the state is rendered (a label) is known only to the driver.
19. **A task's origin lives in `task.data.source`** (`{kind, repo, issue, url}`). The outward projection reads it from there, not from side state. Neither router nor dispatcher reads `data.source`.
20. **`TaskSource` is touched only by `SourcePoller` (core) and `SourceReflectorSink` (driver)**, wired in `app.py`. `dispatcher.py`/`consumer.py` don't import the port — guarded by `test_architecture.py`.
21. **The outward projection is idempotent and doesn't block decision-making.** `report_progress` twice is a no-op; a source failure is isolated by `CompositeEventSink` (and `SourcePoller.tick` catches the exception from `poll()`).
22. **`todo` is the board's name for the inbox's fresh tasks** (`status is None`) — the first column. It is a view concern only: the router and dispatcher never see a `todo` queue, and auto-flow is unchanged (a fresh task passes through `todo` into `start`).
23. **Operator control is a write-side port `TaskControl`, mirroring the read-side `BoardView`.** `restart` is a reset, not a routing decision: it clears `status`/`lastOutcome` and re-inboxes a `failed` task, then the dispatcher decides where next (invariant #3 holds). `TaskControl` is touched only by `TaskControlService` (core), `api/` and wiring — `dispatcher.py`/`consumer.py` don't import it; guarded by `test_architecture.py`.

## Working here

```sh
.venv/bin/pytest -q
```

Python is **3.11** (`/Users/rem/.local/bin/python3.11`, a uv-managed CPython).
Development is a clone + `venv` + `pip install -e ".[dev]"`; **shipping** is
`uv tool install git+https://github.com/onpaj/harness_v2.git`, updated with
`harness update` (a thin wrapper over `uv tool upgrade harness`). `install.sh`
was retired in favour of that — don't reintroduce a second install path.

Unit and integration tests run on in-memory drivers and `FakeClock` — no disk
and no real waiting. Never write a test into them that sleeps in real time.

The deliberate exceptions are `tests/test_smoke.py` (real FS) and
`tests/test_smoke_git.py` (phase 3: real git worktree + artifacts in the worktree +
fake forge, the step's work driven by `ClaudeCliBehavior` with a local test-runner,
not real `claude`). Both crawl along on a real `asyncio.sleep(0.01)` — it's the only
coverage of the real FS/git drivers live. Don't tidy them into an in-memory shape;
that coverage would vanish.

`tests/test_smoke_claude.py` is an **opt-in** smoke with real `claude -p` — it runs
only with `HARNESS_SMOKE_CLAUDE=1`, otherwise it's skipped and `pytest -q` won't run it.
It covers the thin subprocess shell of `ClaudeCliRunner` that the fake runners bypass.

## Git conventions

**Commit straight into `main`.** In this phase that's the intended approach — don't
create a branch, don't open a PR, and don't ask. This applies to the harness's own repo.

**Commit messages are conventional commits — this is now load-bearing.**
`.github/workflows/release.yml` runs python-semantic-release on every push to
`main`: `feat:` bumps the minor, `fix:`/`perf:` the patch, `BREAKING CHANGE:` the
major, and everything else (`docs:`, `chore:`, `test:`, `refactor:`, `ci:`) only
shows up in the notes. A sloppy subject line silently means no release. The
release job pushes back a `chore(release): X.Y.Z` commit carrying `[skip ci]`;
that commit has no feat/fix, so it cannot trigger a release of its own.

## Module map

Dependencies flow strictly downward, no cycles.

| Layer | Modules |
|---|---|
| Base | `models` (imports nothing from the package), `ids` |
| Logic | `router` (knows only `models`) |
| Base (package-free) | `models`, `ids`, `artifacts_layout` (the `.artifacts/<id>/<step>-NN` convention) |
| Ports | `ports/{queue,workflows,strategy,behavior,events,clock,workspace,artifacts,forge,board,agent,repos,source}` |
| Orchestration | `dispatcher`, `consumer`, `source_poller` — know only ports (and not `workspace`/`forge`/`artifacts`/`agent`/`repos`/`drivers`) |
| Behaviors | `behaviors/{landing,agent}` — touch ports, not drivers |
| Drivers | `drivers/{fs_queue,fs_workflows,fifo_strategy,dummy_behavior,stdout_events,system_clock,memory,fs_artifacts,git_workspace,fake_forge,claude_cli,fs_agents,fs_repos,worktree_artifacts,source_reflector,github_client,github_source,github_forge,launchd}` |
| Edges | `app` (wiring), `cli` |

- `projection.py` — in-memory read model of the board; hydration from queues + event stream
- `artifacts_layout.py` — the single source of truth for artifact placement in the worktree (`next_attempt`, `artifacts_dir`); read by both the behavior and `WorktreeArtifactView`
- `ports/board.py` — the `BoardView` port through which the UI looks
- `ports/artifacts.py` — `ArtifactStore` (writing, phase 2) and `ArtifactView` (reading for the UI); phase 3 reads via `WorktreeArtifactView`
- `ports/workspace.py` — `Workspace.attach(task) -> WorkspaceHandle` (worktree + commit)
- `ports/forge.py` — `Forge.open_pull_request(...)` (landing proposes a PR)
- `ports/agent.py` — `AgentRunner.run(...)`, `AgentCatalog.get(name)`, `AgentSpec` (persona as data)
- `ports/repos.py` — `RepositoryRegistry.resolve(name) -> Path` (repo name → path)
- `behaviors/agent.py` — `ClaudeCliBehavior`: attach worktree → allocate attempt → run the agent → the worker commits
- `ports/source.py` — the `TaskSource` port (`poll`/`report_progress`/`finish`) + `Progress`/`FinishResult`
- `source_poller.py` — `SourcePoller`: the core that fills the inbox from the source (knows only ports)
- `drivers/source_reflector.py` — `SourceReflectorSink(EventSink)`: event stream → projection into the source
- `drivers/github_client.py` — `GithubClient` (ABC), `Issue`, `FakeGithubClient`, `HttpGithubClient` (stdlib `urllib`)
- `drivers/github_source.py` — `GithubTaskSource`: issue → task, state → label
- `drivers/github_forge.py` — `GithubForge`: opens the real PR. Slug per task from
  the worktree's origin, base = the repo's default branch, `Closes #n` for an
  issue-born task, `ForgeError` on every failure path
- `drivers/launchd.py` — the background service on macOS: pure `wrapper_script`/
  `plist_bytes` builders (unit-tested) plus a thin `launchctl` shell; driven by
  `harness service install|uninstall|status`
- `api/` — FastAPI board; sees only `BoardView` and `ArtifactView`, never a driver or `ArtifactStore`

## What is responsible for what

- **`TaskQueue`** — the inbox, the step queues, `done/` and `failed/` are all
  instances of the same port. Terminal states are simply queues that nobody consumes.
- **`claim()`** is an atomic `rename` into `<queue>/.processing/`. A single operation
  handles the lease, idempotency and provenance after a crash.
- **`END = "end"`** is a reserved node. It is not a "state with no outgoing edges" —
  a typo would then quietly pass for success.
- **A task has two workspaces** (phase 2). The **worktree** (`repository`/`worktree`)
  holds the code, versioned by the task's git branch. The **artifact folder**
  (harness-owned, not versioned into landing, readable by the UI) holds the
  plan/design/review. Deliberately separate — the worktree stays clean, the UI reads
  without git, `git clean` won't delete the artifacts. See the phase 2 spec for details.
- **`BehaviorResult(outcome, summary)`** is the behavior's return value. `outcome` is
  routed by the dispatcher; `summary` is the commit message, the history line, the PR
  body and the board text.
- **A task is a transaction.** The work lives in an isolated worktree/folder; at the
  end, **landing** lands the artifacts and opens a PR. The harness never touches
  `main` — it only proposes. The merge strategy is a human's call.
- **The agent** (phase 3) is a persona as data: `AgentSpec` carries the prompt, model,
  tools and `allowed_outcomes`. `AgentCatalog` maps a step's name to a spec (default
  identity name==step), the shared `AgentRunner` runs it. The model is per queue, not
  per class — adding an agent = a new file in the catalog, not a new class. `reviewer`
  may return `done`+`request_changes`, the others only `done`.
- **`RepositoryRegistry`** translates a repo name into a path on this machine
  (`repos.json`, machine-specific, uncommitted). The task carries only the name; the
  worktree path (`<worktrees_root>/<task_id>`) is derived by the harness. Reattaching a
  dirty worktree resets it to HEAD (reset-on-reattach).
- **Artifacts in the worktree** (phase 3): the agent writes them to `.artifacts/<id>/`,
  **the worker commits them** together with the code (they ride in the git history and
  in the PR). Attempt numbering (`<step>-NN`) is gapless — an unfinished attempt is
  discarded by reset-on-reattach and the re-run allocates the same number again.
- **`harness init`** writes the default agents (`agents/<step>.json`, the persona
  instructs the verdict block + writing an artifact) and an empty `repos.json`.
  `harness run` injects `ClaudeCliRunner`, `Filesystem{AgentCatalog,RepositoryRegistry}`,
  `GitWorkspace` and `WorktreeArtifactView`.
- **`TaskSource`** (phase 4) is the outside world of work control behind a single port
  with three verbs: `poll()` brings new tasks (a second inbox producer alongside
  `submit`), `report_progress`/`finish` project state outward. The GitHub adapter does
  it with labels, the filesystem one by moving JSON — a swap of driver, not
  surroundings. The task's origin travels with it in `task.data.source`; the outward
  projection is routed by `SourceReflectorSink` per `kind`, a foreign task (a different
  `kind` / no `source`) is silently ignored by the adapter, so tasks from
  `harness submit` pass through without a single outward call. `poll()` claims by
  flipping the label (GitHub's twin of the atomic `rename` in `fs_queue.claim()`) —
  "at most once" ingestion across restarts. Within the process, though,
  `list_issues` reads with read-after-write lag (unlike `rename`), so
  `GithubTaskSource` keeps an in-process ledger of claimed numbers (`_claimed`)
  so a fast poll won't claim the same issue twice.

## Gotchas

- **Each queue has its own `.processing/`.** So after a crash there's no need to record
  anywhere where the task came from — recovery returns it to the queue it sits under.
- **Losing the race for `claim()` is not an error.** `os.replace` raises
  `FileNotFoundError`, the driver returns `None` and the loop takes the next task.
- **Broken JSON has no one to attribute history to.** The file is moved into `failed/`
  as-is, and only the event carries the reason.
- **`DummyBehavior` must return `done` deterministically.** `request_changes_once_at`
  returns `REQUEST_CHANGES` only on a given task's first pass through a given step;
  otherwise the loop would spin forever.
- **`build()` has default working drivers in-memory.** The substrate of the dummy
  behavior, like the dummy itself, is fake. `build` with a `catalog` switches the agent
  steps to `ClaudeCliBehavior`; the real run (`cli._run`) injects `GitWorkspace`,
  `ClaudeCliRunner`, `WorktreeArtifactView`, `Filesystem{AgentCatalog,Repository‑
  Registry}` and `GithubForge` — a swap of driver, not surroundings.
- **Landing is idempotent.** For an existing PR on a branch the forge returns the
  existing one (`GithubForge` matches on `head=owner:branch`). So a re-run after a
  crash won't open a second PR. The push is a plain `git push -u origin` — the task
  branch only ever moves forward, so a rejection is a real anomaly and must fail.
- **Landing needs a pushable remote.** `land` pushes the task branch before it
  proposes, so a registered repo with no `origin` cannot land — that is why the
  git e2e/smoke fixtures create a bare sibling repo and add it as `origin`.
- **A failed PR fails the task.** `GithubForge` raises `ForgeError` on a missing
  `GITHUB_TOKEN`, a non-GitHub origin or an API error, and the task lands in
  `failed/`. Deliberate: before this, `land` reported success while only writing
  to `prs.json`. Offline or in tests, use `--forge fake`.
- **`ArtifactStore.begin(task, step)` allocates the next attempt.** Writing into one
  slot belongs to one run; the second pass (the loop) gets a new subdirectory.
- **The service holds no secret.** launchd hands a process almost no environment, so
  `harness service install` generates a wrapper that resolves `GITHUB_TOKEN` at
  start-up — an explicit variable first, else `gh auth token` from the keyring. The
  plist itself never contains a token; a test asserts that. No token is not fatal:
  GitHub ingestion goes quiet and `harness submit` still works.
- **The LaunchAgent points at uv's shim** (`~/.local/bin/harness`), not at a
  virtualenv. `uv tool upgrade` rebuilds the tool environment but keeps the shim
  path, so an update never invalidates an installed service — it just needs a
  restart to be picked up. `service_entry_point()` falls back to
  `sys.prefix/bin/harness` for a from-source venv.
- **`sys.executable` is useless for locating the entry point.** Resolving it
  follows the venv's python symlink out to the base interpreter — with a
  uv-managed CPython that is `~/.local/share/uv/python/...`, where no `harness`
  script exists. Use `sys.prefix`.
- **The service `PATH` is explicit.** launchd's default `PATH` has no `git`, `gh` or
  `claude`, so the wrapper exports one built by `cli.service_path_entries` (venv bin
  first, then `~/.npm-global/bin`, `~/.local/bin`, `/usr/local/bin`, …). A "claude not
  found" failure deep in a run is usually this.

## Operator

Ondrej Pajgrt — "Ondrej" / "Rem". GitHub `onpaj`. Europe/Prague. The machine context
(NanoClaw, podman) is in `~/CLAUDE.md`.

A previous attempt at this idea lives in this repo's history at commit `7bc0e6e`;
`main` was emptied by commit `b7cab63` so it could be built phase by phase from scratch.
