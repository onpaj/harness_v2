# Self-heal that fixes: routing the healer's issue into the development pipeline

Status: draft
Date: 2026-07-27

## Goal

Self-healing today stops one step short of healing. A failed task is drained
from `failed/` by the `failed-tasks` Check, triaged by the `heal` persona,
deduped, and filed as a GitHub issue labelled `harness:self-heal` — and there it
waits, because nothing consumes that label. The operator reads the issue and
relabels it `harness:todo`, at which point the machinery that already exists
(`processes/harness-todo.json` → `workflows/development.json` → a PR) does the
rest unattended.

That relabel is the whole gap. This spec closes it: the healer files its issue
directly into the pipeline's own ingestion label, so a harness failure becomes a
proposed fix with no human in the middle.

Closing it introduces exactly one new hazard — the pipeline can now feed itself —
so the spec pairs the routing change with a hard one-hop brake.

## Non-goals

- **Auto-merge.** Every self-heal fix still terminates at a PR the operator
  merges. Getting from a green PR to `main` unattended is a separate change the
  operator is implementing outside this spec.
- **Widening what counts as a failure.** Only tasks that reach `failed/` are
  healed. A crash-looping service, a task oscillating `verify → development`,
  and a stuck-dirty PR remain undetected. See §6.
- **A new workflow.** Self-heal fixes travel the existing `development`
  workflow. A lighter bugfix-shaped pipeline may be worth adding later, on
  evidence that `plan → design → architecture` is wasteful for a two-line fix;
  it is not assumed here.
- **A new label vocabulary.** No label is introduced, and none is load-bearing
  (§2).

## 1. The routing change

`workflows/heal.json`'s `file-issue` finisher currently binds:

```json
"file-issue": {
  "kind": "open-issue",
  "from_step": "heal",
  "label": "harness:self-heal"
}
```

**The binding's `label` may not simply be flipped to `harness:todo`.** That
field is not only the label the issue carries — it is also the scope of the
idempotency search: `OpenIssueBehavior` passes `scope_label=self._label`
(`behaviors/open_issue.py`), and `GithubIssueTracker.open_issue` searches the
open issues carrying `scope_label` for the marker before creating. The
ingesting process *removes* `harness:todo` when it claims (swapping it for
`harness:queued`), so a flipped `label` would put the idempotency scope on a
label that disappears seconds later — and a re-run of `file-issue` after the
claim would find nothing and open a **duplicate** issue.

The routing therefore rides on a second label instead, leaving the scope
untouched:

```json
"file-issue": {
  "kind": "open-issue",
  "from_step": "heal",
  "label": "harness:self-heal",
  "allowed_labels": ["harness:todo"]
}
```

`allowed_labels` is the allowlist a draft's own labels are filtered against
(`behaviors/open_issue.py`, wired from the binding config at `cli.py`), so the
`heal` persona adds `"labels": ["harness:todo"]` to its draft and the behavior
lets it through. The issue is then born carrying `harness:self-heal` (stable,
the idempotency scope) **and** `harness:todo` (the routing label, consumed on
claim). Within 30s, `processes/harness-todo.json` claims it and fires it into
`development`.

This is configuration and persona data in the operator's root — no code change
is needed for the routing half.

Both the issue-filing repository and the ingestion scan already agree on
`harness_v2`: `processes/autoheal.json`'s `action.params.repository` is read
back by `cli._autoheal_repo` to wire the finisher, and `repos.json` registers
`harness_v2` so `GithubIssuesCheck` scans it. No new configuration is needed for
the two halves to meet.

## 2. Provenance is the marker, not a label

`open_issue` embeds `<!-- harness-issue:<marker> -->` in every issue body it
opens, and `GithubIssuesCheck` ingests the body verbatim into
`task.data["body"]` (it stamps `title`, `body` and `source` — never labels). So
a task born from a harness-filed issue carries that provenance with no new
plumbing.

