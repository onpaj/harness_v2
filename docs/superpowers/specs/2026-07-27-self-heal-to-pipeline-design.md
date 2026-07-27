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

The `label` becomes `harness:todo`. That is the entire routing change, and it is
configuration in the operator's root (`~/harness-root/workflows/heal.json`), not
code.

The resulting issue carries **both** labels: `GithubIssueTracker.open_issue`
appends `SELF_HEAL_LABEL` to whatever the binding passes, because its
idempotency search is scoped to that label
(`drivers/github_issues.py`, `drivers/github_client.py::search_issue_by_marker`).
Within 30s, `processes/harness-todo.json`'s `github-issues` Check claims the
issue by swapping `harness:todo` → `harness:queued` and fires it into
`development`. The swap does not touch `harness:self-heal`, so marker
idempotency keeps working while the fix is in flight.

Both the issue-filing repository and the ingestion scan already agree on
`harness_v2`: `processes/autoheal.json`'s `action.params.repository` is read
back by `cli._autoheal_repo` to wire the finisher, and `repos.json` registers
`harness_v2` so `GithubIssuesCheck` scans it. No new configuration is needed for
the two halves to meet.

## 2. Provenance is the marker, not a label

`open_issue` embeds `<!-- harness-heal:<failed-task-id> -->` in the issue body,
and `GithubIssuesCheck` ingests the body verbatim into `task.data["body"]`.
So a fix task carries, unmodified and without any new plumbing, the identity of
the failure that spawned it.

This is the mechanism the brake in §3 keys on. `harness:self-heal` is retained
because it arrives for free, and its **only** reader is the pre-existing
idempotency search, which uses it as a cheap index in place of GitHub's Search
API. Nothing this spec adds reads it: the routing in §1 keys on `harness:todo`,
the brake in §3 keys on the marker. It is otherwise legibility for the operator
browsing GitHub, and it can be removed later — the cost of removal is re-keying
`search_issue_by_marker` off the marker alone, nothing more.

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
`data["body"]` carries a `harness-heal:` marker. Such a task is a self-heal fix
attempt that failed. It is settled to `healed/` in the same `evaluate()` call
with a distinct note — `heal-declined: self-heal fix attempt failed (one-hop
limit)` — and yields no `Observation`.

Consequences, stated plainly:

- At most **one** automated fix attempt per root failure. A failed fix attempt
  is board-visible in `healed/` and waits for the operator, exactly as every
  self-heal issue does today.
- `failed/` still drains monotonically. The brake declines to *observe*; it
  never leaves a claimed task in `failed/`, so invariant 25's drain property is
  preserved.
- The note is deliberately distinct from the existing `heal-failed` note, so the
  two decline reasons are separable when reading the board or grepping events.

Detecting the marker should reuse the marker's own construction rather than a
loose substring on `harness-heal:` — `drivers/github_issues.py::marker_comment`
already renders it, and driver-to-driver imports are permitted
(`github_issues_check.py` already imports sibling drivers). The exact seam is an
implementation choice for the plan; the requirement is that the check does not
re-derive the marker's spelling independently.

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

- A failed task whose `data["body"]` carries a `harness-heal:` marker yields no
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
| `file-issue` label → `harness:todo` | `~/harness-root/workflows/heal.json` | operator config |
| One-hop brake on the body marker | `src/harness/drivers/failed_tasks_check.py` | code + tests |
| Invariant 25 wording | `CLAUDE.md` | docs |
| Work-order prompt, operational flagging | `~/harness-root/agents/heal.json`, and the shipped template in `src/harness/cli.py` | persona data + code |
