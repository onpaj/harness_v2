# Plan: sub-hourly `harness service autoupdate` schedules (minute granularity)

## Summary

Add a new `harness service autoupdate` command family that installs a second,
independent macOS LaunchAgent which periodically runs the update-and-restart
sequence for the harness on a schedule the operator controls down to a single
minute (e.g. every 15 minutes), not just hourly-or-coarser. Today `harness
update` is a manual, one-shot command; this makes it schedulable, and — because
an update that nobody restarts is invisible — the scheduled run also restarts
the main run-loop service when the version actually changed.

## Context

`harness update` (`src/harness/cli.py`) already upgrades the installed tool via
`uv tool upgrade harness`, and `harness service {install,uninstall,status}`
(`src/harness/drivers/launchd.py` + `cli.py`) already manages one LaunchAgent
that supervises `harness run`. **Neither today's codebase nor its history
(`7bc0e6e`) contains any autoupdate scheduling at all** — no command, no
launchd `StartInterval`/`StartCalendarInterval` wiring, no duration parsing.
This plan therefore designs the feature from scratch rather than extending an
existing coarser-grained scheduler; "sub-hourly / minute granularity" is
folded in as the baseline unit, not a later refinement.

The natural launchd primitive for "run every N minutes, forever" is
`StartInterval` (a plain seconds count), which already supports minute
granularity trivially — the gap is entirely in the harness's own CLI/driver
layer: there is no code path today that (a) parses an operator-supplied
interval, (b) builds a periodic (as opposed to `KeepAlive`) LaunchAgent, or (c)
chains "update succeeded" into "restart the running service."

## Functional requirements

**FR-1 — `harness service autoupdate install --every <duration>`**
Installs and loads a second LaunchAgent, independent of the main run-loop
service, that fires on a fixed interval and runs the update sequence.
- AC1: `--every 15m` installs a LaunchAgent whose plist has
  `StartInterval == 900` (seconds).
- AC2: `--every 2h` and `--every 1d` are accepted and convert to seconds
  (`7200`, `86400`).
- AC3: Sub-hourly values are accepted: `--every 1m` → `StartInterval == 60`
  is the minimum; there is no hourly floor anywhere in the parser or CLI help.
- AC4: A bare integer, `0m`, a negative value, or a non-integer-minute
  duration (e.g. `90s`, `1.5m`) is rejected with a non-zero exit and a clear
  stderr message — seconds are not an accepted unit, only whole minutes,
  hours or days.
- AC5: The command refuses on non-macOS with the same message shape
  `_require_macos()` already produces for `harness service install`.
- AC6: Re-running `install` with a different `--every` updates the existing
  LaunchAgent's interval (idempotent re-install, mirroring `load()`'s
  bootout-then-bootstrap behaviour for the main service).

**FR-2 — the scheduled run updates *and* restarts the running service**
Each firing of the autoupdate LaunchAgent must actually get the new code into
the running system, not just onto disk.
- AC1: The generated autoupdate wrapper script execs `uv tool upgrade
  <package>` (or shells out to `harness update` — see Open Questions) and,
  when the reported version/commit *changed*, kickstarts the target run
  service (`launchctl kickstart -k gui/<uid>/<service-label>`).
- AC2: When the upgrade is a no-op (already latest), the wrapper does **not**
  kickstart the run service — no unnecessary restart/downtime.
- AC3: The target service label defaults to `com.harness` (`DEFAULT_LABEL`)
  and is overridable via `--service-label`, so a non-default `harness service
  install --label ...` is still reachable.
- AC4: A failed `uv tool upgrade` does not attempt a restart and is visible in
  the autoupdate LaunchAgent's own log file (see FR-4).

**FR-3 — `harness service autoupdate uninstall`**
Stops and removes only the autoupdate LaunchAgent; the main run-loop service
(and its schedule of tasks) is untouched.
- AC1: After uninstall, `launchctl print gui/<uid>/<autoupdate-label>` reports
  not loaded; the main service's `status` is unaffected.
- AC2: Uninstalling when nothing was installed is a no-op that exits 0 with an
  informative message (mirrors `_service_uninstall`).

**FR-4 — `harness service autoupdate status`**
Reports whether the autoupdate LaunchAgent is loaded, and its configured
interval in human-readable form (e.g. "every 15m").
- AC1: Prints label, plist path/presence, loaded state, and the interval
  decoded back from the plist's `StartInterval`.
- AC2: Exit code mirrors `_service_status` (0 loaded, 1 not loaded).
- AC3: Logs land at `<root>/logs/harness-autoupdate.log` and
  `harness-autoupdate.error.log`, separate from the run-loop's own log files,
  so a bad update is diagnosable without wading through run-loop noise.

