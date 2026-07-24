# Review: cron cadence for triggers/Processes

Verdict: **done**

## Method

Read `plan-01.md`, `design-01.md`, `architecture-01.md`, `development-01.md`, then
read the actual diff (`git show b657650`) file by file against the spec's
acceptance criteria, rather than trusting the development note's claims. Ran the
full suite fresh (`.venv/bin/pytest -q`): **1263 passed, 1 skipped** — matches the
commit message. Confirmed `HEAD`'s merge-base with `origin/main` equals
`origin/main` exactly (FR-0 is a clean fast-forward, no drift).

## Conformance to the spec

Every acceptance-criteria bullet is met:

- **Schema**: `triggers/*.json` and the Process `trigger` block each accept
  exactly one of `interval`/`cron` (`fs_triggers.py:87-92`,
  `fs_processes.py:144-150`), both/neither rejected with a clear message. Every
  existing interval-only file keeps validating unchanged (no shared parsing
  path was touched for the interval branch).
- **One occurrence seam**: `ScheduledTrigger.__init__` assigns
  `self._occurrence` to a bound method (`_interval_occurrence` or
  `_cron_occurrence`) once, per the design's chosen dispatch shape. `poll()`,
  `_dedup_key`'s `per-interval` arm, and `per-state` are otherwise untouched.
  `dedup_key` is non-constant in both modes (`test_fires_once_per_cron_occurrence`
  asserts the key differs across occurrences).
- **Pure over `Clock.now()`**: `CronSchedule.occurrence_at_or_before` and
  `parse_cron`'s impossible-schedule scan both take/derive from a string, no
  wall-clock reads. Every cron test drives a `FakeClock` with literal ISO
  strings; no sleeps.
- **Dependency decision recorded**: ADR-0018 documents the stdlib-only choice
  (vs. `croniter`) and the day-walk-vs-minute-scan choice, consistent with the
  project's `urllib`-over-`requests` stance. Supported syntax scoped exactly as
  asked (number, `*`, `,`, `-`, `/`, no named months/days).
- **Timezone**: UTC-only, documented in ADR-0018, CLAUDE.md (new gotcha), and
  the admin form's hint text, each stating "not local time" explicitly.
- **Validation wiring**: `FilesystemTriggerRepository._build_one` and
  `compile_process` both validate the new field, raising
  `TriggerValidationError`/`ProcessValidationError` with `field` set
  (`"interval"`/`"cron"`/`"trigger"` per the documented taxonomy).
  `FilesystemProcessAdmin.write`/`read` round-trip the cadence via
  `ProcessFields.cadence`; the admin form renders a working toggle (verified
  the JS: `hidden`, not `disabled`, so the inactive panel's value still POSTs —
  and a dedicated test asserts no `disabled` attribute is rendered).
- **Tests**: DST-irrelevant UTC timeline via `FakeClock` covering fire-once,
  no-fire-between, and restart-mid-occurrence (via a fresh trigger/poller pair
  and `SourcePoller.seed`, mirroring the existing interval restart test) are
  all present and green. Malformed/impossible cron expressions raise with the
  right `field` in both the trigger and process paths.
- **Docs**: CLAUDE.md invariants #37/#38, the non-constant-dedup-key gotcha,
  the new UTC gotcha, and module-map entries are updated and accurately
  describe the shipped code (checked side-by-side). ADR-0018 and both spec
  addenda are present, dated 2026-07-23, and match the implementation.
- **Fire-once-on-catchup**: implemented as a direct, undocumented-surprise-free
  consequence of reusing `_seen` unchanged — no special-cased startup branch —
  exactly as the plan recommended, and exercised by
  `test_cron_restart_mid_occurrence_does_not_refire`.

## Adherence to architecture

- No new port, no new driver file, no invariant-numbering change beyond
  additive wording — matches the architecture doc's five-touch-point diagram
  exactly (`ports/triggers.py`, `scheduled_trigger.py`, `fs_triggers.py`,
  `fs_processes.py`, `process_admin.py` + `process_form.html`).
- `dispatcher.py`/`consumer.py`/`router.py`/`source_poller.py` are untouched;
  cron is invisible above `ScheduledTrigger`, as required.
- `_LOOKBACK_DAYS` is a single shared constant used by both the forward
  (validation) and backward (runtime) walks — the Feb-29 canary pair
  (`test_feb_29_only_schedule_is_accepted`,
  `test_occurrence_at_or_before_finds_feb_29_from_a_non_leap_year_now`) the
  architecture step flagged as a risk is present and passes.
- `ProcessFields`'s `interval` field was moved to a defaulted, keyword-later
  position; the two call sites the architecture step flagged
  (`tests/test_process_admin_api.py`'s positional constructions) were found
  and fixed to keyword form, confirmed by reading the diff.

## Correctness spot-checks

- `_day_matches`'s POSIX OR-rule (dom OR dow when both restricted, AND-like
  short-circuit when at most one is) matches POSIX cron semantics and is
  shared verbatim between validation and runtime — verified against
  `test_dom_and_dow_both_restricted_is_an_or_not_an_and` and the dom-star
  variant.
- `occurrence_at_or_before`'s candidate filter correctly restricts to
  `(hour, minute) <= (moment.hour, moment.minute)` only on the current day,
  and takes all candidates on any earlier matching day, then picks `max` — this
  yields the latest match at-or-before `now`, checked against
  `test_stepped_minute_field` and the exactly-on-the-minute test.
- Sunday `0`/`7` equivalence normalizes correctly in `_parse_field`'s
  `normalize` callback, and is exercised in the parser (not just assumed).
  `_raw_from_fields`/`_fields_from_raw` round-trip is consistent with
  `ProcessFields.cadence` picking the authoritative value, verified against
  the admin round-trip test.
- `_parse_sink`, `_parse_target`, `_parse_dedup` in `fs_processes.py` are
  unchanged by this feature (only `_parse_cadence` replaces the old
  `_parse_interval`) — no incidental scope creep into unrelated validation
  paths.

No functional requirement is unmet, no architecture conflict, no missing
required test, no correctness bug found. Ran the full suite myself rather than
trusting the development note; it reproduces 1263 passed / 1 skipped.

## Non-binding cleanup suggestion (not blocking)

None found worth flagging — the diff is unusually tight around the stated
scope; no stray refactors, no dead code, no comment noise.
