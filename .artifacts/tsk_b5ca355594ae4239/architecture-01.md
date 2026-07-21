# Architecture assessment — status bar with version and build date/time

Reviews `plan-01.md` (FR-1..FR-6) and `design-01.md` against the actual state of
`src/harness/{cli.py,api/app.py,api/routes.py,api/templates/board.html}` and the
existing test suite. The plan and design are sound and I'm not reopening either.
One piece of the design conflicts with how this codebase actually wires
dependencies into routes, and I'm overriding it below; everything else is
confirmed against source and carried forward as the build spec.

## Alignment with existing patterns and integration points

Read `api/app.py`, `api/routes.py`, `cli.py:390-435` (`installed_commit`,
`version_string`) and `cli.py:693-724` (`serve`) directly. Two things the design
assumed are confirmed as true today:

- `create_app` is called exactly once, at `cli.py:708`, without `version`/
  `build_time` — adding two defaulted kwargs there is a pure addition, no
  existing call site (14 across `tests/`) breaks.
- `version_string()` / `installed_commit()` already exist and are unit-tested
  in `tests/test_cli.py:455-502` by monkeypatching `cli.metadata.version` /
  `cli.metadata.distribution` with a fake `Dist` object. `build_timestamp()`
  should be tested the same way, next to those tests.

One thing the design got wrong, found by reading `api/app.py:53-78` and
`api/routes.py:72-150` side by side: **`app.state` is a write-only artifact
in this codebase today.** `create_app` sets `app.state.view`, `.artifacts`,
`.output`, `.control`, `.clock`, `.coalesce_seconds` — but grepping the whole
tree (`src/`, `tests/`) turns up zero reads of `request.app.state` or
`app.state` anywhere. Every route closure gets its dependencies as an
explicit parameter to `build_html_router(view, artifacts, output, control,
clock, coalesce_seconds)` / `build_json_router(view, artifacts)`, captured at
router-build time. `app.state` assignment looks like it was meant as a
introspection/back-channel that nothing ended up needing.

Design-01 proposes making `index()` and the new `/api/version` the *first*
readers of `request.app.state` in this codebase, and gives `/api/version` a
`Request` parameter that no sibling endpoint in `build_json_router` needs.
That's a second, inconsistent wiring mechanism introduced for two plain
strings, when the dominant one (closure parameters) already does the job with
less surface. See "Proposed architecture" below for the corrected wiring —
everything else in design-01 (CSS, markup placement, `cli.py` changes, FR
coverage) stands as written.

## Proposed architecture

No new files, no new port — this is additive surface on four existing
modules, matching design-01's scope assessment.

### Key decision: thread `version`/`build_time` as router-builder parameters, not via `app.state`/`Request`

**Options considered:**
1. *(design-01, rejected)* Store on `app.state`, read via `request.app.state`
   in `index()` and a new `Request`-taking `/api/version` handler.
2. *(chosen)* Add `version: str`, `build_time: str | None` as parameters to
   `build_html_router(...)` and `build_json_router(...)`, exactly like `view`
   and `artifacts` already are. Handlers close over them; `/api/version` needs
   no `Request` parameter.

**Rationale:** option 2 is a straight extension of the pattern every other
dependency in this file already follows — one mechanism for "how a route
reaches its data," not two. It's also less code: no `Request` param on
`version_info()`, no `request.app.state.X` indirection. `app.state` stays
exactly as unused-by-reads as it is today; this change doesn't have to explain
or fix that pre-existing oddity, just avoid extending it.

Whether `create_app` *also* writes `app.state.version = version` alongside the
router params is a don't-care — harmless either way since nothing reads it,
so leave it out rather than adding dead state.

### Data flow

```
cli.serve()
  computes version_string() + build_timestamp() once
  → create_app(..., version=..., build_time=...)
       → build_html_router(view, artifacts, output, control, clock,
                            coalesce_seconds, version, build_time)
            → index() closes over version/build_time, puts them in the
              board.html template context
            → fragment_board() UNCHANGED — no version/build_time in its
              context, _columns.html never renders them (FR-3)
       → build_json_router(view, artifacts, version, build_time)
            → GET /api/version returns {"version": ..., "build_time": ...}
              straight from closure, no Request needed
```

`board.html` renders the footer from its template context exactly as
design-01 specifies; `_columns.html` is untouched. This is the one part of the
design that was already correctly isolated from the `app.state` question.

## Implementation guidance

### `cli.py`

- Add `build_timestamp() -> str | None` next to `version_string()`, per
  design-01's heuristic (installed distribution's on-disk mtime → UTC
  ISO-8601, `None` on any failure).
