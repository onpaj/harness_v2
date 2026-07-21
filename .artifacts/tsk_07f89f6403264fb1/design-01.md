# Design: Make the harness board mobile friendly

Concrete design for the plan in `plan-01.md`. Scope stays exactly as scoped there:
`src/harness/api/templates/{board,_columns,_task}.html`, CSS-only plus minimal
additive markup, no backend/route/data changes.

## UX/UI

### Wireframes

**Board, mobile (375×667, portrait), before any card is opened.** One column
fills the viewport width with a sliver of the next column showing to signal
horizontal scroll; the row swipes like a carousel.

```
┌───────────────────────────────────┐
│ harness board                      │  <- h1, 16px, unchanged
├───────────────────────────────────┤
│ ┌─────────────────────────────┐┌┄  │
│ │ TODO              3          │┊  │  <- column header
│ │ ┌───────────────────────────┐│┊  │
│ │ │ fix: flaky test in x       ││┊  │  <- card, full column width
│ │ │ myrepo · wt-fix-flaky      ││┊  │     padding bumped for touch
│ │ │ 2026-07-20T10:03Z          ││┊  │
│ │ └───────────────────────────┘│┊  │
│ │ ┌───────────────────────────┐│┊  │
│ │ │ add: retry on poll error   ││┊  │
│ │ │ myrepo · wt-retry          ││┊  │
│ │ │ [working]                  ││┊  │
│ │ └───────────────────────────┘│┊  │
│ └─────────────────────────────┘└┄  │
│   ← swipe →   (column 2/6 peeking) │
└───────────────────────────────────┘
```

**Task detail dialog, mobile.** Grows to near-full viewport instead of a
fixed 640px box; internal content scrolls vertically, and any table wider
than the screen scrolls horizontally *inside itself* only.

```
┌───────────────────────────────────┐  ← ~94vw / ~90vh, centered by the
│ tsk_07f89f6403264fb1          ✕*  │    native <dialog> backdrop
│ ┌───────────────────────────────┐ │
│ │ [ Restart ]  (44px tall)      │ │  <- only when status == failed
│ └───────────────────────────────┘ │
│ repository   myrepo                │
│ worktree     wt-fix-flaky          │  <- key/value table, 2 cols,
│ status       failed                │     already narrow, no wrap needed
│ ...                                 │
│                                     │
│ history                            │
│ ┌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐  │  <- .table-scroll wrapper:
│ ┊ time  actor from to outcome ▶┊  │     overflow-x:auto, page itself
│ └╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘  │     never scrolls sideways
│                                     │
│ live output                        │
│ ┌───────────────────────────────┐ │
│ │ $ running step...              │ │  <- max-height: 40vh on mobile
│ │ ...pre-wrap, 12px monospace    │ │     (vs. 240px fixed on desktop)
│ └───────────────────────────────┘ │
└───────────────────────────────────┘
   tap outside dialog to close (✕* — no new close button added, see
   plan's Open Questions; backdrop tap == click on touch)
```

Desktop layout (≥768px) is visually unchanged: multi-column board at
`min-width: 220px` per column, dialog capped at `max-width: 640px`.

### Component hierarchy

```
board.html                          (page shell: <head>, viewport meta,
│                                     <style>, SSE root, dialog host, JS)
├─ #board  (hx-get target for "/fragment/board", sse-trigger "sse:board")
│   └─ _columns.html                (fragment: .board > .column* > .card*)
│       included both server-side on first render and returned verbatim
│       by GET /fragment/board
│
└─ <dialog id="detail">             (empty on load; native modal)
    └─ _task.html                  (fragment: swapped in on card tap via
                                     GET /fragment/task/{id}, and again on
                                     POST /tasks/{id}/restart)
        ├─ Restart <button>        (conditional on status == failed)
        ├─ 3× <table> → wrapped in .table-scroll for FR-5
        └─ #stage-output           (SSE live-output pane, class-based
                                     styling instead of inline style)
```

No new components. `_columns.html` and `_task.html` remain fragments with
identical `hx-*`/`sse-*` attributes and element ids (`#board`, `#detail`,
`#stage-output`) — required, since `board.html`'s inline `<script>` and the
htmx SSE extension key off those ids.

### Key interactions

1. **Load** — viewport meta makes the page render at device width
   immediately (FR-1); no manual pinch/zoom step.
2. **Browse columns** — horizontal swipe/scroll across `.board` (native
   `overflow-x: auto`, unchanged mechanism); each column now sized so one
   is dominant on screen (FR-2).
3. **Open a task** — tap anywhere on `.card` → existing `hx-get` swaps
   `_task.html` into `#detail` → existing `htmx:afterSwap` listener calls
   `showModal()`. No interaction change, only the dialog's resulting size
   (FR-4).
4. **Read detail** — dialog scrolls vertically as a whole (`max-height:
   90vh; overflow-y: auto` on mobile); any table wider than the dialog
   scrolls horizontally *within its own wrapper* only (FR-5).
5. **Close** — tap outside the dialog (existing backdrop-click handler;
   touch-tap fires the same `click` event). No new UI element.
6. **Restart** (failed tasks only) — tap the `Restart` button, now with a
   ≥44px tall hit target on all viewports; `hx-post` swaps the refreshed
   `_task.html` back into `#detail`, dialog stays open (FR-7).
7. **Watch live output** — SSE stream into `#stage-output` continues
   unchanged; on mobile its `max-height` grows from 240px to 40vh so it
   uses the extra vertical room the taller dialog provides, still capped
   and internally scrollable (FR-6).

