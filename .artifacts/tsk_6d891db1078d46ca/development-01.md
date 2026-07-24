# Development — Read-only node-graph preview for the workflow editor

Input: `plan-01.md`, `design-01.md`, `architecture-01.md`. Implements the
design/architecture exactly as specified — three files touched, no server,
port, model or driver changes.

## What was implemented

### `src/harness/api/static/workflow_preview.js` (new)

A single self-invoking, dependency-free module (matches the existing plain
`<script defer>` convention already used by `format-local-time.js`/
`process_form.js`). Four pure(ish) functions plus a thin DOM-wiring tail, per
the design:

- `parseWorkflow(text)` — `JSON.parse` + shape check mirroring
  `_parse_workflow`'s required fields (`start: string`,
  `transitions: array<{from, on, to, hint?}>`). Anything outside that shape
  returns `{ok: false, reason: "invalid JSON — fix to preview"}` rather than a
  partial graph — an all-or-nothing document check, matching the server. A
  present-but-malformed `maxParallel`/`finishers`/`descriptions` (not a plain
  object) is tolerated by falling back to `{}` for that field alone, since
  those are display-only (badges/tooltips) and shouldn't block the core
  node/edge render.
- `buildGraph(workflow)` — builds the node set in first-seen order from
  `transitions`' `from`/`to` then appends `start` if not already present
  (covers the single-step, zero-transition workflow). Deliberately includes
  `"end"` in the node set, unlike `Workflow.steps()`'s queue-oriented
  exclusion, so the diagram draws the terminal node. Produces `finisherKind`/
  `hasFinisher` (string or `{kind}` object form) and `description` per node.