The marker prefix is **`harness-issue:`, not `harness-heal:`** — it was
deliberately generalised when `open-issue` became a generic finisher
(`docs/superpowers/plans/2026-07-25-generic-open-issue-finisher.md`), because
the healer is no longer its only consumer. A brake matching the old spelling
would never fire: a silent no-op, and the exact failure this design exists to
prevent.

That generality is a property of the brake, not a defect in it. See §3.

`harness:self-heal` is genuinely load-bearing here — as the idempotency
*scope*, not as provenance. It cannot be dropped without re-keying
`search_issue_by_marker`, and §1 depends on it staying put precisely because it
is the one label the ingestion process does not remove.

## 3. The one-hop brake

`FailedTasksCheck.evaluate()` refuses to observe a failed task carrying
`data.heal` — the guard that stops a failed *heal* task from being healed
(invariant 25). A fix task born from a heal-filed issue carries no such marker:
it is an ordinary GitHub-issue task. So after §1, this chain is unbounded:

```
F1 fails → heal → issue I1 → fix task D1 → D1 fails → heal → issue I2 → D2 → …
```

Each generation costs a full `plan → design → architecture → development →
verify → review` run. Nothing terminates it, because the operator — today's
terminator — has been removed from the loop.

**The brake:** `FailedTasksCheck` also declines a failed task whose
`data["body"]` carries a `harness-issue:` marker. It is settled to `healed/` in
the same `evaluate()` call with a distinct note — `heal-declined: fix attempt
for a harness-filed issue failed (one-hop limit)` — and yields no
`Observation`.

**The rule this states is broader than self-heal, deliberately.** Because the
marker is generic, the brake reads as *the harness does not heal a failure of
work it filed for itself* — which also covers the other `open-issue` consumers
(the rotating architecture review filing on `Anela.Heblo`). That is the right
scope: every one of those paths has the same runaway shape, where the harness's
own output becomes its own input. Narrowing the brake to self-heal alone would
require stamping issue labels at ingestion — new plumbing bought to make the
guard *weaker*.

Consequences, stated plainly:

- At most **one** automated fix attempt per root failure. A failed fix attempt
  is board-visible in `healed/` and waits for the operator, exactly as every
  self-heal issue does today.
- `failed/` still drains monotonically. The brake declines to *observe*; it
  never leaves a claimed task in `failed/`, so invariant 25's drain property is
  preserved.
- The note is deliberately distinct from the existing `heal-failed` note, so the
  two decline reasons are separable when reading the board or grepping events.

Detecting the marker must reuse the marker's own construction rather than a
loose substring literal — `drivers/github_issues.py::marker_comment` already
renders it, and driver-to-driver imports are permitted
(`github_issues_check.py` already imports sibling drivers). The spelling having
already changed once, silently, under a design that assumed it stable is the
whole argument: the check must not carry its own copy of the literal.

### Invariant 25 must be amended

Invariant 25 currently reads that recursion "is guarded by a marker
(`data.heal`), not by construction". After §1 that sentence is true but no
longer sufficient — it describes only the healer-heals-healer cycle, not the
healer-heals-its-own-fix cycle this spec creates. The invariant's wording must be
extended to name both guards and both markers. Landing §1 without §3 is a
regression against the invariant as written.

## 4. Retuning the `heal` persona

The persona's output changes audience. Today its issue body is a diagnosis a
human reads before deciding what to do; after §1 it is the input to the `plan`
step, which turns it into numbered functional requirements with acceptance
criteria.

Required prompt changes, in `agents/heal.json`:

- The draft must carry `"labels": ["harness:todo"]`. This is what routes the
  issue into the pipeline (§1) — the binding's `allowed_labels` permits it, and
  without it the issue is filed but never ingested. It is the one change the
  whole design fails silently without.
- The drafted body must read as a **work order**: symptom, how to reproduce,
  the concrete proposed change, and acceptance criteria — the shape `plan`
  consumes. The current prompt asks for a diagnosis "then a concrete proposed
  change", which is close but leaves reproduction and acceptance implicit.
- The existing instruction to recommend **diagnostically rather than
  prescriptively** for operational/tuning findings stays. A timeout number is
  precisely the thing an automated pipeline should not invent.
