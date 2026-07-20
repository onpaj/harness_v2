# Phase 3 — real agent via `claude -p`: Implementation Plan

> **For agentic workers:** implement task by task. For each task: write a failing
> test → run it (red) → implement → run it (green) → commit. Steps have a
> checkbox (`- [ ]`).

**Goal:** Swap `DummyBehavior` for `ClaudeCliBehavior`, which delegates a step's
work to an agent launched via `claude -p`. Agent-per-queue as data (`AgentSpec` +
`AgentCatalog`), a shared `AgentRunner`, a `RepositoryRegistry` (repo name →
path), artifacts versioned in the worktree under `.artifacts/<id>/`.

**Spec:** `docs/superpowers/specs/2026-07-20-orchestration-phase3-design.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. The `claude` CLI is
invoked via `subprocess`/`asyncio.create_subprocess_exec` — no new production
dependency. The real `claude` does NOT run in the test suite — `FakeAgentRunner`
drives it.

## Global Constraints

- **The decision-making roles from phases 1–2 still hold.** The consumer does not
  branch on the outcome value. The dispatcher changes status. The consumer writes
  `lastOutcome`.
- **The driver is swapped, not its surroundings.** `ClaudeCliBehavior` knows
  nothing about the subprocess or CLI flags — it knows only `AgentRunner`. The
  dispatcher/consumer do not import the new ports.
- **The behavior driver commits, not the consumer, not the agent.** The agent only
  writes the artifacts and code; the worker runs `git add`/`commit`.
- **Persona is data.** `ClaudeCliBehavior` has no branch keyed on the agent's name.
- **`task.repository` is a name, not a path.** `RepositoryRegistry` resolves paths.
- **Tests never touch real time or the real `claude`.** Real-FS/git tests may use
  `tmp_path` (as in phase 2). `FakeAgentRunner` is pure Python.
- Time is ISO 8601 UTC with a `Z` suffix.
- Development on `claude/harness-part-three-brainstorm-5w3lwu` (off `main` after
  phase 2).

---

### Task 1: Ports `AgentRunner` / `AgentCatalog` + `AgentSpec`

Foundation. No dependency on the other tasks.

**Files:** `src/harness/ports/agent.py`, `src/harness/drivers/memory.py`,
`tests/test_agent_ports.py`.

**Interfaces:**
- `AgentSpec(name, prompt, model=None, fallback_model=None,
  allowed_tools=(), allowed_outcomes=(Outcome.DONE,))` — frozen dataclass.
  `allowed_outcomes: tuple[Outcome, ...]`.
- `AgentRun(outcome: Outcome, summary: str, raw: str = "")` — frozen.
- `AgentRunner(ABC)`: `async run(*, prompt, spec, cwd, timeout) -> AgentRun`.
- `AgentCatalog(ABC)`: `get(name) -> AgentSpec`; `AgentNotFound(Exception)`.
- `MemoryAgentCatalog(dict[str, AgentSpec])` in `memory.py`: `get` returns a spec
  or raises `AgentNotFound`.
- `FakeAgentRunner` in `memory.py`: constructed with a script
  `runs: dict[str, AgentRun]` or a default `AgentRun`; `run` records the call in
  `self.calls` and returns the scripted `AgentRun`. Optionally `writes:
  dict[str, str]` (relpath→content), which `run` writes into `cwd` — simulating an
  agent producing artifacts/code. No subprocess.

- [ ] **Step 1:** Tests — `AgentSpec` holds its fields and defaults;
  `MemoryAgentCatalog` roundtrip + `AgentNotFound`; `FakeAgentRunner` returns the
  scripted run, records the call, and when it has `writes`, writes the files into
  `cwd` (`tmp_path`).
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: AgentRunner/AgentCatalog ports + AgentSpec + fakes`.

---

### Task 2: Port `RepositoryRegistry`

**Files:** `src/harness/ports/repos.py`, `src/harness/drivers/memory.py`,
`src/harness/drivers/fs_repos.py`, `tests/test_repos.py`.

