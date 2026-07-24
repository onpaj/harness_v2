# Plan: cron expressions for trigger and Process schedules

## Grounding note

This worktree (`harness/tsk_1164e0446678458f`) branched off an old `main` and is
**75 commits behind `origin/main`**. It has none of the generic-triggers /
Process machinery this task builds on (`ports/triggers.py`,
`drivers/scheduled_trigger.py`, `drivers/fs_triggers.py`,
`drivers/fs_processes.py`, `ports/process_admin.py`, the `docs/adr/0014`/`0015`
ADRs, the `2026-07-22-generic-triggers-design.md` / `2026-07-22-processes-design.md`
specs). All code references, invariant numbers and file contents below are read
directly off `origin/main` (fetched during planning), not off this worktree's
HEAD. Merging up is FR-0, a hard prerequisite for everything else here.

The original generic-triggers spec explicitly parked this: *"A real scheduler
daemon (cron), webhooks, distributed leases [are out of scope]. A trigger is
still a plain poll tick; the cadence is a clock-gate inside `poll()`."* This
task lifts that restriction for the *expression* only — the clock-gate,
poll-tick architecture is unchanged; cron becomes a second way to compute the
gate's occurrence identity.

## Summary

`triggers/*.json` and the `trigger` block of `processes/*.json` gain a second,
alternative cadence expression — a standard 5-field cron string — alongside
the existing interval duration. `ScheduledTrigger` grows one internal seam (an
occurrence function of `Clock.now()`) that both cadences feed, so the
clock-gate/no-new-loop and at-most-once-per-occurrence-across-restarts
guarantees the interval path already has apply unchanged to cron. Nothing
downstream of `ScheduledTrigger` (dispatcher, consumer, router, `SourcePoller`)
learns a new concept — a cron trigger is still a plain `Task`-producing
`TaskSource`.

## Context

`ScheduledTrigger.poll()` (`drivers/scheduled_trigger.py`) gates on
`floor(epoch(now) / interval)` and dedups on that bucket. That expresses "every
N seconds/minutes/hours" but not "Monday at 06:00" — the upcoming weekly
automations (architect review, QA/coverage audit) would have to be written as
`168h` anchored to whatever epoch the harness happened to start at, firing at
an arbitrary, unpickable weekday/hour. Operators need to *choose* the wall-clock
moment a weekly (or otherwise calendar-shaped) automation fires, without losing
any of the guarantees `per-interval`/`per-state` dedup and restart-safety
already provide for plain intervals.

## Functional requirements

### FR-0 — Merge this worktree onto `origin/main`

Bring in the entire trigger/Process/Check subsystem (ADR-0014, ADR-0015,
`ports/triggers.py`, `drivers/scheduled_trigger.py`, `drivers/fs_triggers.py`,
`drivers/fs_processes.py`, `ports/process_admin.py`, `drivers/checks.py`, the
generic-triggers/processes specs and plans, and their tests) before any of the
FRs below are touched. Not part of the feature itself — a precondition.

- **AC:** `git merge origin/main` (or rebase) resolves cleanly; `pytest -q`
  and `tests/test_architecture.py` pass unmodified on the merged tree before
  any new code is written.

### FR-1 — A pure, stdlib-only 5-field cron parser and occurrence function

Add to `ports/triggers.py` (alongside the existing `parse_interval`):

- `parse_cron(text: str) -> CronSchedule` — validates a standard 5-field
  expression (`minute hour day-of-month month day-of-week`). Supported syntax
  per field: a literal number, `*`, a list (`a,b,c`), a range (`a-b`), a step
  (`*/n` or `a-b/n`), and combinations thereof (e.g. `*/15`, `1-5`,
  `1,3,5-7`). No named months/days (`JAN`, `MON`) and no 6-field seconds —
  explicitly out of scope for this increment. Day-of-week is `0-7` with both
  `0` and `7` meaning Sunday (POSIX cron convention), `1` = Monday — matches
  the worked example in this task (`"0 6 * * 1"` = Monday 06:00 UTC).
  Raises `ValueError` on a malformed field, an out-of-range value, or a
  syntactically valid schedule that can **never occur** (see below).
