# Design: cron expressions for trigger and Process schedules

Grounded against `origin/main` (fetched during planning and again for this
step — `src/harness/{ports/triggers.py,drivers/scheduled_trigger.py,
drivers/fs_triggers.py,drivers/fs_processes.py,ports/process_admin.py,
api/templates/admin/process_form.html}`, `CLAUDE.md` invariants #35–#41,
`docs/adr/0014,0015,0017`, `docs/superpowers/specs/2026-07-22-{generic-
triggers,processes}-design.md`). FR-0 (merging this worktree onto
`origin/main`) is a precondition established by the plan step and is not
re-litigated here.

This design resolves every open question the plan step left open (§ "Resolved
decisions" below), then specifies the seam, the parser/occurrence algorithm,
the schema, the admin UI change, and the doc/ADR content.

## Resolved decisions

These were the plan's five open questions. Each is now a design decision, not
a question:

1. **Catch-up-on-startup: fire once.** No special-casing — `ScheduledTrigger`
   never distinguishes "startup" from any other poll. On the first poll after
   a restart, `occurrence_at_or_before(now)` returns the same occurrence id it
   would have returned right before the restart; `SourcePoller._seen`,
   seeded from disk, already holds that occurrence's `dedup_key` if a task for
   it was emitted before the crash, so it's suppressed — otherwise (the
   harness was down *through* the whole occurrence and never fired) it fires
   exactly once, on the first poll after restart. This is not new mechanism;
   it is the existing interval-mode behavior, verified in FR-2's restart test.
