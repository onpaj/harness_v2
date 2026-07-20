# Phase 4 — task source connector (GitHub): Implementation Plan

> **For agentic workers:** implement task by task. Each task: write a failing
> test → run it (red) → implement → run it (green) → commit. Steps have a
> checkbox (`- [ ]`).

**Goal:** Tasks flow in from GitHub Issues behind the `TaskSource` port (`poll` /
`report_progress` / `finish`), and their status is projected back out by changing
labels. GitHub is one driver; filesystem/Jira are siblings. Everything sits
behind ports that can be swapped out.

**Spec:** `docs/superpowers/specs/2026-07-20-orchestration-phase4-design.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. **No new production
dependency** — the real GitHub client runs on stdlib `urllib.request`.

## Global Constraints

- **The decision-making roles from phases 1–2 still hold.** The consumer does not
  branch on outcome. The dispatcher changes status. The router is a pure function
  and **does not read** `data.source`.
- **`TaskSource` does not import `dispatcher`/`consumer`.** Only `SourcePoller`
  (core, ports only) and `SourceReflectorSink` (driver) touch it. Wiring lives in
  `app.py`. Enforced by `test_architecture.py`.
- **The outbound projection is idempotent and isolated.** `report_progress` twice
  = no-op; a GitHub failure must not stop the loop.
- **A task's origin lives in `task.data.source`**, not in some side state.
- **Tests touch neither real time nor the network.** In-memory + `FakeClock` +
  `FakeGithubClient`. The real `HttpGithubClient` is not tested in the unit suite
  (it's just supplied; an optional guarded integration test may `skip` without a
  token).
- Time is ISO 8601 UTC with a `Z` suffix.
- Development happens on branch `claude/harness-github-connector-p76esm` (session
  instruction, not a convention from `CLAUDE.md`).

---

### Task 1: Port `TaskSource` + `MemoryTaskSource`

**Files:** `src/harness/ports/source.py`, `src/harness/drivers/memory.py`,
`tests/test_source_memory.py`.

**Interfaces:**
- `Progress(step: str, summary: str = "")` — frozen.
- `FinishResult(ok: bool, pr_url: str | None = None, summary: str = "")` — frozen.
- `TaskSource(ABC)`: attribute `kind: str`; `poll() -> list[Task]`;
  `report_progress(task, progress) -> None`; `finish(task, result) -> None`.
- `MemoryTaskSource(TaskSource)`: `kind = "memory"`.
  - the constructor takes `clock`, `workflow="default"`, optionally `repository`,
    `worktree_root="/memory/worktrees"`.
  - `submit(title, body="") -> str` (test helper) adds an "issue" to the internal
    queue and returns its id.
  - `poll()`: take the issues not yet consumed, mark each as claimed, and assemble
    `Task(id=new_task_id(), workflow_template=workflow, created=clock.now(),
    repository=repository, worktree=f"{worktree_root}/{id}",
    data={"title","body","source":{"kind":"memory","issue":<issue-id>}})`.
  - `report_progress`/`finish`: write into `self.states: dict[str, list]`
    (issue-id → list of projections) — for assertions. `_mine(task)` guards on
    `kind`.

- [ ] **Step 1:** Tests — two `submit`+`poll` yield two tasks with
  `data.source.kind == "memory"`; a second `poll` with no new submit returns `[]`
  (the claim holds); `report_progress`/`finish` write the projection under the
  right issue-id; a task of a foreign `kind` (hand-assembled) is ignored by
  `report_progress` (guard).
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: port TaskSource + in-memory driver`.

---

### Task 2: `SourcePoller` (core)

**Files:** `src/harness/source_poller.py`, `tests/test_source_poller.py`.

**Interfaces:**
- `SourcePoller(*, source: TaskSource, inbox: TaskQueue, events: EventSink)`.
- `tick() -> bool`: `tasks = source.poll()`; for each `inbox.put(task)` and
  `events.emit("ingested", task_id=…, queue="tasks", task=task.to_dict())`;
  returns `bool(tasks)`. **Catch the exception from `poll()`** →
  `events.emit("source_error", source=source.kind, error=str(e))`, return `False`
  (the loop then sleeps and tries again).
