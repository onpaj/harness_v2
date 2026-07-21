# Architecture: make the harness board mobile friendly

## Alignment with existing patterns and integration points

The plan and design are sound and I'm endorsing them essentially as written,
with one tightening (below). I read the three actual templates
(`board.html`, `_columns.html`, `_task.html`), `tests/test_api_html.py`, and
grepped the test suite (`test_board_e2e.py`, `test_api_sse.py`) for any
assertion on inline styles, class names, or the exact `dialog`/`stage-output`
markup. Findings:

- The design's inventory of current CSS/markup (`.column { min-width: 220px;
  flex: 1; }`, `dialog { max-width: 640px; width: 90%; }`, the inline
  `style="..."` on `#stage-output`, the three bare `<table>`s in `_task.html`)
  matches the source exactly — nothing was guessed or drifted.
- No test in the suite asserts on inline CSS, a class name, or exact pixel
  values. `test_api_html.py` asserts on text content, hx-attributes, ids, and
  `"https://" not in body` (a check against externally-hosted assets, not
  against this change). `test_board_e2e.py`/`test_api_sse.py` don't touch
  `#stage-output`'s styling at all. This confirms the plan's non-functional
  requirement ("existing tests keep passing unmodified in behavior") is
  actually true of the code, not just assumed.
- This project's whole UI is one self-contained rendering path: three Jinja
  templates, one inline `<style>` block, two vendored static JS files
  (`htmx.min.js`, `sse.js`), no build step. The design's decision to do
  everything as CSS/markup-only, inside that same `<style>` block, is the only
  choice consistent with that architecture — introducing a stylesheet file,
  a preprocessor, or a JS-driven responsive behavior would be a new pattern
  this codebase doesn't have anywhere else, for a problem plain media queries
  solve completely.
- Per `CLAUDE.md`'s invariants (1–23) and the module map: this change touches
  none of `router.py`, `dispatcher.py`, `consumer.py`, any `ports/`, any
  `drivers/`, or `api/routes.py` — only `api/templates/*.html`. None of the
  23 invariants apply, and `test_architecture.py`'s import-boundary checks
  have nothing here to violate. This is the cleanest possible category of
  change in this codebase: presentation-only, zero surface area against the
  architecture the rest of the project is built to protect.

## Proposed architecture

No new components. The design's three-file change is correct:

```
board.html          <head> viewport meta, <style> block edits (all breakpoint
                     logic lives here — single source for .column, .card,
                     dialog, button, .stage-output, .table-scroll rules)
_columns.html        unchanged (styling inherited from board.html's <style>)
_task.html            + <div class="table-scroll"> wrapper × 3 tables
                       inline style="" on #stage-output → class="stage-output"
```

**Decision — desktop-first, single `@media (max-width: 767px)` block, all
mobile rules additive.** This is the design's choice and I agree with it over
the alternative (mobile-first base + `min-width` override for desktop):
a desktop-first diff is *strictly additive* to the existing `<style>` block —
every current selector's default behavior is untouched, and mobile behavior
is layered on top in one place at the bottom of the block. A mobile-first
rewrite would touch every existing selector's default values, multiplying
the diff and the chance of an unintended desktop regression, for a project
that has no visual regression tooling to catch one. Given "no regression on
desktop" is an explicit non-functional requirement and there's no automated
way to verify it beyond manual spot-checks, minimizing the touched surface on
the desktop path is the right tradeoff.

**Decision — extract `#stage-output`'s inline style to a `.stage-output`
class in `board.html`.** Required, not optional: an inline `style="..."`
attribute cannot participate in a media query. This is the one place the
design's markup change in `_task.html` is load-bearing rather than cosmetic
— confirmed safe above (no test depends on the inline style's presence or
exact value).

**Decision — `.table-scroll` wrapper applies unconditionally (all
viewports), not just inside the mobile media query.** This matches the
design and is correct: `overflow-x: auto` on a container that's never
actually overflowing is a no-op visually and doesn't need a breakpoint guard.
Keeping it unconditional means one wrapper `<div>` in `_task.html`
covers both viewport classes with zero conditional logic.

## Implementation guidance

**Where the changes belong** — no ambiguity, the plan/design already scoped
this precisely, confirmed against the real files:

1. `board.html`:
   - `<meta name="viewport" content="width=device-width, initial-scale=1">`
     in `<head>`, before `<title>` (FR-1).
   - In the existing `<style>` block: add `.stage-output` and `.table-scroll`
     class rules; add a baseline `button { min-height: 44px; ... }` rule;
     replace nothing in the unconditional rules for `.column`/`dialog` — those
     stay exactly as-is (their current values become the desktop default).
   - Append one `@media (max-width: 767px) { ... }` block at the end of
     `<style>` overriding `.column`, `.card`, `dialog`, `.stage-output`, per
     the design's concrete CSS.