2. **Field taxonomy:** `interval|cron|trigger|check|params|target|dedup|sink`
   — `trigger` (already used by `_check_trigger_kind`'s `kind` reservation)
   also covers the new both/neither structural error, since both are "the
   trigger block is malformed" rather than "this one value is malformed."
   `interval` and `cron` are new siblings for a malformed *value* in an
   otherwise-well-formed trigger block. See § Error taxonomy.
3. **Cadence seam shape: bound-method dispatch, not a class pair.** See §
   `ScheduledTrigger`. A `Cadence`/`IntervalCadence`/`CronCadence` class pair
   was considered and rejected for this increment: it buys nothing today (only
   two cadences exist, both already need their own parse step upstream) and
   adds two permanent files for a hypothetical third (condition-only) cadence
   that isn't scoped anywhere. If that third cadence lands later, promoting
   `self._occurrence` from a bound method to an injected strategy object is a
   local, backward-compatible refactor — noted as a follow-up trigger, not
   built now.
4. **Impossible-schedule look-back bound: 4×366 days**, walked at day
   granularity (not minute granularity) — see § Occurrence algorithm. Reusing
   the same day-matching predicate for both the parse-time existence check and
   the runtime backward walk means there is exactly one place that knows the
   day-of-month/day-of-week OR-rule.
5. **Per-trigger `tz`: confirmed out of scope.** UTC-only this increment. The
   caveat text appears in three places, not just CLAUDE.md: the ADR, the two
   spec addenda, and the admin UI's cron field hint (§ UX/UI) — this is
   exactly the class of surprise ("06:00" silently meaning UTC) the operator's
   own automations (weekly architect review, Monday 06:00) would otherwise hit
   first.

## Component design

### `ports/triggers.py` — `parse_cron` / `CronSchedule`

```python
@dataclass(frozen=True)
class CronSchedule:
    minutes: frozenset[int]   # 0-59
    hours: frozenset[int]     # 0-23
    doms: frozenset[int]      # 1-31
    dom_is_star: bool         # True iff the day-of-month field's raw text was exactly "*"
    months: frozenset[int]    # 1-12
    dows: frozenset[int]      # 0-6, POSIX 7 normalized to 0 (Sunday)
    dow_is_star: bool         # True iff the day-of-week field's raw text was exactly "*"

    def occurrence_at_or_before(self, now: str) -> str: ...

def parse_cron(text: str) -> CronSchedule: ...
```

`CronSchedule` is a plain immutable value (like `parse_interval`'s `float`) —
not persisted, rebuilt from the JSON string on every `harness run` /
`FilesystemProcessAdmin.write` validation.

**Field grammar** (per field, comma-separated list of items, each item one
of): a literal number; `*` (full range for that field); `a-b` (inclusive
range); `*/n` or `a-b/n` (stepped, base is `*` or a range) — matching FR-1's
scoped grammar. `dom_is_star`/`dow_is_star` are `True` only when that field's
*entire raw text* is the single character `*` (not `*/2`, not `1-31`) — this
is the same narrow definition vixie-cron and `croniter` use for "which day
fields participate in the OR-rule," and it's the one place the spec must be
explicit, since it changes *matching semantics*, not just range validation.

**Parsing a single field** (shared helper, parametrized by the field's
`(min, max)` and, for day-of-week, a normalizer that folds `7` to `0`):

```
parse_field(raw, lo, hi, normalize=identity) -> frozenset[int]:
    values = set()
    for item in raw.split(","):
        base, _, step_text = item.partition("/")
        step = int(step_text) if step_text else 1
        if step <= 0: raise ValueError(...)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start, end = (int(x) for x in base.split("-", 1))
        else:
            start = end = int(base)
        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise ValueError(f"value out of range in {raw!r}: expected {lo}-{hi}")
        values.update(normalize(v) for v in range(start, end + 1, step))
    return frozenset(values)
```

`parse_cron` splits `text.split()` (whitespace), requires exactly 5 fields
(else `ValueError` naming the actual field count), parses minute (`0,59`),
hour (`0,23`), dom (`1,31`), month (`1,12`), dow (`0,7`, normalizing `7→0`),
records `dom_is_star`/`dow_is_star` from the raw field text, then runs the
**impossible-schedule check** (below) before returning. Every `ValueError`
raised anywhere in this path is what `FilesystemTriggerRepository`/
`compile_process` catch and re-wrap — `parse_cron` itself never raises
`TriggerValidationError`/`ProcessValidationError` (mirrors `parse_interval`,
which is also validation-framework-agnostic).

**Day-matching predicate** (the one place the OR-rule lives, shared by
validation and runtime):

```
def _day_matches(date: date, schedule: CronSchedule) -> bool:
    if date.month not in schedule.months:
        return False
    dom_ok = schedule.dom_is_star or date.day in schedule.doms
    dow_ok = schedule.dow_is_star or (date.weekday() + 1) % 7 in schedule.dows
    # date.weekday(): Mon=0..Sun=6 -> POSIX Sun=0..Sat=6 via (+1) % 7
    if schedule.dom_is_star and schedule.dow_is_star:
        return True
    if schedule.dom_is_star or schedule.dow_is_star:
        return dom_ok and dow_ok  # exactly one is a real constraint -> plain AND
    return dom_ok or dow_ok        # both restricted -> POSIX OR-rule
```

### Occurrence algorithm

`occurrence_at_or_before(now: str) -> str`:

1. Parse `now` (`datetime.fromisoformat(now.replace("Z", "+00:00"))`), floor
   seconds/microseconds to 0 (cron's resolution is the minute — two polls
   inside the same minute must not be treated as different occurrences even
   if seconds differ).
2. Walk backward day by day, starting at `now`'s date, capped at
   `_LOOKBACK_DAYS = 4 * 366` days:
   - Skip the day if `_day_matches(day, schedule)` is `False`.
   - On a matching day, build the set of `(hour, minute)` pairs allowed by
     `schedule.hours × schedule.minutes`; if `day == now`'s date, keep only
     pairs `≤ (now.hour, now.minute)`; take the **maximum** qualifying pair
     (last fire of the day, at-or-before `now` on today). Both sets are
     small (≤24, ≤60) so this is a bounded in-memory max, not a scan — cheap
     even though this is where "the whole day" is considered.
   - If a pair is found, return the ISO minute-aligned UTC timestamp for
     `(day, hour, minute)` (`...T{hh:02}:{mm:02}:00Z`). Stop.
   - Otherwise move to the previous day (full 24×60 grid, no `≤ now` filter)
     and continue.
3. Exhausting the cap without a match raises `RuntimeError` — unreachable for
   any `CronSchedule` that passed `parse_cron`'s validation (step 3 below
   proves a match exists inside this same window), so this is a defensive
   invariant check, not a user-facing error path.

**Cost**: the common case (e.g. weekly) matches on the first or second day
walked back; only a schedule that matches *no* day inside 4×366 days walks the
full cap — and that schedule was already rejected at parse time, so this cost
is paid at most once, at load time, never repeatedly at poll time. No per-poll
minute-by-minute scan over the multi-year bound.

**Impossible-schedule detection** (inside `parse_cron`, after building the
candidate `CronSchedule`): walk forward day by day from a **fixed reference
date** — `date(2028, 1, 1)` (chosen only because it starts a window that
spans a leap year and is far from any real trigger's `now`; it is a validation
fixture, not a clock read) — for `_LOOKBACK_DAYS` days, calling `_day_matches`
on each. If none match, raise
`ValueError(f"cron expression {text!r} can never occur")`. This reuses
`_day_matches`, so the OR-rule and the "can this ever fire" check can never
disagree with each other. `"0 6 31 2 *"` (Feb 31st, `dow="*"` so no OR
rescue) fails this scan every year in the window → rejected at load time.

### `ScheduledTrigger` — one occurrence seam

```python
def __init__(
    self, *, name, clock, check, workflow=None, step=None,
    interval: float | None = None,
    cron: CronSchedule | None = None,
    repository=None, worktree_root=None, dedup="per-interval", sink=None,
) -> None:
    if (interval is None) == (cron is None):
        raise ValueError("exactly one of interval/cron must be set")
    ...
    if cron is not None:
        self._cron = cron
        self._occurrence = self._cron_occurrence
    else:
        self._interval = interval
        self._occurrence = self._interval_occurrence
    self._last_occurrence: Hashable | None = None

def poll(self) -> list[Task]:
    now = self._clock.now()
    occurrence = self._occurrence(now)
    if occurrence == self._last_occurrence:
        return []
    self._last_occurrence = occurrence
    observations = self._check.evaluate()
    return [self._task_for(obs, occurrence, now) for obs in observations]

def _interval_occurrence(self, now: str) -> int:
    epoch = datetime.fromisoformat(now.replace("Z", "+00:00")).timestamp()
    return floor(epoch / self._interval)

def _cron_occurrence(self, now: str) -> str:
    return self._cron.occurrence_at_or_before(now)
```

Deliberate narrowing vs. the plan's FR-2 wording: the constructor takes
`cron: CronSchedule | None`, **not** `str | CronSchedule`. `interval` is
already accepted pre-parsed (`float`, not `"30m"`); `cron` follows the same
convention for symmetry — the string only ever exists in the JSON file /
form submission, and `FilesystemTriggerRepository`/`compile_process` (the only
two callers) already have a `parse_cron` step immediately upstream. No caller
needs `ScheduledTrigger` to parse a raw string itself.

`_bucket`/`_last_bucket` are renamed `_occurrence`/`_last_occurrence`
throughout; `_task_for`'s `bucket` parameter is renamed `occurrence` (type
`Hashable`) and passed straight into `_dedup_key`, which is otherwise
unchanged — it already treats the bucket as an opaque, stringifiable
identity via `dedup_key(...)`. `per-state` dedup (`obs.state_key`) doesn't
reference occurrence at all, so it is untouched by this seam, confirming the
CLAUDE.md invariant that cadence only decides *when* the check runs.

