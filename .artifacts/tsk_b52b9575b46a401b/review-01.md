# Review: sub-hourly `harness service autoupdate` schedules

## Verdict

`done`. The implementation matches design-01.md as corrected by
architecture-01.md, meets every FR/AC in plan-01.md, and the full suite
(509 passed, 1 skipped) is green. I read plan-01.md, architecture-01.md,
development-01.md, and diffed the actual code (`git diff HEAD~1`) rather than
trusting the development summary.

## Conformance to spec (plan-01.md)

- **FR-1** (`install --every`): `parse_interval_minutes` accepts `<N>m/h/d`,
  rejects `0m` (AC1–AC3), rejects bare integers/`90s`/decimals/negatives with
  distinct `ServiceError`s (AC4), `_require_macos()` reused verbatim (AC5),
  re-install with a different `--every` converges via `load()`'s existing
  bootout/bootstrap dance — verified by
  `test_service_autoupdate_install_is_idempotent` (AC6).
- **FR-2** (update-and-restart): `_update --restart-service LABEL` snapshots
  `installed_version_report()` before and after, kickstarts only on a real
  diff (AC1/AC2 — the no-op case has its own regression test), default
  `--service-label` is `com.harness`/overridable (AC3), a failed
  `uv tool upgrade` returns before any restart logic runs (AC4).
- **FR-3** (uninstall): removes only the autoupdate plist, `unload`s only
  the autoupdate label; no-op-when-absent exits 0 with a message (AC1/AC2).
- **FR-4** (status): prints label/plist/state plus a decoded `interval:`
  line, reusing `_print_service_report` for the shared block; exit code
  0/1 matches `_service_status` (AC1–AC3). Log paths
  (`harness-autoupdate{,.error}.log`) match the plan's table.
- **FR-5** (pure parser): `parse_interval_minutes` does no I/O, raises only
  `ServiceError`, fully unit-tested for every rejection path in isolation.
- **NFRs**: no secret in the autoupdate wrapper/plist (explicit tests), no
  new locking code — launchd's single-instance-per-label guarantee is relied
  on and documented in the README, matching the plan's explicit choice not
  to build harness-side locking.

## Adherence to architecture-01.md's corrections

Both mandatory corrections were applied exactly as specified, not just
claimed:

- **Correction 1** (public `kickstart`): `cli.py` imports and calls
  `kickstart(os.getuid(), restart_service)`; `_launchctl` is never imported
  into `cli.py` (confirmed via the diff's import list). `load()`'s own
  trailing kickstart now calls the new public function instead of
  duplicating the argv, exactly as the architecture doc recommended.
- **Correction 2** (comparable before/after snapshots): both `before` and
  `after` in `_update` come from `installed_version_report()`. The
  regression test `test_update_restart_service_does_not_kickstart_on_a_noop_upgrade`
  is present and is exactly the test the architecture doc called out as
  "the one that would have caught the bug."
- Minor tightenings all applied: the immediate-first-run note is printed by
  `install` and documented in the README; `format_interval` is the simple
  d→h→m first-exact-divisor version (no dead fallback machinery); the status
  block is factored into `_print_service_report` and reused by both status
  handlers.

## Invariants / module boundaries

Touches only `drivers/launchd.py` and `cli.py` (plus tests/README) — no
`models.py`, `router.py`, `dispatcher.py`, `consumer.py`, `api/`, or
`projection.py` changes, so invariants #1 and #5 aren't in play for this
feature and `test_architecture.py` passes unmodified as part of the full
suite.

## Correctness spot-checks

- `kickstart` is defined after `load()` in the file but `load()` only
  resolves the name at call time, so the forward reference is safe (verified
  by importing the module and calling `load`/`kickstart` directly).
- `_update`'s new branch is strictly additive: `getattr(args, "restart_service", None)`
  guards the extra "before" subprocess call, and
  `test_update_without_restart_service_keeps_the_manual_hint` asserts exactly
  one `installed_version_report()` call and the original hint text when the
  flag is absent — the flag's absence reproduces prior behavior byte-for-byte.
- A `kickstart` that raises `ServiceError` is caught, printed to stderr, and
  returns exit 1 rather than propagating a traceback — covered by
  `test_update_restart_service_reports_a_failed_kickstart`.
- `service_autoupdate_install` requires an initialized root and a real entry
  point before writing anything, mirroring `_service_install`'s existing
  guards exactly (same order, same error messages).
- Full suite: `.venv/bin/pytest -q` → 509 passed, 1 skipped, 16.7s.

## Non-binding notes (not blocking)

- `_service_autoupdate_status`'s `interval = definition.get("StartInterval")`
  falls back to `"unknown"` via a falsy check (`if interval else 'unknown'`);
  since `parse_interval_minutes` floors at 60s this is unreachable for any
  plist this CLI itself writes, so it's not a real bug — just worth knowing
  if someone ever hand-edits a plist to `StartInterval: 0`.

```json
{"outcome": "done", "summary": "Implementation matches design-01.md as corrected by architecture-01.md: public kickstart() replaces the private _launchctl call, and _update's before/after snapshots both use installed_version_report() (verified by the no-op regression test). All FR/AC items in plan-01.md are met, no architecture invariants are touched (pure CLI/driver feature), and the full suite passes (509 passed, 1 skipped)."}
```
