# Review — status bar with version and build date/time

Reviewed `development-01.md` and the actual diff (`src/harness/{cli.py,api/app.py,
api/routes.py,api/templates/board.html}`, `tests/{test_cli.py,test_board_version.py,
test_architecture.py}`) against `plan-01.md` (FR-1..FR-6) and `architecture-01.md`
(the corrected router-parameter wiring).

## Conformance to spec

- **FR-1** (status bar on every full page load): `index()` renders `version`/
  `build_time` into `board.html`'s context; `test_index_shows_the_version_and_build_time`
  covers it. ✓
- **FR-2** (build timestamp with `unknown` fallback): `board.html` uses
  `{{ build_time or "unknown" }}`; `test_index_shows_unknown_when_build_time_is_absent`
  covers the `None` case. Format matches the ISO-8601 UTC `Z`-suffixed spec exactly
  (`build_timestamp()` truncates to whole seconds and replaces `+00:00` with `Z`). ✓
- **FR-3** (status bar survives SSE refresh): the footer sits outside `#board`, after
  `<dialog id="detail">`; `fragment_board()` is untouched (verified by reading
  `routes.py`); `test_board_fragment_does_not_carry_the_status_bar` asserts `/fragment/board`
  lacks the version while `/` has it. ✓
- **FR-4** (no `cli.py`/`importlib.metadata` import in `api/`): confirmed by reading
  the actual imports in `api/app.py` and `api/routes.py` (neither present), and by the
  new `test_api_does_not_import_cli` guardrail in `test_architecture.py`. ✓
- **FR-5** (`GET /api/version` JSON): implemented in `build_json_router`, returns
  `{"version": ..., "build_time": ...}` including the `null` case;
  `test_version_endpoint_returns_the_values` and
  `test_version_endpoint_returns_null_build_time_when_absent` cover it. ✓
- **FR-6** (`cli.serve()` supplies real values): `serve()` computes `version_string()`
  and `build_timestamp()` once and passes both into the single `create_app(...)` call
  at the site architecture-01 identified. ✓

## Adherence to architecture

The design's `app.state`/`Request` approach was explicitly overridden by
`architecture-01.md` in favor of threading `version`/`build_time` as plain
parameters through `build_html_router`/`build_json_router`, mirroring how `view`/
`artifacts`/`clock` already flow. The actual diff follows this exactly — no
`request.app.state` reads were introduced, no `Request` parameter added to
`version_info()`. `build_timestamp()` uses the public `Distribution.locate_file("")`
API rather than the private `._path`, as the architecture doc required to avoid a
version/backend-sensitive risk.

## Completeness

- All 14 existing `create_app` call sites keep compiling via the new keyword
  defaults (`version: str = "unknown"`, `build_time: str | None = None`);
  `test_create_app_defaults_version_when_not_supplied` covers backward
  compatibility explicitly.
- `build_timestamp()` never raises: catches `PackageNotFoundError`, `OSError`,
  `AttributeError`, `TypeError`, and is exercised by three unit tests (real mtime,
  not-installed, unreadable location).
- The architecture-mandated `test_api_does_not_import_cli` guardrail was added, not
  just performed as a one-time manual check.
- Full suite: `.venv/bin/pytest -q` → 482 passed, 1 skipped (the pre-existing opt-in
  `HARNESS_SMOKE_CLAUDE` smoke, unrelated to this change) — re-ran independently
  during this review and confirmed the same result.

## Correctness

No logic errors found. The mtime-degrades-to-`None` contract is honored throughout;
the JSON endpoint correctly distinguishes `null` from the string `"unknown"` (the
latter is an HTML-only rendering choice, per FR-2/FR-5 as specified — not a
discrepancy). No security or concurrency concerns: two static strings computed once
at process start, no per-request I/O, no new external input surface.

## Verdict

Implementation fully conforms to the plan and the corrected architecture; all FRs
have acceptance-criteria-level test coverage; no correctness issues found.