### `FilesystemTriggerRepository` (`drivers/fs_triggers.py`)

`_build_one` replaces its single `if "interval" not in raw: ... interval =
parse_interval(...)` block with:

```
has_interval = "interval" in raw
has_cron = "cron" in raw
if has_interval == has_cron:
    raise TriggerValidationError(
        f"trigger {path.name} must have exactly one of interval/cron"
    )
if has_cron:
    try:
        cron = parse_cron(raw["cron"])
    except (ValueError, TypeError) as error:
        raise TriggerValidationError(
            f"trigger {path.name} has an invalid cron expression: {error}"
        ) from None
    interval = None
else:
    try:
        interval = parse_interval(raw["interval"])
    except (ValueError, TypeError) as error:
        raise TriggerValidationError(
            f"trigger {path.name} has an invalid interval: {error}"
        ) from None
    cron = None
```

...then passes `interval=interval, cron=cron` into `ScheduledTrigger`.
`TriggerValidationError` gains no `field` attribute — there is no trigger
admin UI (confirmed out of scope in both this task and the original
generic-triggers spec), so "name the file in the message" remains the whole
error contract, unchanged from today.

### `compile_process` (`drivers/fs_processes.py`)

`_parse_interval(where, trigger)` (returns `float`) is replaced by
`_parse_cadence(where, trigger) -> tuple[float | None, CronSchedule | None]`:

