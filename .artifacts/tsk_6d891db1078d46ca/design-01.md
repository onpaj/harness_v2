# Design — Read-only node-graph preview for the workflow editor

Input: `plan-01.md`. This document turns the plan's FRs into a concrete
UI layout, component/function breakdown and data shapes. No server, port,
or model changes — confirmed against `routes.py::_workflow_editor_context`
(only `name`/`is_new`/`text`/`error`/`warnings`/`saved`, unchanged) and
`workflow_editor.html` (a plain form + textarea). Everything below is
additive: one new `<div>` block in the template, one new JS file, one new
CSS block in `app.css`.

One correction to the issue text: this codebase has **no `data-theme`
attribute** — theming is driven entirely by the `prefers-color-scheme`
media query flipping the `:root` custom properties in `app.css` (see
`app.css` lines ~12–70). "Honour the board's theming" therefore means:
use the existing custom properties and nothing else; no separate dark-mode
branch is needed anywhere in the new code.

## UX/UI

### Placement

The preview sits in its own `.field` block, directly under the textarea's
`.field` and above `.actions`, full width of `.panel.wide` (so it doesn't
force the panel wider than 720px — the graph area scrolls horizontally
internally if a workflow has many columns). This matches the plan's
default ("below, full-width").

```
┌─ .panel.wide ──────────────────────────────────────────────┐
│  Definition (raw JSON)                                     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ {                                                     │  │
│  │   "start": "plan",                                    │  │
│  │   "transitions": [                                    │  │
│  │     ...                                        [text- │  │
│  │                                                area]   │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  Preview                                                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ (scrolls horizontally if wide)                        │  │
│  │  ┏━━━━━┓ done  ┌─────────┐ done  ┌──────────────┐     │  │
│  │  ┃PLAN ┃──────▶│ design  │──────▶│ architecture │ ... │  │
│  │  ┗━━━━━┛ start ├─────────┤       └──────────────┘     │  │
│  │   (bold        (badges only on start/end/finisher)    │  │
│  │   border,                                              │  │
│  │   "start"                                              │  │
│  │   badge)                                               │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  [Save]                                                     │
└──────────────────────────────────────────────────────────────┘
```

When the JSON doesn't parse/validate, the graph box is replaced by one
muted line:

```
│  Preview                                                    │
│  invalid JSON — fix to preview                              │
```