- `CronSchedule.occurrence_at_or_before(now: str) -> str` — the most recent
  minute-aligned UTC timestamp ≤ `now` that matches all five fields, honoring
  the standard cron OR-rule when *both* day-of-month and day-of-week are
  restricted (not `*`): a day matches if it satisfies *either* field, not
  both. Pure function of its argument — no wall-clock reads, no imports of
  `time`/`datetime.now()`.
- **Impossible-schedule detection at parse time:** `parse_cron` calls
  `occurrence_at_or_before` (or an equivalent forward/backward search) bounded
  to a fixed look-back window (recommend 4 years, to span every possible leap-day
  alignment) during validation; if no match is found in that window (e.g.
  `"0 6 31 2 *"`, day 31 in February), it raises `ValueError`. This turns a
  schedule that would otherwise poll forever and silently never fire into a
  fail-fast load-time error — same fail-fast posture the acceptance criteria
  ask for.

**AC:**
- `parse_cron("0 6 * * 1")` succeeds; `occurrence_at_or_before` on a Tuesday
  returns the preceding Monday 06:00Z.
- `parse_cron("*/15 * * * *")`, `parse_cron("0 9-17 * * 1-5")` succeed.
- `parse_cron("")`, `parse_cron("* * * *")` (4 fields), `parse_cron("60 * * * *")`
  (out of range), `parse_cron("0 6 31 2 *")` (impossible) all raise `ValueError`
  with a message naming the problem.
- No test in the new suite sleeps in real time or reads the wall clock; every
  case is a fixed `now` string in, a fixed occurrence string out.

### FR-2 — `ScheduledTrigger`: one occurrence seam for both cadences

`ScheduledTrigger.__init__` accepts `interval: float | None = None` **and**
`cron: str | CronSchedule | None = None`, exactly one of which must be set —
mirroring the existing `(workflow, step)` XOR already in this constructor.
Internally, `_bucket`/`_last_bucket` generalize to an occurrence function
(`now -> Hashable`) built once at construction: the interval branch keeps
today's `floor(epoch(now) / interval)`, the cron branch calls
`CronSchedule.occurrence_at_or_before(now)`. `poll()`'s gate
(`occurrence == self._last_occurrence`) and `_dedup_key`'s `per-interval` arm
are otherwise unchanged — they consume whatever the occurrence function
returns, without caring which cadence produced it. `per-state` dedup is
untouched (keys on `Observation.state_key` regardless of cadence — cadence
only decides *when* the check runs, per the existing invariant).

**AC:**
- Existing `tests/test_scheduled_trigger.py` (interval mode) passes with zero
  behavioral change.
- New cron-mode tests via `FakeClock`: fires exactly once when `now` crosses
  an occurrence; returns `[]` for every `now` within the same occurrence
  (e.g. polled every 5 minutes across the day, only the top of the scheduled
  hour yields a task); the emitted `dedup_key` differs across two consecutive
  occurrences (guards the CLAUDE.md gotcha: a constant key would silently
  kill every fire after the first).
- A restart mid-occurrence does not re-fire: seed `SourcePoller._seen` from a
  previously-emitted task's persisted `dedup_key`, tick again at a `now`
  still inside the same occurrence, assert `[]` — the cron twin of the
  existing interval-mode restart test.

### FR-3 — Schema: `cron` as an alternative to `interval`

- `triggers/*.json`: top-level `"cron": "<expr>"` is accepted as an
  alternative to `"interval": "<duration>"`. Exactly one of the two must be
  present; both or neither is a validation error.
- `processes/*.json`: the nested `trigger` object accepts
  `{"cron": "<expr>"}` in place of `{"interval": "<duration>"}` — same XOR
  rule, same object.
- Every currently-valid file (interval-only) keeps validating unchanged — no
  migration needed for existing `triggers/`/`processes/` directories.

