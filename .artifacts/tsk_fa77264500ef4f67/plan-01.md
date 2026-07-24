# Plan: unify outbound reflection on one effective-sink-kind routing rule

## Grounding note

This worktree (`harness/tsk_fa77264500ef4f67`) is **75 commits behind `origin/main`**
and is missing the entire Process/sink/finisher subsystem the task references —
`drivers/slack_sink.py`, `drivers/fs_processes.py`, `ports/process_admin.py`,
`docs/adr/0015..0017-*.md`, and the current `github_source.py`/`source_reflector.py`
all exist only on `origin/main`. Everything below (file contents, docstrings,
test names, invariant numbering) is read directly off `origin/main` at
`e4485d6`, not off this stale branch. **FR-0 merges this worktree up to
`origin/main` before anything else lands** — the same first step every recent
plan in this history (`9a0e548`, `a70512e`, `66dd350`, …) has had to take for
the same reason.

## Summary

Today `GithubLabelReflector` (outbound GitHub-label reflection) and
`SlackWebhookSink` (outbound Slack reflection) are structurally identical
`TaskSource`s that differ only in their `_mine` routing key: the former reads
`task.data.source.kind`+`repo`, the latter reads `task.data.sink.kind`. This
task collapses that into one routing rule — an **effective sink kind**
(`data.sink.kind` if present, else `data.source.kind`) — so `github`, `slack`
and `none` become plain sink kinds, and "reflect back to the GitHub origin" is
simply the default case of the same mechanism instead of a separate concept.

## Context

The Processes spec (`docs/superpowers/specs/2026-07-22-processes-design.md`,
"The sink seam") already states the target design: *"Route reflection by a
destination identity, defaulted to the source... when a real sink lands, a
Process stamps a `data.sink` slot distinct from `data.source`, and the
reflector sink routes on it, defaulting to `source.kind` when `data.sink` is
absent."* That defaulting was written down as an intention but never actually
implemented as shared code — `GithubLabelReflector._mine` still reads
`data.source` directly and has no notion of `data.sink` at all, and
`SlackWebhookSink._mine` has no fallback to `data.source`. CLAUDE.md invariant
#40 documents the two mechanisms as "`none` or `slack`... distinct from the
inbound origin", without mentioning `github` as a sink kind or the
defaulting rule. This task cashes in the seam the spec already promised and
brings the invariant's wording in line with the unified mechanism.

The one asymmetry that must survive the unification: GitHub-label reflection
inherently needs a **target issue**, and a task's only issue is its origin
issue (`data.source.issue`). So `github` can never be a destination
independent of origin the way `slack` is — it is the *degenerate, same-as-
origin* case, which is exactly why the effective kind defaults to
`source.kind` rather than requiring an explicit `sink`.

## Functional requirements

**FR-0 — Ground the branch on `origin/main`.**
Merge/rebase this worktree onto `origin/main` (`e4485d6`) so the Process/sink
subsystem, `docs/adr/0015-0017`, and current `github_source.py`/
`source_reflector.py` actually exist to modify. Acceptance: `pytest -q` and
`tests/test_architecture.py` pass on the merged tree before any FR-1+ work
starts.

**FR-1 — A shared `effective_sink_kind` helper.**
Add a function to `ports/source.py` (beside `dedup_key`, which both drivers
already import from there):
```python
def effective_sink_kind(task: Task) -> str | None:
    sink = task.data.get("sink")
    if isinstance(sink, dict) and sink.get("kind"):
        return sink["kind"]
    return task.data.get("source", {}).get("kind")
```
Acceptance criteria:
- Returns `data.sink.kind` when `data.sink` is a dict with a truthy `kind`.
- Falls back to `data.source.kind` (or `None`) otherwise — including when
  `sink` is absent, `None`, `{}`, or `{"kind": None}`.
- Unit tests in a new `tests/test_effective_sink_kind.py` (or appended to
  `test_source.py` if that file exists post-merge) cover: sink present,
  sink absent + source present, neither present, sink present but empty.

**FR-2 — Route `GithubLabelReflector` through the helper.**
Change `_mine` in `drivers/github_source.py`:
```python
def _mine(self, task: Task) -> bool:
    if effective_sink_kind(task) != self.kind:
        return False
    return task.data.get("source", {}).get("repo") == self._repo
```
The repo/issue are still resolved from `data.source` — only the `kind`
comparison is routed through the shared helper.
Acceptance criteria:
- All existing `tests/test_github_source.py` reflector tests pass unmodified
  (`test_task_without_source_is_noop`, `test_task_from_another_repo_is_not_mine`,
  `test_reflector_report_progress_known_step_sets_step_label`, etc.) — these
  exercise the default-to-source path with no `data.sink` present.
- New test: a task with an explicit `data.sink = {"kind": "github"}` (and a
  `data.source.kind` that is *not* `"github"`, e.g. a scheduled/process
  origin) plus `data.source.repo`/`issue` matching the reflector's configured
  repo is treated as mine (explicit-sink path).
