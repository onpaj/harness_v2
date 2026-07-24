# The triage Process — `claimed_label` + the `label-issue` finisher

Status: approved
Date: 2026-07-23

## Goal

Let a **triage Process** sit upstream of the existing GitHub-issues ingestion
Process: it scans issues under a triage label, runs a `product-manager`
persona against each to judge whether it's well-defined and in scope, and
relabels the issue by verdict — `harness:todo` (the *existing*, untouched
ingestion Process picks it up next) or `harness:needs-info` (parked for a
human to fill in). This spec covers the two small, orthogonal additions that
make it possible and documents the templates an operator copies into their own
`~/harness-root/{processes,workflows,agents}/` to run it — none of these
files are seeded by `harness init`; a triage Process is opt-in, not every
deployment runs one.

## The two additions

**1. `claimed_label` as a second `github-issues` action param.**
`GithubIssuesCheck` already accepted `claimed_label` in code (default
`"harness:queued"`); only `label` was exposed through `processes/*.json`.
`cli.py`'s `github_issues_factory` now reads both, so a triage process can
scan `harness:triage` and claim into `harness:validating` — a label pair
entirely distinct from the ingestion process's `harness:todo`/`harness:queued`
pair, with no shared code path between the two Processes:

```json
{
  "action": {
    "check": "github-issues",
    "params": { "label": "harness:triage", "claimed_label": "harness:validating" }
  }
}
```

`label`/`claimed_label` are both validated as strings at `compile_process`
time. `FilesystemProcessRepository.build()` additionally rejects, at startup,
a batch where two `github-issues` processes share the same literal
`label`/`claimed_label` value (in either role) — naming both offending files.
This is a **static, exact-string check only**: it cannot catch a typo, a
collision mediated through an agent-authored label, or two processes that are
logically incompatible without sharing a literal string. That residual
footgun is accepted, not solved, in this increment; `FilesystemProcessAdmin`
(single-file validation) can't run this check at all, so a hand-edited or
admin-written triage file only gets it at the next full `harness run` startup.

**2. `label-issue` — a finisher that wraps (not replaces) a step's own
behavior.** See ADR-0018 for the full mechanism. In short: a workflow step's
`finishers` binding can now be a structured object, not just a bare kind
string:

```json
{
  "finishers": {
    "triage": {
      "kind": "label-issue",
      "labels": { "done": "harness:todo", "request_changes": "harness:needs-info" }
    }
  }
}
```

`label-issue` lets the step's own agent (the PM persona) run first, then
reads the `BehaviorResult.outcome` it returned and calls
`GithubClient.add_label` on `task.data.source`'s issue with the mapped label.
The mapping is keyed by `Outcome.value` — `"done"`/`"request_changes"` — not
by arbitrary words like `"approve"`/`"reject"`: `Outcome` is a closed,
two-member enum enforced by `Consumer.tick` and the verdict parser, so the PM
persona reuses `done`/`request_changes` exactly as the `reviewer` persona
already does for the same shape of decision (a done/request_changes gate one
level upstream, over an issue instead of a diff). A missing `data.source` or
an outcome absent from the mapping is a no-op — the summary gets a note, the
task still routes normally, `add_label` is simply never called.

`label-issue` is only registered when `GITHUB_TOKEN` is configured (`cli.py`'s
`_run` builds one `GithubClient` and threads it into both `_process_sources`
and the finisher registry). Without a token, a workflow binding a step to
`label-issue` fails at `build()` through the pre-existing "unknown finisher
kind" error — no new error path.

## Templates (documented, not `harness init`-seeded)

Copy these into the harness root to run a triage Process. The step name must
be `triage` for `agents/triage.json` to be found (`AgentCatalog.get` is keyed
by step name, with no separate persona-name indirection) — the
product-manager identity lives in the prompt content, not the filename.

`agents/triage.json`:

```json
{
  "prompt": "You are the harness's product-manager gatekeeper for inbound GitHub issues. An issue reaches you carrying its title and body under task.data. Judge it against three questions: (1) is it clearly and completely defined — could someone start work without asking clarifying questions? (2) is it understandable — is the problem and desired outcome stated in plain language? (3) does it fit the application's vision and have a plausible implementation path in this repository (you may read the checked-out code to judge fit)? Return outcome 'done' if all three hold — this issue is ready to become harness:todo work. Return 'request_changes' if any one doesn't, and say in your summary specifically what's missing (e.g. 'needs: acceptance criteria', 'needs: scope narrowed to one repo').",
  "model": null,
  "fallback_model": null,
  "allowed_tools": [],
  "allowed_outcomes": ["done", "request_changes"],
  "timeout": null
}
```

`workflows/triage.json` — a named, one-step workflow (required, not
workflow-less: a `finishers` binding only exists on a `Workflow`, so the
target must be a served workflow, not a bare `{"step": "triage"}`):

```json
{
  "name": "triage",
  "start": "triage",
  "transitions": [
    { "from": "triage", "on": "done", "to": "end" },
    { "from": "triage", "on": "request_changes", "to": "end" }
  ],
  "finishers": {
    "triage": {
      "kind": "label-issue",
      "labels": { "done": "harness:todo", "request_changes": "harness:needs-info" }
    }
  }
}
```

`processes/triage.json`:

```json
{
  "trigger": { "interval": "5m" },
  "action": {
    "check": "github-issues",
    "params": { "label": "harness:triage", "claimed_label": "harness:validating" }
  },
  "target": { "workflow": "triage" },
  "dedup": "per-state",
  "sink": { "kind": "none" }
}
```

`harness run` must serve the `triage` workflow alongside the primary/ingestion
one (`--workflow default --workflow triage`, or however the deployment names
served workflows) for the process to be live — an operator wiring choice, not
code.

## End-to-end flow

`GithubIssuesCheck` (scanning `harness:triage`) claims an issue by swapping to
`harness:validating` — the same at-most-once claim mechanics
(label swap + in-process ledger) the ingestion process already uses, reused
verbatim. It emits a task with `data.source` and `repository` set (the check's
existing `Observation.repository` field, so the triage task is
worktree-attached with no new code — the PM persona may optionally read the
repository's code to judge implementation fit). `ClaudeCliBehavior` (built
from `agents/triage.json`) runs the PM persona; `label-issue` wraps it, reads
the `done`/`request_changes` verdict, and calls `add_label` with
`harness:todo`/`harness:needs-info` on the same issue — the `harness:
validating` label is not removed (labels are additive here). The task
finishes to `done/` either way. The ingestion Process (scanning
`harness:todo`) picks up an approved issue on its own next tick — no code
path between the two Processes; the hand-off is entirely a GitHub label.

## Out of scope

- **Issue comments carrying the PM's reasoning** (e.g. "needs: acceptance
  criteria" as a comment, not just a label). `GithubClient` has no comment
  verb today. Seam: `add_comment` would be a new `GithubClient` method plus
  one more line in `LabelIssueBehavior.run`, nothing else would need to
  change.
- **Process-admin UI support** for editing the structured `finishers` object
  or `claimed_label`. The write path already accepts both (it validates
  through the same `compile_process`/`_parse_workflow` this spec extends), so
  no admin-specific code was needed to keep the write path working — a
  dedicated form field is a follow-up.
- **Any change to the ingestion `github-issues` process/workflow.** It keeps
  scanning `harness:todo` → `harness:queued` exactly as before; the triage
  process is purely upstream and invisible to it.
