# Architecture assessment: sub-hourly `harness service autoupdate` schedules

## Verdict

The plan and design are sound and buildable as written, with two real bugs to
fix before implementation and a handful of small tightenings. Nothing here
changes the shape of plan-01.md / design-01.md — this is a correction pass on
two implementation details the design got wrong, plus guidance on where the
seams already in `launchd.py`/`cli.py` should be reused rather than
re-invented. I read both artifacts and the current `src/harness/drivers/
launchd.py` and `src/harness/cli.py` in full before writing this.

## Alignment with existing patterns

Confirmed against the actual code, not just the design's description:

- `launchd.py` already separates **pure builders** (`wrapper_script`,
  `plist_bytes`) from a **thin `launchctl` shell** (`_launchctl`, `load`,
  `unload`, `status`) — exactly as the module docstring claims. The design's
  plan to add `parse_interval_minutes`, `autoupdate_wrapper_script`,
  `periodic_plist_bytes` as more pure builders, and to reuse `load`/`unload`/
  `status` verbatim, fits this seam precisely. No changes needed to the shell
  layer's existing three functions.
- `cli.py`'s `service` subparser already nests `service_actions =
  service.add_subparsers(dest="action", required=True)` with leaf parsers
  setting `handler=`, and `main()` ends in a single `args.handler(args)` with
  no branch on `args.action`. Adding `autoupdate` as a fourth
  `service_actions` entry, itself carrying `autoupdate_actions =
  autoupdate.add_subparsers(dest="autoupdate_action", required=True)`, needs
  zero new dispatch code — this is confirmed, not just plausible.
- `ServiceError`, `_require_macos()`, `service_entry_point()`,
  `service_path_entries()`, `uv_executable()`, `installed_commit()`,
  `version_string()`, `installed_version_report()` all exist today with the
  signatures the design assumes. Reusing them as-is is correct and this
  feature adds no parallel versions of any of them.
- This is a pure CLI/driver feature; it touches no `models.py`, `router.py`,
  `dispatcher.py`, `consumer.py`, `api/`, or `projection.py`. Invariants #1
  and #5 (drivers stay behind ports, the UI doesn't know what the harness
  runs on) are naturally preserved because the feature never enters the
  task/workflow domain at all. `test_architecture.py`'s import-boundary
  checks are unaffected.

## Proposed architecture

Unchanged from design-01.md's shape: two new pure builders and one new pure
parser/formatter pair in `launchd.py`, three new thin handlers in `cli.py`,
one additive flag on the existing `_update`. The two corrections below are
implementation-detail fixes, not structural changes.

### Correction 1 — `_launchctl` is module-private; `_update` cannot call it

Design-01.md's pseudocode for the `--restart-service` branch calls
`_launchctl(["kickstart", "-k", f"gui/{uid}/{args.restart_service}"])`
directly from `cli.py`. That does not work: `_launchctl` is underscore-
prefixed in `launchd.py` and is **not** in `cli.py`'s existing import list
(`DEFAULT_LABEL, ServiceError, load, plist_bytes, plist_path, status, unload,
wrapper_script`) — it is the module's private subprocess shell, exactly the
layer `load`/`unload`/`status` exist to wrap so callers never touch
`launchctl` args directly.

**Decision: add a fourth public wrapper, `kickstart(uid: int, label: str) ->
None`, to `launchd.py`, next to `load`/`unload`/`status`.**

```python
def kickstart(uid: int, label: str) -> None:
    """Force-start the agent now, without waiting for its own schedule."""
    _launchctl(["kickstart", "-k", f"gui/{uid}/{label}"])
```

This is a one-line function but it matters architecturally: it keeps
`_launchctl` and raw `launchctl` argument vectors entirely inside
`launchd.py`, so `cli.py` never constructs a `launchctl` command by hand
(which the design's pseudocode would have done, the only place in `cli.py`
that would have broken this pattern). `_update` imports and calls
`kickstart(os.getuid(), args.restart_service)` inside a `try/except
ServiceError`, the same shape every other `launchd.py` call in `cli.py`
already uses. Note `load()` already calls this exact `_launchctl(["kickstart",
"-k", ...])` internally at the end of every install — `kickstart()` factors
that one line out into something both `load()` and `_update` can call, rather
than duplicating the argument vector in two places.

### Correction 2 — the before/after version comparison in `_update` compares two different string shapes

Design-01.md proposes capturing "before" via `installed_commit()` /
`version_string()` and "after" via `installed_version_report()`, then
diffing them. These do not produce comparable strings:

- `version_string()` returns `"0.2.0 (git abc1234)"` (no `harness ` prefix,
  reads local `importlib.metadata` in-process).
- `installed_version_report()` returns whatever `harness --version` prints on
  stdout via subprocess — which is `f"harness {version_string()}"` per the
  existing `argparse` `version=` wiring in `main()` — i.e. `"harness 0.2.0
  (git abc1234)"`.

Comparing these directly would report "changed" on *every* run, even a true
no-op upgrade, because the two snapshots never match on format alone —
directly undermining FR-2 AC2 ("no unnecessary restart"), which is the one
behavior this whole flag exists to get right.

