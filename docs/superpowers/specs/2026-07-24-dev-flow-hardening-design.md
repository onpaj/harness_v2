# Development-flow hardening — design

**Date:** 2026-07-24
**Status:** approved (brainstormed with the operator)

## Context

The `development` workflow (plan → design → architecture → development → review →
land) now has a `request_changes` review loop, a healer on `failed/`, a resolver
Process for merge conflicts, and a CI required check on PRs. Four gaps remain:

1. **Verification is an LLM claim, not a harness fact.** The development and
   review agents *report* "tests pass"; nothing deterministic checks it before a
   PR opens. This contradicts the project's own philosophy: the commit is done
   by the driver, not the LLM (ADR-0006) — test results should get the same
   treatment.
2. **Red CI is a dead end.** The conflicts Process catches `dirty`/`behind`
   PRs, but a PR whose checks failed sits open until a human notices.
3. **Only review can say anything but `done`.** Plan/design/architecture cannot
   reject an infeasible task or bounce a bad design, and every task pays the
   full design step even when it has no UI surface.
4. **Loops are unbounded.** review↔development (and any new loop) can ping-pong
   forever, burning agent runs on a task that will not converge.

## Goals

- A deterministic, zero-token verification gate between development and review.
- Bounded loops with operator-visible parking and one-click restart.
- Early steps that can reject doomed work before code is written, and skip the
  design step for pure backend issues.
- Red-CI PRs surfaced automatically (notify-only; auto-fix deliberately
  deferred — see "Decisions" below).

## Non-goals

- No auto-fix agent for red CI in this increment (see Decisions).
- No collapse of plan/design/architecture into an adaptive single step.
- No changes to the healer, resolver, or landing machinery.

## Decisions

- **Red CI: notify-only, defer auto-fix.** Once the verify gate exists, red CI
  can only come from semantic drift against main, CI-vs-local env differences,
  or repos whose `verify` command is a subset of full CI — all rare. A
  full `github-checks` Check driver + `ci-fix` workflow (the structural twin of
  `github-conflicts` + resolver) was designed and rejected for now as not yet
  earning its code. Instead: a Process built on the existing `command` check
  (a `gh` query for harness-authored PRs with failed check rollups, `per-state`
  keyed on `slug:pr:head_sha`) with `sink: slack`. Pure config, no code. If red
  CI proves frequent in practice, the auto-fix version becomes a follow-up
  increment with data behind it.
- **Loop caps park to a `stalled/` queue and notify — they never auto-heal.**
  The operator chose visibility + manual restart over cap→failed→healer.

## Target workflow shape

Happy path: `plan → design → architecture → development → verify → review →
land → end`. Full edge list:

| from | on | to |
|---|---|---|
| plan | done | design |
| plan | skip_design | architecture |
| plan | reject | end |
| design | done | architecture |
| architecture | done | development |
| architecture | request_changes | design |
| architecture | reject | end |
| development | done | verify |
| verify | done | review |
| verify | request_changes | development |
| review | done | land |
| review | request_changes | development |
| land | done | end |

- `verify` is a new step between development and review.
- `plan`: `done | skip_design | reject`. `architecture`: `done |
  request_changes | reject`. `design` and `development` stay `done`-only;
  `review` keeps `done | request_changes`.
- `reject` routes to `end` with `lastOutcome: reject` (no new queue).
- `heal` and `resolver` workflows are unchanged.

## Increment 1 — verify gate

A deterministic step, following "landing is a step, not magic" (ADR-0009).

- **Config:** `repos.json` entries gain an optional `verify` command string.
  `RepositoryRegistry` gains `verify_command(name) -> str | None`. No command
  configured → the step returns `done` immediately with summary
  "no verify command configured" — repos opt in gradually.
- **Behavior:** new `behaviors/verify.py` (`VerifyBehavior`), ports-only:
  - Attaches the worktree via `Workspace`, runs the command through a new
    small `CommandRunner` port with a subprocess driver (default timeout
    15 min, configurable) — tests drive it with a fake, the `AgentRunner` /
    `ClaudeCliRunner` pattern (invariant 13).
  - Exit 0 → `BehaviorResult("done", "verify passed: <first output line>")`.
  - Non-zero → `BehaviorResult("request_changes", <output tail>)` — the next
    development attempt reads the failure from history and from the artifact.
  - Full stdout/stderr is written as an attempt-indexed artifact
    (`verify-NN/output.txt`) via `artifacts_layout`.
  - Command crash or timeout → raise → `failed/` (healer). A red suite is
    `request_changes`, never `failed`.
