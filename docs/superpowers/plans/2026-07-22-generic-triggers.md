# Generic triggers — Implementation Plan

> **For agentic workers:** implement task by task. Each task: write a failing
> test → run it (red) → implement → run it (green) → commit. Steps have a
> checkbox (`- [ ]`).

**Goal:** A generic **trigger** creates tasks on a schedule — "every hour, check a
condition; if it holds, create a task" — targeting a workflow **or** a single
step. A trigger is a `TaskSource` that produces tasks and reflects nothing
outward; it never places a task (the dispatcher does). Triggers are declared as
data (`triggers/*.json`) with a named, code-backed check.

**Spec:** `docs/superpowers/specs/2026-07-22-generic-triggers-design.md`
**ADR:** `docs/adr/0013-triggers-produce-tasks-not-placements.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. **No new production
dependency** — the scheduled trigger runs on the stdlib and the existing `Clock`.

## Global Constraints

- **The decision-making roles still hold.** The consumer doesn't branch on
  outcome; the dispatcher changes status; the router is a pure function and
  **does not read** `data`. A trigger emits a task with `workflow_template` **or**
  `step` — placement stays the dispatcher's (invariants #3/#8).
- **A trigger is a `TaskSource`, added to the existing `sources` list.** No new
  port, no change to `SourcePoller`, `SourceReflectorSink`, `dispatcher`,
  `consumer`, `router`, `models`, or `build()`'s signature. `TaskSource` stays
  whole (ADR-0010 / invariants #18–#20 unchanged).
- **Cadence lives in the trigger, gated on the `Clock`.** No new loop, no
  per-source interval knob. `poll()` gates on the interval bucket and returns `[]`
  cheaply between fires.
- **Tests touch neither real time nor the network.** `FakeClock` everywhere; the
  disk/threshold check is driven through an injected reader, never a real syscall
  in the unit suite. Filesystem driver tests use `tmp_path` (precedent:
  `test_fs_workflows.py`, `test_fs_agents.py`) — that is not "sleeping in real
  time", it is allowed.
- Time is ISO 8601 UTC with a `Z` suffix (`Clock.now()`).
- Development happens on branch `claude/generic-triggers-design-bxxrld` (session
  instruction, not a convention from `CLAUDE.md`).

---

### Task 1: `Trigger` base — projection is optional

**Files:** `src/harness/ports/source.py`, `tests/test_trigger_base.py`.

**Interfaces:**
- `Trigger(TaskSource)` in `ports/source.py`: `poll()` stays abstract (inherited);
  `report_progress(self, task, progress) -> None` and `finish(self, task, result)
  -> None` are concrete **no-ops** (`return None`). Docstring per the spec: "a
  `TaskSource` that produces tasks but reflects nothing outward."

- [ ] **Step 1:** Test — a minimal `Trigger` subclass implementing only `poll()`
  (return `[]`) instantiates; `report_progress(task, Progress("x"))` and
  `finish(task, FinishResult(ok=True))` are callable and return `None` without
  raising; the subclass is a `TaskSource` (`isinstance`).
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: Trigger base — a TaskSource with no outward projection`.

---

### Task 2: `Check` protocol, `Observation`, interval parsing

**Files:** `src/harness/ports/triggers.py`, `tests/test_triggers_port.py`.

**Interfaces (pure — imports nothing from `drivers/`):**
- `Observation(state_key: str | None = None, data: dict[str, Any] = {})` — frozen.
  `state_key` feeds `per-state` dedup; `data` is shallow-merged into the emitted
  task's `data`.
- `Check(ABC)`: `evaluate() -> list[Observation]`. Empty list = condition not met.
- `CheckFactory = Callable[[dict[str, Any]], Check]` (a type alias; `params` in).
- `parse_interval(text: str) -> float`: `"90s"/"30m"/"1h"/"24h"` → seconds
  (`float`). A bare integer is seconds. Raises `ValueError` on a malformed value.