No border/box around the notice — it reads as an inline hint (reuses the
existing `.hint` text style: `color: var(--text-3); font-size: 13px`),
not an error banner. It is intentionally *not* the same visual weight as
`.banner.error` (the server-side save error): the two must stay visually
distinct because they answer different questions (client "can't preview
this yet" vs. server "this JSON failed validation on save").

### Node visual language

| Node kind | Shape/border | Badge |
|---|---|---|
| Default step | rounded rect, `1px solid var(--border-strong)`, fill `var(--surface)` | none |
| `start` step | same rect, `2px solid var(--accent)`, fill `var(--accent-weak)` | small pill top-left: "start" |
| `end` (reserved `END` = `"end"`) | rounded rect, dashed `1.5px dashed var(--border-strong)`, fill `var(--surface-2)` | small pill top-left: "end" |
| step with a `finishers` entry | default or start styling, plus | small filled dot top-right corner, `fill: var(--accent)`; `<title>` = `finisher: <kind>` |
| step with a `descriptions` entry | unchanged styling | node `<title>` = the description text |

A step that is both `start` and `end`-adjacent (degenerate one-step
workflow, e.g. `start: "solo"`, one transition `solo →(done)→ end`) just
gets the `start` styling on `solo` and the `end` styling on the separate
`end` node — no combined state needed since `start` and `end` are always
different node ids (`end` is a reserved name distinct from any step name,
per `invalid_step_name`/`models.END`).

### Edge visual language

- A straight or gently-curved path, `stroke: var(--text-3)`, `1.5px`,
  arrowhead marker (`<marker>` filled `var(--text-3)`) at the target end.
- Label: the `on` value, small text (`11px`, `fill: var(--text-2)`) on a
  `var(--surface)` backing rect (so it stays legible crossing other
  edges/nodes), centered on the edge's midpoint.
- Hover: native SVG `<title>` child on the edge's `<g>`, text = `hint`
  when non-empty, else the bare `on` value again (so hovering always
  shows *something* consistent, never nothing). Using `<title>` (browser
  tooltip) rather than a hand-rolled tooltip keeps FR-6 from growing new
  interaction/positioning/CSS surface — free accessibility, zero JS event
  wiring beyond appending the element.
- A **back-edge** (target column ≤ source column — e.g. `review
  →request_changes→ development`) is drawn as a quadratic Bézier bowing
  below the straight column band, so it doesn't overlap the forward edges
  it crosses. A **self-loop** (`from === to`) is drawn as a small loop
  bulging above the node. Both still carry the same label/tooltip
  treatment as a forward edge — only the path geometry differs.
- When two or more transitions share the same `(from, to)` pair (two
  different outcomes routing to the same next step), each gets a distinct
  curve offset (`index * 14px` bow) so labels don't stack exactly on top
  of each other.

### Component hierarchy (DOM/JS, not a framework)

```
workflow_editor.html
 └─ <div class="field workflow-preview">
     ├─ <label>Preview</label>
     ├─ <div class="workflow-preview__notice" hidden></div>   (invalid-JSON text)
     └─ <div class="workflow-preview__scroll">
         └─ <svg class="workflow-preview__svg">
             ├─ <defs><marker id="wf-arrow">…</marker></defs>
             ├─ <g class="wf-edges"> … one <g class="wf-edge"> per transition …
             └─ <g class="wf-nodes"> … one <g class="wf-node"> per step …
```

`workflow_preview.js` (new, `api/static/workflow_preview.js`, loaded via
`<script src="/static/workflow_preview.js" defer></script>` at the end of
`workflow_editor.html`) owns all of the above; it is a single
self-invoking module, no globals beyond its own IIFE scope, no build step
(matches the vendored `htmx.min.js`/`sse.js` pattern — plain script tags).

## Component design

`workflow_preview.js` is organized as four pure-ish functions plus a thin
DOM-wiring tail. Each function takes/returns plain data — no function
below `render()` touches the DOM, which keeps the parse/layout logic unit
-testable in isolation if a future step wants that (out of scope here,
but the seam is free).

```js
// 1. Parse + shape-validate. Mirrors _parse_workflow's required fields
//    (start: string, transitions: array of {from, on, to, hint?}); does
//    NOT replicate the server's deeper checks (maxParallel/finishers
//    referencing known steps, reserved-name rejection, etc.) — the server
//    remains the sole save-time authority per FR-4/spec Notes. A shape
//    the parser accepts but the server would reject on save still renders
//    a best-effort preview; that's fine, save-time validation is
//    untouched and still catches it.
function parseWorkflow(text) -> { ok: true, workflow } | { ok: false, reason }

// 2. Build the node/edge graph from the validated shape.
function buildGraph(workflow) -> {
  nodes: [{ id, isStart, isEnd, hasFinisher, finisherKind, description }],
  edges: [{ from, to, on, hint, key }]   // key = `${from}->${on}->${to}` for stable dedupe
}

// 3. Column/row layout. BFS depth from `start`, ties broken by first-seen
//    order; back-edges are ignored for placement (never create a cycle in
//    the layout), a node unreachable from `start` gets column
//    (maxDepth + 1) so it still renders instead of vanishing.
function computeLayout(graph) -> {
  columns: Map<nodeId, columnIndex>,
  rows: Map<nodeId, rowIndex>,
  columnCount, maxRows
}

// 4. Render. The only function touching the DOM; clears and rebuilds the
//    <svg> children every call (no incremental diffing — NFR accepts a
//    full re-render per debounced keystroke for this project's graph
//    sizes).
function render(svgEl, graph, layout) -> void

// Wiring tail: debounce + notice toggle, run once on DOMContentLoaded.
const textarea = document.getElementById('text');
const svg = ...; const notice = ...;
function attempt() {
  const result = parseWorkflow(textarea.value);
  if (!result.ok) { showNotice(result.reason); return; }
  hideNotice();
  render(svg, buildGraph(result.workflow), computeLayout(...));
}
attempt();  // initial render on page load, using the server-rendered {{ text }}
let timer;
textarea.addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(attempt, 150);
});
```

### Read-only guarantee (FR-5)

`workflow_preview.js` never registers a listener on `svg`/`.wf-node`/
`.wf-edge` that mutates `textarea.value` or issues a `fetch`/`htmx`
request — the module has exactly one write target (`svg.innerHTML`/its
child nodes) and exactly one read source (`textarea.value`). This is
checkable by grep (per the plan's FR-5 acceptance: no textarea-write or
network call reachable from a graph event handler) since the file has no
other event listeners at all beyond the `input` listener on the textarea
itself, which only *reads* `textarea.value`.

### Security (XSS via crafted workflow JSON)

Every string that reaches the SVG — node id, `on` label, `hint`, step
`description`, finisher `kind` — is inserted via `textContent` /
`document.createElementNS` + `setAttribute`, never `innerHTML` or
template-string-built markup. This includes the `<title>` tooltip
elements. A workflow file crafted with e.g. `"hint": "<img
onerror=alert(1)>"` renders as literal text, not markup.

## Data schemas

No DB schema, no HTTP request/response shape, no event payload changes —
this feature is entirely client-side rendering of data already present in
the DOM (the textarea's text, itself already server-rendered from
`WorkflowAdmin.read_raw`/the POSTed form value, unchanged).

### Input shape (mirrors `_parse_workflow`, JS-side, read-only mirror — not a new contract)

```ts
// What parseWorkflow() accepts. Anything outside this shape → { ok: false }.
type RawWorkflow = {
  start: string;
  transitions: Array<{
    from: string;
    on: string;
    to: string;
    hint?: string;
  }>;
  maxParallel?: Record<string, number>;   // present but unvalidated for rendering
  finishers?: Record<string, string | { kind: string; [k: string]: unknown }>;
  descriptions?: Record<string, string>;
};
```

Validation performed by `parseWorkflow` (mirrors, does not replace,
server validation):
- `JSON.parse` must succeed.
- Parsed value must be a non-null, non-array object.
- `start` must be a string.
- `transitions`, if present, must be an array; each item must be an
  object with string `from`, `on`, `to` (missing/wrong-typed → reject the
  whole document, matching `_parse_workflow`'s all-or-nothing behavior —
  a partially-typed transitions array degrades to the notice, not a
  partial graph).
- `hint`, when present on a transition, must be a string.
- `maxParallel`/`finishers`/`descriptions`, when present, must be plain
  objects (not arrays) — contents are used as-is for badges/tooltips, not
  deep-validated (a malformed nested value just fails to show a badge
  rather than blocking the render, since none of it affects graph shape).

### Internal graph model (in-memory only, never serialized)

```ts
type GraphNode = {
  id: string;                 // step name, or the literal "end"
  isStart: boolean;           // id === workflow.start
  isEnd: boolean;             // id === "end" (models.END)
  hasFinisher: boolean;
  finisherKind?: string;      // finishers[id].kind, or finishers[id] itself if it's a plain string
  description?: string;       // descriptions[id]
};

type GraphEdge = {
  from: string;
  to: string;
  on: string;
  hint: string;                // "" when absent — tooltip falls back to `on`
  key: string;                 // `${from}->${on}->${to}`, dedupe/curve-offset key
};

type Layout = {
  columns: Record<string, number>;   // nodeId -> column index (0-based, BFS depth from start)
  rows: Record<string, number>;      // nodeId -> row index within its column
  columnCount: number;
  maxRows: number;
};
```

Node set construction (`buildGraph`): iterate `transitions` in order,
for each transition add `from` then `to` to the node list if not already
present (first-seen order — mirrors `Workflow.steps()`'s dedup rule but,
unlike `steps()`, does **not** exclude `"end"`: the diagram must draw the
terminal node). Then, if `start` isn't already in the list, prepend/append
it (append, first-seen order preserved) — covers a `start` with zero
outgoing transitions (the degenerate single-node case), which `steps()`
already includes via its own trailing `if self.start not in found` check.

## Layout algorithm (detail)

1. `columns[start] = 0`. BFS over `edges` grouped by `from`, following
   only **forward** traversal from `start` (standard BFS, first-seen
   order among a node's outgoing edges) — assign each newly-discovered
   node `columns[node] = columns[parent] + 1`. A node already assigned a
   column is not revisited (this is what makes a back-edge like `review
   →development` inert for placement — `development` was already placed
   via the forward `plan→design→architecture→development` chain by the
   time the back-edge is considered).
2. Any node never reached by the BFS (disconnected from `start` — a
   malformed-but-parseable workflow with an orphan step) gets
   `columns[node] = columnCount_from_step_1` (one past the last reached
   column), so it renders instead of silently disappearing (plan's
   explicit requirement).
3. Row assignment: within each column, nodes get sequential row indices
   in the order they were first discovered/added (stable, no packing
   optimization — acceptable for this project's small graphs per the
   plan's NFR).
4. Pixel geometry: `colWidth = 168px`, `rowHeight = 72px`, node box
   `140×48px`, `margin = 24px`. `svg.viewBox` sized to
   `columnCount * colWidth + margin*2` × `maxRows * rowHeight + margin*2`.
   The `.workflow-preview__scroll` wrapper is `overflow-x: auto` so a wide
   graph scrolls horizontally inside the fixed-width panel rather than
   forcing page layout wider (matches the existing `.panel.wide` 720px
   cap).
5. Edge path: forward edge (`columns[to] > columns[from]`) → straight
   line, node-edge to node-edge (not center-to-center, so the line starts
   /ends at the box border, not underneath it). Back-edge or self-loop →
   quadratic Bézier control point offset below (back-edge) or above
   (self-loop) the straight path by `40 + 14*siblingIndex` px, where
   `siblingIndex` counts same-`(from,to)` edges already drawn, so
   parallel transitions between the same two nodes fan out instead of
   overlapping.

## Interfaces

Confirmed against current code — no changes needed to any of these:

- `routes.py::_workflow_editor_context` — unchanged (`name`, `is_new`,
  `text`, `error`, `warnings`, `saved`). No new context field; the JS
  reads the textarea's live DOM value, not a template variable.
- `WorkflowAdmin`/`WorkflowValidationError` (`ports/workflow_admin.py`) —
  untouched; save/validation flow identical to today.
- `app.py`'s `StaticFiles` mount at `/static` (already serves
  `api/static/*`) — `workflow_preview.js` just needs to be dropped into
  that directory; no new mount or route.

### New files

- `src/harness/api/static/workflow_preview.js` — the module described
  above.
- `src/harness/api/templates/admin/workflow_editor.html` — add the
  `.field.workflow-preview` block (after the textarea's `.field`, before
  `.actions`) and the `<script defer>` tag.
- `src/harness/api/static/app.css` — add a `.workflow-preview*` /
  `.wf-node*` / `.wf-edge*` rule block near the other `.field`/`.editor`
  rules (around line ~396–420), using only existing custom properties
  (`--surface`, `--surface-2`, `--border`, `--border-strong`, `--accent`,
  `--accent-weak`, `--on-accent`, `--text`, `--text-2`, `--text-3`) — no
  new variables, no hardcoded colors, so both `prefers-color-scheme`
  branches are covered automatically.

## Edge cases carried into design (from the plan's open questions)

- Single-step workflow (`start` with no transitions): `buildGraph`
  produces one node, zero edges; `computeLayout` places it at column 0,
  row 0; renders as a lone start-styled box. No divide-by-zero (column/row
  counts default to 1 when the node list is non-empty).
- Empty `transitions: []` with a valid `start`: same as above.
- Orphan step unreachable from `start`: rendered in a trailing column per
  the layout algorithm step 2, not dropped.
- Cyclic workflow (the normal `request_changes` back-edge, or a
  pathological longer cycle): BFS-first-seen placement never revisits a
  column-assigned node, so layout always terminates; the cycle's back
  edge(s) render as bowed curves, not as an infinite layout loop.
- Two transitions with identical `(from, on, to)` (duplicate entry in the
  JSON): both are still separate `GraphEdge`s (not deduped) since they're
  separate entries in the authored file — rendered as two overlapping-key
  curves fanned out by the sibling-offset rule; matches "byte-identical,
  never re-serialized" — the preview doesn't second-guess the author's
  file, it draws what's there.
