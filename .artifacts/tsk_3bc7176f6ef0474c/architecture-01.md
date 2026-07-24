# Architecture assessment: fix mobile scrolling in the task detail dialog

Verdict: **approve the design in `plan-01.md`/`design-01.md` as scoped, with one
addition** (a regression test) and **one decision on an open question** (no
full-screen sheet). Verified against the current `board.html`/`_task.html`
source — the plan/design's description of both files is accurate line-for-line,
so no re-scoping is needed before implementation.

## Alignment with existing patterns and integration points

- This repo's frontend is static Jinja templates + htmx + inline `<style>`, with
  zero client-side build step and zero JS beyond the three listeners already in
  `board.html` (`afterSwap` → `showModal()`, backdrop-click → `close()`, `close`
  → clear `innerHTML`) plus the revision de-dup IIFE. The proposed fix adds
  nothing to that surface: one `<meta>` tag, two CSS declarations on an existing
  rule (`dialog`), one CSS declaration on an existing inline style
  (`#stage-output`). It is the smallest change shape this frontend supports.
- Confirmed against source: `board.html:23` today is exactly
  `dialog { border: none; border-radius: 6px; padding: 16px; max-width: 640px; width: 90%; }`
  — no `max-height`/`overflow`, no viewport meta anywhere in `<head>`.
  `_task.html:47-50` confirms `#stage-output`'s inline style is
  `max-height: 240px; overflow: auto` with no `overscroll-behavior`. The
  plan/design's premise is accurate; implementation can proceed directly against
  these line ranges.
- One integration detail worth flagging to the implementer that neither prior
  artifact called out by name: `#stage-output` uses
  `hx-swap="beforeend scroll:bottom"` (`_task.html:47`) — htmx auto-scrolls this
  inner box to its own bottom on every streamed line. That's a second reason
  (beyond the "classic mobile scroll trap" already named in `plan-01.md`) that
  the inner box must stay independently scrollable rather than being flattened
  into the outer dialog's flow: collapsing it would fight htmx's
  scroll-to-bottom behavior. FR-3's `overscroll-behavior: contain` is the right
  fix — it lets the inner box keep its own scroll-to-bottom autoscroll while
  handing off overscroll to the dialog, rather than requiring a structural
  change.
- Per CLAUDE.md invariant #5 (`api/` and `projection.py` never import
  `drivers/`) and the module map's "Edges" layer: this change touches neither.
  It's confined to two files under `src/harness/api/templates/`, served as-is
  by existing `GET /`, `GET /fragment/board`, `GET /tasks/{id}` routes. No
  Python import graph changes, so `tests/test_architecture.py` needs no update
  and is not at risk.

## Proposed architecture

No new components. Three edits, in this order (matches `plan-01.md` rough plan
steps 1–3):

1. **`board.html` `<head>`** — insert
   `<meta name="viewport" content="width=device-width, initial-scale=1">`
   immediately after the `<meta charset="utf-8">` line.
2. **`board.html` `dialog` rule** — extend with
   `max-height: 85vh; max-height: 85dvh; overflow-y: auto;` (duplicate
   `max-height` is the chosen `dvh`-with-`vh`-fallback technique; no
   `@supports` block needed since an unrecognized declaration is simply
   ignored by the CSS parser).
3. **`_task.html` `#stage-output` inline style** — append
   `overscroll-behavior: contain` to the existing `style` attribute.

Decisions carried forward from `design-01.md`, confirmed correct:

- **Dialog stays a centered `<dialog>`, not a full-screen sheet on mobile.**
  `plan-01.md`'s open question raised this as a "more thorough" alternative.
  Rejecting it for this task: the bug is "can't reach content," not "the
  presentation is wrong," and a sheet redesign would touch layout/UX decisions
  (full-bleed width, safe-area insets, a different close affordance) well
  beyond a scroll-reachability fix. Bounding height + scrolling is the minimal
  correct fix; a sheet is a legitimate separate follow-up, not part of this
  task's acceptance criteria.