- New test: a GitHub-origin task (`data.source.kind == "github"`, matching
  repo) that also carries an explicit `data.sink = {"kind": "slack"}` is
  **not** reflected by `GithubLabelReflector` — explicit sink overrides the
  default (documents that `github` is genuinely a fallback, not an
  always-on side channel).

**FR-3 — Route `SlackWebhookSink` through the same helper.**
Change `_mine` in `drivers/slack_sink.py` to
`return effective_sink_kind(task) == self.kind`.
Acceptance criteria:
- All existing `tests/test_slack_sink.py` tests pass unmodified — every
  fixture there sets `data.sink.kind` explicitly, so the fallback path is
  never exercised by them and behavior is unchanged.
- New test: a task with `data.source = {"kind": "slack"}` and no explicit
  `data.sink` is matched by `SlackWebhookSink` (documents default-to-source
  symmetry, even though no Slack-origin `TaskSource` producer exists yet —
  this is a routing-contract test, not a claim that Slack ingestion exists).

**FR-4 — `github` becomes a selectable Process sink kind.**
In `drivers/fs_processes.py`: add `"github"` to `_ACCEPTED_SINK_KINDS`
(`{"none", "slack", "github"}`) and update its docstring/comment. In
`ports/process_admin.py`: update `FilesystemProcessAdmin.sink_kinds()` to
return `("github", "none", "slack")` (sorted, matching `check_names()`'s
`tuple(sorted(...))` convention) and update its docstring.
Acceptance criteria:
- `compile_process` accepts `{"sink": {"kind": "github"}}` without raising
  `ProcessValidationError`; the compiled `ScheduledTrigger.sink == {"kind":
  "github"}` and a fired task carries `data.sink == {"kind": "github"}`
  (mirrors `test_slack_sink_is_accepted_and_stamped_onto_tasks` in
  `test_fs_processes.py`).
- `test_check_names_and_sink_kinds` in `test_fs_process_admin.py` updated to
  assert `admin.sink_kinds() == ("github", "none", "slack")`.
- `test_fs_process_admin.py` gains a `test_github_sink_round_trips` mirroring
  `test_slack_sink_round_trips`.
- `test_unknown_sink_raises_naming_the_file` (sink kind `"teams"`) still
  raises — the accepted set grew, it didn't open up.
- `process_form.html`'s sink radio-group already has a generic `{% else %}`
  branch (title = the raw kind, generic description) — `github` renders
  through it with no template change required for correctness. Whether to
  add a dedicated title/description (like `none`/`slack` get) plus caption
  the caveat below is left to the design step (see Open Questions).

**FR-5 — `GithubTaskSource` composition stays untouched.**
No code change to `GithubTaskSource.__init__`'s
`self._reflector = GithubLabelReflector(...)` construction or to `poll()`
(claim-by-label ingestion). Acceptance: existing `test_github_source.py`
source-side tests (`test_poll_claims_issue_and_builds_task`,
`test_poll_dedups_claimed_issue_despite_label_lag`, etc.) pass unmodified —
ingestion is orthogonal to the reflection-routing change.

**FR-6 — Docs and invariant updates.**
- `docs/superpowers/specs/2026-07-22-processes-design.md`, "The sink seam":
  replace the "partially realized" update note with one recording that
  `effective_sink_kind` now implements the promised default-to-source
  routing, that both `GithubLabelReflector` and `SlackWebhookSink` route
  through it, and that `github` is an accepted `sink.kind` in the schema
  (still the degenerate same-as-origin case, not an independent
  destination — repeat the repo/issue caveat from FR-2).
- `CLAUDE.md` invariant #40: reword from "`none` or `slack`... distinct from
  the inbound origin" to state the single rule — the effective sink kind
  (`data.sink.kind`, defaulting to `data.source.kind`) selects the
  destination; accepted kinds are `none`/`slack`/`github`; `github` is the
  destination that happens to coincide with the origin by construction
  (needs `data.source.issue`), which is exactly why it is the default rather
  than an independently addressable kind the way `slack` is.
- A short ADR (default: new `docs/adr/0018-sink-reflects-a-step-acts.md`,
  next free number after `0017`) recording the boundary: *a step/finisher
  does work and can fail the task (e.g. `open-pr` landing); a sink only
  reflects state and can never fail or route a task.* This is what keeps
  "open a PR" a finisher concept and "change a label" a sink concept even
  though both ultimately call GitHub. See Open Questions for the
  new-ADR-vs-addendum choice.

## Non-functional requirements

- **No behavior change for existing GitHub-origin / explicit-Slack-sink
  tasks** — FR-2/FR-3's existing-test-suite-unmodified acceptance criteria
  are the regression guard.
- **No new I/O, no new blocking calls** — `effective_sink_kind` is a pure
  dict lookup; routing stays O(1) per sink per event, identical to today.
- **Idempotency/isolation preserved** — invariant #21 (report-progress twice
  is a no-op) and `CompositeEventSink`'s per-sink isolation are untouched;
  this task only changes *which* sink a task's events reach, not how each
  sink behaves once matched.
