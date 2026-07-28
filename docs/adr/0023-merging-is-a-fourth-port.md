# ADR-0023: Merging a PR is a fourth port, and the operator sets the bar

Status: Accepted

## Context

The harness has always stopped one step short of the merge button. ADR-0009
put it plainly: *"a task is a transaction; at the end, landing lands the
artifacts and opens a PR. The harness never touches `main` — it only proposes.
The merge strategy is a human's call."* `ports/forge.py` repeats it in its own
docstring, and `MergeChecker` (ADR-0013) was deliberately shaped as *read-only*
— it asks whether a PR merged so a task can be archived, never merges one.

The operator now wants the harness to close that last gap: review an open PR
against a prompt and, when confident enough, merge it.

That is a genuine relaxation of a founding constraint, not a feature bolt-on,
so the question is not only *how* to merge but *where the relaxation lives* —
and what makes it impossible for the LLM to widen on its own.

Three placements were considered:

1. **A fifth verb on `Forge`.** Cheapest in file count, but `Forge`'s own
   docstring promises the opposite, and every driver (`FakeForge`,
   `MemoryForge`) would have to implement an irreversible verb it has no
   business having.
2. **Straight on `GithubClient`**, as a `ConsumerBehavior` under `drivers/` —
   the `LabelIssueBehavior` precedent (ADR-0018). Adequate for adding a label;
   for writing to the default branch it leaves the contract implicit, with no
   place to state the safety properties.
