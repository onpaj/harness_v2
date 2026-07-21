# Architecture assessment: full-screen tabbed task detail view

Reviewed against `plan-01.md`, `design-01.md`, and the current source
(`src/harness/api/templates/{board,_task,_columns}.html`, `src/harness/api/routes.py`,
`tests/test_api_html.py`). Verdict: **approved as designed, with the implementation
notes below.** This is the rare case where the design doc is precise enough to
build from directly — my job here is mostly to confirm boundary compliance and
flag the handful of concrete decisions the dev step needs made for it, not left open.

## Alignment with existing patterns and invariants

This change lives entirely inside `api/templates/` + the CSS/inline-`<script>`
block in `board.html`. It touches none of the layers CLAUDE.md protects:

- **Invariant 5** (`api/`/`projection.py` never import `drivers/`): untouched —
  `routes.py`'s three handlers (`fragment_task`, `restart_task`, `_task_fragment`)
  keep passing exactly `task` and `artifacts.list(task_id)` into the template
  (`routes.py:119-123`). No new context variable, no new route.
- **`BoardView`/`ArtifactView`/`StageOutputView` are the only ports the API
  layer sees** — confirmed by reading `routes.py` top-to-bottom; this design
  adds no fourth. Good.
- The **card → fragment → dialog** flow (`_columns.html:6-9` → `routes.py:137-139`
  → `board.html:33-46`) is the one piece of "plumbing" this design must not
  disturb, and it doesn't: FR-1/FR-2 are purely a CSS + internal-markup change
  to the same swap target (`#detail`), same trigger (`hx-get`/`hx-post` +
  `innerHTML` swap), same lifecycle (`afterSwap` → `showModal()`, `close` →
  clear `innerHTML` → SSE teardown via `htmx:beforeCleanupElement`).

No invariant in the file is at risk. This is a leaf-level UI change; the
architectural question isn't "does this violate a boundary" (it doesn't) but
"is the client-side state machine (tabs) correctly scoped so it doesn't leak
into the boundary that already exists (the SSE lifecycle)." That's the one
real design risk, and design-01.md's answer — CSS visibility, never DOM
removal, for the tab panels — is the correct one. See Risks below for the one
gap in that answer.

## Proposed architecture: confirmed, one refinement

**Where the tab-click listener lives.** design-01.md is correct that it must
be registered once in `board.html`'s static script, delegated off `#detail`,
not inside `_task.html` (which is destroyed/re-parsed on every open/restart —
inline `<script>` there would either re-register a listener per open, or
(if using inline `onclick=` attributes instead) work but scatter behavior
into markup). Concretely, add this as a **fourth** delegated listener
alongside the three that already exist on `#detail` (`board.html:38-46`):

```js
document.getElementById('detail').addEventListener('click', function (event) {
  var tab = event.target.closest('.tab');
  if (!tab || !event.target.closest('.tab-panels')) return; // no-op, see note below
  ...
});
```

Note: the existing backdrop-close listener (`board.html:38-40`) checks
`event.target.id === 'detail'` — i.e. it only fires when the click lands on
the `<dialog>` element itself (the backdrop), not on descendants. A tab-click
listener on the same `<dialog>` node is a second, independent listener and
does not conflict with it (different predicate), so no ordering/precedence
issue — just don't merge the two into one handler with an if/else, keep them
as separate `addEventListener` calls the way the existing three already are.
This keeps each concern (modal-open, backdrop-close, dialog-cleared-on-close,
tab-switch) as one listener with one job — matching the file's existing style
rather than introducing a single dispatch-by-target mega-handler.

**Single-file `_task.html` vs. partials.** Design-01.md defaults to keeping
one file. Approved — three `<div>`s of markup reuse (not new logic) doesn't
earn three new template files in a project with exactly two template files
today (`_task.html`, `_columns.html`) plus the page shell. Split only if
`_task.html` grows past ~150 lines after the rework; at ~50-70 lines expected,
it won't.