- **No new secrets** — `github`'s target (repo/issue) continues to come from
  `data.source`, never a new config surface.

## Data model

No change to `Task` itself. Clarifying the existing shape:
- `task.data.source: {kind, repo?, issue?, url?}` — the task's origin,
  written once at creation (by `GithubTaskSource.poll()` or left absent for
  a hand-submitted/process-born task). Never written by a sink.
- `task.data.sink: {kind}` — the task's explicit destination override,
  written once at creation by `ScheduledTrigger._task_for` for a
  non-`none` Process sink. Never written by ingestion.
- **New derived concept, no new field**: *effective sink kind* =
  `data.sink.kind` if present, else `data.source.kind`. This is what
  `_mine` on every reflecting `TaskSource` compares against its own `kind`.

## Interfaces

- No new endpoints. `ports/process_admin.py::ProcessAdmin.sink_kinds()`'s
  return value grows by one entry (`"github"`), consumed by the existing
  `GET`/`POST` process-form routes in `api/admin.py` and rendered by the
  existing generic option-card loop in `process_form.html`.
- No new CLI flags. `cli.py`'s `_github_reflectors`/`_slack_sinks` wiring is
  unchanged — both continue to construct their drivers exactly as before;
  only the drivers' internal `_mine` logic changes.

## Dependencies and scope

Depends on: FR-0 (merge to `origin/main`) landing first; nothing else in
flight touches `github_source.py`/`slack_sink.py`/`fs_processes.py` per the
current `origin/main` history.

**Out of scope** (explicitly, per the task):
- The stateful create-then-update Slack sink (message-id handle) and a
  dedicated `Reflector` port — both remain future refinement per the spec's
  existing bullet 3.
- **Wiring an actual, functional `github` sink for Process-authored tasks.**
  `ScheduledTrigger._task_for` never stamps `data.source` (a trigger
  reflects nothing outward inbound), so a Process that declares
  `{"sink": {"kind": "github"}}` today produces tasks with
  `data.sink.kind == "github"` but no `data.source.repo`/`issue` — FR-2's
  `_mine` would never match them, i.e. schema-level acceptance does not
  make the sink *functional* for a Process yet. This mirrors exactly how
  `slack` was accepted-but-unwired in the original Processes spec before a
  later increment shipped `SlackWebhookSink` and its `cli.py` wiring; making
  `github` functional for a Process needs a repo/issue association design
  (likely via the not-yet-built `github-issues` check/action) that is
  genuinely separate work. This task only makes the *routing rule* uniform
  and the *kind* schema-valid; it does not claim Process→GitHub-label
  reflection works end-to-end.

## Rough plan

1. **FR-0**: merge/rebase this worktree onto `origin/main`; confirm
   `.venv/bin/pytest -q` and `test_architecture.py` are green on the merged
   tree before touching anything.
2. **FR-1**: add `effective_sink_kind` to `ports/source.py` with its unit
   tests.
3. **FR-2 + FR-3**: switch both `_mine` implementations over, add the new
   default-vs-explicit routing tests, run `test_github_source.py` and
   `test_slack_sink.py` to confirm the existing suites are untouched.
4. **FR-4**: extend `_ACCEPTED_SINK_KINDS` and `sink_kinds()`, update the two
   test files' expectations, add the `github` round-trip/compile tests.
5. **FR-5**: run the full suite to confirm `GithubTaskSource` composition
   and ingestion tests are unaffected.
6. **FR-6**: update the processes-design spec's sink-seam note, CLAUDE.md
   invariant #40, and write the short ADR on the sink-vs-finisher boundary.
7. Run `.venv/bin/pytest -q` and `tests/test_architecture.py` one more time
   full-suite; confirm no test reads `data.source`/`data.sink` from
   `router.py`/`dispatcher.py` (grep, not just tests — invariant #19/#20
   guard).

## Open questions

- **New ADR-0018 vs. an addendum to ADR-0015?** Defaulted above to a new,
  short `0018-sink-reflects-a-step-acts.md` — it records one crisp boundary
  decision (sink vs. finisher), matching the granularity of `0016`/`0017`
  (each one decision, one file) rather than growing `0015`'s scope. The
  design step should confirm or override this.
- **`sink_kinds()` ordering**: defaulted to alphabetically sorted
  (`("github", "none", "slack")`) for consistency with `check_names()`.
  This is a visible (if minor) change to the admin form's option order and
  to the existing `test_check_names_and_sink_kinds` literal — flagging in
  case insertion order (`("none", "slack", "github")`) is preferred instead
  to minimize UI churn.
- **Should the Process form caveat the `github` option** (e.g. "only takes
  effect on tasks with a GitHub origin; a no-op for schedule/check-born
  tasks today") given the FR-4/out-of-scope note above, or is the generic
  `{% else %}` card description acceptable for this increment? Left to the
  design step; either is compatible with this plan's acceptance criteria.
