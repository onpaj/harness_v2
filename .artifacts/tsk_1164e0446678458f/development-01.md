# Development: cron expressions for trigger and Process schedules

Implemented FR-0 through FR-7 from `plan-01.md`, following `design-01.md` and
`architecture-01.md` verbatim (no deviations were needed — both artifacts held
up against the real code).

## FR-0 — merge onto `origin/main`

`git rebase origin/main` (this branch had zero source-file changes, only
`.artifacts/` commits, exactly as `architecture-01.md` predicted) — no
conflicts. `.venv` didn't exist yet in this worktree; created it
(`python3.11 -m venv .venv && pip install -e ".[dev]"`) and confirmed
`pytest -q` was green (1221 passed, 1 skipped) before writing any new code.

## What changed

### `src/harness/ports/triggers.py` (FR-1)

Added, in the order `architecture-01.md` recommended: `_LOOKBACK_DAYS = 4 * 366`
(module constant, shared by both directions), `_parse_field` (per-field grammar:
number, `*`, list, range, step), `_day_matches` (the one place the POSIX
day-of-month/day-of-week OR-rule lives), `CronSchedule` (frozen dataclass,
`occurrence_at_or_before(now) -> str`, a bounded backward day-walk), and
`parse_cron(text) -> CronSchedule` (5-field validation + a forward
impossible-schedule scan from a fixed reference date, reusing `_day_matches`).

### `src/harness/drivers/scheduled_trigger.py` (FR-2)

`ScheduledTrigger.__init__` now takes `interval: float | None = None` and
`cron: CronSchedule | None = None`, XOR-validated right after the existing
`workflow`/`step` XOR check. `_bucket`/`_last_bucket` → `_occurrence`/
`_last_occurrence` (a bound method assigned once in `__init__`:
`_interval_occurrence` or `_cron_occurrence`); `_task_for`/`_dedup_key`'s
`bucket` parameter renamed `occurrence`. `poll()`, `_dedup_key`'s `per-interval`
arm and the untouched `per-state` arm are otherwise unchanged.

### `src/harness/drivers/fs_triggers.py` (FR-4)

`_build_one` replaces the single `interval`-required check with an XOR check
over `interval`/`cron`, parsing whichever is present (`parse_cron`/
`parse_interval`) and passing both (one `None`) into `ScheduledTrigger`.

### `src/harness/drivers/fs_processes.py` (FR-5)

`_parse_interval` renamed in place to `_parse_cadence(where, trigger) ->
tuple[float | None, CronSchedule | None]`: `field="trigger"` for a
missing/non-dict trigger block or one with both/neither cadence value,
`field="interval"`/`field="cron"` for a present-but-invalid value.
`_check_trigger_kind` runs after (order preserved, as
`architecture-01.md` flagged — pre-existing behavior, not new). `_raw_from_fields`/
`_fields_from_raw` are cadence-aware: they write/read whichever of
`{"interval": ...}`/`{"cron": ...}` the fields' `cadence` selects.

### `src/harness/ports/process_admin.py` (FR-6)

`ProcessFields` gains `cadence: str = "interval"` and `cron: str = ""`;
`interval` moved from a required positional-first field to a defaulted one
after `check`/`target_kind`/`target` (source-compatible with every keyword-arg
call site — confirmed, and fixed the two files that weren't:
`tests/test_process_admin_api.py`'s positional `ProcessFields("1h", "always",
"workflow", "default")` calls, which `architecture-01.md` specifically flagged
as unchecked by the design step).

### `src/harness/api/routes.py`

Not named in the design's file list, but required (architecture doc's flagged
risk): `_process_fields_dict`, `_process_fields_from`, `_process_fields_from_form`,
`_process_form_context` and `_NEW_PROCESS_FIELDS` all gained `cadence`/`cron`.
The JSON PUT API stays backward-compatible — a payload with only `interval`
still defaults `cadence` to `"interval"`.

### `src/harness/api/templates/admin/process_form.html` (FR-6)

The Schedule section gained a `seg` cadence toggle (the same markup/JS pattern
`target_kind` already uses), two always-rendered panels (`#cadence-interval`,
`#cadence-cron`, toggled via `hidden`, never `disabled` — confirmed by grep
and by an HTTP-level test that the rendered page contains no `disabled`
attribute, so the hidden panel's value still POSTs), a cron hint stating the
UTC-only caveat explicitly, and an `errors.cron` slot beside `errors.interval`.
`errors.trigger` (the both/neither structural error) renders as the existing
section-level banner. `updateSummary()`'s JS branches on cadence for the live
preview line.