- Imports **ports only** (`ports.source`, `ports.queue`, `ports.events`).

- [ ] **Step 1:** Tests with `MemoryTaskSource` + `MemoryTaskQueue` +
  `MemoryEventSink` — after `submit`+`tick` the task is in the inbox and an
  `ingested` event fired with `queue="tasks"` and `task`; an empty poll →
  `tick()` False; a `poll` that raises → `tick()` False and a `source_error`
  event (fake source with `raises=True`).
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: SourcePoller fills the inbox from the source`.

---

### Task 3: `SourceReflectorSink` (outbound projection)

**Files:** `src/harness/drivers/source_reflector.py`,
`tests/test_source_reflector.py`.

**Interfaces:**
- `SourceReflectorSink(EventSink)`: `__init__(self, sources: list[TaskSource])`.
- `emit(name, **fields)`:
  - needs `fields["task"]` (dict) → `Task.from_dict`; otherwise return.
  - `name == "dispatched"`: `progress = Progress(step=fields.get("to") or
    fields.get("queue",""), summary="")`; for each source
    `source.report_progress(task, progress)`.
  - `name == "finished"`: `finish(task, FinishResult(ok=True))`.
  - `name == "failed"`: `finish(task, FinishResult(ok=False, summary=fields.get("reason","")))`.
  - other names: ignore.
  - routing: call **all** sources; the `_mine` guard lives in the adapter (foreign
    `kind` → no-op). The reflector itself does not deal with `kind`.
- Robustness: `emit` must not let an exception escape other than what
  `CompositeEventSink` catches — but the sink itself upholds the "don't break on
  data" contract (missing field → silent return).

- [ ] **Step 1:** Tests with `MemoryTaskSource` — a `dispatched` event with a task
  (`data.source.kind=="memory"`) calls `report_progress` with `step` from `to`;
  `finished` → `finish(ok=True)`; `failed` → `finish(ok=False)` with reason; an
  event without `task` → nothing; a task of a foreign kind → the source ignores it
  (via the guard).
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: SourceReflectorSink projects status into the source`.

---

### Task 4: Wiring + e2e on in-memory drivers

**Files:** `src/harness/app.py`, `tests/test_phase4_e2e.py`,
`tests/test_app.py` (extension).

**Interfaces:**
- `build(...)` gains `sources: list[TaskSource] | None = None`.
  - default `[]` (backward compatible).
  - builds `pollers = [SourcePoller(source=s, inbox=inbox, events=events) for s in sources]`.
  - the `events` composite additionally receives `SourceReflectorSink(sources)` —
    add it to `CompositeEventSink(...)` **after** `ProjectionSink`, so the outbound
    projection doesn't run before the board projection (order doesn't matter
    functionally, but keep it readable).
  - `Harness` receives `pollers`; `run()` gathers them alongside the
    dispatcher/consumers (`_source_loop(poller, poll_interval, stop)` — tick/sleep
    like the other loops).
- `Harness.__init__` takes `pollers: list[SourcePoller]` (default `[]`).

- [ ] **Step 1:** E2E — `MemoryTaskSource.submit("Fix bug")`; `build` with this
  source, in-memory workspace/artifacts/forge, `FakeClock`, `ScriptedBehavior`
  (or Dummy). Run the loop (pattern from `test_phase2_e2e.py` — a bounded number of
  ticks / `stop`). Verify:
  - the task made it to `done`;
  - `source.states[issue]` contains at least one `report_progress` and one
    `finish(ok=True)`;
  - the task carried `data.source.kind == "memory"` the whole way (the outbound
    projection ran).
  - Backward-compatibility check: a task put directly into the inbox (with no
    source) also flows through and `source.states` knows nothing about it.
- [ ] **Step 2:** Red → **Step 3:** wiring → **Step 4:** green (whole suite).
- [ ] **Step 5:** Commit `feat: phase 4 wiring — poller and reflector in the run loop`.

---

### Task 5: GitHub client — `GithubClient`, `FakeGithubClient`, `HttpGithubClient`

