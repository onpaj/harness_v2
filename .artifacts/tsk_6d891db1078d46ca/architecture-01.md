# Architecture — Read-only node-graph preview for the workflow editor

Input: `plan-01.md`, `design-01.md`. This assessment confirms the design
against the current codebase, states the one implementation decision left
open by the design (SVG vs. hand-rolled — resolved), and gives the
development step a concrete build order plus the exact points it must not
touch.

## Verdict

**Approve the design as written, with one clarification below.** This is a
template + two static-asset changes. It touches zero Python modules, zero
ports, zero drivers. None of the harness's layering invariants (module map,
`test_architecture.py`) are at risk because nothing in `api/` beyond
`workflow_editor.html` changes, and that file already only reads
`_workflow_editor_context`'s existing fields. There is no new architectural
surface to design — the job here is confirming the design's file-level plan
matches reality and flagging the couple of spots where "matches the
existing pattern" needed a real look rather than an assumption.

## Alignment with existing patterns

Verified directly against the current tree (not assumed from the design doc):

- **`workflow_editor.html`** (56 lines) is exactly the plain-form-plus-
  textarea shape the design describes: `.panel.wide` (720px cap) wraps a
  `<form>` with one `.field` around `textarea.editor#text`, then
  `.actions`, then the save/delete forms, then the `{% if error %}` /
  `{% if saved %}` / `{% for warning in warnings %}` banners. The design's
  placement (`.field.workflow-preview` after the textarea's `.field`,
  before `.actions`) is a clean insertion — no restructuring of the
  existing form needed. Confirmed: `error`/`saved`/`warnings` banners
  render *outside* `.panel.wide`, after the form closes, so the new
  preview block (inside the panel, before `.actions`) is visually and
  structurally distinct from that banner — the design's stated separation
  ("must stay visually distinct... answer different questions") is
  already true of the DOM position, not just the CSS treatment.
- **`app.css`** confirms every custom property the design names exists
  today with both light and dark values: `--surface`, `--surface-2`,
  `--border`, `--border-strong`, `--accent`, `--accent-weak`, `--on-accent`,
  `--text`, `--text-2`, `--text-3` (lines 12–70). `.hint` (line 158,
  `color: var(--text-3); font-size: 13px`) is the exact class the design
  proposes reusing for the invalid-JSON notice — confirmed it exists and
  isn't itself used inside `.banner` anywhere, so borrowing it doesn't
  create a collision. `.panel.wide { max-width: 720px }` (line 394) and
  `.field { margin-bottom: 16px }` (line 396) confirm the layout
  constraint the design's horizontal-scroll wrapper is built around.
- **`models.py`** confirms `END = "end"` (line 8) and the exact shapes of
  `Transition` (`from_step`, `on`, `to_step`, `hint: str = ""`, line
  161–169) and `Workflow` (`start`, `transitions`, `max_parallel`,
  `finishers: dict[str, FinisherBinding]`, `descriptions: dict[str, str]`,
  line 182–188), plus `steps()` (excludes `END`, first-seen order,
  line 197–206), `outcomes_for()` (line 216–228) and `description_for()`
  (line 230–232). The design's JS mirror of `steps()` — include `"end"` in
  the rendered node set, unlike `steps()`'s queue-oriented exclusion — is
  the *only* deliberate divergence from the Python shape, and it's called
  out correctly in the design (`buildGraph`'s node-set construction) with
  the right justification (the diagram must draw the terminal node; a
  queue does not need one).
- **`ports/workflow_admin.py`** confirms `WorkflowAdmin.write_raw`'s
  docstring guarantee — parses to validate, "writes the file verbatim (not
  re-serialized) only if it parses" — which is exactly the byte-identity
  property the design leans on to justify a client-side *read* mirror
  never writing back. No change needed here; confirming it only closes the
  loop on invariant #33 (`api/` touches only `WorkflowAdmin`, never a
  driver) — this feature doesn't touch `WorkflowAdmin` at all, so #33 is
  trivially preserved, not stretched.
- **`test_architecture.py`** greps only `.py` files under `src/harness` via
  `imported_modules` (AST-based, Python-only). It has no rule that reaches
  into `api/templates` or `api/static`, and none of its existing checks
  (module-map layering, `drivers/` isolation, `api/`-touches-only-ports)
  can even observe a change confined to those two directories. **This
  feature cannot fail `test_architecture.py` by construction** — worth
  stating explicitly since the plan flags it as a review-time check; the
  architecture-level answer is: there is nothing in that suite this change
  is capable of tripping.