**AC:** unit tests over `FilesystemTriggerRepository`/`compile_process` cover:
cron-only file compiles; interval-only file compiles (regression); both
present raises with a message naming the file; neither present raises with a
message naming the file.

### FR-4 — `FilesystemTriggerRepository` validates and wires `cron`

`_build_one` parses `raw.get("cron")` through `parse_cron` the same way it
parses `interval` today: a `ValueError` becomes
`TriggerValidationError(f"trigger {path.name} has an invalid cron expression: {error}")`.
Passes `interval=` or `cron=` (never both) into `ScheduledTrigger`.

**AC:** extends `tests/test_fs_triggers.py` with a cron-file case and a
malformed-cron-file case (message names the file, per existing convention).

### FR-5 — `compile_process` validates and wires `cron`

`compile_process`'s cadence parsing (today `_parse_interval`, reading
`trigger["interval"]`) generalizes to read `trigger.get("interval")` XOR
`trigger.get("cron")`, raising `ProcessValidationError` with `field` set so
`FilesystemProcessAdmin`/the admin UI can point at the right input. The
existing `_check_trigger_kind` reservation (`trigger.kind`, `"schedule"`-only)
is unaffected — cron is still a `"schedule"`-kind trigger, just a different
cadence expression within it.

**AC:** extends `tests/test_fs_processes.py`/`tests/test_processes_e2e.py`
with cron-cadence cases; extends `tests/test_fs_process_admin.py` for the
`FilesystemProcessAdmin.write` path (a submission with a bad cron string is
rejected with the field mapped, nothing is written — same guarantee `write`
already gives for a bad interval).

### FR-6 — `ProcessAdmin` / admin UI accept the new field

- `ports/process_admin.py`'s `ProcessFields` gains the fields needed to
  express "which cadence, and its value" (e.g. a `cadence` discriminator plus
  an `interval`/`cron` value — exact shape is a design-step decision, see Open
  Questions).
- `FilesystemProcessAdmin`'s `_raw_from_fields`/`_fields_from_raw` round-trip
  both shapes losslessly.
- `api/templates/admin/process_form.html`'s "Schedule" section gains a way to
  pick cron instead of interval (a toggle next to the existing interval-chip
  row) with a cron-syntax hint and its own error slot, following the same
  pattern the `interval` field already uses (`errors.interval` →
  `errors.cron`).

