# Self-heal: failure triage + issue dedup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the self-heal `heal` workflow from "file harness-bug issues only" to "triage every failure and file bug *or* operational/tuning issues, de-duplicated against the repo's open issues."

**Architecture:** Three changes on top of the already-shipped workflow-defined-outcomes machinery (ADR-0018, invariant #42): (1) the `failed-tasks` check stamps a `repository` on each heal task so its steps get a harness-repo worktree + `gh`; (2) the `heal` workflow gains a `dedup` step and switches to self-documenting outcomes (`heal`: `file`/`skip`; `dedup`: `unique`/`duplicate`), with the custom vocabulary living only in the workflow JSON; (3) the `heal` persona becomes a triager and a new `dedup` persona reads open issues. Implements `docs/superpowers/specs/2026-07-24-self-heal-triage-dedup-design.md`.

**Tech Stack:** Python 3.11, `.venv/bin/pytest -q`, in-memory drivers + `FakeClock` for tests.

## Global Constraints

- **English only** — all code, comments, docstrings, strings, tests, docs, commit messages.
- **Conventional commits are load-bearing** — `feat:` bumps minor, `docs:`/`test:`/`refactor:` don't release. Use `feat:` for A/B, `feat:`/`docs:` for C.
- **Tests run on in-memory drivers + `FakeClock`** — never sleep in real time; no disk beyond the `FilesystemTaskQueue` under `tmp_path` the self-heal e2e already uses.
- **Custom outcomes live in the workflow JSON, not persona files.** Per ADR-0018 a persona's `allowed_outcomes` is the workflow-less *fallback* and `fs_agents` validation still restricts it to `{done, request_changes}` — do **not** relax that. The live vocabulary (`file`/`skip`, `unique`/`duplicate`) comes from `Workflow.outcomes_for`, derived from the `heal` workflow's edges at run time.
- **The check's `repository` param must equal the finisher's file-to repo** (`HARNESS_HEAL_REPO`), so `dedup` reads the same repo the issue will be filed in.
- **Invariant #42 holds; the router/dispatcher/consumer are untouched** (invariants #2/#3/#4/#8). `open-issue` finisher, the recursion guard (#25), and the per-task marker are unchanged.

---

## File Structure

- `src/harness/drivers/failed_tasks_check.py` — gains a `repository` ctor param, stamps it on each `Observation`.
- `src/harness/app.py` — the internal `failed-tasks` factory reads `params["repository"]`.
- `src/harness/cli.py` — `HEAL_DEFINITION` (dedup edges + hints + descriptions), `AUTOHEAL_PROCESS_DEFINITION`/`_ensure_autoheal_process` (repository param), `_HEALER_PERSONA` rewrite, new `_DEDUP_PERSONA`, `AGENT_PERSONAS`/`AGENT_MODELS` (`dedup`), `_write_default_agents` (clamp custom outcomes to a loadable fallback).
- `docs/adr/0019-heal-triage-and-dedup.md` — new ADR.
- `CLAUDE.md` — refine invariants #24–#27 to the three-step heal workflow.
- Tests: `tests/test_self_heal_e2e.py` (repo stamp + three routing paths), `tests/test_fs_workflows.py` (heal.json parse + `outcomes_for`), `tests/test_cli.py` (`dedup.json` written, autoheal repo param, clamp).

---

## Task 1: The `failed-tasks` check attaches the harness repo

**Files:**
- Modify: `src/harness/drivers/failed_tasks_check.py`
- Modify: `src/harness/app.py` (the `checks` dict, ~line 628)
- Modify: `src/harness/cli.py` (`AUTOHEAL_PROCESS_DEFINITION` ~832, `_ensure_autoheal_process` ~845, its call site ~1580)
- Test: `tests/test_self_heal_e2e.py`

**Interfaces:**
- Produces: `FailedTasksCheck(*, failed, healed, events, clock, repository: str | None = None)`; each emitted `Observation`/heal task carries `repository`.
- Consumes: `Observation(state_key, data, repository=...)` (already exists, `ports/triggers.py`); `ScheduledTrigger._task_for` already maps `obs.repository` → `task.repository`.

- [ ] **Step 1: Write the failing test** — in `tests/test_self_heal_e2e.py`, a focused test that a repo-configured autoheal stamps the heal task's repository. Seed as the existing e2e does but with `"params": {"repository": "onpaj/harness_v2"}` in `autoheal.json`, run until the heal task is claimable, and assert the fired heal task's `repository == "onpaj/harness_v2"` (read it off the `heal` queue / the `healing`→fired task). Also assert the back-compat path: with `"params": {}`, the heal task's `repository is None`.

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/pytest -q tests/test_self_heal_e2e.py -k repository` → FAIL (heal task repository is `None`).

- [ ] **Step 3: Implement** — three edits:

`failed_tasks_check.py` — add the param and stamp it:
```python
    def __init__(
        self,
        *,
        failed: TaskQueue,
        healed: TaskQueue,
        events: EventSink,
        clock: Clock,
        repository: str | None = None,
    ) -> None:
        self._failed = failed
        self._healed = healed
        self._events = events
        self._clock = clock
        self._repository = repository
```
```python
        return Observation(state_key=task.id, data=data, repository=self._repository)
```

`app.py` — the internal factory reads the param:
```python
        "failed-tasks": lambda params: FailedTasksCheck(
            failed=failed,
            healed=healed_queue,
            events=events,
            clock=clock,
            repository=params.get("repository"),
        ),
```

`cli.py` — thread the heal repo into the written process file. Change `_ensure_autoheal_process` to take the repo and stamp `params`:
```python
def _ensure_autoheal_process(layout: HarnessLayout, heal_repo: str) -> None:
    path = layout.processes / "autoheal.json"
    if path.exists():
        return
    layout.processes.mkdir(parents=True, exist_ok=True)
    definition = {
        **AUTOHEAL_PROCESS_DEFINITION,
        "action": {"check": "failed-tasks", "params": {"repository": heal_repo}},
    }
    path.write_text(
        json.dumps(definition, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
```
Update its call site (in the heal-repo block, ~line 1580+) to pass the already-computed `heal_repo`: `_ensure_autoheal_process(layout, heal_repo)`.

- [ ] **Step 4: Run to verify it passes** — `.venv/bin/pytest -q tests/test_self_heal_e2e.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat: the failed-tasks check attaches the harness repo to heal tasks"`

---

## Task 2: The `heal` workflow gains a `dedup` step + triage personas

**Files:**
- Modify: `src/harness/cli.py` (`HEAL_DEFINITION` ~130; `_HEALER_PERSONA`; new `_DEDUP_PERSONA`; `AGENT_PERSONAS` ~419; `AGENT_MODELS` ~448; `_write_default_agents` ~504)
- Test: `tests/test_fs_workflows.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `HEAL_DEFINITION` with `outcomes_for("heal") == ("file", "skip")` and `outcomes_for("dedup") == ("unique", "duplicate")`; `AGENT_PERSONAS["dedup"] = (_DEDUP_PERSONA, ["Read", "Bash"])`; `AGENT_MODELS["dedup"] = "opus"`.
- Consumes: `Workflow.outcomes_for`, `Transition.hint`, `Workflow.descriptions` (all shipped).

- [ ] **Step 1: Write the failing tests**
  - `tests/test_fs_workflows.py`: parse the new `HEAL_DEFINITION` and assert `outcomes_for("heal") == ("file", "skip")`, `outcomes_for("dedup") == ("unique", "duplicate")`, `description_for("dedup")` is non-empty, and the `dedup→unique→file-issue` / `heal→file→dedup` edges exist with their `hint`s.
  - `tests/test_cli.py`: (a) `_write_default_agents` over the heal workflow writes `agents/dedup.json` (a non-finisher step) with `allowed_tools == ["Read", "Bash"]` and a **loadable** `allowed_outcomes` (subset of `{"done","request_changes"}`, not `["file","skip"]`); (b) it writes `agents/heal.json` whose `allowed_outcomes` is likewise loadable; (c) `_ensure_autoheal_process` writes `params.repository`.

- [ ] **Step 2: Run to verify they fail** — `.venv/bin/pytest -q tests/test_fs_workflows.py tests/test_cli.py -k "heal or dedup"` → FAIL.

- [ ] **Step 3: Implement**

`HEAL_DEFINITION` (cli.py ~130):
```python
HEAL_DEFINITION = {
    "name": "heal",
    "start": "heal",
    "transitions": [
        {"from": "heal", "on": "file", "to": "dedup",
         "hint": "a harness bug, or an operational/tuning problem worth filing"},
        {"from": "heal", "on": "skip", "to": "end",
         "hint": "external/transient, or the task's own request was impossible — nothing to file"},
        {"from": "dedup", "on": "unique", "to": "file-issue",
         "hint": "nothing similar is open in the harness repo"},
        {"from": "dedup", "on": "duplicate", "to": "end",
         "hint": "a correlated issue is already open — settle silently"},
        {"from": "file-issue", "on": "done", "to": "end"},
    ],
    "descriptions": {
        "heal": "diagnose the failed task from its report; decide whether it warrants a GitHub issue",
        "dedup": "read the harness repo's open issues; decide whether the drafted issue is new",
    },
    "finishers": {"file-issue": "open-issue"},
}
```

Rewrite `_HEALER_PERSONA` to triage (no hardcoded outcome names — `compose_prompt` injects `file`/`skip` + hints): classify the failure as a fixable **harness bug**, an **operational/tuning** problem (a step that outran its `timeout`/a resource limit), or an **external/transient** failure. For a bug or operational problem, draft the issue to the artifact path given (first line `# <title>`, then diagnosis + a concrete proposed change; for operational, recommend **diagnostically** — name the exceeded budget and the two levers, raising the step's per-agent `timeout` *or* decomposing the step, without prescribing a number) and finish with the outcome that files it. For an external/transient failure write nothing and finish with the outcome that skips.

Add `_DEDUP_PERSONA`: "You decide whether a drafted GitHub issue duplicates one already open. Read the drafted `issue.md` in `.artifacts/<task>/heal/…`. List the repo's open issues with `gh issue list --state open --limit 100` and read the bodies of any that look related. If a currently-open issue describes the same underlying problem (a strong correlate, not just the same area), finish with the outcome that treats this as a duplicate and name the issue number in your summary. Otherwise finish with the outcome that treats it as new."

Register the new persona/model/tools:
```python
    "heal": (_HEALER_PERSONA, ["Read", "Write"]),
    "dedup": (_DEDUP_PERSONA, ["Read", "Bash"]),
```
```python
    "heal": "opus",
    "dedup": "opus",
```

Clamp `_write_default_agents` so a custom-outcome step still writes a **loadable** persona file (the field is only the fallback; `fs_agents` restricts it to the two constants):
```python
        fallback = [o for o in workflow.outcomes_for(step) if o in (DONE, REQUEST_CHANGES)] or [DONE]
        definition = _agent_definition_template(step, fallback)
```
(Import `DONE`, `REQUEST_CHANGES` from `harness.models` in `cli.py` if not already.)

- [ ] **Step 4: Run to verify they pass** — `.venv/bin/pytest -q tests/test_fs_workflows.py tests/test_cli.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat: heal workflow triages failures and dedups via a new dedup step"`

---

## Task 3: End-to-end proof, ADR, and invariant docs

**Files:**
- Test: `tests/test_self_heal_e2e.py`
- Create: `docs/adr/0019-heal-triage-and-dedup.md`
- Modify: `CLAUDE.md` (invariants #24–#27 note)

**Interfaces:**
- Consumes: the three-step `HEAL_DEFINITION`, `MemoryAgentCatalog` with `heal`+`dedup` specs, `FakeAgentRunner` scripted per step.

- [ ] **Step 1: Write the failing e2e** — extend `tests/test_self_heal_e2e.py` with the three-step heal workflow (copy the new `HEAL_DEFINITION` shape into the test module) and a `MemoryAgentCatalog` carrying a `heal` and a `dedup` `AgentSpec`. Script `FakeAgentRunner` per step and assert three paths:
  1. `heal → file`, `dedup → unique` → `MemoryIssueTracker` opened exactly **one** issue; the heal task ends terminal.
  2. `heal → file`, `dedup → duplicate` → **zero** issues opened; the heal task reaches `end` (silent).
  3. `heal → skip` → `dedup` never runs, **zero** issues opened.
  The runner enforces against the workflow-derived set, so emitting `file`/`skip`/`unique`/`duplicate` is accepted only because the workflow declares those edges — which also proves invariant #42 end to end.

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/pytest -q tests/test_self_heal_e2e.py` → FAIL (no `dedup` handling yet in the test's own definition / assertions).

- [ ] **Step 3: Implement the test** to green against the real behavior (the source is already done in Tasks 1–2); adjust the module's `HEAL_DEFINITION`/specs to the three-step shape.

- [ ] **Step 4: Write the ADR + docs**
  - `docs/adr/0019-heal-triage-and-dedup.md`: Context (the healer filed nothing for operational failures; widening risks duplicate issues), Decision (heal task carries the harness `repository`; `heal` triages `file`/`skip`; a new `dedup` step reads open issues and forks `unique`/`duplicate`; custom vocabulary lives in the workflow, personas keep the fallback), Consequences (operational visibility; silent-on-duplicate; supersedes the two-step-heal portion of `0018-healing-as-a-process.md`; the `repository`-param must equal `HARNESS_HEAL_REPO`).
  - `CLAUDE.md`: refine the invariant #24–#27 prose to the three-step heal workflow (`heal → dedup → file-issue`) and note the check now stamps `repository`. No new invariant number.

- [ ] **Step 5: Full suite + commit** — `.venv/bin/pytest -q` (all green incl. `test_smoke_git.py`), then `git commit -am "feat: prove heal triage+dedup end to end; ADR-0019 + invariants"`

---

## Post-merge deploy note (operator action, not part of the branch)

The running service reads `~/harness-root/{workflows,agents,processes}`, not the source templates. After merge, update the four live files to match: `workflows/heal.json` (three-step), `agents/heal.json` (rewritten persona), new `agents/dedup.json`, `processes/autoheal.json` (add `params.repository: onpaj/harness_v2`), then restart the service. `HARNESS_HEAL_REPO` must equal that param.

## Self-Review

- **Spec coverage:** repo attach → Task 1; triage persona + dedup step + workflow → Task 2; dedup persona → Task 2; e2e three paths + ADR + invariants → Task 3; deferred items (recurrence comment, differentiated labels, threshold) explicitly out of scope in the spec. ✅
- **Placeholder scan:** persona prose is intentionally descriptive (personas are natural-language data, not code); all *code* steps carry exact code. ✅
- **Type consistency:** `FailedTasksCheck(..., repository=...)`, `Observation(..., repository=...)`, `outcomes_for` returns `tuple[str, ...]`, `AGENT_PERSONAS["dedup"]` tuple shape matches existing entries. ✅