**Decision: capture both snapshots with the same function,
`installed_version_report()`, once before the `uv tool upgrade` subprocess
call and once after (already the case for "after").** Both are then real
shell-outs to `entry --version`, guaranteed byte-comparable, and this needs
no new parsing/regex logic:

```python
def _update(args: argparse.Namespace) -> int:
    uv = uv_executable()
    if uv is None:
        ...
    before = installed_version_report() if args.restart_service else None
    result = subprocess.run([str(uv), "tool", "upgrade", PACKAGE_NAME], ...)
    ...
    after = installed_version_report()
    print(f"\nnow: {after}")
    if args.restart_service:
        if before != after:
            try:
                kickstart(os.getuid(), args.restart_service)
            except ServiceError as error:
                print(f"error: update succeeded but restart failed: {error}", file=sys.stderr)
                return 1
            print(f"restarted service {args.restart_service} (version changed)")
        else:
            print(f"service {args.restart_service} left running (no version change)")
        return 0
    print("the running service still has the previous version — restart it with\n"
          "  launchctl kickstart -k gui/$(id -u)/com.harness")
    return 0
```

Guard the extra `before` subprocess call behind `if args.restart_service` —
manual `harness update` (no flag) keeps doing exactly one subprocess call for
`installed_version_report()` (the existing "after" call), so
`test_update_runs_uv_tool_upgrade`'s call-count assumptions are unaffected
when the flag is absent. Also note the explicit failure path: a `kickstart`
that raises `ServiceError` (label not installed, launchd busy) must not be
silently swallowed — the upgrade succeeded but the operator still needs to
know the restart didn't happen. Design-01.md didn't spell this branch out;
treat it as part of FR-2, not an edge case to skip.

### Minor tightenings (apply, don't treat as open questions)

1. **`load()`'s unconditional trailing `kickstart -k` will run the autoupdate
   job once immediately on `install`**, in addition to `RunAtLoad: True`
   already covering the "run once on load" case. This is likely fine — an
   operator installing a schedule reasonably expects it to run once right
   away rather than wait a full interval — but document it explicitly in the
   `install` command's printed output and in the README, since it's an
   observable side effect (`harness-autoupdate.log` gets one entry the moment
   `install` returns, not after the first `--every` elapses) that would
   otherwise look surprising in a log review.
2. **`format_interval` doesn't need the general non-round-tripping fallback
   design-01.md speculates about.** `parse_interval_minutes` is the only
   writer of `StartInterval` in this codebase, and it only ever emits
   multiples of 60 (its floor is `1m`). So `format_interval` only needs to
   try `d` → `h` → `m` in that order and take the first exact divisor; it
   will always find one at `m` at the latest for any value this CLI itself
   produced. Don't build machinery for a hand-edited-plist case nothing here
   produces — if the reviewer wants defensive handling for a foreign
   `StartInterval` later, that's a one-line fallback (`f"{seconds}s"`) to add
   then, not a reason to design it now.
3. **Extract the `status` printing loop instead of duplicating it.** Both
   `_service_status` and the new `_service_autoupdate_status` need "label /
   plist path+presence / launchctl state/pid/last-exit-code lines / final
   state line", and the autoupdate variant adds one more line
   (`interval:`). Factor the existing loop out of `_service_status` into a
   private helper (e.g. `_print_service_report(label, target, report) ->
   int`, returning the exit code) that both handlers call, with the
   autoupdate handler printing its one extra `interval:` line around the
   shared call. This is a pure refactor of existing code, safe because
   `_service_status`'s own tests only assert on stdout content, not on the
   function's internal structure.

## Implementation guidance