### Docs (FR-7)

- `CLAUDE.md`: invariants #37/#38 reworded to describe both cadences (occurrence
  identity, not bucket); a new gotcha for UTC-only cron semantics; the existing
  non-constant-`dedup_key` gotcha reworded to be cadence-generic; module-map
  entries for `ports/triggers.py`/`drivers/scheduled_trigger.py`/
  `drivers/fs_triggers.py`/`drivers/fs_processes.py` updated.
- `docs/adr/0018-cron-cadence-stdlib-parser.md` (new): the stdlib-vs-`croniter`
  decision, the day-walk-vs-minute-scan decision, UTC-only, fire-once-on-catchup.
- Dated (2026-07-23) addenda appended to both
  `docs/superpowers/specs/2026-07-22-generic-triggers-design.md` (superseding
  its "cron is out of scope" line) and `2026-07-22-processes-design.md`.

## Tests added (42 new, all green; 1263 passed / 1 skipped total, up from 1221/1)

- `tests/test_cron_schedule.py` (new, 22 tests): field grammar, the OR-rule
  (both restricted / one restricted / neither), Sunday `0`≡`7`, minute-level
  granularity, and the Feb-29 canary pair
  (`architecture-01.md`'s flagged risk) — `parse_cron("0 0 29 2 *")` is
  accepted, and `occurrence_at_or_before` correctly walks back to 2024 from a
  2026 (non-leap) `now`. Every malformed/impossible case from the plan's AC
  list raises `ValueError`.
- `tests/test_scheduled_trigger.py` (+15): fires once per cron occurrence, no
  fire within the same occurrence, dedup key differs across occurrences,
  `per-state` dedup stays occurrence-blind under cron too, the XOR
  constructor check, and the restart-mid-occurrence test (a fresh
  trigger/poller pair, `SourcePoller._seen` seeded from a previously emitted
  task's persisted `dedup_key`, ticking again inside the same occurrence
  yields nothing) — the cron twin of the existing interval restart guarantee.
- `tests/test_fs_triggers.py` (+4): valid cron file, malformed cron, both
  present, neither present — each naming the file.
- `tests/test_fs_processes.py` (+4): valid cron process (and it actually
  fires), malformed cron (`field == "cron"`), both/neither
  (`field == "trigger"`), explicit `trigger.kind: "schedule"` alongside cron.
- `tests/test_fs_process_admin.py` (+3): cron round-trip through
  `FilesystemProcessAdmin`, a malformed-cron submission writes nothing and maps
  to `field="cron"`, and a cron-cadence submission with a blank box maps to
  `errors.cron` (not `errors.interval`) — the reason `cadence` is an explicit
  discriminator, not inferred from blankness.
- `tests/test_process_admin_api.py` (+4, and fixed 8 positional `ProcessFields`
  calls to keyword form): create a cron process via the HTML form, a bad cron
  string surfaces as a field error and writes nothing, the edit page
  pre-fills the cron value with the right radio checked, and an assertion that
  the rendered page contains no `disabled` attribute (the hidden-vs-disabled
  risk `architecture-01.md` called out).

## Manual verification

Ran a standalone script against the real `FilesystemProcessAdmin`/FastAPI app
(TestClient, no mocks) confirming: the "New process" page renders the cadence
toggle and `syncCadence`/both panels; submitting `cadence=cron, cron="0 6 * *
1"` creates and round-trips a working process; submitting an unparseable cron
string returns 200 with an inline `field-error` and writes nothing (`admin.list()`
stays empty) — never a 500. Also manually verified `parse_cron`/
`occurrence_at_or_before` against the worked examples from the task (Monday
06:00 UTC weekly, `*/15`, `9-17` + weekday range, Feb 29).

## How to verify

```sh
.venv/bin/pytest -q                              # 1263 passed, 1 skipped
.venv/bin/pytest -q tests/test_cron_schedule.py tests/test_scheduled_trigger.py \
  tests/test_fs_triggers.py tests/test_fs_processes.py tests/test_fs_process_admin.py \
  tests/test_process_admin_api.py tests/test_architecture.py
```

No new runtime dependency (`datetime`/`calendar`, stdlib only, per ADR-0018).
Every existing `triggers/*.json`/`processes/*.json` file stays valid unchanged
— the `interval` path's parsing, error messages and behavior are untouched.