- **Risk called out below:** use `metadata.distribution(PACKAGE_NAME)
  .locate_file("")` (or `.locate_file("METADATA")` then `.parent`), not a
  `._path` attribute access. `locate_file` is part of the public
  `importlib.metadata.Distribution` ABC; `_path` is `PathDistribution`'s
  private implementation detail and isn't guaranteed across Python versions
  or alternate `Distribution` backends. Stat that path; on `OSError`/
  `FileNotFoundError`/`AttributeError`, degrade to `None` — same
  never-raises contract `version_string()` already follows for
  `PackageNotFoundError`.
- `serve()`: compute both once before the `create_app(...)` call at line 708,
  as design-01 shows.

### `api/app.py`

```python
def create_app(
    *,
    view: BoardView,
    artifacts: ArtifactView | None = None,
    output: StageOutputView | None = None,
    control: TaskControl | None = None,
    clock: Clock,
    coalesce_seconds: float = 0.25,
    version: str = "unknown",
    build_time: str | None = None,
) -> FastAPI:
    ...
    app.include_router(build_json_router(view, artifacts, version, build_time))
    app.include_router(
        build_html_router(
            view, artifacts, output, control, clock, coalesce_seconds,
            version, build_time,
        )
    )
```

No import of `cli.py` or `importlib.metadata` here (FR-4) — `app.py` only
ever receives two already-computed strings, same as it receives an
already-constructed `Clock`.

### `api/routes.py`

```python
def build_json_router(
    view: BoardView, artifacts: ArtifactView,
    version: str, build_time: str | None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    ...
    @router.get("/version")
    def version_info() -> dict:
        return {"version": version, "build_time": build_time}

    return router


def build_html_router(
    view: BoardView, artifacts: ArtifactView, output: StageOutputView,
    control: TaskControl, clock: Clock, coalesce_seconds: float,
    version: str, build_time: str | None,
) -> APIRouter:
    ...
    @router.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request, name="board.html",
            context={"board": view.snapshot(), "version": version, "build_time": build_time},
        )

    # fragment_board() unchanged — no version/build_time in its context.
```

### `board.html`

Exactly as design-01 specifies: `.status-bar` rule in the existing inline
`<style>` block, `<footer class="status-bar">harness {{ version }} · built
{{ build_time or "unknown" }}</footer>` placed after `<dialog id="detail">`,
outside `#board`.

### Tests

- `tests/test_cli.py`: add `build_timestamp()` tests next to
  `test_version_string_*`, monkeypatching `cli.metadata.distribution` the
  same way (fake object exposing `locate_file`, not `read_text` this time).
- `tests/test_board_e2e.py` or a new `tests/test_board_version.py`: drive
  `create_app(version=..., build_time=..., ...)` through `TestClient`,
  covering FR-1/2/3/5 — assert `/` contains the version string and either the
  fixed `build_time` or `unknown`, assert `/fragment/board` contains neither,
  assert `GET /api/version` returns the exact JSON shape.
- Add an `ast`-based check to `tests/test_architecture.py` (mirroring its
  existing import-graph tests) asserting `harness.cli` is not imported by
  `harness.api.app` or `harness.api.routes` — turns FR-4 from a one-time
  manual check into a standing guardrail, consistent with how every other
  cross-module invariant in this file is enforced.

## Risks and mitigations

- **`importlib.metadata` filesystem layout is version/backend-sensitive.**
  Mitigated by using the public `locate_file` method (see above) instead of
  a private attribute, and by the existing catch-all-degrade-to-`None`
  contract — a wrong or missing mtime never surfaces as an error, only as
  `unknown` in the UI.
- **mtime heuristic doesn't mean "build time."** Already flagged as an open
  question in plan-01 and accepted there as a deliberate, documented
  approximation given no build-stamp pipeline exists. No change needed here;
  just don't let the implementation step over-promise what the field means
  (docstring on `build_timestamp()` should say "install time approximation,"
  not "build time").
- **Introducing a second dependency-wiring mechanism (`app.state` reads)
  would be easy to copy for the next feature that needs a simple value in a
  route**, quietly forking the codebase's convention. Mitigated by the
  closure-parameter design above — no new mechanism, so nothing to
  accidentally propagate.

## Prerequisites before implementation begins

None outside this repo. `version_string()`, `installed_commit()`, and the
`create_app`/router structure all already exist and match what the plan
assumes; no upstream change or decision is blocking. The one open design
question resolved here (wiring mechanism) is settled — the coding step should
follow "Implementation guidance" above rather than design-01's `app.state`
wiring.
