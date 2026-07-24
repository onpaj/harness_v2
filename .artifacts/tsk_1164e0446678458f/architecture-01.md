# Architecture assessment: cron expressions for trigger and Process schedules

## Grounding

Verified directly against `origin/main` (`e4485d6`), not against memory or the
plan/design artifacts' claims alone:

- `src/harness/ports/triggers.py`, `drivers/scheduled_trigger.py`,
  `drivers/fs_triggers.py`, `drivers/fs_processes.py`, `ports/process_admin.py`,
  `api/templates/admin/process_form.html`, `drivers/checks.py`, `ports/source.py`,
  `source_poller.py` — full text read, not excerpted.
- `CLAUDE.md` invariants #35–#41 on `origin/main` (numbering the design cites is
  correct: #37 "clock-gate, not a loop", #38 "bucket-keyed dedup").
- `tests/test_architecture.py`'s two relevant guards:
  `test_scheduled_trigger_imports_only_ports_models_and_ids` (drivers/scheduled_trigger.py
  may import only `harness.models`, `harness.ids`, and `harness.ports.*`) and
  `test_fs_processes_is_a_thin_aggregate_over_the_trigger_drivers` (fs_processes.py
  may additionally import only `drivers.checks` and `drivers.scheduled_trigger`).
- `docs/adr/0000`–`0017` (0018 is next free), and the exact "out of scope" line
  in `docs/superpowers/specs/2026-07-22-generic-triggers-design.md:51` that the
  spec addendum must supersede.
- This worktree's own git history: `HEAD` (`2dcd9a9`) differs from the merge-base
  with `origin/main` (`0c8027b`) by **two commits touching only
  `.artifacts/tsk_1164e0446678458f/*`** — zero source-file changes. This
  materially changes the risk profile of FR-0 (see Risks).

**Verdict: the design (`design-01.md`) is correct.** Every code excerpt,
invariant number, import-boundary claim, and "out of scope" quotation it makes
checks out byte-for-byte against the real files. I found no discrepancy to
correct. This document is not a rewrite of that design — it is the
architectural sign-off, with the integration risk analysis the design step
wasn't scoped to do, sharper sequencing, and the two places I'd tighten the
spec before implementation starts.

## Alignment with existing patterns

The design's central move — generalize `ScheduledTrigger`'s
`floor(epoch(now)/interval)` bucket into an `occurrence(now) -> Hashable`
function, with cron as a second implementation of that same function — is the
correct application of this codebase's dominant pattern: **a port stays fixed,
a driver-internal seam grows a second case, nothing upstream learns a new
concept.** It's the same shape as invariant #39 (a Process compiles into the
*same* `ScheduledTrigger` a bare trigger file does — two authoring surfaces,
one runtime object) and invariant #13 (`AgentRunner` is one seam serving many
personas). The design explicitly rejected a `Cadence`/`IntervalCadence`/
`CronCadence` class hierarchy for this reason, and that rejection is right: two
call sites, no third cadence scoped anywhere, and `_occurrence` as a bound
method promotes to an injected strategy object later without breaking the
public constructor — a real YAGNI call, not corner-cutting.

The **architecture boundary this feature must not cross** is the one
`test_architecture.py` already enforces mechanically:
`drivers/scheduled_trigger.py` imports only `harness.models`/`harness.ids`/
`harness.ports.*`. Putting `parse_cron`/`CronSchedule` in `ports/triggers.py`
(as the design does, next to `parse_interval`) is what keeps this legal —
`ScheduledTrigger` importing `CronSchedule` from a port is fine; importing it
from a hypothetical `drivers/cron.py` would fail the guard test outright. This
is not a style preference, it's the literal thing CI checks. Same for
`fs_processes.py`: its allowed-driver set is exactly `{drivers.checks,
drivers.scheduled_trigger}`, so `_parse_cadence`'s `parse_cron` call must come
from `ports.triggers` (already imported there for `parse_interval`), never
from a new driver import.

