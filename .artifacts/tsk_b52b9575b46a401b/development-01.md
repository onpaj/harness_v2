# Development: sub-hourly `harness service autoupdate` schedules

## Summary

Implemented `harness service autoupdate {install,uninstall,status}`: a second,
independent macOS LaunchAgent that fires on an operator-chosen interval down
to a single minute, runs `harness update`, and — only when the version
actually changed — kickstarts the main run-loop service. Followed
`design-01.md` as corrected by `architecture-01.md` (both corrections
applied as specified: a public `kickstart()` instead of the private
`_launchctl`, and both before/after version snapshots taken with
`installed_version_report()` so a no-op upgrade is recognized correctly).

## Files changed

### `src/harness/drivers/launchd.py`
- `parse_interval_minutes(text) -> int` — parses `<N>m`/`<N>h`/`<N>d`
  (case-insensitive suffix) into seconds; rejects `0`, bare integers,
  seconds-suffixed input, decimals and negatives, all via `ServiceError`.
- `format_interval(seconds) -> str` — the inverse, tries `d` → `h` → `m` and
  takes the first exact divisor (per architecture-01.md's simplification:
  this codebase's only writer of `StartInterval` always emits multiples of
  60, so a fallback for non-round-tripping values isn't needed).
- `periodic_plist_bytes(...)` — sibling to `plist_bytes`, not a
  generalization: sets `StartInterval`, omits `KeepAlive` entirely (not
  `False`), keeps `RunAtLoad: True`. Logs to
  `harness-autoupdate{,.error}.log`.
- `autoupdate_wrapper_script(...)` — sibling to `wrapper_script`; no
  `GITHUB_TOKEN` dance (not needed), just `export PATH=...` then
  `exec "<harness>" update --restart-service "<service_label>"`.
- `kickstart(uid, label)` — new public wrapper around
  `_launchctl(["kickstart", "-k", ...])`, added per architecture-01.md
  Correction 1 (the design's pseudocode called the private `_launchctl`
  directly from `cli.py`, which doesn't work). `load()`'s existing trailing
  `_launchctl(["kickstart", "-k", ...])` call was refactored to call
  `kickstart(uid, label)` instead, so the argument vector exists in exactly
  one place.

### `src/harness/cli.py`
- Imports: `autoupdate_wrapper_script`, `format_interval`, `kickstart`,
  `parse_interval_minutes`, `periodic_plist_bytes` added to the existing
  `from harness.drivers.launchd import (...)` block; added `import plistlib`
  (needed to read back `StartInterval` for `autoupdate status`).
- `_update()`: gained a `--restart-service LABEL` branch. Per
  architecture-01.md Correction 2, both the "before" and "after" snapshots
  are taken with `installed_version_report()` (not
  `version_string()`/`installed_commit()`, which produce a different string
  shape and would never compare equal). The extra "before" subprocess call
  only happens when `--restart-service` is given, so the flag's absence
  reproduces the previous output and subprocess-call-count exactly
  (`test_update_runs_uv_tool_upgrade` passes unmodified). A `kickstart()`
  that raises `ServiceError` is caught and reported on stderr with exit 1 —
  the upgrade succeeded but the restart didn't, and that must not be
  silently swallowed.
- `_print_service_report(label, target, report) -> int` — extracted from
  `_service_status`'s body (pure refactor, output unchanged) so
  `_service_autoupdate_status` can reuse it and only add its own
  `interval:` line.
- `_service_autoupdate_install/_uninstall/_status` — same shape as the
  existing `_service_install/_uninstall/_status`, reusing
  `_require_macos`, `service_entry_point`, `service_path_entries`,
  `load`/`unload`/`status`, `plist_path`. `install` prints a note that it
  also runs once immediately (`RunAtLoad` + `load()`'s trailing kickstart),
  per architecture-01.md tightening #1.
- `main()`: `service` subparser gains a fourth `autoupdate` sub-subparser
  with its own `install|uninstall|status`, mirroring `service` itself.
  `--every` is required on `install` (no implicit default schedule, per the
  plan's resolved open question). `update` gains `--restart-service LABEL`.
  No new dispatch logic — `args.handler(args)` at the bottom of `main()` is
  unchanged.

### `README.md`
New "Autoupdating the service" subsection under "Running it as a service",
documenting `--every` syntax, the restart-only-on-change behavior, the
immediate first-run side effect, and the separate log files.

### Tests
- `tests/test_launchd.py`: unit tests for `kickstart`, every
  `parse_interval_minutes` accept/reject path, `format_interval` round-trips
  (including the non-hour-divisible `5400 → "90m"` case), and content tests
  for `autoupdate_wrapper_script` / `periodic_plist_bytes` (strict bash,
  correct `exec` line, no secret, `StartInterval` set, no `KeepAlive` key,
  correct log paths) — same style as the existing `wrapper_script`/
  `plist_bytes` tests.
- `tests/test_cli.py`:
  - `--restart-service`: kickstart-on-change, no-kickstart-on-no-op (the
    test architecture-01.md flagged as the one that would have caught the
    string-mismatch bug), kickstart-failure → exit 1 + stderr message, and a
    regression test that omitting the flag keeps exactly one
    `installed_version_report()` call and the original manual-mode hint.
  - `service autoupdate`: required-action, required `--every`, non-macOS
    refusal for all three subcommands, bad-interval rejection,
    uninitialized-root rejection, a full `install` round trip (wrapper
    written with the right `exec` line, plist loaded via `load()` with the
    right path/label), idempotent re-install with a changed `--every`,
    `uninstall` (both the removes-something and the already-absent-noop
    cases), and `status` (both loaded-with-interval and not-loaded).

## Verification

```sh
.venv/bin/pytest -q
# 509 passed, 1 skipped (the opt-in real-claude smoke test), in ~17s
```

Also exercised the CLI surface directly:

```sh
.venv/bin/harness service autoupdate --help
.venv/bin/harness service autoupdate install --help
.venv/bin/harness update --help
```

confirming `--every`, `--label`, `--service-label` and `--restart-service`
are wired as designed with no dispatch changes needed in `main()`.

No changes to `models.py`, `router.py`, `dispatcher.py`, `consumer.py`,
`api/`, or `projection.py` — `test_architecture.py`'s import-boundary checks
pass unmodified, confirming this stayed a pure CLI/driver feature.
