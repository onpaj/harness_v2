# Plan — status bar with version and build date/time on the board UI

## Summary

Add a small status bar to the board UI (`src/harness/api/templates/board.html`)
that shows the running harness's version and the date/time it was built. The
version already has a single source of truth (`cli.version_string()`); build
date/time has no existing source and needs a best-effort derivation, since
this project ships via `uv tool install git+...` rather than a wheel with
embedded build metadata.

## Context

Operators (Ondrej) run the board UI locally while `harness run` drives one or
more repos. `harness update` / `uv tool upgrade` can silently leave a stale
process running until the service is restarted (see the `installed_commit()`
docstring in `cli.py`, which exists for exactly this "did the update land?"
uncertainty). A status bar makes "what am I actually looking at right now"
visible at a glance, without running `harness --version` in a terminal.

## Functional requirements

**FR-1 — Status bar renders on every full page load.**
`GET /` (the `index` route in `api/routes.py`) renders a status bar containing
the version string.
- Acceptance: an HTML test client request to `/` returns a 200 whose body
  contains the version string passed to `create_app`.

**FR-2 — Status bar shows build date/time, with a defined fallback.**
Next to the version, the bar shows a build timestamp, ISO-8601 in UTC (e.g.
`2026-07-21T10:32:00Z`). When it cannot be determined (e.g. running from a
source checkout, not an installed package), it shows the literal text
`unknown` instead of an empty or broken string.
- Acceptance: with a fixed `build_time="2026-07-21T10:32:00Z"` passed to
  `create_app`, `/` contains that string. With `build_time=None`, `/` contains
  `unknown`.

**FR-3 — Status bar survives SSE-driven board refreshes.**
The board's `#board` div is replaced wholesale on every `sse:board` event
(`hx-swap="innerHTML"` on `#board`, see `board.html:30`). The status bar must
sit outside that div (i.e. not inside `_columns.html`) so it isn't re-fetched
or blanked on every refresh, and so it doesn't need `view.snapshot()` data at
all.
- Acceptance: the status bar markup is in `board.html`, not in
  `_columns.html`; a test asserts `/fragment/board` (the SSE-refresh partial)
  does *not* need to and does not contain the version string, while `/` does.

**FR-4 — Version/build values are supplied to `create_app`, not computed
inside a request handler.**
`create_app(..., version: str, build_time: str | None)` takes both as plain
constructor arguments (mirroring how `clock: Clock` is already injected).
`api/app.py` and `api/routes.py` must not import `cli.py` or
`importlib.metadata` — `cli.py` already imports `create_app`, so the reverse
import would cycle, and per invariant #5 the API layer must not know what the
harness is packaged/run from.
- Acceptance: `test_architecture.py`-style check (or a manual import check)
  confirms no import of `harness.cli` inside `harness.api`.

**FR-5 — A JSON endpoint exposes the same values.**
`GET /api/version` returns `{"version": "<str>", "build_time": "<str|null>"}`,
alongside the existing `/api/board` JSON endpoint, so scripts/tests don't need
to scrape HTML.
- Acceptance: with `create_app(version="0.2.1", build_time=None, ...)`,
  `GET /api/version` returns `{"version": "0.2.1", "build_time": null}`.

**FR-6 — `cli.serve()` supplies real values.**
`cli.py` computes the version via the existing `version_string()` and a new
`build_timestamp()` function, and passes both into `create_app(...)` at the
one call site (`cli.py:708`).

## Non-functional requirements

- **No new I/O per request.** Version/build strings are computed once at
  process start (in `serve()`), not per HTTP request — avoids repeated
  `importlib.metadata` lookups on every board poll.
- **No sensitive data exposed.** The version string already may include a
  7-char git commit hash (`version_string()`); that's already printed by
  `harness --version` today and is not a new disclosure. The board binds to
  `127.0.0.1` only (unchanged).
- **Graceful degradation.** Any failure to determine the build timestamp
  (package not installed, metadata unreadable) must degrade to `None` /
  `"unknown"`, never raise and break the board.

## Data model

No new persisted entities. Two plain strings travel from `cli.py` →
`create_app` → `app.state` → route context → template:
- `version: str` — always available (falls back to `"unknown (not installed)"`
  today via `version_string()`).
