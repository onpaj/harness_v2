# ADR-0012: StageOutputView, a third read-only UI surface

Status: Accepted

## Context

`BoardView` shows *where* a task is and `ArtifactView` shows *what it
produced* — but neither shows what a currently running agent step is doing
right now, which matters most for the steps that take the longest (a `claude
-p` invocation can run for minutes). Without a live surface, an operator
watching the board during a long-running step has no signal beyond "still
running" until the step finishes and an artifact appears. No existing
invariant in `CLAUDE.md` names this capability before this ADR — it is the
first place the decision gets written down, not a grounding of prior text.

## Decision

`ports/logs.py`'s `StageOutputView` is a third, read-only UI port, alongside
`BoardView` and `ArtifactView`: `tail(task_id)` returns the lines buffered so
far, `subscribe(task_id)` streams them live (replaying the buffer first, then
new lines, completing when the stage ends). `drivers/stage_output.py`'s
`StageOutputProjection` is both an `EventSink` and a `StageOutputView` — fed
from the same event stream as the board's projection, exactly the way
`BoardProjection` is. It is deliberately **live-only**: a `claimed` event resets
the buffer for a fresh attempt, and a `consumed`/`failed` event clears it
entirely — nothing here is persisted, because the durable audit trail is the
committed artifacts (ADR-0006), not this ring buffer. `behaviors/agent.py`'s
`ClaudeCliBehavior` is the only emitter: it tags each rendered line the agent
runner streams with `task_id`/`step`/`attempt` and emits a `stage_output`
event — carrying `task_id`, not `task`/`queue` (invariant #7's requirement is
for task-*movement* events; this one is neither a movement nor consumed by the
board projection, so it does not need to satisfy that shape). `api/routes.py`
wires `/api/tasks/{id}/output/events` to it as a server-sent-events stream.

## Consequences

- The board's task-detail view can show a live tail of what an in-flight step
  is producing, without either the board or `StageOutputView` knowing that the
  producer is a subprocess running `claude -p` — the same `AgentRunner`
  abstraction (ADR-0007) that hides the CLI details from the behavior also
  keeps them out of the UI.
- A slow-consuming SSE subscriber cannot block the orchestration loop: the ring
  buffer drops the oldest line rather than backpressuring the emitter, so the
  live tail is best-effort by design.
- Because the buffer is cleared when a stage ends, restarting the harness (or
  simply waiting past a step's completion) loses that stage's live output —
  which is intentional scope, not a gap: anyone who needs the durable record
  reads the committed artifact instead.