**Files:** `src/harness/drivers/github_client.py`,
`tests/test_github_client.py`.

**Interfaces:**
- `Issue(number, title, body, url, labels: tuple[str,...])` — frozen; `labels`
  is a tuple.
- `GithubClient(ABC)`: `list_issues(repo, *, label) -> list[Issue]`;
  `add_label(repo, number, label)`; `remove_label(repo, number, label)`
  (idempotent — a missing label is a no-op).
- `FakeGithubClient`: holds issues in `dict[int, Issue]` (seed in the constructor
  or via `add_issue`); `list_issues` returns those with `label` in `labels`;
  add/remove rebuild `labels` (frozen → new instance via `replace`). `remove_label`
  ignores a missing one.
- `HttpGithubClient(token, *, api="https://api.github.com", opener=None)`:
  - `urllib.request` (stdlib). Header `Authorization: Bearer <token>`,
    `Accept: application/vnd.github+json`.
  - `list_issues`: `GET {api}/repos/{repo}/issues?state=open&labels={label}` →
    map JSON to `Issue` (careful: PRs are also "issues" — filter out those with a
    `pull_request` key).
  - `add_label`: `POST …/issues/{n}/labels` with `{"labels":[label]}`.
  - `remove_label`: `DELETE …/issues/{n}/labels/{label}`; swallow 404.
  - `opener` injectable, so it can be tested without the network (a fake opener
    returns canned responses). **Real HTTP is never called in CI.**

- [ ] **Step 1:** Tests — `FakeGithubClient`: `list_issues` filters by label;
  add/remove change `labels`; removing a nonexistent one is a no-op.
  `HttpGithubClient` with a fake `opener`: `list_issues` filters out PRs and maps
  the fields; `add`/`remove` build the right method+URL+body (assert against the
  request captured by the fake opener).
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: GithubClient + fake and stdlib http driver`.

---

### Task 6: `GithubTaskSource`

**Files:** `src/harness/drivers/github_source.py`,
`tests/test_github_source.py`.

**Interfaces:**
- `GithubTaskSource(TaskSource)`, `kind = "github"`.
- `__init__(*, client, clock, repo, workflow="default", repository, worktree_root,
  select_label="harness:todo", claimed_label="harness:queued",
  pr_label="harness:pr-open", failed_label="harness:failed",
  step_labels: dict[str,str] | None = None)`.
- `_managed`: `{claimed_label, pr_label, failed_label, *step_labels.values()}`.
- `poll()`: `for issue in client.list_issues(repo, label=select_label):`
  `client.remove_label(repo, issue.number, select_label)`;
  `client.add_label(repo, issue.number, claimed_label)`; assemble a `Task` with
  `data={"title":issue.title,"body":issue.body,
  "source":{"kind":"github","repo":repo,"issue":issue.number,"url":issue.url}}`.
- `report_progress(task, progress)`: `if not _mine: return`;
  `label = step_labels.get(progress.step)`; `if label: _set_state(number, label)`.
- `finish(task, result)`: `if not _mine: return`;
  `_set_state(number, pr_label if result.ok else failed_label)`.
- `_set_state(number, target)`: `for l in _managed - {target}:
  client.remove_label(repo, number, l)`; `client.add_label(repo, number, target)`.
- `_mine(task)`: `task.data.get("source",{}).get("kind") == "github"`;
  `_issue(task)`: `task.data["source"]["issue"]`.

- [ ] **Step 1:** Tests with `FakeGithubClient` (seed issue #1 with `harness:todo`):
  - `poll()` → issue #1 has `harness:queued` and not `todo`; the task has
    `data.source.issue==1`.
  - a second `poll()` (no new `todo`) → `[]`.
  - `report_progress(task, Progress("development"))` with `step_labels={"development":
    "harness:coding"}` → the issue has `harness:coding`, not `queued`.
  - an unknown step (not in `step_labels`) → labels unchanged (coarse default).
  - `finish(ok=True)` → `harness:pr-open`; `finish(ok=False)` → `harness:failed`;
    always exactly one managed label.
  - a task without `data.source` → `report_progress`/`finish` no-op (guard).
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: GithubTaskSource — issue → task, status → label`.

