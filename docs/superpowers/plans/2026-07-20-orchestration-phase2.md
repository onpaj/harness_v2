# Phase 2 — artifacts, worktree, and landing: Implementation Plan

> **For agentic workers:** implement task by task. Each task: write a failing
> test → run it (red) → implement → run it (green) → commit. Steps have a
> checkbox (`- [ ]`).

**Goal:** A task works in a worktree named in the task, produces artifacts into a
harness folder, commits per phase with a meaningful message, and at the end opens
a PR — all behind ports that can be swapped for a driver.

**Spec:** `docs/superpowers/specs/2026-07-20-orchestration-phase2-design.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. The runtime adds only
what phase 1 already has (FastAPI/uvicorn/jinja2 for the board). The git driver
calls the system `git` via `subprocess` — no new production dependency.

## Global Constraints

- **The decision-making roles from phase 1 still hold.** The consumer does not
  branch on the outcome value. The dispatcher changes status. `lastOutcome` is
  written by the consumer.
- **`repository`/`worktree` is read only by the behavior.** Router/dispatcher
  still only `(status, lastOutcome)`.
- **The commit is done by the behavior driver, not the consumer, not the LLM.**
- **Dispatcher/consumer do not import `Workspace`/`Forge`/`ArtifactStore`.** Wiring
  lives in `app.py`. `api/` touches only `ArtifactView`. Enforced by `test_architecture.py`.
- **Artifacts are attempt-indexed** — a re-run of a step does not overwrite the
  previous attempt.
- **Tests do not touch real time.** Real-driver tests (git, fs) may use
  `tmp_path` — just like `test_fs_queue.py` in phase 1.
- Time is ISO 8601 UTC with a `Z` suffix.
- Development on branch `claude/harness-phase-two-brainstorm-6u384o` (not directly
  main — for this phase the session's instructions apply, not the convention from
  `CLAUDE.md`).

---

### Task 1: `BehaviorResult` and `summary` in history

The behavior stops returning a bare `Outcome`. A cross-cutting change, after which
the whole suite must be green again.

**Files:** `src/harness/models.py`, `src/harness/ports/behavior.py`,
`src/harness/consumer.py`, `src/harness/drivers/memory.py`,
`src/harness/drivers/dummy_behavior.py`, affected tests.

**Interfaces:**
- `BehaviorResult(outcome: Outcome, summary: str = "")` — frozen dataclass in `models.py`.
- `HistoryEntry` gains `summary: str | None = None`; `to_dict` adds it only when
  it is not `None`; `from_dict` reads `raw.get("summary")`.
- `ConsumerBehavior.run(task) -> BehaviorResult`.
- Consumer: `result = await behavior.run(task)`; validation
  `isinstance(result, BehaviorResult) and isinstance(result.outcome, Outcome)`;
  otherwise `_fail(...)`. `_deliver` writes `last_outcome=result.outcome.value` and
  `HistoryEntry(..., outcome=result.outcome.value, summary=result.summary or None)`.
  The `consumed` event gets `summary=result.summary`.
- `ScriptedBehavior` and `DummyBehavior` return `BehaviorResult` (Scripted:
  `BehaviorResult(outcome, summary=f"{step}")` is enough; Dummy for now
  `BehaviorResult(Outcome.DONE, "done")`).

- [ ] **Step 1:** Tests — `test_models.py`: `BehaviorResult` holds the fields;
  `HistoryEntry` with `summary` round-trips and without summary omits the key.
  `test_consumer.py`: after a tick the `inbox` carries a history entry with
  `summary`; an invalid return (not a `BehaviorResult`) → `failed/`. Adjust the
  existing consumer tests to the new return type.
- [ ] **Step 2:** Red.
- [ ] **Step 3:** Implement across the files. The consumer may write summary — still
  no branch on the *value* of the outcome (the invariant test verifies this).
- [ ] **Step 4:** `.venv/bin/pytest -q` — whole suite green.
- [ ] **Step 5:** Commit `feat: behavior returns BehaviorResult (outcome + summary)`.

---

### Task 2: Port `ArtifactStore` / `ArtifactView` + `MemoryArtifactStore`

**Files:** `src/harness/ports/artifacts.py`, `src/harness/drivers/memory.py`,
`tests/test_artifacts_memory.py`.

**Interfaces:**
- `ArtifactRef(step: str, attempt: int, name: str)` — frozen; `to_dict()`.
- `ArtifactView(ABC)`: `list(task_id) -> tuple[ArtifactRef, ...]`,
  `read(task_id, step, attempt, name) -> str | None`.
- `ArtifactSlot(ABC)`: `attempt: int`, `put(name: str, content: str) -> None`.
- `ArtifactStore(ArtifactView)`: `begin(task_id, step) -> ArtifactSlot` — allocates
  the **next** attempt (0,1,2,…) for the pair `(task_id, step)`.
- `MemoryArtifactStore` implements everything over a dict.

- [ ] **Step 1:** Tests — `begin` twice for the same `(task, step)` gives attempt 0
  and 1; `put`+`read` round-trip; `list` returns all refs across steps/attempts;
  `read` of a nonexistent one → `None`.
- [ ] **Step 2:** Red.
- [ ] **Step 3:** Implement the port + `MemoryArtifactStore`.
- [ ] **Step 4:** Green.
- [ ] **Step 5:** Commit `feat: port ArtifactStore/ArtifactView + in-memory driver`.

---

### Task 3: Port `Workspace` + `MemoryWorkspace`

**Files:** `src/harness/ports/workspace.py`, `src/harness/drivers/memory.py`,
`tests/test_workspace_memory.py`.

**Interfaces:**
- `WorkspaceHandle(ABC)`: `path` (str/Path), `branch: str`,
  `commit(message: str) -> str | None`.
- `Workspace(ABC)`: `attach(task: Task) -> WorkspaceHandle`.
- `MemoryWorkspace`: `attach` returns a handle with `branch=f"harness/{task.id}"` and
  a fictitious `path`; `commit` records the message into `handle.commits: list[str]` and
  returns a fictitious sha `f"sha{len(commits)}"`; "nothing to commit" need not be
  simulated (that's what the git driver test is for). `MemoryWorkspace.handles` holds
  the issued handles for assertions in tests.

- [ ] **Step 1:** Tests — `attach` gives a branch derived from task.id; a repeat
  `attach` of the same task returns a handle with the same branch (reuse); `commit`
  records the message and returns a sha.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: port Workspace + in-memory driver`.

