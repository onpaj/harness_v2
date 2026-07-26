# ADR-0021: `open-issue` is a generic finisher; serving is data

Status: Accepted

## Context

ADR-0016 made a step's finishing behavior data — a kind resolved against a
registry. `open-issue` was the one kind that did not honour it: it read its
marker from `task.data["heal"]["of"]`, scanned artifacts for a step literally
named `heal`, took its repo from a wiring-time `--heal-repo`, forced
`harness:self-heal` onto every issue, and filed exactly one. It could not be
bound to any step that was not the healer's.

A second consumer — a daily rotating architecture review filing 0–5 issues per
run — differs from the healer only in repository, label, step name and
cardinality: exactly what was hardcoded.

Separately, `--heal-repo`'s value had to be both a GitHub `owner/repo` slug and
a `repos.json` key. On the reference install those disagreed, so self-healing
was silently inert.

This supersedes the `--heal-repo`/`HARNESS_HEAL_REPO` wiring-time repo binding
described in ADR-0018 and the drift risk between it and `repos.json` recorded
in ADR-0019: both are removed in favor of the single `action.params.repository`
field below.

## Decision

- `OpenIssueBehavior` is driven by its `FinisherBinding.config`: `label`
  (carried by every issue, and the scope of the idempotency search),
  `from_step` (whose *presence* selects replace-vs-wrap), `allowed_labels`
  (the allowlist a draft's own labels are filtered against).
- A step's artifact carries its drafts as a fenced `json` block holding an
  array; parsing is a pure module, `issue_drafts.py`, and the **last** block
  wins — the same rule `_extract_verdict` applies to the agent's final
  message. An empty artifact is zero drafts; a non-empty one with no readable
  array is an error.
- A draft's marker is `<task id>:<sha1(title)[:8]>` — task-scoped so a re-run
  re-finds what it already filed, content-scoped so reordered findings match
  the right issue.
- The repository is derived from `task.repository` through the registry and
  the clone's `origin` remote, as `GithubForge` already does. `repos.json`
  keeps holding paths only. The resolver is *injected* into the behavior,
  because `behaviors/` may not import `drivers/`.
- `IssueTracker.open_issue` gains `scope_label`; idempotency is per
  `(repo, scope_label, marker)`.
- `harness run` serves every `workflows/*.json`; `--workflow` and
  `--all-workflows` are removed, as are the resolver and heal force-adds that
  approximated the same rule. An empty `workflows/` is workflow-less mode.
- `--heal-repo`/`HARNESS_HEAL_REPO` are removed. `harness init` seeds
  `processes/autoheal.json`; its `action.params.repository` is the one place
  self-healing is pointed at a repo, and it is a registry *name*.

## Consequences

- The healer is one binding of a generic kind. That is the test of the
  abstraction: had heal needed a special case, the generalization would be
  wrong.
- Serving-everything is only safe *because* of the generalization: the
  finisher kind used to be registered only when `--heal-repo` was set, so
  serving the seeded `heal` workflow without it exited 2 at build.
- A stale file in `workflows/` now gets live queues and joins the
  cross-workflow finisher-conflict check, so an incoherent leftover fails the
  build instead of being ignored. Intended — fail fast on incoherent data.
- The `workflow 'resolver' does not exist` crash-loop class is gone: serving
  what exists cannot name a file that is absent.
- **Self-healing is now active by default, not opt-in.** Every `harness init`
  root ships a live `processes/autoheal.json` on a 30s interval — before this
  change, autoheal existed only when an operator passed `--heal-repo`, so a
  bare `init` + `run` left `failed/` undrained. Now `failed/` drains and
  `heal`/`dedup` run on every root from the moment `harness run --agent
  claude` starts, whether or not anyone has configured a target: the seeded
  `action.params == {}` means it files nothing anywhere until an operator
  sets `params.repository` to a name in `repos.json`. This is an
  operator-visible behavior change — a freshly initialized root now does
  real (if inert) work on a schedule it did not opt into — and it is safe
  only because a repository-less `open-issue` binding fails the task with an
  explicit message rather than filing against the wrong repo, and because
  `action.params.repository` is now validated at process-compile time
  (`field="params"`, `ProcessValidationError` exits 2 with `error: …`)
  instead of drifting silently the way a mismatched `--heal-repo` slug used
  to.