- **Existing JS pattern**: `api/static/process_form.js` and
  `format-local-time.js` are already present as plain, non-module `<script
  defer>` files with no build step, confirming `workflow_preview.js` slots
  into an established convention rather than introducing a new one. (Not
  read in full — file existence and the vendored `htmx.min.js`/`sse.js`
  pattern cited in the design is enough to confirm "plain script tag, no
  bundler" is how this codebase already does client JS.)

## Proposed architecture

No new architectural components — this is UI-layer-only. The shape is
exactly what the design specifies:

```
workflow_editor.html
  + <div class="field workflow-preview">        (new markup, ~10 lines)
  + <script src="/static/workflow_preview.js" defer></script>

api/static/workflow_preview.js                  (new file, ~150-250 lines)
  parseWorkflow(text)   -> {ok, workflow} | {ok:false, reason}
  buildGraph(workflow)  -> {nodes, edges}
  computeLayout(graph)  -> {columns, rows, columnCount, maxRows}
  render(svg, graph, layout) -> void   (only DOM-touching function)
  + wiring tail: initial render, debounced `input` listener

api/static/app.css
  + .workflow-preview*, .wf-node*, .wf-edge* rule block
```

### Key decision: client-side SVG, hand-rolled layout — confirmed, not revisited

The plan left this as an "open question" defaulting to SVG; the design
committed to it. At the architecture level this default is correct and
should not be reopened:

- **Client-side vs. server-rendered**: client-side is the only option that
  satisfies FR-1 (live re-render as the operator types, no round trip) and
  keeps `_workflow_editor_context` (invariant: unchanged, confirmed above)
  untouched. Server-rendering would require either a new endpoint (a
  preview-fetch on every keystroke — network-bound, against the NFR) or
  server-side templating of the *unsaved* textarea content on every
  request, which doesn't exist as a concept in this app's request/response
  model. Client-side is architecturally simpler and strictly smaller in
  surface area.
- **SVG vs. HTML/CSS-grid**: SVG is the right primitive for arbitrary-angle
  arrows, curved back-edges and precise label-on-edge placement — a
  CSS-grid approach would fight the layout algorithm for anything beyond
  strictly-forward edges (the `review →request_changes→ development`
  back-edge in the very example workflow the issue gives). Confirmed no
  existing SVG-rendering code in this codebase to reuse or diverge from —
  this is genuinely new, self-contained code.
- **Hand-rolled BFS-column layout vs. a vendored library**: the NFR
  explicitly rules out a new dependency unless the hand-rolled layout
  proves insufficient, and it won't for this project's graph sizes (single
  digits to low tens of steps, confirmed by scanning existing
  `workflows/*.json` shapes in spirit — nothing in this codebase's own
  workflow files approaches a scale where BFS-column placement would
  produce an unreadable result). No vendoring needed; don't second-guess
  this later without a concrete counter-example workflow in hand.

### Data flow

```
textarea.value (DOM, already server-rendered from {{ text }})
   │  read on load + on debounced `input` event
   ▼
parseWorkflow(text)  ── ok:false ──▶ show .workflow-preview__notice, stop
   │ ok:true
   ▼
buildGraph(workflow)
   │
   ▼
computeLayout(graph)
   │
   ▼
render(svg, graph, layout)  ── full rebuild of svg children, no diffing
```

One-directional, no feedback path. The architecturally load-bearing
property is that **no arrow points back into `textarea.value`** — this is
what makes the feature read-only by construction rather than by
convention. The development step should be able to point at the finished
`workflow_preview.js` and show that `textarea.value` is assigned nowhere
and no `fetch`/`htmx.ajax` call exists in the file, which is what FR-5's
acceptance criterion asks for.

## Implementation guidance

Build order (each step independently checkable before moving to the next):

1. **`parseWorkflow`** first, with no DOM dependency — feed it strings by
   hand (in a scratch REPL/console, not a new test file — this project has
   no JS test runner and the design correctly scopes automated testing to
   Python; see Risks) covering: valid example workflow, invalid JSON
   (trailing comma), valid JSON but wrong shape (`start` missing,
   `transitions` not an array, a transition missing `to`), single-step
   workflow (`transitions: []` or absent). This is the highest-value
   function to get exactly right since every downstream function trusts
   its output shape.
2. **`buildGraph`** — node-set construction is the one place the design
   deliberately diverges from `Workflow.steps()` (includes `"end"`); get
   this right against the example workflow (7 transitions → 6 steps + end
   = 7 nodes) before writing layout.
3. **`computeLayout`** — BFS from `start`, ignore back-edges for column
   placement, orphan nodes get a trailing column. Verify against the
   example (linear chain, one back-edge `review→development`) and a
   synthetic disconnected-node case by hand.
4. **`render`** — SVG construction via `createElementNS`/`setAttribute`/
   `textContent` only, per the design's XSS guidance; no `innerHTML`
   anywhere in this file. This is a hard constraint, not a style
   preference — flag it as a review checkpoint.
5. **Wiring tail + CSS** — debounced `input` listener, initial call on
   load, notice show/hide, then the `app.css` block using only the
   properties confirmed above.
6. **Template edit last** — once the JS is self-contained and functions
   correctly, add the `<div>` + `<script defer>` to
   `workflow_editor.html`. Keep this a small, easily-revertable diff at
   the end so an issue found in manual testing doesn't require re-touching
   the template.

### Where new code belongs (confirmed paths)

- `src/harness/api/static/workflow_preview.js` — new file.
- `src/harness/api/templates/admin/workflow_editor.html` — insert only
  (lines 32–34 boundary: after the textarea's closing `</div>`, before
  `<div class="actions">`); plus one `<script>` tag before `</body>`.
- `src/harness/api/static/app.css` — new rule block, append near the
  existing `.field`/`.editor`/`.banner` rules (lines ~389–436) for
  locality; exact insertion point is a style preference, not a constraint.

Nothing else. No route, no port, no model, no driver file is touched by
this feature — confirmed above, not assumed.

## Risks and mitigations

- **Risk: no JS test harness in this project.** `pytest` covers Python
  only; there's no automated check that `parseWorkflow`/`buildGraph`/
  `computeLayout` behave correctly on the edge cases both the plan and
  design enumerate (orphan node, single-step workflow, duplicate
  `(from,on,to)`, cyclic back-edge). *Mitigation*: treat the design's
  "Edge cases carried into design" section as a manual test checklist for
  the review step — load each shape into the running admin UI and eyeball
  the result — rather than skipping verification because it isn't
  automatable. Do not invent a JS test framework/build step for this one
  feature; that would be new infrastructure the plan explicitly didn't ask
  for and the codebase doesn't otherwise have.
- **Risk: XSS via crafted workflow JSON** (a workflow file with a hostile
  `hint`/`description`/step-name string, e.g. committed by a
  malicious/compromised admin-panel user). *Mitigation*: already
  architected out in the design — `textContent`/`setAttribute` only, never
  `innerHTML` or template-string HTML. The development step must not
  introduce a convenience `el.innerHTML = ...` anywhere in
  `workflow_preview.js`; this is worth a explicit grep at review time
  (`grep -n innerHTML api/static/workflow_preview.js` should return
  nothing).
- **Risk: debounce/render performance on a large pasted workflow.** Low
  probability given this project's workflow sizes, and the NFR already
  accepts full re-render per keystroke with no diffing. No mitigation
  beyond what the design already specifies — not worth engineering
  headroom for a scale this project doesn't have.
- **Risk: visual regression on the existing editor layout** (the new
  `.field.workflow-preview` block changing panel height/scroll behavior
  unexpectedly). *Mitigation*: the design's own layout keeps the block
  inside the existing `.panel.wide` 720px cap with internal horizontal
  scroll for the graph — verify at review time by loading a workflow with
  many columns (e.g. a synthetic 10-step linear chain) and confirming the
  *page* doesn't grow wider, only the graph's internal scroll area does.

## Prerequisites before implementation begins

None outstanding. The design is fully specified (visual language, DOM
structure, function boundaries, layout algorithm, data schemas, edge
cases) and every file/interface it depends on has been confirmed present
and unchanged in the current tree. Development can start directly from
`design-01.md`'s Component design and Layout algorithm sections.
