# Design — status bar with version and build date/time on the board UI

Implements FR-1..FR-6 from `plan-01.md`.

## UX/UI

### Wireframe

The status bar is a one-line footer, visually separated from the board by a
rule, sitting **below** `#board` and outside it:

```
┌──────────────────────────────────────────────────────────────┐
│ harness board                                                 │
│                                                                 │
│  ┌ TODO ──────┐  ┌ START ─────┐  ┌ REVIEW ────┐  ┌ END ──────┐│
│  │ card        │  │ card        │  │ card        │  │ card    ││
│  │ card        │  │              │  │              │  │        ││
│  └─────────────┘  └──────────────┘  └──────────────┘  └────────┘│
│ ················································· (rule) ······│
│  harness 0.2.1 (git a1b2c3d) · built 2026-07-21T10:32:00Z      │
└──────────────────────────────────────────────────────────────┘
```

Degraded case (`build_time=None`):

```
  harness 0.2.1 (git a1b2c3d) · built unknown
```

No loading/error/empty states beyond this: the bar has no data dependency
beyond the two strings supplied at page render, so it can't be "loading" or
"empty" the way `#board` can.

### Component hierarchy

```
board.html
├── <h1>harness board</h1>
├── <div id="board" hx-get="/fragment/board" hx-trigger="sse:board" ...>
│     └── {% include "_columns.html" %}      (rewritten wholesale on every sse:board)
├── <dialog id="detail">                     (task detail modal)
└── <footer class="status-bar">              ← NEW, sibling of #board, not a child
      harness {{ version }} · built {{ build_time or "unknown" }}
```

Placed after `<dialog id="detail">` and before the closing `<script>` block,
i.e. structurally last in `<body>` — a footer, both visually and in the DOM.
It is emphatically **not** moved into `_columns.html`: that partial is the
thing `hx-swap="innerHTML"` blows away on every `sse:board` event (FR-3), and
the status bar has no reason to be refetched — its values are static for the
life of the process.

### Key interactions

- **Full page load (`GET /`)**: server renders `board.html` once, with
  `version`/`build_time` baked into the HTML. No JS involved.
- **SSE board refresh (`sse:board` → `GET /fragment/board`)**: only replaces
  `#board`'s innerHTML with `_columns.html`. The footer is untouched because
  it lives outside `#board` and htmx never touches elements outside the
  target of a swap.
- **Server restart / update**: the bar does not update itself. An operator
  must reload the page to see new values — same as `<title>` or any other
  static shell content today (explicitly out of scope per plan).
- **No client-side JS added.** The bar is plain server-rendered text; it
  needs no `<script>` of its own.

## Component design

Four touch points, each a small, additive change to an existing module — no
new files, no new port.

### `cli.py`