- **Wiring:** `behavior_for` binds the `verify` step to `VerifyBehavior` the
  way `land → landing` is bound today (via the ADR-0016 registry seam or a
  plain step→behavior binding — the plan decides whichever is cheaper). There
  is **no agent JSON** for verify; it is not a persona.
- **Prompt:** the development persona gains one line — a revision round may be
  triggered by a verify artifact; read it and fix the failures.

## Increment 2 — loop caps + `stalled/`

- **Config:** workflow JSON gains optional `maxRounds: {step: N}`, parsed and
  validated by `FilesystemWorkflowRepository` exactly like `maxParallel`;
  absent → unbounded (existing files keep today's behavior). The default
  development workflow ships with `maxRounds: {development: 3, design: 2}`.
- **Enforcement:** the dispatcher, not the router. `route()` stays a pure
  `(status, outcome)` function (invariants 4/8 untouched). When routing says
  "next step = S" and the task's history already contains `maxRounds[S]`
  dispatcher entries *into* S (counted only after the latest restart-reset
  marker), the dispatcher parks the task instead — a dispatcher status
  decision, its mandate under invariant 3. No new state: the count is derived
  from `task.history`.
- **`stalled/`:** a new terminal queue, sibling of `healed/` — nobody consumes
  it (invariant 24 extends; `failed/` keeps its single reader, `stalled/` gets
  none). The parking history entry summarizes why: "development hit
  maxRounds=3, last verdict: <tail of last request_changes summary>".
- **Surfacing:** the label reflector renders it as `harness:stalled` via the
  same `report_progress` path as every move; the board gains a `stalled`
  column. If `SLACK_WEBHOOK_URL` is set, an event-sink listener posts one line
  ("task X stalled at development after 3 rounds") — wiring in `cli._run`, no
  new port.
- **Recovery:** `TaskControl.restart` accepts `stalled/` tasks with the same
  reset semantics as `failed/`: clear status/outcome, append a reset marker,
  re-inbox; the dispatcher decides fresh and the round counter restarts from
  the marker.

## Increment 3 — early gates

- **Outcomes:**
  - `plan`: `skip_design` when the issue has no UI/UX/frontend surface (pure
    backend/infra/refactor — design would only restate the plan); `reject`
    when the task is infeasible, contradicts the codebase, or is too ambiguous
    to plan. The reasoning goes into the plan artifact.
  - `architecture`: `request_changes` → design when the design is fixable;
    `reject` when the task is doomed regardless of design. The summary must
    say which.
- **`reject` reflection:** `FinishResult` gains a resolution field
  (`done | rejected`) plus summary text. The GitHub driver renders `rejected`
  as a comment on the source issue (the agent's reasoning) plus a
  `harness:rejected` label, and leaves the issue **open** — rejection is
  advice; the human decides. The core stays GitHub-blind; a driver that does
  not care ignores the field. No PR exists at reject time, so no landing or
  merge machinery is involved; `IssueReconciler` archives the task when the
  human closes or re-labels the issue.
- **Caps interplay:** the architecture↔design bounce is bounded by
  `maxRounds: {design: 2}` via increment 2 — nothing extra.
- **Prompts:** plan/architecture personas get the new verdict options with
  tight "only when" criteria mirroring the review persona's, so `reject` does
  not become a lazy out.

## Increment 4 — red-CI notify Process (config only)

`processes/ci-red.json`: trigger ~5 min, action `command` (a `gh` search for
open harness-authored PRs whose check rollup is failed, one stdout line per
PR, `per-state` dedup), `sink: slack`. A Process must target a workflow or
step, so the target reuses the heal workflow's shape: a one-step `ci-red`
workflow whose single step is finished by the existing `open-issue` finisher
(ADR-0016) — each newly-red PR gets a short tracking issue on its repo, and
the Slack sink posts the movement line. All three files (process, workflow,
agent persona drafting the issue text) are operator config in the harness
root, not code in this repo; documented here so the deferred auto-fix
increment has its baseline.

## Implementation order

1. Verify gate (biggest effect, smallest change)
2. Loop caps + `stalled/`
3. Early gates
4. Red-CI notify process (operator config, any time)

Each increment is independently shippable and separately planned.

## Testing

- All new core logic (dispatcher cap counting, verify outcome mapping,
  `FinishResult` resolution) is driven in-memory with fakes (`FakeClock`,
  fake `CommandRunner`, memory queues) — no disk, no sleeping, per the repo's
  testing invariants.
- `test_architecture.py` gains the usual guards: `CommandRunner` is unknown to
  dispatcher/consumer; `stalled/` is never read by any loop.
- The smoke tests extend minimally: `test_smoke_git.py` gets a verify step with
  a trivial real command (`true`/`false`) to cover the subprocess driver.
