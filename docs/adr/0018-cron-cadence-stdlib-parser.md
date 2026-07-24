# ADR-0018: Cron cadence via a stdlib-only parser, UTC-only, fire-once-on-catchup

Status: Accepted

## Context

A trigger's cadence was a plain interval (`parse_interval`, `ports/triggers.py`):
`ScheduledTrigger.poll()` gated on the interval bucket
`floor(epoch(now) / interval)`. That expresses "every N seconds/minutes/hours"
but not a calendar schedule — a weekly automation ("Monday 06:00") had to be
written as `168h` anchored to whatever epoch the harness happened to start at,
firing at an arbitrary, unpickable weekday/hour. Operators need to *choose* the
wall-clock moment a calendar-shaped automation fires, without losing any of the
guarantees the interval path already has: clock-gate (no new loop), and
at-most-once-per-occurrence dedup across restarts.

Three decisions had to be made before this could be built: how the expression
is parsed (stdlib vs. a dependency), what timezone it evaluates in, and what
happens if the harness was down over a scheduled occurrence.

## Decision

**A minimal, in-repo, stdlib-only 5-field cron parser — not `croniter` or an
equivalent dependency.** `parse_cron`/`CronSchedule` (`ports/triggers.py`) parse
a standard `minute hour day-of-month month day-of-week` expression using only
`datetime`/`calendar` from the standard library. Supported syntax per field: a
literal number, `*`, a list (`a,b,c`), a range (`a-b`), a step (`*/n` or
`a-b/n`). No named months/days (`JAN`, `MON`), no 6-field seconds, no
`@yearly`/`@daily` shorthand. This follows the project's existing stdlib-first
stance for runtime dependencies (`urllib` over `requests`, `drivers/github_client.py`):
the supported grammar is small and numeric-only, so a hand-rolled parser's
marginal complexity is clearly cheaper than a new dependency. Revisit only if
named months/days, 6-field seconds, or timezone-aware evaluation are scoped
later — at that point a dependency's marginal cost stops being clearly worse.

Occurrence lookup (`CronSchedule.occurrence_at_or_before`) is a **bounded
backward day-walk**, not a minute-by-minute scan and not a full calendar
recurrence-rule engine: walk backward one calendar day at a time (capped at
`_LOOKBACK_DAYS = 4 * 366`, spanning every leap-day alignment), and within the
first matching day, resolve the largest `(hour, minute)` pair at-or-before
`now` by set membership — a bounded in-memory max over at most 24 × 60
candidates, not a scan. The same day-matching predicate (`_day_matches`,
including the POSIX day-of-month/day-of-week OR-rule) is shared by this
runtime walk and by `parse_cron`'s **impossible-schedule check**: a forward
scan from a fixed reference date across the same `_LOOKBACK_DAYS` window,
rejecting a schedule that would otherwise poll forever and silently never fire
(e.g. `"0 6 31 2 *"`, day 31 in February) as a `ValueError` at load time
instead. Reusing one predicate for both means the OR-rule can never drift out
of sync between validation and runtime, and the common case (any schedule
firing at least weekly) resolves in single-digit iterations — only an
impossible schedule ever pays the full bound, and that one is rejected before
it can be polled.

**UTC-only this increment; no per-trigger `tz` field.** `Clock.now()` is
already a UTC ISO string throughout the codebase; cron fields are matched
against that string's UTC components directly. A per-trigger timezone is a
noted, separate follow-up (would need a timezone database — stdlib `zoneinfo`
or an equivalent — deliberately not pulled in yet, since the operator's
current automations are satisfied by choosing a UTC time once and documenting
the offset). The admin UI's cron field hint states the UTC caveat explicitly,
not only in this ADR and CLAUDE.md — an operator running Europe/Prague typing
`"0 6 * * 1"` expecting 06:00 local time is exactly the surprise this decision
must not hide.

**Fire-once-on-catchup**, as a direct consequence of reusing
`SourcePoller._seen` unchanged rather than adding bespoke startup logic. If the
harness was down over a scheduled occurrence, `ScheduledTrigger` never
distinguishes "startup" from any other poll: `occurrence_at_or_before(now)` on
the first poll after restart returns the same occurrence id it would have
returned right before the crash. If a task for that occurrence was already
emitted before the crash, `_seen` (seeded from disk at startup) suppresses a
duplicate; if it was never emitted, the trigger fires exactly once, on the
first poll after restart — never more, since the very next poll sees the same
occurrence again and gates on it. This matches "at-most-once per occurrence"
without a special case, and is exactly the existing interval-mode restart
behavior, extended to cron for free by the shared occurrence seam.

## Consequences

- `ScheduledTrigger` gains one occurrence seam (`self._occurrence`, a bound
  method assigned in `__init__` to either `_interval_occurrence` or
  `_cron_occurrence`) shared by both cadences, replacing the interval-only
  `_bucket`/`_last_bucket`. A `Cadence`/`IntervalCadence`/`CronCadence` class
  hierarchy was considered and rejected: it buys nothing today (only two
  cadences exist, both already need their own parse step upstream in
  `FilesystemTriggerRepository`/`compile_process`) and adds permanent
  structure for a hypothetical third (condition-only) cadence that isn't
  scoped anywhere. Promoting `_occurrence` from a bound method to an injected
  strategy object later, if a third cadence lands, is a local,
  backward-compatible refactor.
- `triggers/*.json` and the `processes/*.json` `trigger` block each accept a
  new, optional `cron` field as a sibling of `interval` — exactly one of the
  two must be present (both or neither is a validation error, `field="trigger"`
  in the Process error taxonomy, since the block itself is malformed rather
  than one value being invalid). Every existing interval-only file keeps
  validating unchanged; no migration.
- `ProcessFields` (`ports/process_admin.py`) gains a `cadence` discriminator
  (`"interval"` | `"cron"`) plus a new `cron` value field, mirroring the
  existing `target_kind`/`target` pattern. `interval` moves from a required
  positional-first field to a defaulted one — source-compatible with every
  call site that already used keyword arguments.
- The admin process form's "Schedule" section gains a cadence toggle (the same
  `seg` radio-group markup/JS pattern `target_kind` already uses), a cron input
  with its UTC-caveat hint, and an `errors.cron` slot beside the existing
  `errors.interval` — `errors.trigger` (the both/neither structural error) has
  no single input to anchor to, so it renders as the section-level banner the
  form already provides.
- No new port, driver file, or invariant number beyond additive wording on
  invariants #37/#38 (CLAUDE.md): everything above `ScheduledTrigger` —
  `SourcePoller`, `dispatcher`, `consumer`, `router`, `TaskSource` — is
  unaware cron exists, and stays that way.