**FR-5 — duration parsing is a pure, unit-tested function**
A new `parse_interval_minutes(text: str) -> int` (returns seconds) in
`drivers/launchd.py`, following the existing "pure builder, no I/O" pattern of
`wrapper_script`/`plist_bytes`.
- AC1: Accepts `<N>m`, `<N>h`, `<N>d` (case-insensitive suffix), integer `N`
  only.
- AC2: Rejects zero, negative, non-integer, missing-suffix, and
  seconds-suffixed input, each with a distinct, actionable error message.

## Non-functional requirements

- **No overlap / thundering herd**: launchd does not start a second instance
  of the same `Label` while the previous invocation is still running, so a
  short interval (e.g. 1m) paired with a slow `uv tool upgrade` cannot stack
  concurrent upgrade processes under the same label — the next `StartInterval`
  firing is simply skipped until the job is free. Document this assumption
  explicitly rather than adding harness-side locking, since it mirrors how the
  existing `KeepAlive` service already relies on launchd's own supervision.
- **No secrets in the new plist/wrapper**, consistent with invariant already
  tested for the run-loop service (`test_plist_carries_no_secret`) — the
  autoupdate wrapper needs no `GITHUB_TOKEN`, only `uv`/`PATH`, so this is
  simpler than the existing wrapper, not harder.