- The body must state **explicitly when a finding is operational/tuning rather
  than a code defect**, so `plan` scopes it as a configuration change instead of
  a refactor.

The persona keeps its read-only tool set and its prohibition on running or
fixing code. Its deliverable is still the draft; the fixing happens downstream,
in a pipeline with a worktree, tests and a review step.

Operational findings are deliberately **not** routed differently from code
defects. Both file into `harness:todo`. The distinction is carried in the body
for `plan` to act on, and the operator's merge gate is the backstop. If
timeout-tuning PRs prove noisy in practice, splitting the two paths is a cheap
follow-up — it is not pre-built here.

## 5. Verification

**§1 is configuration and is proven live.** Force a failure (a task whose step
raises), then assert: exactly one issue appears carrying both `harness:todo` and
`harness:self-heal`; within one poll interval its label is swapped to
`harness:queued`; a task for it appears in the `development` workflow.

**§3 needs unit coverage** in `tests/`, alongside the existing
`FailedTasksCheck` recursion-guard tests:

- A failed task whose `data["body"]` carries a `harness-issue:` marker yields no
  `Observation`, lands in `healed/`, and records the one-hop note.
- A failed task with an unmarked body still yields exactly one `Observation`
  (the brake does not over-trigger on ordinary GitHub-issue tasks).
- A failed task carrying `data.heal` still takes the pre-existing path with its
  own note (the two guards do not collapse into one).

**The end-to-end assertion that matters** is negative: fail the *fix* task and
confirm no second issue is filed. This is the property the whole brake exists
for and the one a passing unit test alone does not demonstrate.

`tests/test_architecture.py` must stay green — the brake adds no import that
`dispatcher.py`/`consumer.py` may not have.

## 6. Deferred, named rather than dropped

Two failure classes are real, out of scope here, and each deserves its own spec:

- **Stuck-task detection.** A task oscillating `verify → development`, or
  sitting in one step past a threshold, never terminates and so never reaches
  `failed/`. This is the class that historically burns the most unattended
  spend. A new Check plus a Process; the design question is what threshold and
  measured how.
- **Service liveness.** A crash-looping service produces no task at all, and a
  harness Process cannot watch the harness — if the service is down, nothing
  polls. This needs a watcher outside the service (a launchd agent), and its
  output channel cannot be a GitHub issue that only the down service reads. A
  push notification to the operator is the honest design.

## 7. Known tail risk

Merging a self-heal PR cuts a release (python-semantic-release on every push to
`main`), and `com.harness.autoupdate` installs it within 30 minutes. A bad
self-heal fix can therefore crash-loop the service — and a down service heals
nothing, including itself.

This risk predates this spec, but the spec increases how often code the operator
did not author reaches that path. It is **not** solved here. The mitigation is
the existing precondition on any push touching startup: run the new code against
a copy of the real root with `serve` stubbed and require exit 0. That check
should be treated as mandatory before merging a self-heal PR, not merely
advisable.

## Summary of changes

| Change | Where | Kind |
|---|---|---|
| `allowed_labels: ["harness:todo"]` on the `file-issue` binding | `~/harness-root/workflows/heal.json` | operator config |
| Draft emits `labels: ["harness:todo"]` | `~/harness-root/agents/heal.json` + the template in `src/harness/cli.py` | persona data + code |
| One-hop brake on the `harness-issue:` marker | `src/harness/drivers/failed_tasks_check.py` | code + tests |
| Invariant 25 wording | `CLAUDE.md` | docs |
| Work-order prompt, operational flagging | same persona files as above | persona data + code |

## Correction note (2026-07-27)

The first draft of this spec was researched against `~/harness-app`, a worktree
base that had drifted behind `main`. Three claims were wrong and are corrected
above: the marker prefix (`harness-issue:`, not `harness-heal:`), the effect of
flipping the binding's `label` (it moves the idempotency scope onto a label the
ingester deletes), and the belief that `harness:self-heal` is force-appended
independent of config (it is the scope label, supplied by config). Research the
tree you are going to change.