```
def _parse_cadence(where, trigger):
    if not isinstance(trigger, dict):
        raise ProcessValidationError(
            f"process {where} must have a trigger object", field="trigger"
        )
    has_interval, has_cron = "interval" in trigger, "cron" in trigger
    if has_interval == has_cron:
        raise ProcessValidationError(
            f"process {where} trigger must have exactly one of interval/cron",
            field="trigger",
        )
    if has_cron:
        try:
            return None, parse_cron(trigger["cron"])
        except (ValueError, TypeError) as error:
            raise ProcessValidationError(
                f"process {where} has an invalid cron expression: {error}",
                field="cron",
            ) from None
    try:
        return parse_interval(trigger["interval"]), None
    except (ValueError, TypeError) as error:
        raise ProcessValidationError(
            f"process {where} has an invalid interval: {error}", field="interval"
        ) from None
```

`compile_process` calls `interval, cron = _parse_cadence(where, raw.get("trigger"))`
before `_check_trigger_kind` (unaffected — it still reads `trigger.get("kind",
"schedule")` off the same dict) and passes `interval=interval, cron=cron` into
`ScheduledTrigger`. The module docstring's `field` enum
(`interval|check|params|target|dedup|sink`) gains `cron` and `trigger` (the
latter already existed as a value `_check_trigger_kind` produces for an
unsupported `trigger.kind`; it now also covers the both/neither structural
error — same field, two different messages, per decision 2 above).

### `ProcessFields` / `FilesystemProcessAdmin` (`ports/process_admin.py`,
`drivers/fs_processes.py`)

```python
@dataclass(frozen=True)
class ProcessFields:
    check: str
    target_kind: str
    target: str
    cadence: str = "interval"   # "interval" | "cron" — which value field is authoritative
    interval: str = ""
    cron: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sink_kind: str = "none"
    dedup: str = "per-interval"
```

`cadence` mirrors the existing `target_kind`/`target` pattern exactly: a
discriminator plus (now two, not one) value fields. It is *not* inferred from
"whichever of interval/cron is non-blank," even though that would work for a
well-formed round trip — an explicit discriminator is what lets a user who
toggles to "cron" but leaves the box empty get `errors.cron` (not
`errors.interval`), because `_raw_from_fields` writes based on `cadence`, not
on blankness:

```python
def _raw_from_fields(fields: ProcessFields) -> dict:
    trigger = {"cron": fields.cron} if fields.cadence == "cron" else {"interval": fields.interval}
    return {
        "trigger": trigger,
        "action": {"check": fields.check, "params": dict(fields.params)},
        "target": {fields.target_kind: fields.target},
        "dedup": fields.dedup,
        "sink": {"kind": fields.sink_kind},
    }

def _fields_from_raw(raw: dict) -> ProcessFields:
    trigger = raw["trigger"]
    cadence = "cron" if "cron" in trigger else "interval"
    ...
    return ProcessFields(
        ...,
        cadence=cadence,
        interval=trigger.get("interval", ""),
        cron=trigger.get("cron", ""),
    )
```

Field reordering note: `interval` moves from the first field to a defaulted
position after `check`/`target_kind`/`target` (a dataclass requires
non-default fields before defaulted ones, and `interval` is no longer always
required). Every existing call site in `tests/test_fs_process_admin.py`
constructs `ProcessFields` with keyword arguments, so this reorder is
source-compatible; no call site needs to change for this reason alone.

`FilesystemProcessAdmin.write` is otherwise unchanged: `_raw_from_fields` →
`compile_process` (now cron-aware) → `ProcessValidationError(field=...)` →
`ProcessAdminValidationError({field: message})`, nothing written on failure.

## UX/UI — admin process form

Only `api/templates/admin/process_form.html`'s "Schedule" section changes;
no new route (`ProcessAdmin` gains no new abstract method — `cadence` rides
inside the existing `ProcessFields`/form-POST body).

