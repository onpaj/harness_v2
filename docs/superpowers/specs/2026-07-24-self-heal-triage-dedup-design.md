# Self-heal: failure triage + issue dedup — design (2026-07-24)

## Problem

Self-healing today (ADR-0018) is conservative to a fault. The `heal` persona
(`cli._HEALER_PERSONA`) files an issue **only** when a failure is a fixable
*harness bug* — a violated driver contract, a wiring gap, a missing workflow
edge, an unhandled error path. Everything else — a step that outran its
`--agent-timeout` (1800s), a flaky network, an unauthenticated tool — is
classified "expected/external", the persona returns `request_changes`, and the
`heal` workflow settles the task silently into `healed/` with nothing filed.

Two consequences:

1. **Operational failures are invisible.** The dominant real failure on this
   deployment is the `development` step hitting its 1800s budget on a large
   task (observed on harness #94 and Anela.Heblo #3730). The healer, by design,
   files nothing — so a recurring operational problem produces zero signal.
2. **The remit is too narrow.** The operator wants the healer to also surface
   tuning/operational problems (timeouts, resource limits) as diagnostic
   issues — not just harness code bugs.

Widening the remit naively re-introduces a problem the old design dodged by
filing almost nothing: **duplicate issues**. The `open-issue` finisher's
idempotency marker is the *failed task id* (`data.heal.of`), so ten timeouts on
ten different tasks would open ten near-identical issues.

## Scope

**In:**

1. The `heal` task runs against the **harness repo** (a worktree + `gh`), so a
   new `dedup` step can read the repo's open issues. Enabled by a `repository`
   param on the `failed-tasks` check — no new mechanism.
2. The `heal` persona is **widened to triage**: it classifies a failure as a
   fileable problem (harness bug **or** operational/tuning) vs. a non-fileable
   external/transient one, and drafts an issue for the former. Timeout advice is
   **diagnostic, not prescriptive** ("hit its 1800s budget — raise the step's
   `timeout` or decompose it", never a hard number).
3. A new **`dedup` step** sits between diagnosis and filing: it reads the
   harness repo's open issues and decides whether a correlated one already
   exists. New → file it; correlate exists → settle silently to `healed/`.
4. The `heal` workflow uses **workflow-defined outcomes** (ADR-0018, invariant
   #42, already shipped): `heal` forks `file`/`skip`, `dedup` forks
   `unique`/`duplicate` — each self-documenting via a transition `hint`. No
   overloading of `request_changes`.

**Out (deliberately deferred):**

- **Recurrence signal.** On a duplicate the task settles silently; it does *not*
  comment "recurred on tsk_X, N total" on the existing issue. A follow-up.
- **Differentiated labels.** The `open-issue` finisher keeps its single
  `("harness:self-heal",)` label; bug vs. operational is expressed in the issue
  body, not a distinct label. A follow-up.
- **Threshold filing** (suppress until the Nth occurrence). The `dedup` step's
  semantic "is this already open?" check is the chosen de-duplication; no
  persistent counter is introduced.
- Any change to `IssueTracker`, `OpenIssueBehavior`, or the recursion guard
  beyond what §Unchanged records.

## Design

### 1. Attach the harness repo to heal tasks — a check param, not new plumbing

`FailedTasksCheck._observation` currently returns
`Observation(state_key=task.id, data=data)` (`drivers/failed_tasks_check.py`) —
no `repository`, so the emitted heal task is repo-less (`task.repository is
None`) and its agent steps get no worktree. But `Observation.repository` already
flows into the emitted task (`ScheduledTrigger._task_for`:
`repository=obs.repository or self._repository`, `scheduled_trigger.py:111`),
and the check already takes a `params` dict.

- `FailedTasksCheck.__init__` gains an optional `repository: str | None = None`;
  `_observation` stamps it: `Observation(state_key=task.id, data=data,
  repository=self._repository)`.
- The `failed-tasks` factory (registered inside `app.build()`) reads
  `params.get("repository")` and passes it through.
- `processes/autoheal.json` sets it:
  `"action": {"check": "failed-tasks", "params": {"repository": "onpaj/harness_v2"}}`.
  The default `AUTOHEAL_PROCESS_DEFINITION` (`cli.py:832`) gains the same param,
  defaulting to the configured heal repo (`HARNESS_HEAL_REPO`) so the check's
  worktree repo and the finisher's file-to repo stay identical.

**Consequence (intended):** every step of the heal task now attaches the harness
worktree. `heal` gains real code context for diagnosis (issue #94's design
already flagged this as "arguably a feature"); `dedup` gets `gh`. This does
**not** change where issues are filed — `OpenIssueBehavior` files against its
fixed injected `repo` (from `HARNESS_HEAL_REPO`), independent of
`task.repository` (`behaviors/open_issue.py`). The two are decoupled and both
point at the harness repo.

### 2. The `heal` workflow gains a `dedup` step

`HEAL_DEFINITION` (`cli.py:130`) and the live `workflows/heal.json` become:

```json
{
  "name": "heal",
  "start": "heal",
  "transitions": [
    {"from": "heal",  "on": "file",      "to": "dedup",
     "hint": "a harness bug, or an operational/tuning problem worth filing"},
    {"from": "heal",  "on": "skip",      "to": "end",
     "hint": "external/transient, or the task's own request was impossible — nothing to file"},
    {"from": "dedup", "on": "unique",    "to": "file-issue",
     "hint": "nothing similar is open in the harness repo"},
    {"from": "dedup", "on": "duplicate", "to": "end",
     "hint": "a correlated issue is already open — settle silently"},
    {"from": "file-issue", "on": "done", "to": "end"}
  ],
  "descriptions": {
    "heal":  "diagnose the failed task from its report; decide whether it warrants a GitHub issue",
    "dedup": "read the harness repo's open issues; decide whether the drafted issue is new"
  },
  "finishers": {"file-issue": "open-issue"}
}
```

Because outcomes are workflow-derived (invariant #42), `heal`'s vocabulary is
`{file, skip}` and `dedup`'s is `{unique, duplicate}` — computed live by
`ClaudeCliBehavior` from these edges and enforced by the runner. No persona
`allowed_outcomes` edit is needed or authoritative; the `hint`/`descriptions`
text is injected into each step's prompt by `compose_prompt`.

### 3. The `heal` persona — triage, diagnostic advice

`cli._HEALER_PERSONA` (bound to the `heal` agent) is rewritten to:

- classify the failure into **harness bug**, **operational/tuning** (timeout,
  resource limit), or **external/transient** (flaky network, unauthenticated
  tool, a task whose own request was impossible/wrong);
- for a **bug or operational** failure, draft the issue to the artifact path the
  harness gives it (first line `# <title>`, then diagnosis + concrete proposed
  change), and choose the **`file`** outcome;
- for **operational** failures, keep the recommendation **diagnostic**: name the
  budget that was exceeded and the two levers (raise the step's per-agent
  `timeout`, or decompose the step) without prescribing a value;
- for an **external/transient** failure, write nothing and choose **`skip`**.

The persona no longer names `done`/`request_changes`; `compose_prompt` lists the
`file`/`skip` choices and their hints. Its tools stay `["Read", "Write"]` — it
drafts from the failure report; the attached worktree is available but it is not
required to run anything.

### 4. The `dedup` persona — a new agent (`agents/dedup.json`)

A new default persona (added to `_AGENT_PERSONAS`/`_AGENT_MODELS` in `cli.py`,
written by `_write_default_agents` when a workflow binds the step):

- **tools:** `["Read", "Bash"]` — `Read` for the `heal` step's drafted
  `issue.md` artifact (under `.artifacts/<task.id>/heal/…` in the shared
  worktree), `Bash` for `gh issue list --state open --limit 100` (and reading
  candidate bodies) in the harness worktree. `GITHUB_TOKEN` is in the service
  env.
- **judgment:** does the drafted issue duplicate or strongly correlate with any
  currently-open issue? A correlate → **`duplicate`** (summary names the `#`); no
  correlate → **`unique`**.
- **model:** `opus` (a judgment task), mirroring `heal`.
- **allowed_outcomes:** the workflow-less fallback only; the live vocabulary is
  `{unique, duplicate}` from the workflow.

### 5. Unchanged

- `OpenIssueBehavior` / the `open-issue` finisher: still reads the **`heal`**
  step's draft (`_latest_draft` filters `ref.step == "heal"`), still files
  against its fixed injected repo, still idempotent by the per-task marker
  (`data.heal.of`). With semantic dedup now in the `dedup` step, that marker's
  role narrows to guarding a crash between claim and file — unchanged behavior.
- `FailedTasksCheck`'s recursion guard (skip a failed task carrying `data.heal`,
  invariant #25): unaffected — `state_key`/marker logic is orthogonal to
  `repository`.
- `route()`, the dispatcher, the consumer: untouched (invariants #2/#3/#4/#8).

## Files

- `drivers/failed_tasks_check.py` — `repository` ctor param + stamp on the
  `Observation`.
- `app.py` — the `failed-tasks` factory reads `params["repository"]`.
- `cli.py` — `HEAL_DEFINITION` (add `dedup` edges + `descriptions`);
  `AUTOHEAL_PROCESS_DEFINITION` (add the `repository` param);
  `_HEALER_PERSONA` (rewrite to triage, `file`/`skip`); a new `_DEDUP_PERSONA`
  in `_AGENT_PERSONAS`/`_AGENT_MODELS`.
- Live `harness-root` files (`workflows/heal.json`, `agents/heal.json`,
  `agents/dedup.json`, `processes/autoheal.json`) — updated to match; called out
  as a deploy step, since the running service reads them, not the source
  templates.
- `docs/adr/0019-heal-triage-and-dedup.md` — new ADR (see below).
- Tests (see §Testing).

## Invariants touched

- **#24–#27 refined:** the `heal` workflow is now three steps (`heal` → `dedup`
  → `file-issue`), the deliverable is still opened by the `open-issue` finisher
  (never the LLM, #26), and the single reader of `failed/` is still the
  `failed-tasks` check (#24). The check now stamps the emitted heal task's
  `repository`. Record this in a new **ADR-0019** (`0019-heal-triage-and-dedup.md`),
  superseding the two-step-heal-workflow portion of `0018-healing-as-a-process.md`.
  (Note: `0018` is already collided across five parallel-PR files — not this
  change's job to renumber; `0019` is the next free integer.)
- **#42 relied upon, unchanged:** `heal`/`dedup` outcomes are workflow-derived;
  the personas' `allowed_outcomes` are fallback only.

## Testing

All on the in-memory drivers + `FakeClock` (no disk, no real waiting), extending
`tests/test_self_heal_e2e.py` and unit tests:

- `test_failed_tasks_check.py` — the emitted `Observation`/task carries the
  configured `repository`; absent the param it stays repo-less (back-compat).
- `test_self_heal_e2e.py` — three end-to-end paths with a scripted
  `FakeAgentRunner`:
  1. `heal → file`, `dedup → unique` → `open-issue` files exactly one issue;
  2. `heal → file`, `dedup → duplicate` → **no** issue filed, task in `healed/`;
  3. `heal → skip` → no `dedup`, no issue, task in `healed/`.
- `test_fs_workflows.py` — the three-step `heal.json` (with `descriptions` and
  per-transition `hint`s) parses; `outcomes_for("heal") == ("file", "skip")`,
  `outcomes_for("dedup") == ("unique", "duplicate")`.
- `test_cli.py` — `_write_default_agents` writes `agents/dedup.json` when the
  heal workflow binds it; `AUTOHEAL_PROCESS_DEFINITION` carries the `repository`
  param.
- Full suite green (`.venv/bin/pytest -q`), including `test_smoke_git.py`.

## Execution (packages)

Small enough for three commits on a feature branch
(`claude/heal-triage-dedup`), each a conventional commit:

- **A — repo attach:** `failed_tasks_check.py` + `app.py` factory +
  `AUTOHEAL_PROCESS_DEFINITION` param + `test_failed_tasks_check.py`. `feat:`.
- **B — the workflow + personas:** `HEAL_DEFINITION` (`dedup` edges +
  descriptions), `_HEALER_PERSONA` rewrite, new `_DEDUP_PERSONA`,
  `_write_default_agents` coverage; `test_fs_workflows.py` + `test_cli.py`.
  `feat:`.
- **C — e2e + ADR + docs:** the three `test_self_heal_e2e.py` paths,
  `docs/adr/0019-…md`, and CLAUDE.md invariant #24–#27 refinement note. `feat:`
  / `docs:`.

A live-file deploy step (updating the four `harness-root` files + service
restart) is operator action after merge, not part of the branch.

## Risks / call-outs

- **Persona files in `harness-root` vs. source templates.** The running service
  reads `harness-root/{workflows,agents,processes}`; the source `cli.py`
  templates only seed a fresh `init`. Both must be updated — the branch changes
  the templates; a post-merge deploy step updates the live files (or re-runs the
  relevant `harness agent init`/regeneration). Flagged so the fix isn't "green
  tests, unchanged behavior in production".
- **`repository` param vs. `HARNESS_HEAL_REPO` drift.** The check's worktree repo
  (param) and the finisher's file-to repo (`HARNESS_HEAL_REPO`) must match, or
  `dedup` reads a different repo's issues than where the issue lands. The default
  template ties them; a hand-edited `autoheal.json` that sets one and not the
  other is the footgun — the ADR notes it.
- **`gh` in the heal worktree.** `dedup` relies on `gh` + `GITHUB_TOKEN` on the
  service `PATH` (already true for the service; the git-smoke fixtures use a fake
  issue list, never real `gh`).