The dedup design is the correct reading of invariant #38: "bucket-keyed"
already generalizes to "occurrence-keyed" without a wording change to the
*mechanism*, only to the *value* — `dedup_key`'s third argument was always
`Hashable`-via-`str()`, never typed as `int`. The design's choice to keep
`per-state` dedup completely untouched is right and is the thing most likely
to get quietly broken by a careless refactor: it's tempting to thread
`occurrence` through `_dedup_key`'s `per-state` branch "for consistency," and
that would be wrong — `per-state` dedup existing to be cadence-blind is the
whole point of invariant #38's parenthetical.

## Proposed architecture

No new port, no new driver file, no new invariant number that isn't strictly
additive wording on #37/#38. Five touch points, in dependency order:

```
ports/triggers.py           +parse_cron, +CronSchedule, +_day_matches (module-private)
        │
        ▼
drivers/scheduled_trigger.py  ScheduledTrigger gains cron= param, one seam
        │                     (_occurrence bound method, replacing _bucket)
        ├────────────────────┬─────────────────────┐
        ▼                    ▼                      ▼
drivers/fs_triggers.py   drivers/fs_processes.py   drivers/fs_processes.py
(_build_one validates    (_parse_cadence replaces  (ProcessFields/_raw_from_fields/
 cron XOR interval)       _parse_interval)          _fields_from_raw round-trip)
                                                     │
                                                     ▼
                                        api/templates/admin/process_form.html
                                        (Schedule section: cadence toggle)
```

Everything above `ScheduledTrigger` — `SourcePoller`, `dispatcher`, `consumer`,
`router`, `TaskSource` — is provably untouched: none of them import
`ports.triggers` or `drivers.scheduled_trigger` today (confirmed by the same
architecture tests), and nothing in this feature adds such an import. That is
the strongest guarantee this design offers and it should be treated as a hard
constraint during implementation, not just a nice property: **if any FR
requires touching `source_poller.py`, `dispatcher.py`, `consumer.py`, or
`router.py`, the seam has failed and the design needs to be revisited before
writing more code.**

### Key decision: occurrence lookup is a bounded backward day-walk, not a forward calendar recurrence rule

Considered and rejected alternatives, for the record (the design states the
choice but not the alternatives it beat):

1. **Minute-by-minute backward scan** — correct but O(minutes since last
   occurrence), unbounded for a sparse schedule (e.g. `"0 0 1 1 *"`, once a
   year) — up to ~525,600 iterations in the worst realistic case. Rejected on
   the NFR's own terms ("must not brute-force minute-by-minute").
2. **A full calendar/cron library dependency (`croniter`)** — correct and
   fast, but breaks the project's stated stdlib-only-for-runtime-deps stance
   (the `urllib`-over-`requests` precedent this task explicitly asks to be
   weighed). Rejected, recorded in ADR-0018 as the design specifies — this is
   the right call for a 5-field numeric-only grammar; revisit only if
   named-months/6-field/timezone support is scoped later, since at that point
   a hand-rolled parser's marginal complexity stops being clearly cheaper than
   a dependency.