**Interfaces:**
- `RepositoryRegistry(ABC)`: `resolve(name) -> Path`;
  `RepositoryNotFound(Exception)`.
- `MemoryRepositoryRegistry(dict[str, Path])` in `memory.py`.
- `FilesystemRepositoryRegistry(config: Path)` in `fs_repos.py`: reads JSON
  `{"<name>": "<path>"}`; `resolve` returns a `Path` or `RepositoryNotFound`.
  Broken/missing config → `RepositoryNotFound` with a clear message.

- [ ] **Step 1:** Tests — memory resolve + not found; fs reads JSON (`tmp_path`),
  resolve returns the path, unknown name → `RepositoryNotFound`, broken JSON → the
  same.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: port RepositoryRegistry + fs and in-memory driver`.

---

### Task 3: `ClaudeCliRunner` — real driver around `claude -p`

Pure functions (build args, parse verdict) are testable without a subprocess; the
thin subprocess shell is covered by the opt-in smoke test (Task 8), not by
`pytest -q`.

**Files:** `src/harness/drivers/claude_cli.py`, `tests/test_claude_cli.py`.

**Interfaces:**
- `build_argv(*, prompt, spec, output_format="json") -> list[str]` — pure
  function. Assembles `["claude", "-p", prompt, "--output-format", "json",
  "--permission-mode", "bypassPermissions", "--setting-sources", "project"]` and
  appends `--append-system-prompt <spec.prompt>`, `--model <spec.model>` (when not
  None), `--fallback-model …` (when not None), `--allowedTools <…>` (when
  non-empty).
- `parse_verdict(stdout, *, allowed) -> AgentRun` — pure function. From the
  `claude -p` JSON envelope it extracts the final text, and from that
  `{outcome, summary}`; an `outcome` outside `allowed` → `VerdictError`; a
  missing/unreadable JSON → `VerdictError`. `raw` carries stdout.
- `ClaudeCliRunner(AgentRunner)`: `run` assembles `argv`, launches
  `asyncio.create_subprocess_exec` in `cwd` with `timeout` (kill + `VerdictError`
  on expiry), raises `AgentError` on a nonzero exit, otherwise returns
  `parse_verdict(stdout, allowed=spec.allowed_outcomes)`.
- Verdict convention: the agent in its persona should end with a block
  ` ```json {"outcome": "...", "summary": "..."} ``` `; `parse_verdict` takes the
  last such block. (The persona in the default catalog instructs this — Task 6.)

- [ ] **Step 1:** Tests (pure functions, no subprocess) — `build_argv` contains the
  correct flags for a spec with/without model, tools, fallback; `parse_verdict`
  reads a valid verdict, maps `done`/`request_changes` to `Outcome`, outside
  `allowed` → `VerdictError`, broken JSON → `VerdictError`.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: ClaudeCliRunner — build_argv, parse_verdict, subprocess`.

---

### Task 4: Attempt helper — artifacts in the worktree

The write side of phase 2's `ArtifactStore` shrinks down to computing the attempt
path in the worktree. The read-side `ArtifactView` gets a driver that reads
`.artifacts/` in the worktree.

**Files:** `src/harness/drivers/worktree_artifacts.py`,
`src/harness/ports/artifacts.py` (only if `ArtifactView` needs fine-tuning),
`tests/test_worktree_artifacts.py`.

**Interfaces:**
- `next_attempt(worktree: Path, task_id: str, step: str) -> tuple[int, str]` —
  counts the existing `.artifacts/<task_id>/<step>-*.md`, returns `(NN, relpath)`
  where `relpath = ".artifacts/<task_id>/<step>-<NN:02d>.md"`. Task-level artifacts
  (without a suffix) are not handled by the helper — the agent writes them directly
  per its persona.
