# ADR-0018: A sink reflects, a step/finisher acts

Status: Accepted

## Context

Unifying outbound reflection onto one `effective_sink_kind` routing rule
(`data.sink.kind`, falling back to `data.source.kind`) makes `github` a plain
sink kind alongside `none`/`slack` — the same mechanism `GithubLabelReflector`
and `SlackWebhookSink` both route through. But the harness already has a
second GitHub-facing mechanism that could be mistaken for the same thing:
`open-pr` landing, a finisher (ADR-0016) that calls
`Forge.open_pull_request(...)`. Both "change a label" and "open a PR" call the
GitHub API on behalf of a task; nothing in the code so far states *why* one is
a sink and the other is a finisher, so a future author could plausibly reach
for either mechanism for a new GitHub-touching feature and land it in the
wrong place.

## Decision

The boundary is not "which API it calls" but **what happens if it fails and
whether it decides where the task goes next**:

- **A step or finisher does work and can fail the task.** `open-pr` calls
  `Forge.open_pull_request`, which raises `ForgeError` on a missing
  `GITHUB_TOKEN`, a non-GitHub origin, or an API error — and that failure
  lands the task in `failed/` (ADR-0009, invariant #3's landing exception).
  A finisher is squarely on the routing path: its outcome is what the
  dispatcher acts on next.
- **A sink only reflects already-decided state and can never fail or route a
  task.** `report_progress`/`finish` return `None`; `CompositeEventSink` and
  `SourcePoller.tick` isolate any exception a sink driver raises so it can
  never affect dispatch (invariant #21). `GithubLabelReflector` swapping a
  label is best-effort and idempotent — a failed label write is silently
  absorbed, never surfaced as a task failure.

This is why GitHub labels are a `TaskSource`/reflection concept (this task's
unification) while PR creation stays a finisher/`ConsumerBehavior` concept,
even though both are "GitHub": the two mechanisms are separated by
authority over the task's fate, not by which external system they touch.

## Consequences

- A future GitHub-touching feature is placed by asking "can this fail the
  task, or does it only ever mirror a decision already made?" — not by
  whether it happens to call the same API as an existing sink or finisher.
- `github`-as-sink-kind (this task) and `open-pr`-as-finisher-kind (ADR-0016)
  can now both exist without the routing unification blurring the line
  between them: widening `_ACCEPTED_SINK_KINDS` to include `github` does not
  imply GitHub PR creation could ever become a sink, and vice versa.
- No code changes from this ADR alone — it records the boundary the
  `effective_sink_kind` unification makes it newly possible to conflate, so
  the next author doesn't have to re-derive it from first principles.