- `computeLayout(graph)` — BFS from `start` over forward edges only
  (first-seen order per node's outgoing edges); a node already column-assigned
  is never revisited, which is what makes a back-edge (e.g.
  `review→request_changes→development`) inert for placement rather than an
  infinite loop. A node unreachable from `start` gets one trailing column so
  it still renders. Rows are sequential first-seen order within a column.
- `render(svg, graph, layout)` — the only DOM-touching function. Rebuilds the
  SVG's children from scratch on every call (`defs`/marker, one `<g class="wf-
  edge">` per transition, one `<g class="wf-node">` per step). Every element
  is built via `document.createElementNS` + `setAttribute`, every text value
  via `.textContent` — no `innerHTML`, no template-string markup, anywhere in
  the file (confirmed by grep, see Verification). Edge geometry: straight
  line for a forward edge, quadratic Bézier bowing below for a back-edge
  (`columns[to] <= columns[from]`) or above for a self-loop (`from === to`);
  duplicate `(from, to)` pairs fan out via a sibling-index offset so labels
  don't stack. Node badges: a small pill ("start"/"end") top-left on the
  `start`/`end` nodes; a small dot top-right on a step with a `finishers`
  entry (native `<title>` = `finisher: <kind>`); a node-level `<title>` when
  the step has a `descriptions` entry; an edge-level `<title>` = `hint` when
  present, else the bare `on` value.
- Wiring tail: on `DOMContentLoaded`, looks up `#text` (the textarea),
  `#workflow-preview-svg`, `#workflow-preview-notice`; runs `attempt()`
  immediately (renders the server-rendered initial text) and again on every
  debounced (150ms) `input` event. `attempt()` shows/hides the notice and
  toggles the SVG's `hidden` attribute based on `parseWorkflow`'s result — no
  crash path, since every function is wrapped by the `ok`-check before any
  DOM write.

Read-only guarantee: the file assigns to `textarea.value` nowhere and makes no
`fetch`/`htmx` call anywhere — it has exactly one write target (the SVG's
children) and one read source (`textarea.value`), confirmed by grep.

### `src/harness/api/templates/admin/workflow_editor.html` (edited)

Two small insertions, exactly where the design/architecture specified:

- A `<div class="field workflow-preview">` block (notice `<div>` + a
  `.workflow-preview__scroll` wrapper containing the `<svg>`) inserted after
  the textarea's `.field` and before `.actions` — inside `.panel.wide`, so it
  shares the same 720px cap with internal horizontal scroll for wide graphs.
- `<script src="/static/workflow_preview.js" defer></script>` before
  `</body>`.

No other line changed — the textarea's `id="text"`/`name="text"` and the
`{{ text }}` interpolation are untouched, so the existing save flow and
`test_workflow_editor_shows_exact_file_text`-style byte-identity checks are
unaffected.

### `src/harness/api/static/app.css` (edited)

One new rule block (`/* --- Workflow preview --- */`) appended after
`.actions`, before `/* --- Banners --- */`, using only existing custom
properties (`--surface`, `--surface-2`, `--border`, `--border-strong`,
`--accent`, `--accent-weak`, `--on-accent`, `--text`, `--text-2`, `--text-3`,
`--radius-sm`) — both `prefers-color-scheme` branches covered automatically,
no new variables, no hardcoded colors.

## Verification

- `env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN .venv/bin/pytest -q` →
  **1350 passed, 1 skipped**. (This worktree's ambient shell has
  `HARNESS_HEAL_REPO`/`GITHUB_TOKEN` set, which perturbs 8 unrelated
  `test_cli.py` heal/finisher-wiring tests when left in the environment —
  confirmed pre-existing and unrelated to this change by reproducing the same
  8 failures on a stash of just this diff removed; scrubbing those two env
  vars, which is how CI runs, gives a fully clean pass.)
- `grep -n innerHTML src/harness/api/static/workflow_preview.js` → only the
  doc-comment describing the constraint; `grep -n "\.innerHTML\s*="` → no
  matches. `grep -n "textarea.value\s*="` and `grep -n "fetch(\|htmx"` → no
  matches — confirms FR-5 (read-only) and the XSS mitigation mechanically.
- `node -c src/harness/api/static/workflow_preview.js` → syntax OK.
- Logic exercised directly with `node` (a fake `document.createElementNS`
  shim, no browser needed — this project has no JS test runner, per the
  architecture doc's stated risk/mitigation) against:
  - The issue's exact example workflow → 7 nodes, 7 edges, columns
    `plan=0, design=1, architecture=2, development=3, review=4, land=5,
    end=6` (the back-edge `review→development` correctly ignored for column
    placement).
  - Invalid JSON, missing `start`, non-array `transitions`, a transition
    missing `to` → all rejected (`ok: false`).
  - Single-step workflow (`{"start": "solo"}`, no `transitions` key) → one
    node, zero edges, 1×1 layout, no divide-by-zero.
  - An orphan node unreachable from `start` → placed in the trailing column
    (2) rather than dropped.
  - Duplicate `(from, on, to)` transitions → both kept as separate edges (no
    dedup), per the design's byte-identity philosophy.
  - A step with a `finishers` entry (string form) and a `descriptions`
    entry → `hasFinisher`/`finisherKind`/`description` all populated
    correctly.
  - `render()` against a fake SVG element → completes without throwing.
- Rendered the editor end-to-end through `TestClient` (in-process, no real
  browser): `GET /admin/workflows/default` returns the new
  `#workflow-preview-svg`/`#workflow-preview-notice` markup and the
  `/static/workflow_preview.js` script tag, and the textarea still contains
  the byte-identical definition (checked via `html.unescape`, mirroring
  `test_api_workflows.py`'s existing assertion style). `GET
  /static/workflow_preview.js` serves the file with a 200.
- To verify visually in a browser: `harness run` (or start the admin app),
  open `/admin/workflows/<name>` for a workflow with a back-edge (e.g. the
  default agent workflow in the issue), and confirm: `plan` shows a "start"
  badge, `end` shows a dashed border + "end" badge, the
  `review→request_changes→development` edge bows below the forward edges,
  hovering an edge/node shows the `hint`/`description` tooltip, deleting a
  `}` from the textarea swaps the graph for the "invalid JSON — fix to
  preview" notice without disturbing the Save button or the server-side error
  banner, and toggling OS light/dark mode restyles the graph via the existing
  custom properties with no separate branch needed.

## Scope confirmation

Only the three files the plan/design/architecture named were touched:
`src/harness/api/static/workflow_preview.js` (new),
`src/harness/api/templates/admin/workflow_editor.html`,
`src/harness/api/static/app.css`. No route, port, model, or driver file
changed — `tests/test_architecture.py` cannot observe this change by
construction (it greps only `.py` files under `src/harness`), consistent with
the architecture doc's conclusion, and the full suite confirms it passes
unmodified.