---

### Task 4: Port `Forge` + `MemoryForge`

**Files:** `src/harness/ports/forge.py`, `src/harness/drivers/memory.py`,
`tests/test_forge_memory.py`.

**Interfaces:**
- `PullRequest(number: int, url: str, branch: str, title: str)` — frozen.
- `Forge(ABC)`: `open_pull_request(task, *, branch, title, body) -> PullRequest`.
- `MemoryForge`: records the PR into `self.opened: list[dict]`; **idempotence** —
  a second call for the same `branch` returns the existing PR (same number).

- [ ] **Step 1:** Tests — opening a PR returns number/url/branch/title and records it;
  a second call for the same branch does not create a new one, returns the same one.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: port Forge + in-memory driver with PR idempotence`.

---

### Task 5: DummyBehavior phase 2

**Files:** `src/harness/drivers/dummy_behavior.py`, `tests/test_dummy_behavior.py`.

**Interfaces:**
- `DummyBehavior(*, clock, workspace: Workspace, artifacts: ArtifactStore,
  delay=5.0, request_changes_once_at=None)`.
- `run(task)`:
  1. `handle = workspace.attach(task)`
  2. `slot = artifacts.begin(task.id, task.status)`
  3. `slot.put(f"{task.status}.md", f"# {task.status}\n\n{summary}\n")`
  4. `handle.commit(f"[{task.status}] {summary}")` (the dummy may edit nothing in the
     worktree; on the mem driver commit just records the message — on the git driver
     "nothing to commit" is allowed and returns `None`).
  5. `return BehaviorResult(outcome, summary)`.
- `summary` is deterministic, e.g. `f"{task.status}: done"` and for
  request_changes `f"{task.status}: changes requested"`.
- The request_changes-once logic from phase 1 stays.

- [ ] **Step 1:** Tests with `MemoryWorkspace`+`MemoryArtifactStore` — after `run`
  an artifact `<task>/<step>/0/<step>.md` exists; the workspace recorded a commit
  with a message starting with `[<step>]`; the return is a `BehaviorResult` with a
  summary; request_changes-once returns REQUEST_CHANGES only the first time.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: DummyBehavior writes an artifact and commits the work`.

