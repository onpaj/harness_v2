# Development: unify outbound reflection on one effective-sink-kind routing rule

## FR-0 — grounding

Rebased `harness/tsk_fa77264500ef4f67` onto `origin/main` (`git rebase
origin/main`) — fast-forward-shaped as architecture-01 predicted (only the
three artifact-writing commits existed on the branch, none touching code), no
conflicts. Set up the missing `.venv` (`python3.11 -m venv .venv && pip
install -e ".[dev]"` — this worktree had never had one). Confirmed
`.venv/bin/pytest -q` green (1221 passed, 1 skipped) on the merged tree before
touching anything, per the plan's hard gate.

## What changed

**`src/harness/ports/source.py`** — new `effective_sink_kind(task) -> str |
None`: returns `data.sink.kind` when `data.sink` is a dict with a truthy
`kind`, else falls back to `data.source.kind`. Pure dict lookup, no I/O,
placed beside `dedup_key` per design-01/architecture-01. Updated
`TaskSource`'s class docstring to describe routing via the effective kind
instead of `data.source.kind` directly.

**`src/harness/drivers/github_source.py`** — `GithubLabelReflector._mine` now
routes the kind comparison through `effective_sink_kind`, keeping the
instance-scoped repo check (`data.source.repo == self._repo`) and `_issue()`
reading `data.source.issue` directly, exactly as design-01 specifies. Updated
the class docstring to describe the effective-kind match and restate the
issue-resolution asymmetry.

**`src/harness/drivers/slack_sink.py`** — `SlackWebhookSink._mine` is now
`effective_sink_kind(task) == self.kind`. Updated the module and comment
prose to describe the shared default-to-source rule instead of "never
`data.source`".

**`src/harness/drivers/fs_processes.py`** — `_ACCEPTED_SINK_KINDS` widened to
`{"none", "slack", "github"}` with a docstring noting the degenerate-case
caveat; `_parse_sink`'s comment updated to name all three kinds.
`FilesystemProcessAdmin.sink_kinds()` now returns `tuple(sorted(_ACCEPTED_SINK_KINDS))`
— `("github", "none", "slack")` — matching `check_names()`'s convention, per
architecture-01's settled (not open) decision.

**`src/harness/ports/process_admin.py`** — `sink_kinds()`'s abstract-method
docstring updated to the new tuple and its sorted-alphabetical contract.

**`src/harness/api/templates/admin/process_form.html`** — added a dedicated
`{% elif option == 'github' %}` branch to the sink option-cards, using
architecture-01's corrected copy ("Reflects to the task's origin issue. No-op
unless the action populates a GitHub source (e.g. github-issues)."), not
design-01's original ("schedule or check-born") wording, since
`GithubIssuesCheck`-driven Processes already populate `data.source` today and
the original phrasing would have been actively wrong for that case. No CSS
change needed — `.option-cards` is `auto-fill`, confirmed by architecture-01.

**`docs/adr/0018-sink-reflects-a-step-acts.md`** (new) — records the
sink-vs-finisher boundary: a step/finisher does work and can fail the task
(`open-pr` → `ForgeError` → `failed/`); a sink only reflects already-decided
state and can never fail or route a task (`report_progress`/`finish` return
`None`, exceptions isolated by `CompositeEventSink`/`SourcePoller.tick`). This
is why GitHub labels are a sink concept and GitHub PR creation stays a
finisher concept even though both call the GitHub API.

**`docs/superpowers/specs/2026-07-22-processes-design.md`** — appended a
*second* dated blockquote (`Update 2026-07-23 — routing unified`) to "The sink
seam" section, following the existing dated-update convention rather than
rewriting the historical narrative or the compilation-validation table (left
untouched per architecture-01's explicit instruction not to over-correct the
file's other pre-existing `none`-only staleness).

**`CLAUDE.md` invariant #40** — reworded from "`none` or `slack`... distinct
from the inbound origin" to state the single effective-sink-kind rule,
including `github` as an accepted kind, its degenerate-same-as-origin nature,
and a cross-reference to ADR-0018 for the sink/finisher boundary.

## Tests added

- `tests/test_source.py` (new) — six cases for `effective_sink_kind`: explicit
  sink wins over source, falls back to source when sink absent, neither
  present → `None`, empty `sink` dict falls back, `sink.kind` falsy falls
  back, sink-present-no-source returns the sink kind.
- `tests/test_github_source.py` — added `test_reflector_matches_explicit_sink_on_a_non_github_origin`
  (explicit `data.sink={"kind":"github"}` overrides a non-github
  `data.source.kind`, repo/issue still resolved from `data.source`),
  `test_reflector_explicit_other_sink_overrides_github_origin_default`
  (a GitHub-origin task with an explicit `sink={"kind":"slack"}` is *not*
  reflected by the label reflector), and
  `test_reflector_matches_github_issues_check_shaped_task` — the
  architecture-01-flagged regression guard built with the exact shape
  `ScheduledTrigger._task_for` produces from a `GithubIssuesCheck` observation
  (`data={"source": {...}}`, no `sink` key), confirming the live-in-production
  github-issues+reflection path is unaffected.
- `tests/test_slack_sink.py` — added `test_matches_source_kind_when_no_explicit_sink`,
  a routing-contract test for the default-to-`source.kind` path (no Slack
  `TaskSource` producer exists yet; this only documents the routing rule).
- `tests/test_fs_processes.py` — added
  `test_github_sink_is_accepted_and_stamped_onto_tasks`, mirroring the
  existing Slack compile/stamp test.
- `tests/test_fs_process_admin.py` — updated
  `test_check_names_and_sink_kinds` to the new tuple, added
  `test_github_sink_round_trips` mirroring the Slack one.
- `tests/test_fs_processes.py::test_unknown_sink_raises_naming_the_file`
  (existing, unmodified) still asserts `"teams"` is rejected — confirms the
  accepted set grew, not opened up.

## Verification

```
.venv/bin/pytest -q
# 1233 passed, 1 skipped (was 1221 passed, 1 skipped pre-change; +12 new tests)

.venv/bin/pytest -q tests/test_architecture.py
# 26 passed — import-boundary invariants (#19/#20) still hold

grep -rn "data\[.source\|data\[.sink\|data\.get(.source\|data\.get(.sink" \
  src/harness/router.py src/harness/dispatcher.py src/harness/consumer.py
# no matches — router/dispatcher/consumer still never read data.source/data.sink
```

All existing `test_github_source.py`/`test_slack_sink.py` fixtures pass
unmodified (the plan's regression-guard acceptance criterion), and
`test_unknown_sink_raises_naming_the_file` still rejects an unknown kind.

## Scope notes (unchanged from plan/design/architecture)

- `GithubTaskSource.__init__`/`poll()` untouched — ingestion (claim-by-label)
  is orthogonal to this change (FR-5), confirmed by the existing
  `test_poll_claims_issue_and_builds_task` etc. passing unmodified.
- No `Task` schema change; no new port; no new architectural layer.
- Out of scope, as before: the stateful create-then-update Slack sink, a
  dedicated `Reflector` port, and making a Process-declared `github` sink
  functional for actions other than `github-issues` (no repo/issue
  association design for those exists yet).