- **`build_timestamp() -> str | None`** — new function, placed next to
  `version_string()` (both read `PACKAGE_NAME`'s installed distribution
  metadata; keeping them adjacent mirrors that shared concern).
  - Resolves the installed distribution via `metadata.distribution(PACKAGE_NAME)`;
    on `metadata.PackageNotFoundError` (source checkout, no install) → `None`.
  - Derives an mtime from the distribution's on-disk location (its dist-info
    directory) and formats it as UTC ISO-8601 with a `Z` suffix, e.g.
    `2026-07-21T10:32:00Z` (`datetime.fromtimestamp(mtime, tz=timezone.utc)`
    truncated to whole seconds).
  - Every failure path (`PackageNotFoundError`, `AttributeError`,
    `OSError`/`FileNotFoundError` on `.stat()`, or any path that doesn't
    resolve to a real filesystem entry — e.g. a distribution shape this
    heuristic didn't anticipate) is caught and degrades to `None`. This
    function must never raise; it decorates the board, it doesn't gate it.
  - No caching inside the function itself — the non-functional requirement
    ("computed once") is enforced by the *caller* (`serve()`) invoking it
    exactly once per process, not by memoizing here.
- **`version_string()`** — unchanged, reused as-is (already produces
  `"<version> (git <hash>)"` or `"<version>"` or `"unknown (not installed)"`).
- **`serve()`** — the `create_app(...)` call gains two keyword arguments,
  computed once right before the call:
  ```python
  app = create_app(
      view=harness.projection,
      artifacts=harness.artifacts,
      output=harness.stage_output,
      control=harness.control,
      clock=SystemClock(),
      version=version_string(),
      build_time=build_timestamp(),
  )
  ```

### `api/app.py`

- `create_app(...)` gains two keyword-only parameters with defaults, so every
  existing call site (14 in `tests/`, none of which pass version info today)
  keeps compiling unchanged:
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
  ```
- Both are stored on `app.state` next to the existing `view`/`artifacts`/etc.:
  `app.state.version = version`, `app.state.build_time = build_time`. No new
  concept — `app.state` is already the vehicle by which the port objects
  reach the route closures; this reuses the same channel for two plain
  strings.
- **No import of `cli.py` or `importlib.metadata` here** (FR-4). `app.py`
  only ever receives already-computed strings — it does not know they came
  from installed-package metadata, or that they could be recomputed at all.

### `api/routes.py`

- `build_html_router`'s `index()` handler reads `request.app.state.version`
  / `request.app.state.build_time` and adds them to the template context
  alongside `board`:
  ```python
  @router.get("/", response_class=HTMLResponse)
  def index(request: Request) -> HTMLResponse:
      return TEMPLATES.TemplateResponse(
          request=request,
          name="board.html",
          context={
              "board": view.snapshot(),
              "version": request.app.state.version,
              "build_time": request.app.state.build_time,
          },
      )
  ```
  (`view` here is the closure's existing `view: BoardView` parameter — no new
  parameter needed on `build_html_router` itself, since `app.state` is
  reached through `request`, matching how `control`/`clock` are threaded
  today via closure rather than `request.app.state` — the version/build_time
  path uses `request.app.state` specifically because that's the channel
  `create_app` already writes them to, and threading two plain strings
  through router-builder parameters as well would be a second path to the
  same values for no benefit.)
- `fragment_board()` (the SSE-refresh handler, `_columns.html`) is
  **unchanged** — it must not gain `version`/`build_time` in its context,
  since `_columns.html` never renders them (FR-3).
- `build_json_router` gains one endpoint:
  ```python
  @router.get("/api/version")
  def version_info(request: Request) -> dict:
      return {
          "version": request.app.state.version,
          "build_time": request.app.state.build_time,
      }
  ```
  Placed next to the existing `/api/board`. `build_json_router` currently
  takes `(view, artifacts)` and builds its router directly from those
  closures; `/api/version` instead reads `request.app.state`, so it needs a
  `Request` parameter where the others don't — consistent with how `index()`
  above reaches the same state.

### `board.html`

- New CSS rule in the existing inline `<style>` block, in the same terse
  single-line style as its neighbors:
  ```css
  .status-bar { margin-top: 16px; padding-top: 8px; border-top: 1px solid #dfe1e6; color: #97a0af; font-size: 11px; font-family: ui-monospace, monospace; }
  ```
- New markup, placed after `<dialog id="detail"></dialog>`:
  ```html
  <footer class="status-bar">harness {{ version }} · built {{ build_time or "unknown" }}</footer>
  ```
  Jinja's `or` handles both `None` and, defensively, an empty string the same
  way — collapsing to the literal `unknown` (FR-2). No new template filter
  needed (unlike `basename`, this is a one-line fallback).

## Data schemas

No persisted/DB schema changes — this feature is two read-only strings
computed at process start and threaded through existing render paths.

### In-process values

| Value | Type | Computed by | Lifetime |
|---|---|---|---|
| `version` | `str` | `cli.version_string()` | once, at `serve()` start |
| `build_time` | `str \| None` | `cli.build_timestamp()` | once, at `serve()` start |

### `app.state` (FastAPI)

```
app.state.version: str
app.state.build_time: str | None
```

### Template context — `GET /` (`index`, renders `board.html`)

```python
{
    "board": BoardSnapshot,   # unchanged
    "version": str,
    "build_time": str | None,
}
```

### Template context — `GET /fragment/board` (`fragment_board`, renders `_columns.html`)

```python
{
    "board": BoardSnapshot,   # unchanged — no version/build_time added (FR-3)
}
```

### `GET /api/version` response body

```json
{
  "version": "0.2.1 (git a1b2c3d)",
  "build_time": "2026-07-21T10:32:00Z"
}
```

Degraded case:

```json
{
  "version": "unknown (not installed)",
  "build_time": null
}
```

`build_time`, when not `null`, is always UTC ISO-8601 with second precision
and a literal `Z` suffix (`YYYY-MM-DDTHH:MM:SSZ`) — no timezone offset
variants, no fractional seconds.