3. **Day-level backward walk, minute/hour resolved by set-membership within a
   matching day** (the design's choice) — bounded by the same 4×366-day cap
   the impossible-schedule check already needs to establish termination, reuses
   the identical `_day_matches` predicate for both purposes (one place owns the
   POSIX OR-rule), and the common case (any schedule that fires at least
   weekly) resolves in single-digit iterations. This is the right choice: it's
   the cheapest option that still shares one source of truth for "does this
   day match" between load-time validation and runtime lookup — a structural
   guarantee that the OR-rule can't drift out of sync between the two call
   sites, not just a performance win.

### Key decision: `cron` is pre-parsed before reaching `ScheduledTrigger`

The design narrows the plan's `cron: str | CronSchedule | None` to
`cron: CronSchedule | None` only. This is correct and should be treated as
binding, not a stylistic option: it matches `interval`'s existing contract
(`ScheduledTrigger` never sees a duration string, only the parsed `float`),
keeps `ScheduledTrigger` provably free of any parsing/validation logic (it
already has zero `try`/`except` today — `poll()` and `_bucket` are pure
transformations), and means the *only* two places `parse_cron` is ever called
are `FilesystemTriggerRepository._build_one` and `compile_process`
(`FilesystemProcessAdmin.write` reaches it transitively through
`compile_process`, not directly) — one parse path, two callers, matching the
existing `parse_interval` shape exactly.

### Key decision: `field="trigger"` absorbs the both/neither structural error

The design's error taxonomy (`trigger` for "the block itself is malformed,"
`interval`/`cron` for "a present value is malformed") is the right split
because it's the one the admin form's DOM already supports: `errors.trigger`
has nowhere to anchor to a single input (there are now two candidate inputs,
shown/hidden by the toggle), so it must render as the section-level banner —
exactly the slot the form's `{{ 'error' if errors.interval }}` section-wrapper
condition already provides, extended to `errors.interval or errors.cron or
errors.trigger`. Don't let implementation invent a third slot for this; there
isn't a fourth input to attach it to.

## Implementation guidance

### `ports/triggers.py`

Add below `parse_interval`, in this order (each depends only on what precedes
it, so this is also a sane commit-internal ordering if the implementer wants
sub-commits):

1. `_parse_field(raw, lo, hi, normalize=lambda v: v) -> frozenset[int]` —
   module-private, per the design's shared-helper sketch. Keep it a free
   function, not a `CronSchedule` static/classmethod — it's used once per field
   during construction, before a `CronSchedule` instance exists.
2. `_day_matches(date, schedule) -> bool` — module-private, takes a
   `datetime.date` and a `CronSchedule`. This is the one function both
   `parse_cron`'s impossible-schedule check and
   `CronSchedule.occurrence_at_or_before`'s runtime walk call — do not let a
   second copy of the OR-rule appear anywhere else, including inline in the
   admin form's JS summary line (that stays cosmetic text, never re-derives
   matching).
3. `CronSchedule` (frozen dataclass) with `occurrence_at_or_before(now: str) ->
   str` as an instance method — needs `self` to reach `minutes`/`hours`/etc.,
   so it can't be the free function `_day_matches` is.
4. `parse_cron(text: str) -> CronSchedule` — last, since it constructs a
   candidate `CronSchedule` and then calls `_day_matches` in a loop to validate
   it exists.

`_LOOKBACK_DAYS = 4 * 366` as a module constant, shared by both the
parse-time forward scan and (implicitly, as the walk's cap) the runtime
backward walk — one named constant, not two literals that could drift apart.

### `drivers/scheduled_trigger.py`

Rename `_bucket` → `_interval_occurrence`, `_last_bucket` → `_last_occurrence`
(type `Hashable | None`), and the `_task_for`/`_dedup_key` parameter `bucket` →
`occurrence`. Add `_cron_occurrence`. In `__init__`, the XOR check
(`(interval is None) == (cron is None)`) goes immediately after the existing
`(workflow is None) == (step is None)` XOR check — same style, same
`ValueError`, not a different exception type. Assign `self._occurrence` last,
after both branches are validated, so a future maintainer reading top-to-bottom
sees "validate, then wire" not "wire, then validate."

One thing the design's constructor sketch doesn't show explicitly: `interval`
and `cron` should both default to `None` in the signature (matching
`workflow`/`step`'s existing pattern), keeping every current call site
(`ScheduledTrigger(..., interval=3600, ...)`, positional-free per today's
tests) working unchanged as long as it still supplies exactly one. Confirmed
safe — `tests/test_scheduled_trigger.py`'s existing calls are all keyword-only
already.

### `drivers/fs_triggers.py` and `drivers/fs_processes.py`

Both files already import `parse_interval` from `ports.triggers` — add
`parse_cron` to the same import line, don't add a second import statement.
`fs_processes.py`'s `_parse_interval` function should be renamed
`_parse_cadence` in place (not added alongside a preserved
`_parse_interval` — there is exactly one cadence-parsing call site inside
`compile_process`, so a lingering unused `_parse_interval` would be dead code).
`fs_triggers.py` has no equivalent extracted helper today (`_build_one` inlines
the interval parse); the design's inline XOR-then-branch block is the right
shape to match that file's existing style — don't extract a helper function
there just for symmetry with `fs_processes.py`, since `fs_triggers.py`'s
`_build_one` doesn't extract *any* of its other field parsers either (compare
`_parse_target`, which *is* extracted, because it's reused nowhere else but is
non-trivial — cron/interval parsing is comparably non-trivial only on the
`fs_processes.py` side, where `field=` tagging makes extraction pay for itself).

### `ports/process_admin.py` / `drivers/fs_processes.py` admin round-trip

`ProcessFields`'s reorder (`interval` moving from a required positional-first
field to a defaulted field after `check`/`target_kind`/`target`) is
source-compatible with every current caller **only** if every current
construction site uses keywords — the design asserts this after checking
`tests/test_fs_process_admin.py`; the implementer should re-run that same grep
over `tests/test_process_admin_api.py` and `api/` (wherever `ProcessFields(...)`
is constructed for template rendering) before relying on it, since those
weren't the file the design's claim was checked against.

