# Review: unify outbound reflection on one effective-sink-kind routing rule

## Verdict: done

## What I checked

Read plan-01/design-01/architecture-01/development-01, then independently
diffed `e4485d6..d346e14` file-by-file against what those documents specify,
rather than trusting development-01's summary.

**FR-0 (grounding).** Confirmed: `d346e14`'s parent history includes
`e4485d6` and all the intermediate commits; `drivers/slack_sink.py`,
`drivers/fs_processes.py`, `ports/process_admin.py` exist and match
`origin/main`'s shape before this task's edits. `.venv/bin/pytest -q` is
green (1233 passed, 1 skipped) and `tests/test_architecture.py` is green
(26 passed), run independently, not taken on faith.

**FR-1 (`effective_sink_kind`).** `ports/source.py` — exact function from
design-01, pure dict lookup, no I/O, placed beside `dedup_key`. Six unit
tests in new `tests/test_source.py` cover all four truth-table rows plus the
falsy/empty-dict edge cases.

**FR-2 (`GithubLabelReflector._mine`).** Routes the kind check through
`effective_sink_kind`; `_issue()` still reads `data.source.issue` directly,
preserving the "github can name a destination via the helper but can't
supply one" asymmetry the design calls for. All existing reflector tests
pass unmodified. Three new tests cover: explicit-sink-on-non-github-origin,
explicit-other-sink-overrides-github-default, and the architecture-01-flagged
`GithubIssuesCheck`-shaped regression case (built via the actual
`ScheduledTrigger._task_for` merge shape, not a hand-rolled fixture) — this
was the one correctness risk architecture-01 called out by name, and it's
covered exactly as required.

**FR-3 (`SlackWebhookSink._mine`).** One-liner, `effective_sink_kind(task) ==
self.kind`. Existing tests pass unmodified (all set `data.sink` explicitly);
new test covers the default-to-`source.kind` symmetry.

**FR-4 (`github` as a Process sink kind).** `_ACCEPTED_SINK_KINDS` widened,
`sink_kinds()` now `tuple(sorted(...))` → `("github", "none", "slack")` per
architecture-01's settled (not open) decision. `process_form.html` gained the
dedicated `github` branch using architecture-01's corrected copy ("No-op
unless the action populates a GitHub source (e.g. github-issues)"), not
design-01's original ("schedule or check-born") wording — correctly picking
up the architecture step's correction. `test_unknown_sink_raises_naming_the_file`
still rejects `"teams"`, confirming the set grew rather than opened up.

**FR-5 (`GithubTaskSource` untouched).** Confirmed via diff — no change to
`__init__`/`poll()`; ingestion tests pass unmodified.

**FR-6 (docs).** ADR-0018 states the sink-vs-finisher boundary exactly as
specified (can-it-fail-the-task, not which API it calls). CLAUDE.md
invariant #40 reworded to state the single effective-sink-kind rule,
`github`'s degenerate-same-as-origin nature, and cross-references ADR-0018.
The processes-design spec got a second dated blockquote appended to "The
sink seam" following the existing convention, rather than rewriting the
historical narrative or the other pre-existing stale `none`-only passages
elsewhere in the file — matches architecture-01's explicit instruction not
to over-correct.

## Invariants

- #19/#20: grep confirms `router.py`/`dispatcher.py`/`consumer.py` still
  read neither `data.source` nor `data.sink`.
- #21 (idempotent, non-blocking, isolated reflection): untouched — this task
  only changes which sink a task's events reach, not per-sink behavior.
- #40: reworded consistently with the shipped code, not just aspirationally.

## Assessment

Every acceptance criterion in the spec is met, the implementation follows
architecture-01's guidance precisely including its two corrections over
design-01 (form copy, the github-issues regression test), and the diff
introduces no scope creep — `_issue()`, `GithubTaskSource`, `_parse_sink`'s
validation logic, and `ScheduledTrigger` are all correctly left untouched
per the stated non-goals. No correctness, concurrency, or security issues
found. Test coverage is thorough and includes the one edge case
(github-issues-shaped task with no `sink` key) that would have been easy to
miss and hardest to catch in review. No changes requested.