- **Idempotent install**: re-running `autoupdate install` with new flags must
  converge to the new plist, not error because the label already exists
  (reuse `load()`'s bootout/bootstrap dance from `launchd.py` verbatim).
- **Minute-level precision only** — no second-level scheduling is exposed;
  this matches launchd's own practical floor for a supervised job and keeps
  the parser's surface area small.

## Data model

No changes to `models.py`, tasks, queues, or the board. This feature is
entirely outside the task/workflow domain — it manages two on-disk artifacts
per LaunchAgent (a `.sh` wrapper and a `.plist`), the same shape the existing
`service install` already produces:

| Entity | Path | Owner |
|---|---|---|
| Main run-loop plist | `~/Library/LaunchAgents/<label>.plist` | `service install` (existing) |
| Main run-loop wrapper | `<root>/harness-run.sh` | `service install` (existing) |
| Autoupdate plist | `~/Library/LaunchAgents/<autoupdate-label>.plist` | `service autoupdate install` (new) |
| Autoupdate wrapper | `<root>/harness-autoupdate.sh` | `service autoupdate install` (new) |

Default `<autoupdate-label>` = `<label>.autoupdate` (e.g. `com.harness.autoupdate`
when the main service uses the default `com.harness`), so the two LaunchAgents
are visually and namespace-linked without colliding.

## Interfaces

CLI only — no HTTP/API or board changes (`api/` and `projection.py` are
untouched, consistent with invariant #5: they don't know what drivers do).

```
harness service autoupdate install [--root PATH] [--every 15m]
                                    [--label com.harness.autoupdate]
                                    [--service-label com.harness]
harness service autoupdate uninstall [--label com.harness.autoupdate]
harness service autoupdate status    [--root PATH] [--label com.harness.autoupdate]
```

`service_actions` in `cli.py` (currently `install|uninstall|status`) gains a
fourth action, `autoupdate`, itself a sub-subparser with its own
`install|uninstall|status` — mirrors the existing `service` subparser
structure one level down rather than inventing a new pattern.

## Dependencies and scope

**Depends on / touches:**
- `src/harness/drivers/launchd.py` — new pure builders: `parse_interval_minutes`,
  `autoupdate_wrapper_script`, and either a generalized `plist_bytes` (accepting
  an optional `start_interval_seconds` and switching `KeepAlive` off when set)
  or a sibling `periodic_plist_bytes`. Reuses `load`/`unload`/`status`/`plist_path`
  as-is — no changes needed there.
- `src/harness/cli.py` — new `_service_autoupdate_install/_uninstall/_status`
  handlers and subparser wiring; reuses `service_entry_point`,
  `service_path_entries`, `_require_macos`, `uv_executable`, `PACKAGE_NAME`.
- `tests/test_launchd.py` — unit tests for the new pure builders (interval
  parsing edge cases, plist shape, wrapper content), same style as existing
  wrapper/plist tests.
- `tests/test_cli.py` — CLI-level tests for the new subcommands (argument
  validation, non-macOS refusal, idempotent re-install), mirroring
  `test_service_install_refuses_*` / `test_update_*`.
- `README.md` — new subsection under "Running it as a service" documenting
  `harness service autoupdate install --every <duration>`.

**Explicitly out of scope:**
- Any change to `harness update`'s own manual behavior or exit codes.
- Windows/Linux scheduling (`systemd timer`, Task Scheduler) — this feature is
  macOS/launchd-only, same restriction `_require_macos()` already imposes on
  `harness service`.
- Second-level or cron-expression scheduling syntax (`* * * * *`) — minutes/
  hours/days via `<N><unit>` is sufficient and keeps the parser trivial.
- Automatically discovering *which* run-loop service to restart when multiple
  are installed under different labels/roots on one machine — the operator
  supplies `--service-label` explicitly (FR-2 AC3).
- Notifying the operator (Slack/email/etc.) of a completed or failed
  autoupdate — logs to the existing log-file convention are the only surface
  for now.

## Rough plan

1. **`drivers/launchd.py`**: add `parse_interval_minutes(text) -> int`
   (seconds, minute/hour/day units only, whole-minute validation) with focused
   unit tests for every rejection path.
2. **`drivers/launchd.py`**: add `autoupdate_wrapper_script(*, harness, package,
   service_label, uid, path_entries) -> str` — a pure builder producing a
   strict-bash script that runs `uv tool upgrade <package>`, compares
   before/after installed version, and conditionally kickstarts
   `gui/<uid>/<service_label>`. Unit test each branch (version changed →
   kickstart; unchanged → no kickstart; upgrade failed → no kickstart, correct
   exit code propagated to the log).
3. **`drivers/launchd.py`**: extend `plist_bytes` with an optional
   `start_interval_seconds: int | None = None` parameter — when given, sets
   `StartInterval` and turns `KeepAlive` off (a periodic one-shot job, not a
   supervised long-running loop); `RunAtLoad` stays true so a missed window
   after a reboot still catches up promptly. Add plist unit tests for the new
   branch (`StartInterval` present, `KeepAlive` False) alongside the existing
   ones, which continue covering the unchanged `KeepAlive=True` service path.
4. **`cli.py`**: wire `service_actions.add_parser("autoupdate", ...)` with its
   own `install|uninstall|status` sub-subparsers and `--every`/`--label`/
   `--service-label` arguments; implement the three handlers reusing existing
   helpers (`_require_macos`, `service_entry_point`, `service_path_entries`,
   `load`/`unload`/`status`, `plist_path`). Default labels/log filenames per
   the Data model table above.
5. **`tests/test_cli.py`**: cover argument validation (bad `--every`,
   non-macOS refusal), install/uninstall/status round-trip against the fake
   `launchctl` seam already used by `test_launchd.py`'s bootout/bootstrap
   tests, and the idempotent-reinstall case (AC6 of FR-1).
6. **README.md**: document the new subcommand next to the existing "Running it
   as a service" section, including the minute-granularity examples (`--every
   15m`) and the "this also restarts the main service" behavior so operators
   aren't surprised by a mid-interval restart.
7. Full suite: `.venv/bin/pytest -q`.

## Open questions

- **Does the autoupdate wrapper shell out to `uv tool upgrade` directly, or
  invoke `harness update` as a subprocess?** Calling `harness update` reuses
  the existing version-diffing/error-message logic (`_update`,
  `installed_version_report`) but means the wrapper depends on the *currently
  installed* `harness` correctly performing its own upgrade-and-report cycle
  non-interactively, which it already does today. Default assumed for this
  plan: **wrapper execs `<harness> update`** (via `service_entry_point()`,
  same as the main wrapper's `exec "<harness>" run ...`) and greps its
  stdout/the freshly-installed `--version` for a version change, rather than
  re-implementing the `uv tool upgrade` call — keeps the update logic in one
  place. Confirm this matches the intended trust boundary before implementing.
- **Should `harness update` itself grow a `--restart-service <label>` flag**
  (so the manual and scheduled paths share one code path), instead of the
  autoupdate wrapper doing the before/after version comparison in bash?
  Default assumed: yes — add `--restart-service` to `_update` in `cli.py` so
  the kickstart logic lives in Python (testable) rather than in the generated
  bash wrapper (only smoke-testable). The autoupdate wrapper then simply execs
  `harness update --restart-service <label>`.
- **What happens if `--every` is shorter than a typical `uv tool upgrade`
  round-trip (network fetch + reinstall)?** Assumed acceptable per the NFR
  above (launchd naturally skips overlapping firings); flag if the reviewer
  wants an explicit minimum (e.g. floor `--every` at 5m) instead of trusting
  launchd's supervision.
- **Default `--every` if omitted** — assumed the flag is required (no
  implicit schedule), since silently picking e.g. hourly would contradict the
  "operator controls granularity" premise of this feature. Confirm during
  design.
