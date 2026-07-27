# Automatic PR merging — design

*2026-07-27*

## The request

> An automatic manager — some sort of process, or a part of the process —
> that is able to actually merge an open PR. It should validate based on some
> prompt, and if confident enough, it could merge the pull request.

## What it is

A **Process** the operator opts into, composed entirely of primitives that
already exist. No new orchestration concepts; every existing invariant holds
unchanged.

```
processes/automerge.json
  trigger : every 5m
  action  : github-mergeable   ──▶ one Observation per clean, harness-authored PR
  target  : workflow "automerge"
  dedup   : per-state          ◀── required; the default per-interval drops PRs
  sink    : none

workflows/automerge.json
  merge-review ──approve──▶ merge ──done──▶ end
       └────────reject──────────────────────▶ end

  finishers: { merge: { kind: merge-pr, from_step: merge-review,
                        min_confidence: 0.8, method: squash, dry_run: true } }
```

The task attaches to the **PR's own branch** through `data.branch` — invariant
#28's override, the identical path the `resolver` workflow already uses — so
the reviewer persona reads the real diff in a real worktree rather than an API
summary.

## The four roles, and why each is where it is

### The action: `github-mergeable`

The exact complement of `github-conflicts` (ADR-0018). Both scan the same
harness-authored open PRs and partition them by `mergeable_state` with **no
overlap**:

| state | owner | effect |
|---|---|---|
| `behind` | `github-conflicts` | update the branch server-side, no task |
| `dirty` | `github-conflicts` | fire the `resolver` workflow |
| `clean` | **`github-mergeable`** | fire the `automerge` workflow |
| everything else | nobody | left alone rather than guessed at |

`clean` is carrying deliberate weight: it is GitHub's own verdict that the PR
merges cleanly, every **required** status check is green, and every
**required** review is present. The harness therefore never re-implements
branch protection — an operator tightens the automerge gate by tightening the
repo's protection rules, where that belongs. (The corollary is a real
deployment hazard, recorded as a gotcha: on an *unprotected* repo, `clean`
means only "no conflict", handing the persona's confidence the whole decision.)

Two exclusions are the check's own, both failing closed:

- a **draft** PR is never a candidate;
- a PR carrying `harness:no-automerge` is vetoed — a per-PR override a human
  can apply with no config change and no harness restart.

State key is `slug:pr:head_sha`, so a re-pushed PR is a genuinely new candidate
(the previous review judged different code) while an unchanged one is not
re-reviewed every tick.

### The prompt: `agents/merge-review.json`

Persona-as-data (invariant #14). It is instructed to read the diff *and the
surrounding source*, to weigh scope creep and blast radius alongside
correctness, and — the part that matters — to treat rejection as cheap:

> Approve only when you would merge it yourself without asking anyone.
> Anything else […] is a rejection, and a rejection costs nothing but a
> human's glance. A wrong merge costs the default branch.

It ends its review with a fenced json block carrying a **confidence**, parsed
by `merge_verdict.py` (`issue_drafts.py`'s twin, package-free).

### The gate: who sets the bar

The persona supplies a *judgement*. The `merge-pr` finisher supplies the
*decision*, comparing that confidence against `min_confidence` **from the
workflow binding**.

This split is the design's centre of gravity. The agent never supplies the
threshold it is judged against, and has no tool that can merge — so a persona
that drifts toward writing `"confidence": 1.0` on everything still cannot lower
the gate. Only an operator editing a JSON field can. It also makes the feature
tunable without prompt surgery: raising the bar is a one-field edit, not a
persona rewrite that would need re-validating.

### The port: `PullRequestMerger`

A fourth GitHub-touching port, distinct from `Forge`/`MergeChecker`/
`IssueTracker`. The reasoning is ADR-0023's; in short, this is where the
harness's founding "we only propose" posture is relaxed, and a port keeps the
relaxation explicit, revocable (wire no merger → nothing can merge) and
unreachable from orchestration (guarded by `test_architecture.py`).

Two safety properties belong to the **contract**, so every driver inherits
them:

1. **The merge is pinned to a sha.** `expected_sha` is the head the reviewer
   read; a driver MUST refuse when the head moved past it. This is what makes
   merging unreviewed code *structurally impossible* rather than unlikely.
   GitHub implements it natively, so it costs one field.
2. **A refusal is not a failure.** A moved head, a newly-red check, a
   protection rule → `MergeRefused`, settled benignly; the next scan
   re-reviews. Only a genuine fault → `MergeError`, failing the task.
   `MergeRefused` subclasses `MergeError` so a forgetful driver author fails
   *safe*.

## The asymmetry

Every path that cannot establish a merge is safe, and none merges by accident:

| situation | result | merges? |
|---|---|---|
| confidence ≥ threshold, head current | merged | **yes** |
| `dry_run: true` | decision recorded on the board | no |
| confidence < threshold | settles `done`, reason in the summary | no |
| head moved since the scan | settles `done`, next scan re-reviews | no |
| no `head_sha` to pin to | settles `done` | no |
| no pull-request source | settles `done` | no |
| verdict unreadable / missing | **fails** the task (a real fault) | no |
| review returned `reject` | routes straight to `end` | no |

Wrongly refusing costs a human one click. Wrongly merging costs the default
branch. The tests assert the *absence* of a merge on every refusal path, not
merely that the summary string reads well.

## It ships withheld

`harness init` seeds `workflows/automerge.json` and `agents/merge-review.json`
as dormant data (exactly like `resolver`/`heal`) and seeds **no**
`processes/automerge.json`. Unlike autoheal — which drains a queue the harness
fills itself, and so is on by default — automerging is a *posture*, and the
Process stays the operator's to create.

Even then, the seeded binding ships `dry_run: true`: the step reviews and
records what it *would* have merged, with the confidence, on the board. An
operator watches this persona decide on their PRs, then flips one field. The
dry-run period is how trust is earned with evidence rather than assumed at
configuration time, and it costs one boolean to offer.

## Deliberately out of scope

- **Posting the review back to GitHub** as a real PR review. Wants a
  `GithubClient.create_review` verb; nothing else would change.
- **Reacting to review comments** on the PR — a separate inbound action.
- **Consulting live tasks**, so a PR whose own harness task is still in flight
  is skipped. The current design leans on `head_prefix` + `clean` instead;
  this is the same "live-task-consulting mode" already noted as a follow-up for
  scheduled triggers generally.
- **Merge queues / batching.** One PR, one decision.