- `WorktreeArtifactView(worktrees_root: Path)` — an `ArtifactView` reading from
  `<worktrees_root>/<task_id>/.artifacts/<task_id>/`: `list` returns `ArtifactRef`
  (step, attempt, name) parsed from the file names; `read` returns the content.
  Task-level files (without `-NN`) → `attempt = 0` or a dedicated flag (to be
  decided: `attempt = -1` marks task-level). `read` of a nonexistent file → `None`.

- [ ] **Step 1:** Tests (`tmp_path`) — `next_attempt` grows 01→02 based on the
  existing files; `WorktreeArtifactView.list/read` reads the flat files,
  distinguishes task-level from step-attempt, nonexistent → `None`.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: attempt helper and WorktreeArtifactView`.

---

### Task 5: `ClaudeCliBehavior`

**Files:** `src/harness/behaviors/agent.py`,
`src/harness/behaviors/__init__.py`, `tests/test_agent_behavior.py`.

**Interfaces:**
- `ClaudeCliBehavior(*, clock, workspace, runner: AgentRunner, spec: AgentSpec,
  timeout: float = 600.0)`.
- `run(task)`:
  1. `handle = workspace.attach(task)` (reset-on-reattach is handled by
     `GitWorkspace`, Task 6).
  2. `attempt, relpath = next_attempt(handle.path, task.id, task.status)`.
  3. `prompt = compose_prompt(task, step=task.status, artifact_relpath=relpath)`
     — explains the step's task to the agent, where to write the artifact, to read
     the previous `.artifacts/<id>/`, and to end with a verdict block.
  4. `run = await runner.run(prompt=prompt, spec=spec, cwd=handle.path,
     timeout=timeout)`.
  5. `handle.commit(run.summary)` — the worker commits.
  6. `return BehaviorResult(run.outcome, run.summary)`.
- An exception from the runner (`AgentError`/`VerdictError`/timeout) bubbles up —
  the consumer handles it via `_fail`. `ClaudeCliBehavior` does not branch on the
  outcome value.

- [ ] **Step 1:** Tests (`tmp_path` + `GitWorkspace` or a real-FS handle +
  `FakeAgentRunner`) — after `run`, the agent was called with the correct cwd; the
  prompt carries the attempt relpath; when `FakeAgentRunner.writes` contains an
  artifact, after `run` there exists `.artifacts/<id>/<step>-01.md` and it is
  committed; the return is a `BehaviorResult` with a summary; a `FakeAgentRunner`
  with a verdict outside `allowed` (set up so the runner raises) → the exception
  bubbles up.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: ClaudeCliBehavior — agent writes, worker commits`.

---

### Task 6: `GitWorkspace` via registry + reset; wiring; default agents

**Files:** `src/harness/drivers/git_workspace.py`, `src/harness/app.py`,
`src/harness/cli.py`, `tests/test_git_workspace.py`, `tests/test_app.py`.

**Interfaces:**
- `GitWorkspace(registry: RepositoryRegistry, worktrees_root: Path)`:
  - `attach(task)`: `base = registry.resolve(task.repository)`;
    `worktree = worktrees_root / task.id`; if it does not exist,
    `git -C base worktree add worktree -b harness/<task_id>`; if it does exist,
    **reset-on-reattach**: `git -C worktree reset --hard HEAD` + `git -C worktree
    clean -fd` (without `-x`). Handle as before.
- `build(...)`: adds `runner`, `catalog: AgentCatalog`, `registry:
  RepositoryRegistry`, `worktrees_root`, `agent_timeout`. Default in-memory /
  fake for `build` without configuration; `harness run` injects `ClaudeCliRunner`,
  `FilesystemAgentCatalog`, `FilesystemRepositoryRegistry`, `GitWorkspace`,
  `WorktreeArtifactView`.
- Per-step behavior: `behavior_for(step)` → `LandingBehavior` for `landing_step`,
  otherwise `ClaudeCliBehavior(spec=catalog.get(step), …)`. If the spec is missing
  → `AgentNotFound` (fail fast at build time).
- **Landing** loses its copy step — the artifacts are already in the worktree; it
  just opens the PR. Adjust `LandingBehavior` and its tests.