---

### Task 6: LandingBehavior

**Files:** `src/harness/behaviors/__init__.py`,
`src/harness/behaviors/landing.py`, `tests/test_landing_behavior.py`.

**Interfaces:**
- `LandingBehavior(*, clock, workspace, artifacts: ArtifactView, forge: Forge,
  dest="docs/tasks")`.
- `run(task)`:
  1. `handle = workspace.attach(task)`
  2. For each `ArtifactRef` from `artifacts.list(task.id)` write the content to
     `handle.path / dest / task.id / step / attempt / name` (on the mem workspace it's
     enough to record the "landed" paths — see below) and `handle.commit("[land] task artifacts")`.
  3. `body = _compose_body(task.history)` — aggregation of `summary` from consumer entries.
  4. `pr = forge.open_pull_request(task, branch=handle.branch,
     title=_title(task), body=body)`
  5. `return BehaviorResult(Outcome.DONE, f"opened PR {pr.url}")`.
- `MemoryWorkspace.WorkspaceHandle` gets `staged: list[tuple[str,str]]` for
  asserting the landed files (path, content), so that landing can be tested without disk.

- [ ] **Step 1:** Tests — after `run` the forge records one PR for the task's branch;
  the PR body contains the summaries from history; the handle has the landed artifacts;
  the return carries the PR url. Idempotence: a second `run` does not create a second PR.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: LandingBehavior lands artifacts and opens a PR`.

---

### Task 7: Wiring, `land` step, and e2e

**Files:** `src/harness/app.py`, `src/harness/cli.py`,
`tests/test_app.py` / `tests/test_phase2_e2e.py`.

**Interfaces:**
- `build(...)` builds `ArtifactStore`, `Workspace`, `Forge` (phase 2 default:
  in-memory for `build` without a root does not exist — for an fs run it's git+fs+fake).
  Add the parameters `workspace`, `artifacts`, `forge`, `landing_step="land"`.
- Per-step behavior: the consumer of the `landing_step` step gets `LandingBehavior`,
  the others `DummyBehavior`. `build` assembles `behaviors: dict[str, ConsumerBehavior]`
  and passes each consumer its own.
- The default workflow in `cli.py` (`DEFAULT_DEFINITION`) gets `land` between `review`
  and `end`.
- Consumer constructor: add `behavior` per instance (it already takes it) — only
  the wiring passes different ones.

- [ ] **Step 1:** E2E test on in-memory drivers (Memory Workspace/Artifacts/
  Forge, FakeClock, ScriptedBehavior replaces Dummy where the loop needs to be
  steered — but for artifacts/commits use Dummy). The task flows through
  `plan→…→review→land→end`; one `request_changes` loop. Verify:
  - the task ends in `done`;
  - the artifacts have a second attempt at both `development` and `review`;
  - the workspace carries per-phase commits;
  - the forge records exactly one PR;
  - the history carries `summary` on the consumer entries.
- [ ] **Step 2:** Red → **Step 3:** wiring → **Step 4:** green (whole suite).
- [ ] **Step 5:** Commit `feat: phase 2 wiring, land step, and e2e flow`.

---

### Task 8: Real drivers — `FilesystemArtifactStore` and `GitWorkspace`

**Files:** `src/harness/drivers/fs_artifacts.py`,
`src/harness/drivers/git_workspace.py`, `src/harness/drivers/fake_forge.py`,
`tests/test_fs_artifacts.py`, `tests/test_git_workspace.py`.

**Interfaces:**
- `FilesystemArtifactStore(root: Path)`: attempt = number of existing subdirectories
  `<root>/<task>/<step>/`; `begin` creates `<root>/<task>/<step>/<attempt>/`;
  `put` writes a file; `list`/`read` read from disk.
- `GitWorkspace(repos_root: Path | None = None)`:
  - `attach(task)`: `worktree = Path(task.worktree)`, `repo = task.repository`.
    If the worktree does not exist, `git -C <repo> worktree add <worktree> -b
    harness/<task_id>` (base = the repo's current HEAD); otherwise reuse. The handle holds
    `path`, `branch`.
  - `commit(message)`: `git -C <path> add -A`; if `git status --porcelain` is
    empty → `None`; otherwise `git -C <path> commit -m message` and return the sha
    (`git rev-parse HEAD`). Set `GIT_AUTHOR_*`/`committer` env so the test doesn't fail
    on a missing identity.
- `FakeForge`: like `MemoryForge`, but for an fs run (may push the branch to
  a configured bare remote or just record to a file). In phase 2 a record into
  `<root>/prs.json` is enough.

- [ ] **Step 1:** Tests with `tmp_path`. `fs_artifacts`: attempt grows on disk;
  round-trip. `git_workspace`: create a tmp git repo with a single commit, `attach`
  creates a worktree on the `harness/<id>` branch; edit a file, `commit` returns a sha
  and `git log` on the branch shows it; `commit` without a change returns `None`.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: fs artifacts and git worktree driver`.