### `api/templates/admin/process_form.html`

The existing `target_kind` toggle (`<div class="seg" role="radiogroup">` with
paired `<input type="radio">`, JS function `syncTargetKind()`) is the literal
template to copy for the new `cadence` toggle — same CSS class, same
show/hide-via-JS pattern, same "both panels always in the DOM so a server
round-trip after a validation error doesn't lose the operator's other panel's
value" behavior `syncTargetKind()` already relies on. Add a
`syncCadence()` mirroring `syncTargetKind()`'s structure, called on load and on
toggle change, alongside the existing `updateSummary()` extension the design
specifies.

### Suggested implementation order

Not a re-derivation of the plan's FR numbering, but the dependency-respecting
order that lets `pytest -q` stay green after each step (useful if the
implementer commits incrementally rather than as one FR-per-commit):

1. FR-0 (merge/rebase) — see Risks: given zero source-file changes on this
   branch, prefer `git rebase origin/main` over `git merge origin/main` for a
   linear history, unless the team's convention (check recent `origin/main`
   merge commits, if any) favors merge commits for worktree catch-ups.
2. FR-1 (`parse_cron`/`CronSchedule` + its own test module) — fully
   self-contained, zero callers yet, safe to land alone.
3. FR-2 (`ScheduledTrigger` seam) — the interval-mode regression suite must
   pass with **zero diffs to its assertions**, only to the renamed internals;
   if any existing interval test needs its assertions changed, that's a signal
   the seam isn't as transparent as intended.
4. FR-4 and FR-5 in either order (independent files, both depend only on
   FR-1+FR-2) — FR-4 is smaller, doing it first is a cheap confidence check
   before tackling FR-5's `ProcessFields` reorder.