2. `_task.html`:
   - Wrap each of the three `<table>` elements in `<div class="table-scroll">
     ... </div>` — no change to the rows/cells inside.
   - Change `<div id="stage-output" style="...">` to `<div id="stage-output"
     class="stage-output">`. `sse-swap="line"` and `hx-swap="beforeend
     scroll:bottom"` are untouched.
3. `_columns.html`: no changes at all — confirmed the design is right that
   `.column`/`.card` sizing is fully controlled from `board.html`'s
   `<style>` block, and `_columns.html` carries no inline styles today.
4. `tests/test_api_html.py`: add one assertion to
   `test_index_renders_board_shell` (or a new small test) —
   `assert 'name="viewport"' in body` and `assert "width=device-width" in
   body`. This is the only test change in scope; do not touch or restructure
   existing tests, since none of them need to change for this to pass (as
   verified above).

**Order of implementation** (smallest-risk-first, each step independently
verifiable):
1. Viewport meta tag (FR-1) + its test assertion — zero interaction with
   layout, verify first in isolation.
2. `.stage-output` class extraction in `_task.html`/`board.html` — a pure
   refactor (inline style → class, same computed values) before any new
   mobile rule is added, so a visual diff at this point should be *none* at
   any viewport. Good checkpoint to confirm nothing broke before adding new
   behavior.
3. `.table-scroll` wrapper markup — same "no visible desktop change"
   checkpoint.
4. `button` baseline sizing — small, isolated, affects only the one Restart
   button today.
5. The `@media (max-width: 767px)` block (`.column`, `.card`, `dialog`,
   `.stage-output` overrides) — the one step with actual new visual
   behavior; do this last so everything it builds on is already verified
   inert on desktop.

**Contracts that must not move** (the design already lists these; restating
because they're the actual regression risk, not the CSS): `#board`, `#detail`,
`#stage-output` element ids; `hx-get`/`hx-post`/`hx-target`/`hx-swap`,
`sse-connect`/`sse-swap`/`sse-close` attributes and their values, on every
element that currently has them. The inline `<script>` in `board.html`
(`htmx:afterSwap` → `showModal()`, backdrop-click → `close()`, the revision
de-dup listener) queries by id, not by class or structure, so none of the
CSS/class changes here can break it — but any accidental id rename during
the edit would.

## Risks and mitigations

- **Risk: `dialog { width: 94vw; max-height: 90vh; overflow-y: auto; }`
  interacts with the browser's native `<dialog>` centering in a way that
  clips content on very short viewports (e.g. landscape phone, ~375×375
  after browser chrome).** Mitigation: this is exactly what manual device-
  emulation verification (plan step 8) is for — check both portrait and
  landscape at the target widths before calling this done, not just
  375×667 portrait. If landscape clips, the fix is dropping `max-height`
  slightly (e.g. `85vh`) rather than adding new markup.
- **Risk: `-webkit-overflow-scrolling: touch` on `.table-scroll` is a
  legacy iOS Safari property, ignored (harmlessly) by every current engine.**
  Not a functional risk — modern WebKit does momentum scrolling on
  `overflow-x: auto` natively — but worth a one-line note so a future reader
  doesn't wonder why it's there or bother "fixing" it. No action needed
  beyond leaving it; removing it isn't worth a diff line either way.
- **Risk: selecting the bare `button` element for the 44px baseline rule is
  correct *today* (one button in the whole UI) but is a landmine if a second,
  differently-sized button is ever added** (e.g. a future "cancel" action)
  without the author noticing the existing global rule. Mitigation: none
  needed now — the design's own rationale (avoid touching `_task.html`
  markup for a single button) is the right call for this task's scope — but
  flagging it so a future PR that adds a second button knows to check this
  rule rather than being surprised by it.
- **Risk: manual-only verification (no screenshot/visual regression tooling
  in this repo) means the "no regression on desktop" requirement is only as
  good as whoever runs the devtools check.** Mitigation: the ordering above
  (extract-style-first, then wrapper-markup, then sizing, then media query)
  turns one big "did I break anything" question into four small ones, each
  checkable by comparing before/after screenshots at one desktop width — the
  cheapest substitute for real regression tooling this task's scope
  justifies.

## Prerequisites before implementation begins

None. The plan and design are complete, consistent with the current
templates and test suite (verified by direct comparison against source, not
assumed), and require no new dependency, port, or backend change. Development
can start directly from the design doc's concrete CSS/markup, in the
implementation order above.