**Where new code belongs** (matches design-01.md's module map exactly):

| Piece | File | Kind |
|---|---|---|
| `parse_interval_minutes` | `drivers/launchd.py` | pure builder |
| `format_interval` | `drivers/launchd.py` | pure builder (inverse of the above) |
| `periodic_plist_bytes` | `drivers/launchd.py` | pure builder, sibling to `plist_bytes` |
| `autoupdate_wrapper_script` | `drivers/launchd.py` | pure builder, sibling to `wrapper_script` |
| `kickstart(uid, label)` | `drivers/launchd.py` | thin `launchctl` shell, sibling to `load`/`unload`/`status` |
| `--restart-service` on `update` | `cli.py` | extends existing `_update`, additive only |
| `_service_autoupdate_install/_uninstall/_status` | `cli.py` | new handlers, same shape as the three existing `_service_*` |
| `autoupdate` sub-subparser wiring | `cli.py` `main()` | new `add_parser` calls only, no new dispatch logic |
| `_print_service_report` extraction | `cli.py` | refactor of `_service_status`'s existing body |

**Key contracts:**

- `parse_interval_minutes(text: str) -> int` raises `ServiceError`, never
  anything else — callers need exactly one `except ServiceError` clause, the
  same contract `load()` already gives its callers.
- `kickstart(uid, label)` raises `ServiceError` on failure, mirroring
  `load`/`unload`. It performs **no** wait/poll (unlike `load`'s
  `_wait_until_unloaded` dance) — a kickstart is fire-and-forget from the
  caller's perspective, matching how `_service_install`'s own call to
  `load()` already ends the function without polling for the new process to
  actually be up.
- `periodic_plist_bytes` and `plist_bytes` stay two functions, not one
  parameterized function — endorsed as designed. `KeepAlive: True` vs.
  `StartInterval` + no `KeepAlive` key are different launchd job *kinds*, and
  keeping them as two five-line builders keeps every existing `plist_bytes`
  test passing completely unmodified, with zero risk of an `if` branch in the
  shared function accidentally coupling the two job kinds' semantics later.
- `_update`'s new branch is strictly additive and gated on
  `args.restart_service` being present — the flag's absence must reproduce
  today's output byte-for-byte (this is what keeps
  `test_update_runs_uv_tool_upgrade`'s existing assertions valid unmodified).

**Data flow for a scheduled firing** (the part worth spelling out since it
crosses two processes):

```
launchd (StartInterval elapses)
  → harness-autoupdate.sh (wrapper, generated by autoupdate_wrapper_script)
      exec harness update --restart-service com.harness
        → _update(): before = installed_version_report()   [subprocess: old entry --version]
        → subprocess: uv tool upgrade harness               [replaces the installed shim's target]
        → after = installed_version_report()                [subprocess: new entry --version]
        → before != after?
              yes → kickstart(uid, "com.harness")            [launchctl kickstart -k gui/<uid>/com.harness]
              no  → nothing further
        → exit 0 (or 1 if upgrade failed, or 1 if kickstart failed post-upgrade)
  → launchd records the wrapper's exit code; stdout/stderr already went to
    harness-autoupdate.log / harness-autoupdate.error.log via the plist
```

One process, one exit code, one pair of log files per firing — no new IPC,
no new state file. The only shared state between the manual and scheduled
paths is the `--restart-service` flag itself; everything else about
"did the version change" is re-derived fresh on every call rather than
persisted, which is correct — there is nothing to get out of sync.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `_launchctl` privacy violation as designed (Correction 1) would either fail to import or force `cli.py` to hand-build `launchctl` argv, breaking the module boundary `launchd.py` exists to enforce | Add public `kickstart()`; implementation must not import `_launchctl` into `cli.py` under any circumstance — flag in code review if it appears |
| Before/after string mismatch (Correction 2) silently defeats FR-2 AC2, restarting the service on every scheduled firing regardless of whether an update actually happened | Use `installed_version_report()` for both snapshots; add a unit test asserting `_update(["--restart-service", "x"])` does **not** call `kickstart` when the mocked before/after strings are equal — this is the one test that would have caught the bug design-01.md would have shipped |
| A short `--every` (e.g. `1m`) paired with a slow `uv tool upgrade` could overlap firings | No new code needed — launchd already refuses to start a second instance of the same `Label` while one is running (confirmed existing behavior relied on by the design, not re-verified here since it's a launchd guarantee, not harness code); document in README rather than build a lock file |
| A missed `except ServiceError` around the new `kickstart()` call in `_update` would crash the wrapper's `exec` with a Python traceback instead of a clean non-zero exit, appearing as a mysterious autoupdate failure in the log | Explicit `try/except ServiceError` around the new call, per Correction 2's code sketch; add a test forcing `kickstart` to raise and asserting exit code 1 with a message on stderr |
| Immediate double-fire on `install` (RunAtLoad + `load()`'s trailing kickstart) reads as a bug in a log review | Not a functional risk (launchd's single-instance guarantee absorbs it) — purely a documentation gap; mention in the `install` command's printed output and README, per tightening #1 above |

## Prerequisites before implementation begins

None outside this repo — no new dependency, no schema change, no config file
format change. The two corrections above should be treated as settled before
writing code (not re-opened as questions): `kickstart()` is a new public
function in `launchd.py`, and `_update`'s before/after snapshot both come
from `installed_version_report()`. Everything else in design-01.md — the
sub-subparser wiring, the sibling `periodic_plist_bytes`/
`autoupdate_wrapper_script`, `--every` required with no default, the
`<label>.autoupdate` naming convention — is confirmed against the real code
and ready to implement as written.

```json
{"outcome": "done", "summary": "Reviewed plan-01.md and design-01.md against the real launchd.py/cli.py. Design is sound but has two real bugs: (1) it calls the private _launchctl directly from cli.py — fixed by adding a public kickstart(uid, label) to launchd.py alongside load/unload/status; (2) its before/after version-diff for --restart-service compares version_string() against installed_version_report(), two different string formats that would never match, defeating the no-restart-on-noop requirement — fixed by using installed_version_report() for both snapshots. Also recommends extracting the shared status-printing loop, simplifying format_interval (no writer ever produces a non-round-tripping StartInterval), and documenting the immediate first-run side effect of install. No structural changes to the proposed architecture; all corrections are implementation-detail fixes to apply before coding."}
```