- [ ] **Step 1:** Tests — `parse_interval` maps `"1h"→3600`, `"30m"→1800`,
  `"45s"→45`, `"2"→2`; malformed (`"1x"`, `""`, `"h"`) raises `ValueError`. A
  trivial `Check` subclass returning a fixed list satisfies the ABC.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: Check port, Observation, interval parsing`.

---

### Task 3: `ScheduledTrigger`

**Files:** `src/harness/drivers/scheduled_trigger.py`,
`tests/test_scheduled_trigger.py`.

**Interfaces:**
- `ScheduledTrigger(Trigger)`:
  - `__init__(*, name, clock, interval: float, check: Check,
    workflow: str | None = None, step: str | None = None, repository=None,
    worktree_root=None, dedup: str = "per-interval")`. Exactly one of
    `workflow`/`step` must be set (else `ValueError`). `dedup ∈ {"per-interval",
    "per-state"}`.
  - `kind = f"scheduled:{name}"` (unique projection-routing key; a `Trigger`
    stamps no matching `data.source`, so the reflector lists and ignores it).
  - `poll()`:
    - `now = self._clock.now()`; `bucket = self._bucket(now)` where
      `bucket = floor(epoch(now) / interval)` (parse the ISO string to epoch
      seconds — `datetime.fromisoformat`, `Z`→`+00:00`).
    - **Gate:** if `bucket == self._last_bucket`, return `[]` (already handled this
      period, even after a `sleep(0)` re-poll). *Note: the gate is a cheap
      short-circuit; correctness across restarts still rests on `dedup_key` +
      `SourcePoller._seen`, so a restart mid-interval with `_last_bucket` reset to
      `None` re-evaluates but the poller drops the duplicate by key.*
    - Set `self._last_bucket = bucket`, call `check.evaluate()`, build one `Task`
      per `Observation` via `_task_for(obs, bucket)`; return them.
  - `_task_for(obs, bucket)`: `Task(id=new_task_id(), created=now,
    workflow_template=workflow, step=step, repository=repository,
    worktree=(f"{worktree_root}/{id}" if worktree_root else None),
    dedup_key=self._dedup_key(bucket, obs), data={**obs.data})`. **No
    `data.source`** (nothing to reflect).
  - `_dedup_key(bucket, obs)`:
    - `per-interval` → `dedup_key(self.kind, self._target_str, bucket)`.
    - `per-state` → `dedup_key(self.kind, self._target_str, obs.state_key)` (**no
      bucket**; a `None` `state_key` under `per-state` is a `ValueError` at
      evaluate time — a `per-state` check must supply one).
  - `_target_str` = `f"wf:{workflow}"` or `f"step:{step}"`.

- [ ] **Step 1:** Tests with `FakeClock` and a fake `Check`:
  - `interval=3600`, `workflow="wf"`, `always`-style check (one empty
    observation): at `t=0` `poll()` → 1 task with `workflow_template=="wf"`,
    `step is None`, `data.source` absent, a non-`None` `dedup_key`; advance clock
    `+1800s` → `poll()` → `[]` (same bucket); advance to `+3600s` → `poll()` → 1
    new task with a **different** `dedup_key` (next bucket).
  - `step="cleanup"` target → task has `step=="cleanup"`, `workflow_template is
    None`.
  - `per-state` dedup: a check returning the same `state_key` in two different
    buckets yields two tasks with the **same** `dedup_key` (the poller would drop
    the second); a changed `state_key` yields a different key. A `per-state` check
    with `state_key=None` raises.
  - `obs.data` is merged into `task.data`.
  - constructing with both/neither of `workflow`/`step` raises `ValueError`.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: ScheduledTrigger — interval x check x target`.

---

### Task 4: Built-in checks

**Files:** `src/harness/drivers/checks.py`, `tests/test_checks.py`.

**Interfaces:**
- `AlwaysCheck(Check)`: `evaluate()` → `[Observation()]` (one empty observation).
  Factory ignores `params`.