- `HarnessLayout` += `worktrees`, `agents`, `repos` (config). `harness init`
  writes the default agents `agents/<step>.json` (the persona instructs the verdict
  block + writing the artifact into `.artifacts/`; `reviewer` has `allowed_outcomes`
  done+request_changes, the others only done) and an empty `repos.json` with a hint.
- `api/` gets `WorktreeArtifactView` instead of the fs artifact store.

- [ ] **Step 1:** Tests — `GitWorkspace.attach` resolves the name via the registry,
  creates the worktree at the derived path; a second `attach` of a dirty worktree
  resets it (a file added outside a commit after `attach` disappears). `build`
  assigns landing vs. agent behavior; a missing agent → error. Landing without
  copying.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: GitWorkspace via registry+reset, wiring, default agents`.

---

### Task 7: E2E on the fake runner

**Files:** `tests/test_phase3_e2e.py`.

- [ ] **Step 1:** E2E on in-memory drivers (`MemoryRepositoryRegistry`,
  `MemoryAgentCatalog`, `FakeAgentRunner` scripted per step, `GitWorkspace` over a
  tmp repo or `MemoryWorkspace` where it suffices, `FakeClock`). A task with a repo
  name flows through `plan→…→review→land→end`; the `reviewer` fake returns
  `request_changes` once. Verify:
  - the task ends in `done`;
  - `FakeAgentRunner` was called per step with the correct cwd and spec;
  - the artifacts (fake `writes`) have `development-02` and `review-02` alongside
    `-01`;
  - the history carries `summary` on the consumer rows;
  - the forge records a single PR.
- [ ] **Step 2:** Red → **Step 3:** wiring fine-tuning → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: phase 3 e2e on the fake agent runner`.

---

### Task 8: Architecture, opt-in smoke, documentation

**Files:** `tests/test_architecture.py`, `tests/test_smoke_git.py` (edit),
`tests/test_smoke_claude.py` (new, opt-in), `CLAUDE.md`.

- [ ] **Step 1:** Architecture tests: `dispatcher.py`/`consumer.py` do not import
  `ports/agent`, `ports/repos`, or the new drivers; `api/` touches only
  `ArtifactView`; the behavior does not branch on outcome (extend the existing
  test).
- [ ] **Step 2:** `test_smoke_git.py` — move the artifacts into `.artifacts/` in the
  worktree (the phase 2 dummy behavior stays for this smoke, or is replaced with
  `ClaudeCliBehavior` + a `FakeAgentRunner` writing files). Verify the artifacts are
  in the worktree, not in a separate folder.
- [ ] **Step 3:** `test_smoke_claude.py` — **opt-in**, `@pytest.mark.skipif(not
  os.environ.get("HARNESS_SMOKE_CLAUDE"))`. Runs the real `claude -p` on a trivial
  task in a tmp repo, verifies the verdict + commit. Never runs in `pytest -q`
  without the env flag.
- [ ] **Step 4:** `CLAUDE.md` — module map for the new ports/drivers, invariants
  13–17, a "What is responsible for what" section on the agent, the registry, and
  artifacts in the worktree. `.venv/bin/pytest -q` green.
- [ ] **Step 5:** Commit `docs: CLAUDE.md for phase 3; opt-in claude smoke`.

---

## Order and dependencies

```
T1 (Agent ports) ─┬─> T3 (ClaudeCliRunner) ─┐
T2 (RepoRegistry)─┤                          ├─> T5 (ClaudeCliBehavior) ─> T6 (wiring) ─> T7 (e2e) ─> T8 (arch+smoke+docs)
T4 (attempt/View)─┘                          │
                                             └─(T4 → T5, T6)
```

T1–T2 are independent (shared `memory.py` — write them serially so they aren't
edited at once). T3–T4 stand on T1. T5 ties together runner+attempt+workspace. T6
wires up the registry, per-step agents, and landing. T7 e2e. T8 closes it out.