---

### Task 7: CLI, architecture, smoke, documentation

**Files:** `src/harness/cli.py`, `tests/test_architecture.py`,
`tests/test_smoke_github.py` (new), `CLAUDE.md`.

**Interfaces:**
- `cli.py run`: add `--github-repo`, `--github-label` (default `harness:todo`),
  `--github-workflow` (default `default`), `--worktree-root`. When `--github-repo`
  and `GITHUB_TOKEN` are in the env, build
  `GithubTaskSource(client=HttpGithubClient(token), clock=SystemClock(), repo=…,
  repository=<local repo path>, worktree_root=…, step_labels=<default map for
  DEFAULT_DEFINITION steps>)` and pass `build(..., sources=[source])`. Without that,
  `sources=[]` (behavior unchanged).
- Default `step_labels` for the default workflow: a reasonable coarse map, e.g.
  `{"development":"harness:in-progress","review":"harness:in-review","land":"harness:landing"}`
  (other steps with no label → less noise; it's just a default, not law).

- [ ] **Step 1 (architecture):** extend `test_architecture.py`:
  - `dispatcher.py`/`consumer.py` do not import `harness.ports.source`
    (add `ports.source` to the check, or a new test analogous to `WORK_PORTS`).
  - `source_poller.py` imports only `harness.ports.*` + `harness.models`
    (no `harness.drivers`).
  - `test_only_app_and_cli_wire_drivers` must still pass with the new modules
    (source_poller is top-level and imports no drivers).
- [ ] **Step 2 (smoke):** `tests/test_smoke_github.py` — fully in-memory except for
  the phase's intent: `FakeGithubClient` with a seeded `harness:todo` issue,
  `GithubTaskSource`, in-memory workspace/artifacts/forge, `FakeClock`,
  `ScriptedBehavior`. Run the loop (briefly, like the phase2 e2e — **no real
  sleep**, via a bounded number of ticks / `stop`). Verify the final state: the
  issue has `harness:pr-open`, the task is in `done/`. (This smoke **touches
  neither network nor disk** — it's an e2e of the connector, not of real GitHub.)
- [ ] **Step 3 (docs):** `CLAUDE.md` — module map for `ports/source`,
  `source_poller`, `source_reflector`, `github_*`; invariants 13–16; the "What is
  responsible for what" section on `TaskSource` and the outbound projection.
- [ ] **Step 4:** `.venv/bin/pytest -q` — whole suite green.
- [ ] **Step 5:** Commit `docs+test: architecture, smoke and CLAUDE.md for phase 4`.

---

## Ordering and dependencies

```
T1 (TaskSource + Memory) ─┬─> T2 (SourcePoller) ─┐
                          └─> T3 (Reflector) ────┼─> T4 (wiring + e2e) ─┐
T5 (GithubClient) ─────────> T6 (GithubTaskSource) ────────────────────┴─> T7 (CLI+arch+smoke+docs)
```

T1 is the foundation (port + memory driver). T2/T3 build on T1 and go in parallel,
but both touch tests with `MemoryTaskSource` — write them serially because of the
shared `memory.py`. T4 joins the loop. T5/T6 are the GitHub branch, independent of
T2–T4 until T7. T7 closes it out (the CLI wires the real client, architecture +
smoke + docs).

## Notes for implementation

- **Don't edit `memory.py` in parallel with another task** — T1 adds
  `MemoryTaskSource` to it; the others don't touch it.
- **The reflector reads both `to` and `queue`.** `dispatched` carries `from`/`to`
  (see `dispatcher._move`); `queue` is present too. Take `to`, with `queue` as a
  fallback.
- **`CompositeEventSink` isolates failures** — the outbound projection needn't fear
  bringing down the loop, but keep `report_progress` idempotent anyway (reconcile
  for free).
- **No new production dependency.** `HttpGithubClient` = `urllib.request` + `json`.
  If you're tempted to reach for `requests`/`httpx`, don't.
