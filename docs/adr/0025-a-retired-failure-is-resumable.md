# ADR-0025: A retired failure is resumable at the step it died at

Status: Accepted

Additive to ADR-0024 per ADR-0000's convention — that decision (a claimed
failure lands in `done`, a declined one stays in `failed`) is unchanged and
still authoritative.

## Context

ADR-0024 retires a claimed failure into `done/` with `status = END` and names
its own cost: "`done` holds two kinds of ending. Distinguishing them is a
history read (`actor == "failed-tasks"`), not a column read."

Nothing performed that read. Six `development` tasks that timed out at their
implementation step sat in `done` looking exactly like completions — worse than
neutral, because the card's accent chain reads the `last_outcome == "done"` left
over from the step that passed *before* the timeout and painted them green, in a
column whose own template comment forbids exactly that lie.

They were also unrecoverable. `Restart` is gated on `status == "failed"` and
`TaskControlService.restart` searches only `failed/`, so neither the button nor
a hand-rolled POST reached them. And a from-scratch restart would have been the
wrong tool anyway: `plan`, `design` and `architecture` had each succeeded and
written an artifact, and invariant #30 means the worktree still held all three.

## Decision

**A terminal failure can be resumed at the step it died at, and the board says
which tasks those are.**

- **`TaskControl` gains `resume`.** It is not a routing decision. It writes the
  `(status, lastOutcome)` pair the task held *before* it was dispatched into the
  failing step and returns it to the **inbox**; `route()` re-derives the failing
  step from that pair and the dispatcher places it, exactly as the first time.
  No queue is written directly (invariant #3), `route()` is unchanged, and a
  failure at the workflow's start step degenerates to `(None, None)` — which is
  what `restart` already produces — with no special case.
- **The failing step comes from history, never from `status`.** By the time a
  failure settles, `status` is the word `failed`; the step's name survives only
  as the `from_step` of the `-> failed` entry. Three pure functions in `models`
  expose it: `failure_trace` (how it failed and where to rewind), and
  `is_retired_failure` / `resumable_failure` — two separate predicates, because
  "is this `done` task a healed failure" and "was this task's current terminal
  position reached by failing" are different questions with different answers
  for a declined failure in `failed/` and for a completion that failed earlier
  in its life.
- **The distinction is a card marker, not a column.** The `healed` column stays
  retired. A retired failure suppresses the `is-done` accent, carries a
  `retired` badge naming its failed step, and is findable by the board's
  fulltext filter.
- **`resume` spans both terminal queues.** A declined failure in `failed/` has
  the same good-steps-already-done problem; `restart` remains the right tool
  when the prior artifacts are themselves suspect.

## Consequences

- **The healer's own diagnosis gets more accurate for free.** It rendered
  `task.status` as the failing step, so every heal request ever filed said
  `failed at step 'failed'`. It now reads the same trace.
- **Invariant #30 loses its justification's absoluteness.** "The original
  worktree is permanently inert once its task reaches a terminal state" is why
  the harness never cleans up worktrees; a resume revives one. The mechanism
  copes — invariant #31's reattach path resets only when local `HEAD` is behind
  or equal to `origin/<branch>` and raises on divergence — but if another task
  force-checked-out the same branch into a second worktree meanwhile, resuming
  the original leaves two live worktrees on one branch. Nothing here changes
  worktree handling; it makes the case reachable.
- **A resumed step writes a fresh artifact.** `next_attempt` counts existing
  `<step>-NN.md` files, so the record of the failed attempt is kept, consistent
  with ADR-0006.
- **The affordance has the retention window's lifetime.** Once terminal tasks
  are archived for age, an archived task is off every board column and its
  Resume button with it.
- **Two kinds of ending still share one column, on purpose.** ADR-0024's trade
  stands: the board answers "is anyone still on the hook for this?", and both
  answer no. What changes is that it no longer answers "did this succeed?"
  wrongly.
