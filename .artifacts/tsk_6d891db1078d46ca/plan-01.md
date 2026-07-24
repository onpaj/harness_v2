# Plan — Read-only node-graph preview for the workflow editor

## Summary

The workflow admin editor (`workflow_editor.html`) is a raw JSON textarea with
no visualization of the state machine it defines. This adds a **read-only**
node-graph preview next to the textarea: as the operator types valid workflow
JSON, a small hand-rolled JS renderer parses it client-side and draws steps as
nodes and transitions as labelled directed edges, using an inline SVG built
from a topological-depth column layout. No new dependency, no server
round-trip, no change to save/validation semantics or to `route()`.

## Context

A workflow is `{start, transitions: [{from, on, to, hint?}], maxParallel?,
finishers?, descriptions?}` (parsed by `_parse_workflow` in
`drivers/fs_workflows.py`; the in-memory shape is `Workflow`/`Transition` in
`models.py`). The admin edits the *raw text* of this file
(`WorkflowAdmin.write_raw`) and it is never re-serialized — byte-identity
through the editor is load-bearing (see the port's docstring). Today the only
way to understand the shape of a workflow is to read the transitions list by
eye. This task adds a visual, always-in-sync diagram that never becomes a
second source of truth: it is derived by re-parsing the textarea's current
text on every keystroke (debounced) and is thrown away, not written back.

## Functional requirements

**FR-1 — Live client-side parse of the textarea.**
On page load and on every textarea input (debounced ~150ms), JS attempts to
parse the current text as the same JSON shape `_parse_workflow` accepts:
object with `start: string` and `transitions: array<{from, on, to, hint?}>`
(`maxParallel`, `finishers`, `descriptions` optional).
- Acceptance: typing valid JSON re-renders the graph within the debounce
  window; typing invalid JSON never throws an uncaught exception (verified by
  a browser console check / no JS error surfaced).

**FR-2 — Graph rendering.**
Render one node per entry in the step set (`start`, every distinct
`from`/`to` across transitions, deduped, excluding `"end"` from that set and
adding it back as one explicit terminal node whenever any transition targets
`"end"` or step count is otherwise non-empty) plus one edge per transition,
each edge labelled with its `on` value.
- Acceptance: for the example workflow in the issue (7 transitions, 6 steps +
  `end`), the preview shows 7 nodes and 7 labelled edges with no overlap
  severe enough to make labels unreadable at default zoom.

**FR-3 — Start/end visual distinction.**
The `start` node gets a distinct visual treatment (border/fill color, small
"start" badge). Any node named `"end"` (the reserved `END` constant, see
`models.py: END = "end"`) gets a distinct terminal treatment (different shape
or dashed border, "end" badge). A step that is simultaneously `start` and has
no outgoing edges is a degenerate but legal one-step-then-end workflow and
must render without crashing.
- Acceptance: visually inspect the example workflow — `plan` marked start,
  `end` marked terminal, the other 5 steps in the default style.

**FR-4 — Graceful degradation on invalid/incomplete JSON.**
While the parse fails (`JSON.parse` throws, or the shape check rejects it —
e.g. missing `start`, `transitions` not an array, a transition missing
`from`/`on`/`to`), the preview area shows a small muted notice ("invalid
JSON — fix to preview") instead of a stale or broken graph, and the existing
save-time error banner (`{% if error %}`) is completely unaffected — this is
a separate, client-only concern layered next to it.
- Acceptance: delete a `}` from the textarea → notice appears, no console
  error, Save button and its server-side validation banner still behave
  exactly as before.

**FR-5 — Read-only.**
No pointer/keyboard interaction on the graph mutates the textarea or sends
any request. No drag, no node creation, no click-to-edit. The only allowed
interactions are non-mutating affordances such as hover tooltips (FR-6).
- Acceptance: grep the new JS for any textarea/fetch/htmx write call
  triggered from graph event handlers — there must be none.

**FR-6 — Nice-to-have: hints, descriptions, finisher marker.**
If it doesn't complicate FR-1–5:
- An edge with a non-empty `hint` shows it on hover (SVG `<title>` or a
  lightweight custom tooltip) instead of / in addition to the bare `on` label.
- A step present in `descriptions` shows that text on hover over the node.
- A step present in `finishers` gets a small distinguishing marker (e.g. a
  corner dot/icon) with the finisher kind on hover.
This is explicitly droppable under time pressure; FR-1–5 are the required cut.

## Non-functional requirements

- **No new runtime dependency.** Any layout logic is hand-rolled JS (columns
  by BFS/topological depth from `start`, per the issue's own suggestion). If
  a tiny vendored library were used it must be committed under
  `api/static/`, but the plan is to avoid needing one — the graphs here are
  small (a handful of steps).
- **No re-serialization.** The preview must never write back into the
  textarea or otherwise touch the value the form submits.
- **Performance:** re-render must stay comfortably interactive for the
  workflow sizes this project has (single digits to low tens of steps) —
  a full re-render per debounced keystroke is fine, no incremental diffing
  needed.
- **Theming:** must use the existing CSS custom properties (`--bg`, `--text`,
  `--surface`, `--surface-2`, `--border`, `--border-strong`, `--accent`,
  `--accent-weak`, `--on-accent` — see `app.css` `:root` / dark-mode block)
  so it matches both `data-theme` states with no hardcoded colors.
- **No server changes required** for the core preview (FR-1–5 are pure
  client-side parsing of text already in the DOM). `routes.py`/
  `_workflow_editor_context` need no new fields unless the implementer
  decides a hidden per-page config value is useful (not expected).
- **Security:** node/edge labels and hint/description text come from
  operator-authored JSON and must be inserted as text content (`textContent`
  / SVG `<text>` nodes), never via `innerHTML`/string-concatenated markup,
  to avoid script injection through a crafted workflow file.

## Data model

No changes to `models.py`, `fs_workflows.py`, or any port. The feature
consumes the existing shapes read-only, client-side:
- `start: string`
- `transitions: [{from: string, on: string, to: string, hint?: string}]`
- `descriptions?: {[step: string]: string}` (nice-to-have)
- `finishers?: {[step: string]: string | {kind: string, ...}}` (nice-to-have)
- `end` is not a key in the JSON — it is the reserved string `"end"`
  appearing as a `to` (rarely a `from`) value, matching `models.py: END`.

Derived in-browser (mirrors `Workflow.steps()`/`outcomes_for` without
importing Python): the step set is every distinct `from`/`to` across
transitions plus `start`, in first-seen order — `"end"` is included in that
set like any other step name (matching `Workflow.steps()`'s own exclusion
only of `END` from *queue* steps; the diagram must still draw it as a node).

## Interfaces

- **UI flow only**, no new HTTP endpoints. `GET /admin/workflows/{name}` and
  `GET /admin/workflows/new` render `workflow_editor.html` exactly as today;
  the new markup (a `<div id="workflow-preview">` panel) and a new script tag
  are added to that template.
- New static assets under `api/static/`, referenced by the template:
  - `workflow_preview.js` — parses the textarea, computes layout, renders SVG.
  - (styling can live in `app.css` alongside the other admin styles, or a
    small addition scoped under a `.workflow-preview` class — implementer's
    call; prefer `app.css` for consistency with the rest of the admin UI.)
- No change to `routes.py` handlers, `_workflow_editor_context`, or
  `WorkflowAdmin`/`WorkflowValidationError`.

## Dependencies and scope

**Rests on:** the existing `workflow_editor.html` template/textarea, existing
theming variables in `app.css`, the JSON shape already validated by
`_parse_workflow`/`WorkflowAdmin.write_raw` (the client mirrors, but does not
replace, that validation — the server remains the sole authority on save).

**Explicitly out of scope** (per the issue):
- Editing the workflow via the diagram (no drag, no node/edge creation from
  the graph).
- Live board overlays showing where in-flight tasks currently sit.
- Any change to workflow execution, routing, `router.py`, or the dispatcher.
- A general-purpose graph library dependency (CDN or vendored) unless the
  hand-rolled layout proves genuinely insufficient — not expected for this
  project's workflow sizes.
- Multi-workflow / cross-file visualization (only the workflow currently
  open in the editor).

## Rough plan

1. **Design** — sketch the node/edge visual language (start badge, end
   terminal shape, default node, edge label placement, hover
   tooltip behavior) and the layout algorithm (BFS depth from `start` →
   columns; ties broken by first-seen order; a step unreachable from `start`
   still gets drawn, e.g. in a trailing column, so a malformed-but-parseable
   graph doesn't silently disappear a node).
2. **Architecture** — decide the parse/validate boundary (a small JS function
   mirroring `_parse_workflow`'s shape checks, returning `{ok, workflow}` or
   `{ok: false}`), where the SVG mounts in the DOM relative to the textarea,
   and the debounce/event-wiring approach (plain `input` listener, no
   framework).
3. **Development**:
   - Add `<div class="workflow-preview" id="workflow-preview">` under the
     textarea field in `workflow_editor.html`, plus a `<script src="/static/
     workflow_preview.js" defer></script>`.
   - Implement `workflow_preview.js`: parse/validate, build step set +
     adjacency, compute column layout, render SVG nodes (rect/circle) and
     edges (path + arrowhead marker + text label), wire an `input` listener
     with debounce, wire the invalid-JSON notice.
   - Add CSS rules (in `app.css`) using existing custom properties for node
     fill/stroke, start/end distinction, edge stroke/label color, dark-mode
     coverage free via the existing variables.
   - Implement nice-to-haves (hint/description tooltips, finisher marker) if
     time allows, behind the same render pass.
4. **Review** — manual check against the acceptance criteria per FR, dark/
   light theme toggle, an intentionally-broken JSON edit, a workflow with a
   step unreachable from `start`, and a single-step (`start` with no
   transitions) workflow.
5. **Land** — commit, PR as usual (no workflow/behavior/port changes, so no
   architecture-test impact expected; confirm `tests/test_architecture.py`
   still passes since only `api/templates` and `api/static` change).

## Open questions

- **Exact SVG vs. HTML/CSS-grid rendering** — plan assumes inline SVG
  (natural fit for arrows/labels); if the implementer finds CSS-only easier
  for this project's small graphs, that's an acceptable substitution as long
  as FR-1–5 hold. Default: SVG.
- **Debounce interval** — picked 150ms as a sensible default balancing
  responsiveness and avoiding a render-per-keystroke; not a hard requirement.
- **Where exactly the preview sits** (beside vs. below the textarea) — issue
  says either is fine; default to below, full-width, so the textarea keeps
  its current width/position and existing layouts/screenshots aren't
  disrupted.
- **Cycle handling in layout** — the example and typical workflows have a
  back-edge (`review` →`request_changes`→ `development`), which is not a DAG.
  Default: layout by BFS-first-seen depth (a spanning-tree-like column
  assignment, ignoring back-edges for column placement) and simply draw the
  back-edge as a curved/longer edge back to an earlier column — still
  correct as long as every node appears exactly once and every transition is
  drawn, even if the layout isn't a strict layered DAG rendering.
