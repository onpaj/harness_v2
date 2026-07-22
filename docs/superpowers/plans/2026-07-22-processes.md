# Processes — Implementation Plan

> **For agentic workers:** implement task by task. Each task: write a failing
> test → run it (red) → implement → run it (green) → commit. Steps have a
> checkbox (`- [ ]`).

**Goal:** A **Process** is a compile-time authoring aggregate — a
`processes/*.json` file naming a trigger (cadence), an action (a named `Check`),
a target (workflow **or** step) and a `sink` (outbound reflection, `none`-only in
v1). `FilesystemProcessRepository` compiles each into a `ScheduledTrigger` (a
`TaskSource`) that joins the existing `sources` list. Nothing under orchestration
learns the word "process."

**Spec:** `docs/superpowers/specs/2026-07-22-processes-design.md`
**ADR:** `docs/adr/0015-process-authoring-aggregate.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. **No new production
dependency** — a Process compiles to the existing `ScheduledTrigger` on the
stdlib and the existing `Clock`.

## Global Constraints

- **No new port, no new runtime object.** A Process compiles to a
  `ScheduledTrigger`; the harness sees only `TaskSource`s. No change to
  `SourcePoller`, `SourceReflectorSink`, `dispatcher`, `consumer`, `router`,
  `models`, any `ports/*`, `scheduled_trigger.py`, `checks.py`, or `build()`'s
  signature. `TaskSource` stays whole (ADR-0010 / invariants #18–#20).
- **Placement stays the dispatcher's.** A Process targets a workflow **or** a
  step; `route()` places the produced task (invariants #3/#8/#35).
- **The `sink` is validated but `none`-only.** It is the forward-compat seam; a
  non-`none` sink is a build error in v1. No reflector driver, no `data.sink`
  written.
- **`triggers/*.json` is untouched.** A Process compiles to the same
  `ScheduledTrigger` a bare trigger file does; both surfaces coexist.
- **Tests touch neither real time nor the network.** `FakeClock` everywhere;
  filesystem driver tests use `tmp_path` (precedent: `test_fs_triggers.py`).
- Development happens on branch `claude/triggers-actions-architecture-7i2avm`
  (session instruction, not a `CLAUDE.md` convention).

---

### Task 1: `FilesystemProcessRepository` — `processes/*.json` → `ScheduledTrigger`

**Files:** `src/harness/drivers/fs_processes.py`,
`tests/test_fs_processes.py`.

**Interfaces:**
- `ProcessValidationError(Exception)` — message names the offending file.
- `FilesystemProcessRepository(root: Path)`.
- `build(*, clock, checks: dict[str, CheckFactory] = BUILTIN_CHECKS,
  repository=None, worktree_root=None, known_targets: set[str] | None = None)
  -> list[ScheduledTrigger]`:
  - missing dir → `[]`; else read every `*.json` (sorted).
  - per file, parse the nested aggregate and **fail fast** (raise
    `ProcessValidationError` naming the file) on:
    - broken JSON / non-object;
    - missing/non-object `trigger`, or missing/malformed `trigger.interval`
      (via `parse_interval`);
    - missing/non-object `action`, or `action.check` not in `checks`;
    - `target` not exactly one of `{"workflow"}` / `{"step"}`; a `target` value
      not in `known_targets` when it is supplied;
    - `dedup` not in `{"per-interval", "per-state"}`;
    - `sink` present and not `{"kind": "none"}` (the only accepted sink in v1).
  - build one `ScheduledTrigger(name=<name|stem>, clock=clock,
    interval=parse_interval(trigger["interval"]),
    check=checks[action["check"]](action.get("params", {})),
    workflow=…, step=…, repository=repository, worktree_root=worktree_root,
    dedup=<dedup>)` per file. `sink` is validated then discarded (no driver).

- [ ] **Step 1:** Tests with `tmp_path` + `FakeClock`:
  - a valid `always`/`workflow` process → one `ScheduledTrigger`
    (`kind == "scheduled:<name>"`, interval parsed, `workflow` wired, `step`
    None); polling it once (fresh bucket) yields a task with
    `workflow_template == "wf"`.
  - a `disk-threshold`/`step` process with `action.params` and
    `dedup: "per-state"` → a working trigger targeting the step; an injected
    check path is not needed (build only asserts construction + target).
  - `name` defaults to the file stem when absent; an explicit `name` overrides.
  - `sink: {"kind": "none"}` is accepted; `sink` absent is accepted.
  - missing dir → `[]`.
  - each invalid file raises `ProcessValidationError` naming the file: broken
    JSON, non-object, missing `trigger`, missing/bad `trigger.interval`, missing
    `action`, unknown `action.check`, no `target`, both targets, `target`
    outside a supplied `known_targets`, unknown `dedup`, and a non-`none`
    `sink.kind`.
- [ ] **Step 2:** Red → **Step 3:** implementation → **Step 4:** green.
- [ ] **Step 5:** Commit `feat: FilesystemProcessRepository — processes/*.json compiles to a ScheduledTrigger`.

---

### Task 2: Wiring + e2e + `harness init`

**Files:** `src/harness/cli.py`, `tests/test_processes_e2e.py`,
`tests/test_cli_init.py` (extension).

**Interfaces:**
- `cli.py`: a `_process_sources(args, root, registry, *, clock, known_targets)
  -> list[TaskSource]` helper (mirrors `_scheduled_sources`) that builds the
  process list via `FilesystemProcessRepository(root / "processes").build(...)`
  with the same `known_targets` (served workflow names ∪ their steps ∪ catalog
  agents) and `worktree_root`, then is appended to the run's `sources` list right
  after `_scheduled_sources` (`cli.py:1357-1359`). Missing/empty `processes/` →
  `[]`; the harness runs exactly as today. **`build()` gains no parameter.**
- `harness init`: create an empty `processes/` directory next to `agents/`,
  `triggers/` and `repos.json` (the `(root / "triggers").mkdir(...)` line at
  `cli.py:134`).

- [ ] **Step 1:** E2E (`FakeClock`, in-memory workspace/artifacts/forge, Dummy
  behavior) — build `FilesystemProcessRepository` over a `tmp_path/processes`
  holding one `always`/`workflow=<served>` process, pass the compiled
  `ScheduledTrigger`s to `build(sources=...)`, run the loop bounded (pattern from
  the generic-triggers e2e / `test_phase4_e2e.py`, no real sleep): the fire-0
  task reaches `done`; advancing the clock past a boundary and ticking the source
  loop yields a **second** task; the process source receives no effective
  `report_progress`/`finish` (it's a `Trigger`). A workflow-less `step` target →
  the task is placed by the **dispatcher** into that step's queue (nobody wrote
  there directly). Backward-compat: with no processes, behaviour is unchanged.
- [ ] **Step 2:** `harness init` test — `processes/` exists after init; a
  subsequent `FilesystemProcessRepository(...).build()` over it returns `[]`.
- [ ] **Step 3:** Red → implementation → green (whole suite).
- [ ] **Step 4:** Commit `feat: wire processes; harness init writes processes/`.

---

### Task 3: Architecture, docs, CLAUDE.md

**Files:** `tests/test_architecture.py`, `CLAUDE.md`.

- [ ] **Step 1 (architecture):** extend `test_architecture.py`:
  - `fs_processes.py` imports only `harness.ports.*` + `harness.drivers.checks`
    + `harness.drivers.scheduled_trigger` (it composes the two trigger drivers) —
    a targeted test asserting it pulls in no *orchestration* module and no port
    outside `ports.{clock,triggers}`; the existing `test_only_app_and_cli_wire_
    drivers` already guards that non-wiring modules don't import it.
  - Re-assert (no code change needed) that `dispatcher`/`consumer` import neither
    `ports.source` nor `ports.triggers` — the Process path adds no orchestration
    import. (Covered by existing tests; note it in the plan, no new test.)
- [ ] **Step 2 (docs):** `CLAUDE.md` — add invariants **39–40** (verbatim from
  the spec); a module-map row for `drivers/fs_processes`; a "What is responsible
  for what" bullet on processes ("a Process is a compile-time aggregate that
  compiles to a `ScheduledTrigger`; the `sink` is a `none`-only forward-compat
  seam"); a Gotchas note that a Process and a bare trigger can express the same
  automation two ways (deliberate), and that the `sink`/action seams are recorded
  but unbuilt.
- [ ] **Step 3:** `.venv/bin/pytest -q` — whole suite green.
- [ ] **Step 4:** Commit `docs+test: architecture and CLAUDE.md for processes`.

---

## Ordering and dependencies

```
T1 (FilesystemProcessRepository) ─> T2 (wiring + e2e + init) ─> T3 (arch + docs)
```

T1 is the whole compiler and is self-contained (it reuses `ScheduledTrigger` and
`BUILTIN_CHECKS`, both already shipped). T2 wires it into the run loop and
`init`. T3 closes it out.

## Notes for implementation

- **Reuse, don't reinvent.** `FilesystemProcessRepository` is a *thin aggregate*
  over `FilesystemTriggerRepository`'s building blocks — `parse_interval`,
  `BUILTIN_CHECKS`, `ScheduledTrigger`. Do not duplicate the clock-gate or the
  dedup logic; only the *schema shape* (nested `trigger`/`action`/`target`/`sink`)
  differs from a bare trigger file.
- **The `sink` is validated, then discarded.** Do not stamp `data.sink`, do not
  build a reflector, do not accept any `kind` but `none`. The field's only job in
  v1 is to reserve the schema slot and reject a misconfiguration loudly.
- **No `build()` signature change and no new loop.** A compiled Process is a
  `TaskSource`; it rides the existing `sources` argument, `SourcePoller`, and
  `_source_loop`. Resist adding a "process loop."
- **Do not migrate `GithubTaskSource` and do not add a `github-issues` check
  here.** Both are recorded seams in the spec (§"The action seam"), deferred so
  this increment stays network-free and invariant-safe.
- **Keep the vocabulary straight.** A Process *references* a workflow; it is not
  a workflow. The `sink` is the outbound (progress/outcome) side, distinct from
  `lastOutcome` (the behavior's routing verdict).