- `build_time: str | None` — ISO-8601 UTC string, or `None`.

## Interfaces

- **HTML**: a `<footer class="status-bar">` (or similar) at the bottom of
  `board.html`, outside `#board`, e.g.:
  `harness {{ version }} · built {{ build_time or "unknown" }}`.
- **JSON**: new `GET /api/version` in `build_json_router` (`api/routes.py`),
  returning `{"version": ..., "build_time": ...}`.

## Dependencies and scope

Depends on the existing `cli.version_string()` (unchanged, reused as-is).

**In scope:**
- New `cli.build_timestamp()` helper.
- `create_app` / `app.state` / route/template wiring to surface both values.
- One new JSON endpoint.
- Status bar markup + minimal CSS in `board.html`.
- Tests covering FR-1 through FR-5.

**Out of scope:**
- Building a real packaging-time build-stamp pipeline (e.g. a generated
  `_build_info.py` written by a custom setuptools hook, or `SOURCE_DATE_EPOCH`
  wired through CI). The project ships via `uv tool install git+...`
  (`install.sh` was deliberately retired in favor of this single path per
  `CLAUDE.md`), so there is no existing build step to hook into without a
  larger packaging change. This plan uses a best-effort heuristic instead (see
  Open Questions) and flags the gap rather than solving it.
- Auto-refreshing the status bar when the server process itself is updated
  and restarted — the browser must be reloaded to see new values, same as any
  other static per-page-load content today.

## Rough plan

1. **`cli.py`**: add `build_timestamp() -> str | None`, next to
   `version_string()`. Best-effort derivation (see Open Question 1); return
   `None` on any failure or when not installed, mirroring the existing
   `except metadata.PackageNotFoundError` fallback pattern in
   `version_string()`.
2. **`api/app.py`**: extend `create_app(...)` with required `version: str` and
   `build_time: str | None` params; store both on `app.state`.
3. **`api/routes.py`**:
   - Pass `version`/`build_time` (read off `request.app.state`) into the
     `index()` route's template context.
   - Add `GET /api/version` to `build_json_router`.
4. **`board.html`**: add the status bar markup outside `#board`, with a couple
   lines of CSS consistent with the existing inline `<style>` block.
5. **`cli.py` `serve()`**: compute `version_string()` /
   `build_timestamp()` once and pass them into the `create_app(...)` call at
   `cli.py:708`.
6. **Tests**: extend `test_board_e2e.py` (or a new `test_board_version.py`)
   covering FR-1, FR-2, FR-3, FR-5 via `TestClient`; a small unit test for
   `build_timestamp()` monkeypatching `importlib.metadata` the same way
   existing version tests do (check `tests/` for a `version_string` test to
   match style/location).
7. Run `.venv/bin/pytest -q`.

## Open questions

1. **What exactly is "build date/time" here, given there's no build-stamp
   pipeline?** Default: derive it from the installed distribution's metadata
   directory mtime (`importlib.metadata.distribution(PACKAGE_NAME)` → its
   `RECORD`/dist-info path `stat().st_mtime`), formatted as ISO-8601 UTC. This
   approximates "when this install was placed," which is what an operator
   running `harness update` actually wants to confirm, and needs no new
   packaging step. It is *not* a true compiler-style build timestamp (uv/pip
   may or may not normalize wheel file mtimes for reproducibility) — flagging
   this as a heuristic to revisit if it proves unreliable in practice.
2. **Timezone**: default to UTC (unambiguous, matches how the rest of the
   codebase avoids implying a local timezone). If the user wants
   Europe/Prague local time displayed instead, that's a one-line format
   change at step 4.
3. **Show the commit hash too?** `version_string()` already appends
   `(git <short-hash>)` when available — reusing it as-is means the status
   bar shows the commit for free; no separate commit field planned.
4. **Does the status bar need to update without a page reload if the server
   restarts on a new version?** Default: no — out of scope, matches the
   default assumption that a browser reload is required to see fresh
   version/build info, consistent with no other part of the shell (`<title>`,
   etc.) being live-patched.
