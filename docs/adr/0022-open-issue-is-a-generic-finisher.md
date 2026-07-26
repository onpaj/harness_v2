# ADR-0022: `open-issue` is a generic finisher; serving is data

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

**This ADR also supersedes ADR-0021 (`autoheal-enabled-by-its-process-file`).**
That ADR merged into `main` (PR #129) while this branch was in flight, from
the same diagnosis this ADR reached independently — `HARNESS_HEAL_REPO`'s
ambient, two-place configuration was the problem — but with a different fix.
Commit `b31740a` resolves the merge in this branch's favor; this is where that
choice is recorded and justified, per ADR-0000's additive convention.

Agreed with ADR-0021: `HARNESS_HEAL_REPO` has no reason to live in the
environment — it names a public repo, not a secret (invariant #40's
`SLACK_WEBHOOK_URL` parallel that motivated the variable never held) — and
`processes/autoheal.json` is the right home for that configuration. Both ADRs
also agree an unattended service must not crash-loop on config it cannot be
told to fix.

Reversed: ADR-0021 keeps `--heal-repo <owner/repo>` as a bootstrap — a thin
generator whose only remaining job, once the environment variable is gone, is
writing `processes/autoheal.json` the first time (never clobbering a
hand-edited one). This ADR deletes it outright. ADR-0021's own argument for
why the process file, not the environment, is the right home — every
automation in the harness is enabled by a file in `processes/` — argues just
as much against a flag whose only remaining job is writing that file:
`harness init` now seeds `processes/autoheal.json` unconditionally (Decision,
below), the same way it already seeds `workflows/heal.json` and
`agents/heal.json` — a bootstrap flag producing a file `init` already produces
is a second path to the same artifact, not a needed one.

The substantive difference is what the field means. ADR-0021 removed the
second configuration *surface* (the environment variable) but not the
*ambiguity* already present in the one it kept: there,
`action.params.repository` is a GitHub `owner/repo` slug, stamped directly as
`task.repository` for the worktree *and* read back out as the slug the
finisher files against — one string doing two jobs (a `repos.json` key and a
GitHub API identity) that happen to look alike but need not agree. On the
reference install they didn't: the value in `processes/autoheal.json` matched
neither a name in `repos.json` nor the shape the finisher needed, so
self-healing sat silently inert for weeks, the only trace roughly two dozen
startup warnings nobody was watching for. This ADR's own Decision (below,
written before ADR-0021 merged) already closes that gap a different way:
`action.params.repository` is a `repos.json` **name** — the same kind of value
`task.repository` already is everywhere else in the harness (invariant #15)
— and the GitHub slug is *derived* from that repo's clone `origin`, exactly
the way `GithubForge` already resolves one for landing. The field now has
exactly one meaning.

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
  cross-workflow finisher-conflict check — but only once `cli._run`'s own
  pre-flight check (`_validate_served_workflows`) confirms its finisher
  bindings can actually be wired: one that can't (e.g. the pre-generic
  string form `{"file-issue": "open-issue"}`, which parses to an empty
  config and fails the `open-issue` factory's own `label` check — the exact
  shape `workflows/heal.json` shipped with on the reference install before
  this branch) is dropped from the served set with a `warning:` instead of
  failing the whole build. **Revised from this ADR's original text**, which
  said a workflow-level incoherence like this "fails the build instead of
  being ignored" — true of `build()` in isolation, but wrong once `harness
  run` serves every file in `workflows/` unconditionally: a launchd-supervised
  service restarting into the same fatal error on every attempt is
  functionally "ignoring" the rest of a perfectly good root, just more
  loudly. **Revised again, for the same reason as the note above**: the
  first cut of this pre-filter caught every `ValueError`
  `validate_workflow_finishers` could raise, including an *unknown* kind —
  so `{"kind": "call-a-webhook"}` (or `label-issue` bound while
  `GITHUB_TOKEN` is unset) also silently dropped its workflow instead of
  failing the run, which is the wrong side of the operator's own rule: an
  unknown kind is a value that is *set and wrong*, not a missing one.
  Fixed by giving the unknown-kind case a distinct type
  (`UnknownFinisherKind`, still a `ValueError` subclass) that
  `_validate_served_workflows` re-raises instead of catching — so what
  still fails the whole build is: an unknown finisher kind (now caught at
  this very pre-filter, before `build()` is even reached, not merely "if it
  survives" the filter), or a genuine binding conflict between two
  workflows that both survive it (only `build()` itself can see that one,
  since it's a cross-workflow fact). A `processes/*.json` Process that
  targets only a workflow this filter drops is skipped right along with it
  (a warning naming both, not a `ProcessValidationError`) — otherwise
  `heal.json`'s drop would just move the same crash-loop one layer down,
  into `FilesystemProcessRepository.build()` rejecting `autoheal.json`'s
  `{"workflow": "heal"}` target as unresolvable.
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
  to. What was still missing, until now: the seeded empty default is *valid*,
  so it never hit that validation, and stayed silent while spending an agent
  call on `heal` and one on `dedup` per drained failure and filing nothing.
  `cli._run` now warns about exactly that at startup
  (`_warn_missing_autoheal_repository`) — a missing value is "not configured
  yet", not a typo, so this is a warning, never an error, keeping the
  fail/warn split consistent throughout: a *set* `action.params.repository`
  that's wrong still fails loud at compile time; an *absent* one now warns
  instead of staying silent.
- **An operator who configured self-healing under ADR-0021 has to change one
  value.** `action.params.repository` in their `processes/autoheal.json`
  holds a GitHub slug (e.g. `onpaj/harness_v2`). That is no longer what the
  field means: process-compile validation now checks it against
  `repos.json`'s own names, so a slug that isn't also a registered name fails
  loudly (`ProcessValidationError`, `field="params"`) rather than silently
  filing against the wrong place the way the pre-ADR-0021 drift did. The fix
  is a one-line edit — replace the slug with that repo's `repos.json` name —
  not a re-run of any bootstrap: there is none any more (`--heal-repo` is
  gone; see above).