- `DiskThresholdCheck(Check)`: `__init__(*, path, percent, usage=shutil.disk_usage)`
  — `usage` injectable for tests. `evaluate()`: read `used/total`; if
  `used/total*100 >= percent` return `[Observation(state_key=f"{path}:over",
  data={"title": f"disk {path} over {percent}%"})]`, else `[]`.
- `BUILTIN_CHECKS: dict[str, CheckFactory]` = `{"always": ..., "disk-threshold":
  lambda p: DiskThresholdCheck(path=p["path"], percent=p["percent"])}`.

- [ ] **Step 1:** Tests — `AlwaysCheck.evaluate()` → one observation.
  `DiskThresholdCheck` with an injected `usage` returning 85%/100% and
  `percent=80` → one observation with a `state_key`; at 50% → `[]`. `BUILTIN_CHECKS`
  builds each by name; a factory called with the right `params` yields the check.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: built-in trigger checks (always, disk-threshold)`.

---

### Task 5: `FilesystemTriggerRepository`

**Files:** `src/harness/drivers/fs_triggers.py`, `tests/test_fs_triggers.py`.

**Interfaces:**
- `FilesystemTriggerRepository(root: Path)`.
- `build(*, clock, checks: dict[str, CheckFactory] = BUILTIN_CHECKS,
  repository=None, worktree_root=None, known_targets: set[str] | None = None)
  -> list[ScheduledTrigger]`:
  - read every `*.json` under `root` (missing dir → `[]`).
  - per file validate, **failing fast** (raise a `TriggerValidationError` naming
    the file) on: unknown top-level `kind` (only `"scheduled"` in v1); malformed /
    missing `interval` (via `parse_interval`); `check` name not in `checks`;
    `target` not exactly one of `{"workflow"}` / `{"step"}`; `dedup` not in
    `{"per-interval","per-state"}`; and — when `known_targets` is supplied — a
    `target` value not in it.
  - build one `ScheduledTrigger` per file (`name` = the file stem unless a `name`
    key overrides), passing `check = checks[name](params)`.

- [ ] **Step 1:** Tests with `tmp_path` — a valid `always`/`workflow` file →
  one `ScheduledTrigger` (`kind=="scheduled:<stem>"`, interval parsed, target
  wired); a `disk-threshold`/`step` file with `params` → a working trigger;
  missing dir → `[]`; each invalid file (bad interval, unknown check, no target,
  both targets, unknown `dedup`, `target` outside a supplied `known_targets`)
  raises `TriggerValidationError` naming the file.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: FilesystemTriggerRepository — triggers/*.json`.

---

### Task 6: Wiring + e2e + `harness init`

**Files:** `src/harness/cli.py`, `tests/test_generic_triggers_e2e.py`,
`tests/test_cli_init.py` (extension).

**Interfaces:**
- `cli.py`: a `_scheduled_sources(args, root, registry, *, clock) ->
  list[TaskSource]` helper that builds the trigger list via
  `FilesystemTriggerRepository(root / "triggers").build(...)` with the served
  workflow names ∪ known steps as `known_targets`, then append it to the `sources`
  list already assembled from `_github_sources` + `_mergeability_sources`
  (`cli.py:1250-1251`). Missing/empty `triggers/` → no scheduled sources; the
  harness runs exactly as today. **`build()` gains no parameter** — triggers are
  `TaskSource`s in the existing `sources` argument.