5. FR-6 (admin UI) — depends on FR-5's `field=` taxonomy being final.
6. FR-7 (docs) — last, since it documents the as-built shape; drafting doc
   text before FR-1–6 land risks the docs describing a decision that changed
   during implementation (e.g. if the day-walk direction or lookback constant
   gets tuned while writing FR-1's tests).

## Risks and mitigations

**FR-0 is lower-risk than the plan/design frame it.** Both artifacts describe
it as "hard prerequisite," which is correct, but they don't note that this
worktree's branch has made **zero source-file changes** since diverging from
`origin/main` — only two commits adding files under
`.artifacts/tsk_1164e0446678458f/`. A `git rebase origin/main` (or merge; either
resolves trivially) cannot conflict on any file this feature will touch,
because nothing in this branch has touched `src/`, `tests/`, or `docs/adr/`
yet. Treat FR-0 as a mechanical `git fetch && git rebase origin/main`
followed by `pytest -q`, not a merge-conflict-resolution exercise — if it
*does* conflict, that means `origin/main` moved again since this assessment
(`e4485d6`) and the merge-base should be re-checked, not fought through blindly.

**The impossible-schedule look-back direction is asymmetric and worth a named
test, not just a passing mention.** `parse_cron`'s validation scan walks
*forward* from a fixed `date(2028, 1, 1)`; `occurrence_at_or_before`'s runtime
walk goes *backward* from `now`. Both share `_day_matches`, so the OR-rule
itself can't drift — but the *cap* (`_LOOKBACK_DAYS`) is reused for two
direction-opposite purposes, and a leap-day-only schedule (`"0 0 29 2 *"`, Feb
29 only) is the one case where getting the cap's arithmetic wrong in either
direction silently breaks: too short a window in the forward validation scan
falsely rejects a valid quadrennial schedule; too short a window in the
backward runtime walk (if the two ever use different constants) would make a
just-passed-validation schedule wrongly report "no occurrence" at runtime for
years-old `now` values near a non-leap year. Mitigation: the design already
shares one `_LOOKBACK_DAYS` constant for both — implementation must not let a
future edit special-case one direction's window without the other. Add
`test_parse_cron_accepts_feb_29_only` and a matching
`test_occurrence_at_or_before_finds_feb_29_from_a_non_leap_year_now` as the
canary pair for this coupling; the plan/design's AC lists don't call out Feb
29 by name and should.

**`_check_trigger_kind` and `_parse_cadence` both read `raw.get("trigger")` —
ordering matters for error-message quality, not correctness.** `compile_process`
calls `_parse_cadence` before `_check_trigger_kind` today (per the design's
sketch, unchanged from the current `_parse_interval`-then-`_check_trigger_kind`
order). If a process file has *both* an invalid cadence (e.g. neither
`interval` nor `cron`) *and* an unsupported `trigger.kind`, the operator sees
the cadence error first, never the kind error. This is pre-existing behavior
(today's `_parse_interval` already runs first), so the feature isn't
introducing a new bug — but the design should note this ordering is
deliberately preserved, not accidentally inherited, so a future reviewer
doesn't "fix" it into simultaneous multi-field validation (which would be a
different, larger change to the error-reporting contract, out of scope here).

**No risk found in the admin UI change beyond what the design already flags.**
The "both panels always render, JS shows one" pattern is proven by
`target_kind`'s existing implementation, so this is a copy of a working
pattern, not new interaction design. The one thing to verify manually (per the
design's own AC and this repo's `verify` convention) is that submitting the
form with the cron panel hidden still POSTs the cron input's value if it's
non-empty from a prior round-trip — i.e. confirm the hidden panel's `<input>`
isn't `disabled` (which would drop it from the POST body), only visually
hidden. `target_kind`'s implementation should be checked for which of the two
it uses before the cadence toggle copies it blindly.

## Prerequisites before implementation begins

1. FR-0 rebase/merge onto current `origin/main` tip, `pytest -q` green,
   `tests/test_architecture.py` green, before any FR-1 code is written.
2. Confirm `origin/main` hasn't moved again since this assessment
   (`e4485d6`) — re-fetch immediately before starting FR-0, not from this
   document's cached SHA.
3. No other prerequisite: no schema migration, no data backfill, no feature
   flag — every existing `triggers/*.json`/`processes/*.json` file stays valid
   unchanged, confirmed by direct reading of `_build_one`/`compile_process`'s
   current field-presence checks (both already gate on `"interval" not in
   raw`/`"interval" not in trigger`, which the design's XOR check is a strict
   superset of).

## Summary for the next step

Proceed to implementation FR-1 → FR-7 in the order above. The design is
sound and fully grounded; the two additions this assessment makes binding are
(a) the Feb-29 canary test pair for the shared look-back constant, and (b) the
hidden-vs-disabled check on the cadence toggle's non-visible panel input
before assuming `target_kind`'s pattern transfers verbatim.