## Component design

### Breakpoint strategy

Desktop-first, one breakpoint: everything in today's unconditional CSS
stays as the default (desktop) rule; a single `@media (max-width: 767px)`
block at the end of the `<style>` element in `board.html` overrides sizing
for phones/small tablets. This matches the plan's non-functional
requirement ("no regression on desktop") with the smallest diff — every
mobile rule is an addition, not a rewrite of an existing selector.

`767px` is chosen (not e.g. 480px) so the layout also fixes itself on
larger phones in landscape and small tablets, consistent with the plan's
target range (~320–430px portrait) plus headroom.

### `board.html`

**1. Viewport meta** (FR-1) — add to `<head>`, before `<title>`:

```html
<meta name="viewport" content="width=device-width, initial-scale=1">
```

**2. Style block additions/edits** (FR-2, FR-3, FR-4, FR-6, FR-7):

Move the live-output pane's inline `style="..."` (currently in
`_task.html`) into a named class here, so it participates in the media
query instead of needing an inline override:

```css
.stage-output {
  max-height: 240px; overflow: auto; background: #091e42; color: #c1c7d0;
  padding: 8px; border-radius: 4px; white-space: pre-wrap;
  font: 12px ui-monospace, monospace;
}
```

Give the table-scroll wrapper (used in `_task.html`, FR-5) a rule that
applies at all widths (it's a no-op unless a table is actually wider than
its container, which mainly happens on mobile, but it's harmless on
desktop too):

```css
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
```

Give the `Restart` button a baseline touch-friendly size at all widths
(FR-7) — selecting the bare `button` element, since it's the only button
in the app today, avoids touching `_task.html`'s markup:

```css
button {
  min-height: 44px; padding: 8px 16px; font: inherit;
  border-radius: 4px; border: 1px solid #dfe1e6; background: #fff;
  cursor: pointer;
}
```

Mobile overrides:

```css
@media (max-width: 767px) {
  .column { min-width: 85vw; flex: 0 0 auto; }
  .card { padding: 12px 10px; }
  dialog { width: 94vw; max-width: 94vw; max-height: 90vh; overflow-y: auto; padding: 12px; }
  .stage-output { max-height: 40vh; }
}
```

Rationale per rule:
- `.column`: replaces the desktop `flex: 1` sizing (which, combined with
  `min-width: 220px`, currently lets 2–3 narrow columns cram onto a phone
  screen) with a fixed viewport-relative width and `flex: 0 0 auto` so
  flexbox stops trying to shrink/grow it — the existing `overflow-x: auto`
  on `.board` then does the horizontal-scroll work unchanged (FR-2).
- `.card`: desktop padding (`8px`) stays; mobile bumps it slightly so the
  card's total tappable height clears ~44px even for a title-only card
  with no repo/since line (FR-3).
- `dialog`: replaces the fixed `max-width: 640px; width: 90%` with
  near-full-viewport sizing and turns on internal vertical scroll, so
  content (three tables + live output) never gets clipped or forces the
  page to scroll (FR-4). The native `<dialog>` backdrop still centers it;
  no positioning rules needed.
- `.stage-output`: grows the live-output cap now that the dialog itself
  has more vertical room, without letting it dominate the whole dialog
  (FR-6). Font stays at 12px on both breakpoints — already at the "don't
  shrink below ~12px" floor from the plan.

### `_columns.html`

No markup changes. `.column` and `.card` styling is entirely handled by
the `board.html` style block edits above; `hx-get`/`hx-target` attributes,
element structure, and classes (`card`, `title`, `repo`, `since`, `badge`)
are untouched, so `test_api_html.py`'s content assertions keep passing.

### `_task.html`

Two additive, non-semantic markup changes:

1. Wrap each of the three `<table>` elements in `<div class="table-scroll">`
   (FR-5):

```html
<div class="table-scroll">
<table>
  <tr><th>repository</th><td>{{ task.repository or "—" }}</td></tr>
  ...
</table>
</div>
```

   (same wrapper around the `history` and `artifacts` tables).

2. Replace the live-output div's inline `style="..."` with `class="stage-output"`:

```html
<div id="stage-output" class="stage-output" sse-swap="line" hx-swap="beforeend scroll:bottom"></div>
```

`id="stage-output"`, `sse-swap`, `hx-swap`, the `hx-post`/`hx-target` on
`Restart`, and the `sse-connect`/`sse-close` attributes on the outer `div`
are all unchanged — nothing the SSE extension or the JS in `board.html`
depends on moves.

## Data schemas

None. No `Task`, `BoardView`, `ArtifactView`, port, or route changes —
this task is presentation-only (Jinja templates + inline CSS in
`board.html`). The one test addition (asserting the viewport `<meta>` tag
in `test_api_html.py`'s existing index test, per the plan) checks response
body text, not a schema.

## Traceability

| Requirement | Change |
|---|---|
| FR-1 viewport scaling | `board.html` `<head>` meta tag |
| FR-2 columns usable | `.column` mobile override in `board.html` |
| FR-3 cards legible/tappable | `.card` mobile override in `board.html` |
| FR-4 dialog usable | `dialog` mobile override in `board.html` |
| FR-5 tables don't force page scroll | `.table-scroll` wrapper class + `_task.html` markup |
| FR-6 live-output usable | `.stage-output` class + mobile `max-height` override |
| FR-7 restart reachable | `button` baseline sizing in `board.html` |