---

### Task 9: Board — a view of artifacts

**Files:** `src/harness/ports/board.py` or the new `ArtifactView` already from Task 2;
`src/harness/api/routes.py`, `src/harness/api/app.py`, template, `tests/test_api_*`.

**Interfaces:**
- `create_app(view=..., artifacts: ArtifactView, clock=...)`.
- Route `GET /tasks/{id}/artifacts` → JSON `[{step, attempt, name}]`.
- Content route `GET /tasks/{id}/artifacts/{step}/{attempt}/{name}` → text.
- The task detail in HTML shows the list of artifacts (links to the content).
- `api/` imports **only** `ArtifactView`, not a driver — `test_architecture.py`.

- [ ] **Step 1:** Tests — the JSON list returns the task's artifacts; the content returns text;
  a nonexistent one → 404. Architecture: `api/` does not touch a driver.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: board shows a task's artifacts`.

---

### Task 10: Architecture, smoke, and documentation

**Files:** `tests/test_architecture.py`, `tests/test_smoke.py` (or
`test_smoke_git.py`), `CLAUDE.md`.

- [ ] **Step 1:** Architecture tests: `dispatcher.py`/`consumer.py`
  do not import `ports/workspace`, `ports/forge`, `ports/artifacts` nor drivers;
  `api/` imports only `ArtifactView`. The consumer still does not branch on outcome.
- [ ] **Step 2:** Smoke on real git: init a repo in `tmp_path`, submit a task with
  `repository`/`worktree`, run the loop with a shortened interval, wait, verify —
  the task in `done/`, the worktree has commits, `prs.json` has the PR, artifacts on disk.
  (Polls with a real short `asyncio.sleep` like the existing smoke; the **only** exception
  to "don't sleep in real time".)
- [ ] **Step 3:** Update `CLAUDE.md` — the module map for the new ports/drivers,
  invariants 8–12, the section "What is responsible for what" about the two surfaces and landing.
- [ ] **Step 4:** `.venv/bin/pytest -q` — everything green.
- [ ] **Step 5:** Commit `docs: CLAUDE.md for phase 2; smoke on real git`.

---

## Order and dependencies

```
T1 (BehaviorResult) ─┬─> T5 (DummyBehavior) ─┐
T2 (ArtifactStore) ──┤                        ├─> T7 (wiring+e2e) ─> T9 (board) ─> T10 (smoke+docs)
T3 (Workspace) ──────┼─> T6 (Landing) ───────┘         │
T4 (Forge) ──────────┘                                  └─> T8 (fs+git drivers)
```

T1–T4 are independent foundations (except for the shared `memory.py` — write them
serially, so they aren't edited at once). T5/T6 stand on the foundations. T7 joins them.
T8 (real drivers) and T9 (board) are independent. T10 closes it out.