**AC:** `tests/test_process_admin_api.py` covers reading/writing a
cron-cadence process through the HTTP form path; a manual smoke pass through
the running admin UI (per this repo's `verify`/`run` conventions) confirms
the toggle renders and a bad cron string surfaces inline, not as a 500.

### FR-7 — Documentation

- `CLAUDE.md`: invariants #37 ("a scheduled trigger owns its cadence via the
  Clock, not a loop") and #38 (dedup is bucket-keyed) get cron-aware wording
  (either amended in place or a new invariant added — design step's call), the
  module-map entries for `ports/triggers.py`/`drivers/scheduled_trigger.py`/
  `drivers/fs_triggers.py`/`drivers/fs_processes.py` gain a cron mention, and
  a **new gotcha** documents the UTC-only semantics.
- `docs/superpowers/specs/2026-07-22-generic-triggers-design.md` and
  `docs/superpowers/specs/2026-07-22-processes-design.md` gain a dated
  addendum superseding their "cron is out of scope" line, cross-referencing
  this feature's own spec/plan.
- A new ADR (`docs/adr/0018-cron-cadence-stdlib-parser.md`, next free number
  after `0017`) records: (a) the in-repo-parser-vs-`croniter` decision and why
  (stdlib-only runtime deps, precedent `urllib` over `requests`), (b) the
  UTC-only timezone semantics and the deferred per-trigger `tz` follow-up,
  (c) the fire-once-on-catchup decision for a startup gap.

## Non-functional requirements

- **No new runtime dependency.** `datetime`/`calendar` (stdlib) only —
  consistent with this project's stdlib-first stance (`urllib` over
  `requests` is the precedent cited in the task).
- **Determinism/testability.** Every new code path takes `now` as data (from
  `Clock.now()` or a test's literal string) — no `datetime.now()`,
  `time.time()`, or sleeps anywhere in the cron parser, `CronSchedule`, or
  `ScheduledTrigger`'s occurrence seam.
- **Performance.** Occurrence lookup must not brute-force minute-by-minute
  over a multi-year bound on every poll tick. A day-level walk (checking
  day-of-month/month/day-of-week first, then narrowing to hour and minute
  within a matching day) keeps the common case (e.g. "every Monday") to a
  handful of iterations; only a pathological/impossible expression pays the
  full bound, and that case is rejected at load time (FR-1), not paid
  repeatedly at poll time.
- **Backward compatibility.** Zero changes required to any existing
  `triggers/*.json` or `processes/*.json` file; `parse_interval`'s behavior
  and error messages are untouched.

## Data model

No change to `Task` or `Observation`. New, implementation-internal (not
persisted) shapes:

- `CronSchedule` (in `ports/triggers.py`): the parsed 5-field sets plus
  `occurrence_at_or_before(now: str) -> str`. Not a dataclass persisted
  anywhere — built fresh from the JSON string on every `harness run` /
  `FilesystemProcessAdmin.write` validation, exactly like `parse_interval`'s
  `float` result today.
- `ScheduledTrigger`: `_interval: float | None`, `_cron: CronSchedule | None`
  (exactly one non-`None`), `_last_occurrence: Hashable | None` (replaces
  today's `_last_bucket: int | None` — same field, generalized type).
- `ProcessFields` (`ports/process_admin.py`): gains the cadence-choice fields
  (shape TBD in design, see Open Questions); `interval: str` remains for
  backward compatibility with existing callers/tests of the port.

## Interfaces

- **`triggers/*.json`** (unchanged shape except the new optional field):
  ```json
  { "kind": "scheduled", "cron": "0 6 * * 1", "check": "always", "target": {"workflow": "architect-review"} }
  ```
  vs. today's
  ```json
  { "kind": "scheduled", "interval": "30m", "check": "always", "target": {"step": "cleanup"} }
  ```
  — exactly one of `interval`/`cron` present.
- **`processes/*.json`** `trigger` block:
  ```json
  "trigger": { "cron": "0 6 * * 1" }
  ```
  vs. today's `"trigger": { "interval": "30m" }` — same XOR rule; the
  existing optional `trigger.kind` (`"schedule"`-only) reservation is
  untouched.
- **Admin UI** (`GET/POST /admin/processes`, `/admin/processes/{name}`): the
  existing "Schedule" form section gains a cadence toggle; no new routes.
- **`ProcessAdmin`** port: no new abstract methods; `ProcessFields`'s shape
  changes (additive/back-compat — see FR-6 and Open Questions).
- No changes to `Check`, `Observation`, `TaskSource`, `SourcePoller`,
  `dispatcher`, `consumer`, or `router` — the whole point of the "one seam"
  design is that everything above `ScheduledTrigger` is unaware cron exists.

## Dependencies and scope

**Depends on:** the generic-triggers/Process subsystem as it exists on
`origin/main` (ADR-0014/0015, `ScheduledTrigger`, `FilesystemTriggerRepository`,
`FilesystemProcessRepository`/`compile_process`, `ProcessAdmin`) — none of
which exists in this worktree yet (FR-0).

**Out of scope (explicitly, this increment):**
- Per-trigger timezone (`tz` field) — cron fields evaluate in UTC only;
  documented as a follow-up, not built now.
- 6-field cron with seconds; named months/days (`JAN`, `MON`, `SUN`);
  `@yearly`/`@daily`/`@hourly` shorthand aliases.
- "Suppress while an equivalent task is still open" — cron dedup is
  at-most-once-*per-occurrence*, the same limitation `per-interval` already
  has for plain intervals (a live-task-consulting mode remains a noted,
  separate follow-up per the existing gotcha).
- Removing or deprecating interval durations — additive only.
- A live cron-expression tester / "next N occurrences" preview in the admin
  UI beyond validate-on-save (natural follow-up once the parser exists).
- Changing `source_interval` semantics — still just polling granularity; a
  cron fire lands on the first poll at/after its occurrence, never exactly on
  it.

## Rough plan

1. **FR-0:** merge/rebase this worktree onto `origin/main`; confirm
   `pytest -q` and `tests/test_architecture.py` are green before writing any
   new code.
2. **FR-1:** `parse_cron`/`CronSchedule` in `ports/triggers.py` + a dedicated
   unit test module (field parsing, OR-rule, invalid syntax, impossible
   schedule, no-wall-clock-reads).
3. **FR-2:** generalize `ScheduledTrigger`'s bucket→occurrence seam, add the
   `cron` constructor param; extend `tests/test_scheduled_trigger.py` with
   cron-mode cases (fire-once, no-fire-between, restart-mid-occurrence via
   `SourcePoller._seen` seeding).
4. **FR-4:** `FilesystemTriggerRepository` reads/validates `cron`; extend
   `tests/test_fs_triggers.py`.
5. **FR-5:** `compile_process` reads/validates `trigger.cron`; extend
   `tests/test_fs_processes.py`, `tests/test_processes_e2e.py`.
6. **FR-6:** `ProcessFields`/`FilesystemProcessAdmin` round-trip; extend
   `tests/test_fs_process_admin.py`; `process_form.html` toggle; extend
   `tests/test_process_admin_api.py`; manual smoke of the admin UI.
7. **FR-7:** CLAUDE.md invariants/module-map/gotcha, spec addenda, new
   ADR-0018.
8. Full suite (`pytest -q`) including `tests/test_architecture.py` unmodified;
   manual smoke via this repo's `verify`/`run` conventions for the admin UI
   change.

## Open questions

1. **Catch-up-on-startup semantics.** If the harness was down over an
   occurrence, does it fire once on the first poll after restart (occurrence
   ≤ now, not yet in `_seen`), or skip straight to the next occurrence?
   **Recommendation: fire-once-on-catchup** — matches "at-most-once per
   occurrence" (never more than one catch-up fire) and is the natural
   consequence of reusing `_seen` unchanged; no special-casing needed. Record
   this explicitly in the design-step spec regardless of which way it lands.
2. **Validation `field` taxonomy.** Should a malformed cron string map to
   `field="cron"`, a malformed interval to `field="interval"`, and the
   both/neither structural error to one of those two, or to a new shared key
   (e.g. `"schedule"`/`"trigger"`)? This drives `ProcessFields`'s exact shape
   (FR-6) and the form's error-slot wiring — left for the design step.
3. **Cadence seam shape.** A bound closure computed once in
   `ScheduledTrigger.__init__` (`self._occurrence_fn: Callable[[str], Hashable]`)
   vs. a small `Cadence` class pair (`IntervalCadence`/`CronCadence`) — both
   satisfy "one internal seam" and keep `dedup_key` non-constant; the class
   pair reads better if a third cadence (e.g. a purely condition-driven
   trigger with no schedule at all) looks likely soon. Left for the design
   step to pick.
4. **Impossible-schedule look-back bound.** Recommend 4 years (spans every
   leap-day alignment) with a day-level (not per-minute) walk; the exact
   constant and walk strategy are a design-step sizing decision, not a
   product decision.
5. **Per-trigger `tz`.** Confirmed out of scope this increment (see Dependencies
   and scope); the UTC-only caveat must appear in both updated specs and in
   the admin UI's cron field hint text, not just in CLAUDE.md — operator runs
   Europe/Prague, and "06:00" silently meaning UTC is exactly the kind of
   surprise this task exists to avoid elsewhere.
6. **Admin UI cron preview.** Deferred (see Dependencies and scope); flag as a
   natural fast-follow once `CronSchedule.occurrence_at_or_before` exists,
   since a "next 3 fires" preview is then a two-line addition.
