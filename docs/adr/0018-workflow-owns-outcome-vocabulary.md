# ADR-0018: The workflow owns the outcome vocabulary

Status: Accepted

## Context

`Outcome` was a closed enum with exactly two members, `DONE` and
`REQUEST_CHANGES` (`models.py`). Every behavior in the system — regardless of
step or persona — could report only one of those two values, even though the
routing layer underneath was already outcome-agnostic: `Transition.on` is a
free string matched by equality in `Workflow.target`, and `route()` never
cared what the string spelled. The enum was the only thing standing between
the graph and a richer vocabulary — a triage step forking `ui` vs `backend`, a
review that can `approve` / `request_changes` / `escalate`, was inexpressible
not because the router couldn't follow such an edge, but because no behavior
could ever emit the outcome that would drive it.

Worse, the vocabulary a step could emit was declared **twice**, by hand, with
nothing keeping the two in sync:

- the workflow's transitions (`{from, on, to}`) — the edges the router
  actually follows;
- the persona's `AgentSpec.allowed_outcomes` — the set the prompt lists and
  the runner enforces at the verdict boundary.

`harness agent init`'s `_allowed_outcomes_for` derived the second from the
first, but only once, at generation time, frozen into the persona file on
disk. Any later edit to the workflow's edges — a new outcome added, an old one
retired — left the persona's copy stale: the agent was never told about a new
outcome the graph could route on, or was still offered one the graph no
longer handled (which, under the old closed enum, could silently reach
`route()` and fall through to `Finished()` with no matching edge). A snapshot
masquerading as a live declaration is exactly the kind of drift bug that gets
worse the longer a workflow lives.

## Decision

The workflow is the single, live source of truth for a step's outcome
vocabulary; the persona's copy demotes to a fallback for the one case where no
workflow exists to ask.

- `Outcome` is no longer a closed enum. `BehaviorResult.outcome` and
  `AgentRun.outcome` are plain, non-empty strings. `DONE = "done"` and
  `REQUEST_CHANGES = "request_changes"` survive as module-level string
  constants — the two names every existing behavior already used — so call
  sites keep referring to them by name, not by literal; nothing about how
  those two outcomes behave has changed.
- `Workflow.outcomes_for(step) -> tuple[str, ...]` is the live, authoritative
  vocabulary: the unique `on` values of the edges leaving `step`, in
  definition order, derived from the graph itself every time it is asked —
  never frozen. `ClaudeCliBehavior` resolves the task's workflow at run time
  and feeds this derived set to **both** the prompt and the runner's
  enforcement, by constructing the agent's spec as
  `replace(self._spec, allowed_outcomes=derived)`. One source, two uses, no
  drift — see CLAUDE.md invariant #42.
- `AgentSpec.allowed_outcomes` keeps its field and its default (`("done",)`),
  but its meaning narrows to the **workflow-less fallback**: a task carrying
  only a `step` and no `workflow_template`, an unresolvable workflow
  reference, or a step the workflow declares no outgoing edges for. The
  healer and the resolver's own steps run this path today, and continue to.
  `harness agent init` keeps seeding it — harmless, now advisory, so a
  persona file written before this change behaves exactly as before.
- Two prompt-only hints give a workflow author room to steer the agent's
  *choice* without adding any routing mechanism: `Transition.hint` (per
  outcome, "pick this when …") and `Workflow.descriptions` (per step, "what
  this pipeline needs from you here"). Both are read only by
  `ClaudeCliBehavior`'s prompt composition; `route()` never touches either —
  invariant #4 holds exactly as before.
- The consumer's invalid-result guard becomes a shape check — non-empty
  string — never a branch on the outcome's value. Invariant #2 is unaffected:
  the consumer still decides nothing; it is simply no longer allowed to
  assume there are only two possible values to not-decide between.

## Consequences

- A data-driven conditional pipeline — a triage step forking `ui`/`backend`
  to skip a design step entirely for backend-only work — needs **zero new
  mechanism**. It is an ordinary richer set of transitions plus a hint; the
  skip is a routing outcome the triage step emits, not a property of the
  step being skipped.
- The drift bug is now structurally impossible: there is exactly one place
  that declares what a step may emit — the workflow's own edges — so a graph
  edit and the persona's vocabulary can no longer disagree with each other.
- `route()` and the dispatcher are completely untouched by this decision.
  They still decide only on `(status, last_outcome)` and workflow presence;
  the outcome being an open string rather than a closed enum changes nothing
  about how a match is made. Invariant #4 holds.
- The enforcement point does not move: an out-of-vocabulary verdict is still
  rejected at the runner boundary, against `spec.allowed_outcomes` — it is
  simply fed the live, workflow-derived set instead of a frozen snapshot, so
  a stale persona can no longer let a bad outcome through, nor reject a good
  one the graph has since grown to support.