```
┌─ ① Schedule ──────────────────────────────────────────────┐
│ ( Interval )  ( Cron )                     <- seg toggle   │
│                                                             │
│  When "Interval" is selected (today's UI, unchanged):       │
│  [5m] [15m] [30m] [1h] [6h] [24h]          <- chip row      │
│  Interval: [ 30m________________ ]                          │
│  A number with a unit: s seconds, m minutes, h hours.        │
│                                                             │
│  When "Cron" is selected:                                   │
│  Cron: [ 0 6 * * 1____________________ ]                     │
│  minute hour day-of-month month day-of-week, evaluated in    │
│  UTC. e.g. "0 6 * * 1" = every Monday at 06:00 UTC — not      │
│  06:00 local time; a per-trigger timezone isn't built yet.    │
│  [ field-error slot, shown only when errors.cron is set ]     │
└─────────────────────────────────────────────────────────────┘
```

- The toggle is a `seg` control (same markup/class the Target section already
  uses for `workflow`/`step`, `<input type="radio" name="cadence">`), not a
  new widget kind.
- Both panels always render in the DOM (server round-trip keeps whichever
  value the operator submitted); JS shows only the one matching the checked
  radio, mirroring `syncTargetKind()`'s existing show/hide pattern for
  `target_kind`.
- Error slots are siblings, like today's `errors.interval`: `errors.interval`
  stays under the interval input, a new `errors.cron` sits under the cron
  input — `compile_process`'s `field="trigger"` (both/neither) has no single
  input to anchor to, so it's surfaced as the section-level banner the form
  already has for whole-section problems (the same place `errors.trigger`
  would land if `_check_trigger_kind` ever produced one, i.e. the existing
  `{{ 'error' if errors.interval or errors.cron or errors.trigger }}` pattern
  on the section wrapper, extending the current
  `errors.interval`-only condition).
- The live summary line ("Every **30m**, run **always** …") gains a cron
  branch: "**Every 0 6 * * 1** (UTC) …" when cron is selected — cosmetic,
  same `updateSummary()` function, one added conditional.
- No live "next N fires" preview (explicitly deferred, decision unchanged
  from the plan) — validate-on-save is the only feedback this increment
  gives; the field-error slot already tells the operator immediately if the
  expression doesn't parse or can never fire (`ValueError` from `parse_cron`
  surfaces verbatim as the field message, same as a bad interval today).

## Data schemas

### `triggers/*.json` (top-level, unchanged except the new field)

```json
{ "kind": "scheduled", "cron": "0 6 * * 1", "check": "always", "target": {"workflow": "architect-review"} }
```
```json
{ "kind": "scheduled", "interval": "30m", "check": "always", "target": {"step": "cleanup"} }
```
Exactly one of `interval`/`cron` present; every other key is unchanged.

### `processes/*.json` `trigger` block

```json
"trigger": { "cron": "0 6 * * 1" }
```
```json
"trigger": { "interval": "1h" }
```
Same XOR rule; the existing optional `trigger.kind` (`"schedule"`-only)
reservation is untouched and can appear alongside either value:
`"trigger": {"cron": "0 6 * * 1", "kind": "schedule"}`.

### `ProcessFields` (`ports/process_admin.py`) — see § Component design above
for the exact shape (`cadence` discriminator + `interval`/`cron` values).

### `CronSchedule` (`ports/triggers.py`) — internal, not persisted; see §
Component design for the exact fields
(`minutes/hours/doms/dom_is_star/months/dows/dow_is_star`).

### No change to `Task`, `Observation`, or any wire/event payload.
`ScheduledTrigger._task_for` still produces a plain `Task`; `dedup_key`'s
third positional argument changes from an `int` bucket to a `Hashable`
occurrence (`int` for interval, `str` for cron) — `dedup_key` already
stringifies it (`":".join([kind, *(str(part) for part in parts)])`), so no
change to that function or to the persisted `Task.dedup_key`'s shape (it was
always just a string).

## Error taxonomy (final)