3. **A new port.** The `IssueTracker` precedent (ADR-0022, invariant #26): a
   third GitHub-touching verb got its own port rather than crowding `Forge`,
   precisely because "opens a PR", "relabels an issue" and "creates an issue"
   are different capabilities with different blast radii.

## Decision

**Merging is a fourth port, `ports/pr_merge.py::PullRequestMerger`** — distinct
from `Forge` (opens PRs), `MergeChecker` (reads PR state) and `IssueTracker`
(creates issues).

Three consequences follow, and they are the point of the choice:

- **The relaxation is explicit and revocable.** A deployment that wants the old
  posture back wires no merger, and no workflow can merge anything. The
  founding constraint becomes a wiring decision rather than a property lost
  forever.
- **Orchestration cannot name it.** `test_architecture.py` forbids
  `dispatcher.py`/`consumer.py` from importing `ports.pr_merge`, mirroring
  invariants #27/#32/#34. "Only a bound finisher can merge" is structural, not
  conventional.
- **The safety properties live in the contract**, where every driver inherits
  them, rather than in one driver's implementation.

### The safety properties belong to the port

**1. The merge is pinned to a sha.** `merge()` takes `expected_sha` — the head
the reviewer actually read — and a driver MUST refuse when the PR's head has
moved past it. This is the load-bearing guarantee: it makes merging code that
no agent reviewed structurally impossible, rather than merely unlikely. GitHub
implements it natively (`PUT /pulls/{n}/merge` with `sha`, 409 on mismatch), so
the cost is one field.

**2. A refusal is not a failure.** "The head moved", "a required check went
red", "branch protection said no" are ordinary states of the world, raised as
`MergeRefused` and settled benignly; the next scan re-reviews the new head.
Only a genuine fault (auth, network, 5xx) raises `MergeError` and fails the
task. `MergeRefused` subclasses `MergeError` so that a driver author who
forgets the distinction fails safe — nothing merges — rather than merging by
accident.

### The operator sets the bar, not the agent

The `merge-review` persona writes a review ending in a fenced json block
carrying a **confidence** (`harness/merge_verdict.py`, the twin of
`issue_drafts.py`). The `merge-pr` finisher compares it against
`min_confidence` **from the workflow binding** and decides.

This is invariant #9's philosophy — *the commit is done by the driver, not the
LLM* — carried to its most consequential case. The agent supplies a judgement;
it never supplies the threshold it is judged against, and it has no tool that
can merge. A persona that learns to write `"confidence": 1.0` on everything
still cannot lower the gate; only an operator editing the binding can.

Splitting *judgement* from *threshold* is also what makes the feature tunable
without prompt surgery: raising the bar is a one-field edit in a JSON file, not
a rewrite of a persona whose behavior would then have to be re-validated.

### It ships withheld — by `dry_run`, not by a missing file

`AUTOMERGE_DEFINITION`'s binding sets `dry_run: true`. The step runs, reviews,
and records what it *would* have merged, on the board and in the task's data —
but merges nothing until an operator flips one field.

Configuring a Process is not the same as trusting it. The dry-run period is how
an operator accumulates evidence about *this* persona on *their* PRs before
granting it the button, and it costs one boolean to provide.

`harness init` seeds all three pieces: `workflows/automerge.json`,
`agents/merge-review.json` **and** `processes/automerge.json`. One Process
covers every repository — `GithubMergeableCheck.evaluate()` iterates
`RepositoryRegistry.names()`, so there is nothing per-repo to author and adding
a repo to `repos.json` puts it under review automatically.

This revises the decision as originally accepted, which seeded the workflow and
the persona but deliberately **no** Process, reasoning that automerging is a
posture rather than a queue that needs draining. That held only while "the
operator must author a file" was the sole safety gate. It no longer is:
`dry_run` is the real gate and a strictly better one, because it exercises the
whole path on real PRs and *shows* the operator what this persona would have
done — which authoring a file from scratch never did. So the Process ships and
the withholding moves entirely to `dry_run`. The seeder never clobbers an
existing file, exactly like the autoheal one.

Seeding a `github-*` Process by default only stays compatible with the
harness's "no token is not fatal" promise because the `github-mergeable`
factory raises `MissingCredential`, which `FilesystemProcessRepository.build()`
skips with a warning rather than failing the run. Without that, this one seeded
file would make every tokenless run exit 2.

### GitHub's `clean` is the gate we defer to

`GithubMergeableCheck` only considers PRs GitHub reports as `mergeable_state ==
"clean"` — its own verdict that the PR merges cleanly, every *required* check
is green and every *required* review is present. The harness therefore never
re-implements branch protection; an operator tightens the automerge gate by
tightening the repo's protection rules, where that belongs.

The check is the exact complement of `GithubConflictsCheck` (ADR-0018's
`github-conflicts` action): the two partition the same PR list with no overlap
— `behind` → update the branch, `dirty` → the resolver workflow, `clean` → this
one. Two further exclusions are the check's own and both fail closed: a draft
PR is never a candidate, and any PR carrying `harness:no-automerge` is vetoed
— a per-PR override a human can apply with no config change.

## Consequences

- A fourth GitHub-touching port, and a fourth entry in the "unknown to
  orchestration" family of architecture tests (invariant #44).
- The seeded Process must declare `"dedup": "per-state"`, and that is
  load-bearing rather than stylistic: the check emits one observation *per
  candidate PR*, and the default `per-interval` collapses every observation in
  a tick onto one key — three mergeable PRs would yield one review, silently.
  `per-state` keys each task on `slug:pr:head_sha`, which is also what
  re-reviews a re-pushed PR and leaves an unchanged one alone.
- `GithubClient` gains a real `merge_pull_request` verb. Its `FakeGithubClient`
  test helper of the same name — which simulated *GitHub* merging a PR, a
  different thing — was renamed `mark_merged`.
- `PullRequestInfo` gains `title`/`body`/`labels`/`draft`, all defaulted, so
  every existing construction site compiles unchanged.
- **The asymmetry is deliberate and load-bearing**: many paths refuse to merge
  (no source, no sha, unreadable verdict, confidence below the bar, a moved
  head), and none merges by accident. Wrongly refusing costs a human one click;
  wrongly merging costs the default branch. The tests assert the *absence* of a
  merge on every refusal path, not merely a tidy summary string.
- Not addressed here, and deliberately left for a later increment: reacting to
  review comments on the PR, posting the review back to GitHub as an actual
  PR review, and consulting live tasks so a PR whose own harness task is still
  in flight is skipped.