**Server renders the active tab, not client-side defaulting.** This is the
best decision in the design and worth calling out explicitly as the chosen
approach over the alternative (always render all three panels inert, JS picks
the default on `afterSwap`). Rendering `class="active"` server-side on the
Task-info panel/tab means FR-3 and FR-4 (default tab, no leaked state) require
*zero* client state — every fragment render is a fresh, complete answer to
"which tab is active," so there is nothing to reset, forget to reset, or get
wrong across task switches. Don't introduce a client-side "remember which tab
was open" `data-*` attribute or JS variable; it's explicitly out of scope
(plan-01.md's non-goals) and would undermine this exact property for no
requirement gain.

## Implementation guidance

**File-by-file:**

1. `board.html` CSS block (`:root`/`dialog` rule at line 23): replace
   `max-width: 640px; width: 90%` with the full-screen rule from design-01.md
   (`width: 96vw; height: 92vh; max-width: none; padding: 0;` plus
   `dialog#detail[open] { display: flex; flex-direction: column; }`). Add the
   `.task-detail`/`.tab`/`.tab-panel` rules (flex column, `flex: 1; min-height:
   0; overflow: auto` on the panels container) in the same `<style>` block,
   next to the existing `.card`/`.badge` rules — don't start a second
   `<style>` tag or an external stylesheet; the project has exactly one CSS
   location today and this doesn't need a build step to add.
2. `board.html` `<script>` block: add the one new delegated listener described
   above, after the existing three (`board.html:35-46`), before the
   revision-dedup IIFE (`board.html:53-72`) — ordering among these three/four
   listeners doesn't matter functionally (different event targets/predicates),
   but keeping the dialog-lifecycle listeners grouped together above the
   unrelated revision-dedup logic matches the file's existing organization.
3. `_task.html`: restructure per the design's component hierarchy — header
   (id/title + restart button, conditionally, unchanged guard), `nav.tabs`
   (three buttons, `data-tab` attrs, first marked `active`), `div.tab-panels`
   (three `section`s, `data-panel` attrs, first marked `active`). The three
   `<table>` bodies and the SSE `<div>` are relocated, not rewritten — same
   Jinja loops, same `hx-post`/`hx-target` attributes on the restart button.
4. No changes to `routes.py`, `_columns.html`, or any port/driver file.

**Data flow:** unchanged end-to-end — `BoardView.get()` → `Task` (with
`.history`) and `ArtifactView.list()` → `list[ArtifactRef]` are still the only
two things `_task_fragment` assembles (`routes.py:115-123`), still rendered by
one Jinja template. The SSE output stream (`StageOutputView.subscribe` via
`/api/tasks/{id}/output/events`, `routes.py:158-163`) is unchanged; only its
container's CSS (`max-height: 240px` → fill the Output panel) changes.

**Contract this design introduces** (design-01.md's schema table, confirmed
correct and worth treating as load-bearing for the dev step): `data-tab` /
`data-panel` values `info|history|output` must match exactly between the nav
buttons and the panel sections, and `active` is the only class the JS
listener touches (add to the clicked tab + matching panel, remove from their
siblings). Keep this as a plain string match (`data-tab` value === `data-panel`
value) rather than positional (nth-child) matching — positional matching
would silently break if a tab is ever reordered.

## Risks and mitigations

1. **Risk: `display: none` panels still receive `sse-swap` appends correctly,
   but a naive implementation could instead use `hidden` attribute + `!important`
   collision with some other rule, or — worse — conditional Jinja
   (`{% if active_tab == 'output' %}`) that would remove the SSE div from the
   DOM on tab switch.** This is the one place a well-intentioned
   "simplification" during implementation would silently reintroduce the bug
   the design exists to avoid (SSE teardown/reconnect on tab switch, losing
   buffered output). Mitigation: the dev step must implement hiding as CSS
   only (`.tab-panel:not(.active) { display: none; }`), verify by opening a
   task's Output tab, switching to Task info and back, and confirming
   (devtools Network tab) the `/api/tasks/{id}/output/events` connection is
   not re-opened. This is already called out in plan-01.md step 4 — keep it
   as an explicit manual-verification gate before this step is marked done,
   not just an assumption.

2. **Risk: `dialog#detail[open] { display: flex }` interacts with the
   native `<dialog>` backdrop / centering behavior differently across
   browsers** (Chrome/Safari render `<dialog>` centering via UA stylesheet
   `position: fixed; inset: 0; margin: auto`, which generally coexists fine
   with an explicit `width`/`height` override, but is worth a visual check
   rather than assuming). Mitigation: manual check in whatever browser is
   used for the `run` skill / dev-flow verification (plan-01.md step 5) at
   both ~1024px and ~768px viewport widths, per the plan's stated responsive
   floor — this is a visual QA step, not something a unit test can catch.

3. **Risk (low): `tests/test_api_html.py` currently asserts only substrings**
   (`"review" in body`, `"Restart" in body`, confirmed by reading the file —
   no table-structure or CSS-visibility assertions exist). Confirmed correct:
   no test changes are structurally required. One thing to double check after
   the rework: `test_restart_button_shown_only_for_failed_task` asserts
   `"/tasks/tsk_1/restart" not in working_body` — as long as the restart
   button's `{% if %}` guard moves into the new header without being
   duplicated elsewhere (e.g. accidentally also rendered inside a tab panel),
   this keeps passing unmodified. Flag, don't gate: run the full suite after
   the rework regardless, per plan-01.md step 6.

4. **No risk to backend/data layer.** There is no scenario in this change
   where a bug here could corrupt task state, mis-route a task, or violate
   any of the 23 CLAUDE.md invariants — the entire blast radius is "the detail
   view renders or behaves wrong in the browser." That containment is itself
   worth stating plainly: it's why this task doesn't need the same scrutiny
   a dispatcher/consumer/router change would.

## Prerequisites before implementation begins

None outstanding. Both open questions in plan-01.md that could have blocked
implementation are resolved by design-01.md:
- Default-tab-when-running (plan's open question 1): design keeps the plan's
  default assumption (always Task info) — resolved, not deferred; the dev
  step should not revisit this mid-implementation.
- Tab bar visual style (plan's open question 2): design specifies reusing the
  existing `.badge`/`.card` visual language, no new UI-kit — resolved.
- Test coverage on `_task.html` markup (plan's open question 3): confirmed by
  direct reading of `tests/test_api_html.py` in this review — substring-only,
  no changes required.

The dev step can proceed directly from design-01.md's component hierarchy and
CSS rules; this assessment adds no new requirement, only the listener-scoping
and hiding-mechanism guardrails above.