| `field` value | Raised when |
|---|---|
| `trigger` | the `trigger` object is missing/not-a-dict, has both `interval` and `cron`, has neither, or names an unsupported `trigger.kind` (existing `_check_trigger_kind` path, untouched) |
| `interval` | `interval` is present but fails `parse_interval` |
| `cron` | `cron` is present but fails `parse_cron` (malformed syntax, out-of-range field, or a schedule that can never occur) |
| `check` / `params` / `target` / `dedup` / `sink` | unchanged from today |

`FilesystemTriggerRepository`'s `TriggerValidationError` keeps its existing
no-`field`, file-naming-only contract (no trigger admin UI exists to map a
field onto).

## Documentation content (for the docs/ADR step to apply)

- **CLAUDE.md invariant #37** ("owns its cadence via the `Clock`, not via a
  loop") — reworded to name both cadences: *"`poll()` gates on an occurrence
  identity — `floor(now / interval)` for an interval cadence, the most recent
  matching UTC timestamp `≤ now` for a cron cadence — and returns `[]` cheaply
  between fires; the shared `source_interval` is only polling granularity,
  true for either cadence."*
- **CLAUDE.md invariant #38** ("bucket-keyed dedup") — reworded: *"A scheduled
  trigger's `per-interval` `dedup_key` is occurrence-keyed (the interval
  bucket, or the cron occurrence timestamp), giving at-most-once per
  occurrence across restarts for either cadence — the same non-constant-key
  guarantee, one seam."*
- **New gotcha**: *"Cron fields are UTC-only, always. `"0 6 * * 1"` fires at
  06:00 **UTC**, not 06:00 in the operator's local timezone (Europe/Prague,
  currently UTC+1/+2) — there is no per-trigger `tz` field yet. Say so in the
  admin UI's cron hint, not only here."*
- Module map: `ports/triggers.py` gains "`parse_cron`/`CronSchedule` — a
  stdlib-only 5-field cron parser and occurrence function, the cron twin of
  `parse_interval`." `drivers/scheduled_trigger.py`'s entry gains "one
  occurrence seam serves both an interval and a cron cadence." `fs_triggers.py`
  / `fs_processes.py` entries gain "validates `cron` the same way as
  `interval` — exactly one of the two, fail-fast."
- **Spec addenda**: a dated (2026-07-23) section appended to both
  `2026-07-22-generic-triggers-design.md` (superseding its "a real scheduler
  daemon (cron) ... [is] out of scope" line) and `2026-07-22-processes-design.md`,
  each stating: cron is now supported as an alternative cadence *expression*
  within the same clock-gate architecture (no new loop, no new port); UTC-only,
  `tz` deferred; fire-once-on-catchup; cross-referencing this feature's own
  spec/plan/design docs and the new ADR.
- **`docs/adr/0018-cron-cadence-stdlib-parser.md`** (next free number after
  0017), same shape as ADR-0017 (Status/Context/Decision/Consequences):
  - *Decision:* an in-repo stdlib (`datetime`/`calendar`) 5-field parser, not
    `croniter` — precedent `urllib` over `requests` (no new runtime
    dependency for a POC-scale need: 5-field numeric syntax only, no
    timezone database, no shorthand aliases).
  - *Decision:* UTC-only this increment; per-trigger `tz` is a noted,
    separate follow-up (would need a timezone-database dependency or stdlib
    `zoneinfo`, deliberately not pulled in yet).
  - *Decision:* fire-once-on-catchup, as a consequence of reusing
    `SourcePoller._seen` unchanged rather than a bespoke rule.
  - *Consequences:* `ScheduledTrigger` gains one seam (bound-method dispatch,
    not a class hierarchy — decision 3 above) shared by both cadences;
    `parse_cron` rejects an impossible schedule at load time via a bounded
    (4×366-day) existence scan, so a mistyped `"31 2"` fails `harness run`
    immediately instead of silently never firing.

## Testability notes

Every new function takes `now`/a reference date as an explicit argument —
`parse_cron`, `CronSchedule.occurrence_at_or_before`, `_day_matches`, and both
`ScheduledTrigger._interval_occurrence`/`_cron_occurrence` read no wall clock
and sleep nowhere, so the whole surface is `FakeClock`-testable with literal
ISO strings in, literal ISO strings or ints out — consistent with every other
port in this codebase (`Clock`, `AgentRunner`, `Forge`).