- **`vh` + `dvh` double-declaration over `@supports` or JS `visualViewport`
  polyfills.** Simplest option that degrades gracefully; no runtime cost, no
  new JS. Consistent with the "No new JS" non-functional requirement.

## Implementation guidance

- **Where the code goes:** exactly `src/harness/api/templates/board.html`
  (head + `dialog` rule) and `src/harness/api/templates/_task.html`
  (`#stage-output` style attribute). No other file needs to change.
- **Contract:** the three `GET` routes' response *bodies* change (more meta,
  more CSS text); their response *shapes* (status code, Jinja context passed
  in, HTML structure/IDs/classes used by htmx `hx-target`/`hx-swap` selectors)
  do not. `hx-target="#detail"`, `id="stage-output"`, `sse-swap="line"` etc.
  are all unchanged strings — htmx wiring in `board.html`'s `<script>` block
  keeps matching them.
- **Data flow:** unaffected. Task/artifact data still flows
  `BoardView`/`ArtifactView` → Jinja context → template render, unchanged in
  this fix; only the CSS/meta text wrapped around that data changes.
- **Regression coverage — resolving the plan's open question:** add it, don't
  leave it optional. It's a two-assertion, zero-risk addition to
  `tests/test_api_html.py`:
  - served `/` response contains
    `<meta name="viewport" content="width=device-width, initial-scale=1">`
  - the `dialog` CSS rule text contains both `max-height` and `overflow-y:
    auto`
  These are string-level checks (consistent with how this test file already
  asserts on rendered HTML), not visual/layout tests — they exist to catch
  someone later deleting the meta tag or the overflow rule while touching
  `board.html` for an unrelated reason, not to verify actual mobile rendering.
  Do not attempt to add Playwright or any browser-rendering dependency for
  this task — confirmed there is no such harness in this repo
  (`tests/` is entirely `TestClient`-based), and introducing one is out of
  scope per `plan-01.md`.
- **Manual verification remains required and is the primary acceptance
  check**, per `plan-01.md` step 5: browser devtools device emulation at
  375×667 and 390×844 confirming the dialog scrolls all the way to the
  artifacts table and past live output, plus a quick 1280×800 desktop check
  showing unchanged short-content presentation.

## Risks and mitigations

- **Risk: `85vh`/`85dvh` clips content on very short viewports (e.g. landscape
  phone, ~360px tall) leaving little room, or looks odd on very tall desktop
  monitors.** Mitigation: this is a max-height cap, not a fixed height — for
  content shorter than 85% of viewport height the dialog sizes to content as
  today (confirmed: `max-height` only constrains, doesn't force sizing).
  Verify explicitly during manual testing at the two mobile sizes named above;
  no code mitigation needed beyond what's already planned.
- **Risk: `overscroll-behavior: contain` is unsupported in very old WebKit,**
  leaving the FR-3 "stuck scroll" case unfixed on those browsers only.
  Mitigation: it's a progressive enhancement — worst case on an unsupported
  browser is today's existing (already-buggy) behavior for that one inner
  edge case, not a new regression; FR-2 (the dialog becoming scrollable at
  all) is the change that actually fixes the reported bug and has no such
  compatibility gap.
- **Risk: someone later adds a new stacked section to `_task.html` (e.g.
  another table) without re-checking mobile scroll.** Mitigation: none needed
  now — FR-2 makes the *dialog* the scroll container for all of its content,
  so new sections inherit reachability for free; this is precisely why
  bounding the outer dialog (rather than special-casing the artifacts section)
  is the right level to fix this at.
- **No risk to `test_architecture.py` or any invariant** — verified above,
  zero Python import changes.

## Prerequisites before implementation begins

None outstanding. Both `plan-01.md` and `design-01.md` accurately describe the
current code (verified by direct read of `board.html`/`_task.html` in this
step), the fix is fully scoped to two files, and the test suite has no
existing assertions on the `<style>` block or viewport meta that would need
updating (confirmed via grep — no hits for `dialog`/`viewport`/`max-height`/
`overflow` in `tests/test_api_html.py`). Implementation can proceed directly
per the "Implementation guidance" section above.
