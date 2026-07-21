# Development — status bar with version and build date/time

Implements FR-1..FR-6 from `plan-01.md`, following the wiring corrected in
`architecture-01.md` (closure/router parameters, not `app.state`/`Request`
reads).

## Summary

Added a one-line status bar footer to the board UI showing the running
harness's version and an install-time approximation of "build time," plus a
`GET /api/version` JSON endpoint exposing the same two values. No new files,
no new port — additive changes to four existing modules and one new test
file.

## Files changed

- **`src/harness/cli.py`**
  - Added `build_timestamp() -> str | None`, next to `version_string()`.
    Resolves the installed distribution via `metadata.distribution(PACKAGE_NAME)
    .locate_file("")` (the public `Distribution` API, not the private `._path`),
    stats that path's mtime, and formats it as UTC ISO-8601 with a `Z` suffix
    and second precision. Every failure path (`PackageNotFoundError`, `OSError`,
    `AttributeError`, `TypeError`) degrades to `None` — never raises.
  - `serve()` now computes `version_string()` and `build_timestamp()` once and
    passes them into the single `create_app(...)` call site.
- **`src/harness/api/app.py`**
  - `create_app(...)` gains `version: str = "unknown"` and
    `build_time: str | None = None` keyword params, threaded into
    `build_json_router` and `build_html_router`. All 14 existing call sites in
    `tests/` keep compiling unchanged (defaults). No import of `cli.py` or
    `importlib.metadata` — `app.py` only receives two already-computed
    strings.
- **`src/harness/api/routes.py`**
  - `build_json_router(view, artifacts, version, build_time)` gains
    `GET /api/version` returning `{"version": ..., "build_time": ...}`,
    closing over the two values directly (no `Request` param needed).
  - `build_html_router(..., version, build_time)`: `index()` adds `version`/
    `build_time` to the `board.html` template context. `fragment_board()`
    (the SSE-refresh partial rendering `_columns.html`) is untouched — it
    never receives these values, so the status bar is never blanked on a
    board refresh.
- **`src/harness/api/templates/board.html`**
  - New `.status-bar` CSS rule in the existing inline `<style>` block.
  - New `<footer class="status-bar">harness {{ version }} · built
    {{ build_time or "unknown" }}</footer>`, placed after
    `<dialog id="detail">` and outside `#board`, so `hx-swap="innerHTML"` on
    `#board` never touches it.
- **`tests/test_cli.py`**
  - Three new tests next to the existing `version_string()` tests:
    `build_timestamp()` reads a distribution location's mtime and formats it
    correctly; returns `None` on `PackageNotFoundError`; returns `None` when
    the resolved path doesn't exist.
- **`tests/test_board_version.py`** (new)
  - `create_app(version=..., build_time=..., ...)` driven through
    `TestClient`: `/` contains the version and build time (or `unknown` when
    `build_time=None`); `/fragment/board` does **not** contain the version
    (FR-3); `GET /api/version` returns the exact JSON shape, including the
    `build_time: null` case; `create_app()` without version args still
    renders `/` with the `"unknown"` default (backward compatibility).
- **`tests/test_architecture.py`**
  - New `test_api_does_not_import_cli()` guardrail: asserts nothing under
    `src/harness/api/` imports `harness.cli` or `importlib.metadata` —
    turns FR-4 into a standing check rather than a one-time manual read.

## Deviations from the design docs

None beyond what `architecture-01.md` already flagged and corrected (router
parameters instead of `app.state`/`Request`). Implementation follows
"Implementation guidance" in that document exactly, including the
`locate_file("")` API choice over the private `._path` attribute.

## How to verify

```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]" -q
.venv/bin/pytest -q
```

482 tests pass, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE` smoke, unrelated
to this change).

Manual check against the real editable install:

```sh
.venv/bin/python -c "
from harness import cli
print(cli.version_string())
print(cli.build_timestamp())
"
```

prints e.g. `0.2.1` / `2026-07-21T16:19:09Z`.

To see the status bar rendered: `harness run --port 8000` (or drive
`create_app(...)` through `TestClient` as in `tests/test_board_version.py`)
and open `/` — the footer reads `harness <version> · built <timestamp>`,
survives an SSE-triggered board refresh, and `GET /api/version` returns the
same two values as JSON.