- `harness init`: create an empty `triggers/` directory next to `agents/` and
  `repos.json` (optionally a single `triggers/example.json.disabled` comment
  sample that the repository skips because it isn't `*.json`).

- [ ] **Step 1:** E2E (`FakeClock`, in-memory workspace/artifacts/forge,
  `ScriptedBehavior`/Dummy) — a `ScheduledTrigger(interval=3600, always,
  workflow=<served>)` in `sources`. Run the loop bounded (pattern from
  `test_phase4_e2e.py`, no real sleep): the fire-0 task reaches `done`; advancing
  the clock past a boundary and ticking the source loop yields a **second** task;
  the trigger receives no effective `report_progress`/`finish` (it's a `Trigger`).
  A workflow-less `step` target: assert the task is placed by the **dispatcher**
  into that step's queue (nobody wrote there directly). Backward-compat: with no
  triggers, behaviour is unchanged.
- [ ] **Step 2:** `harness init` test — `triggers/` exists after init; a
  subsequent `FilesystemTriggerRepository(...).build()` over it returns `[]`
  (empty dir, or the sample skipped).
- [ ] **Step 3:** Red → implementation → green (whole suite).
- [ ] **Step 4:** Commit `feat: wire scheduled triggers; harness init writes triggers/`.

---

### Task 7: Architecture, docs, CLAUDE.md

**Files:** `tests/test_architecture.py`, `CLAUDE.md`.

- [ ] **Step 1 (architecture):** extend `test_architecture.py`:
  - `dispatcher.py`/`consumer.py` import neither `harness.ports.source` (already
    guarded) **nor** `harness.ports.triggers` — add `ports.triggers` to the
    orchestration check.
  - `scheduled_trigger.py` imports only `harness.ports.*` + `harness.models` +
    `harness.ids` (no `harness.drivers`) — a new test mirroring
    `test_source_poller_imports_only_ports_and_models`.
  - `test_only_app_and_cli_wire_drivers` still passes: `scheduled_trigger`,
    `checks`, `fs_triggers` are drivers touched only by `app.py`/`cli.py` — covered
    by the existing glob check, no per-file test needed (invariant #33 shape).
- [ ] **Step 2 (docs):** `CLAUDE.md` — add invariants **34–37** (verbatim from the
  spec); module-map rows for `ports/triggers`, `drivers/{scheduled_trigger,checks,
  fs_triggers}`; a "What is responsible for what" bullet on triggers ("a `Trigger`
  produces tasks and reflects nothing; cadence is a clock-gate; dedup is
  bucket-keyed"); a Gotchas note on the non-constant `dedup_key` and the
  `_seen`-grows-per-fire / not-suppress-while-in-flight limitation.
- [ ] **Step 3:** `.venv/bin/pytest -q` — whole suite green.
- [ ] **Step 4:** Commit `docs+test: architecture and CLAUDE.md for generic triggers`.

---

## Ordering and dependencies

```
T1 (Trigger base) ─┐
T2 (Check + parse) ─┴─> T3 (ScheduledTrigger) ─┬─> T5 (FS repo) ─> T6 (wiring+e2e) ─> T7 (arch+docs)
T4 (built-in checks) ──────────────────────────┘
```

T1 and T2 are independent foundations (different files). T3 needs both (it's a
`Trigger` that runs a `Check`) and is tested with a *fake* check, so it doesn't
wait on T4. T4 (real checks) and T3 can go in parallel. T5 composes T3 + T4 from
disk. T6 wires it into the run loop and `init`. T7 closes it out.

## Notes for implementation

- **No `build()` signature change and no new loop.** A `ScheduledTrigger` is a
  `TaskSource`; it rides the existing `sources` list, `SourcePoller`, and
  `_source_loop`. Resist adding a "trigger loop" — the clock-gate inside `poll()`
  is the whole mechanism.
- **The gate is an optimization; `dedup_key` is the guarantee.** Keep both. The
  in-memory `_last_bucket` short-circuits the `sleep(0)` re-poll within a run;
  the persisted, bucket-keyed `dedup_key` + `SourcePoller._seen` is what makes a
  period fire at most once **across restarts** (ADR-0010's mechanism, non-constant
  key).
- **A `per-state` trigger must supply `state_key`.** Its dedup key omits the
  bucket, so a `None` `state_key` would collapse every state into one — treat it
  as a config/usage error, not a silent default.
- **Don't reach for a real cron / a scheduling library.** `datetime` +
  `Clock.now()` is the whole cadence. If tempted by `APScheduler`/`croniter`, don't
  — it would add a production dependency and a second clock the tests can't fake.
- **`data.source` stays empty on triggered tasks.** That is what makes the
  reflector ignore them by the same `_mine()` path that ignores `harness submit`
  tasks — do not stamp a source just to look symmetric with GitHub.
