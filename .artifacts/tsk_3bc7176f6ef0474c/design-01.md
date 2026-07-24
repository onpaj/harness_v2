# Design: fix mobile scrolling in the task detail dialog

Concrete design for the fix scoped in `plan-01.md` (FR-1..FR-4): the task detail
`<dialog>` in `board.html`/`_task.html` gets a real viewport meta tag and a bounded,
scrollable height, so the artifacts section (and everything below it) is reachable
on phone-sized screens. CSS/markup only, no backend or route changes.

## UX/UI

### Current vs. fixed layout (ASCII, iPhone SE-class viewport, 375×667)

**Before** — no viewport meta (page laid out at ~980px virtual width, then scaled
down by the mobile browser) and the dialog has no height/overflow budget, so it
either gets clipped by the browser's own UA behavior or renders far taller than the
viewport with no scroll container to reach the bottom:

```
┌ phone viewport (zoomed out) ─────┐
│ ┌ dialog (unbounded height) ───┐ │
│ │ tsk_xxx                      │ │
│ │ [info table]                 │ │
│ │ history                      │ │
│ │ [history table]              │ │
│ │ artifacts                    │ │  <- below the fold, no
│ │ [artifacts table]            │ │     scroll container reaches it
│ │ live output                  │ │
│ │ [#stage-output, own scroll]  │ │
│ └───────────────────────────────┘ │
└────────────────────────────────────┘
        (content overflows; page pinch-zoomed, gestures unreliable)
```

**After** — viewport meta makes the browser use real CSS pixels; the dialog is
capped at `85vh`/`85dvh` and becomes the scroll container for everything inside it:

```
┌ phone viewport (375×667, real px) ┐
│ ┌ dialog (max-height: 85vh) ────┐ │  ▲ scrolls
│ │ tsk_xxx                       │ │  │ (overflow-y: auto
│ │ [info table]                  │ │  │  on the dialog)
│ │ history                       │ │  │
│ │ [history table]               │ │  │
│ │ artifacts                     │ │  │  <- now reachable by
│ │ [artifacts table]             │ │  │     scrolling the dialog
│ │ live output                   │ │  │
│ │ ┌ #stage-output (240px) ────┐ │ │  │
│ │ │ inner scroll, contained   │ │ │  │  (overscroll-behavior:
│ │ │ (own overflow: auto)      │ │ │  │   contain — drag past its
│ │ └───────────────────────────┘ │ │  │   bottom hands off to the
│ └────────────────────────────────┘ │  ▼   dialog instead of stalling)
└─────────────────────────────────────┘
```

Desktop (≥640px width, short-to-medium content) is visually unchanged: the dialog
still centers at `max-width: 640px; width: 90%`; `max-height`/`overflow-y` only
engage once content is taller than 85% of the viewport, which today's short task
records rarely hit.

### Component hierarchy (existing DOM, unchanged structure)

```
board.html
└── <dialog id="detail">                 (native modal; owns viewport-relative sizing)
    └── [innerHTML swapped by htmx from GET /tasks/{id}]  →  _task.html fragment
        ├── <h2> task id
        ├── [restart button]             (conditional, status == failed)
        ├── <table> task info
        ├── <h3> history / <table>
        ├── <h3> artifacts / <table>     ← the section the bug report names
        └── <h3> live output
            └── <div id="stage-output">  (existing inner scroll box, 240px)
```

No new components. The fix changes two stylesheet rules (`dialog`, and the inline
style on `#stage-output`) and adds one `<meta>` tag — the fragment `_task.html`
keeps its current section order and markup.

### Key interactions

1. **Open detail on mobile.** Tap a card → htmx swaps `_task.html` into `#detail`
   → `showModal()` fires (`board.html:36`) → dialog renders at `max-height: 85vh`
   with its own vertical scrollbar/touch-scroll if content overflows.
2. **Scroll to artifacts.** User drags upward inside the dialog (anywhere on its
   background, not just inside a nested box) → the dialog itself scrolls, per
   FR-2 — this is the interaction that is currently broken.
3. **Scroll past live output.** Once the drag reaches `#stage-output` and its
   content is shorter than the drag distance, `overscroll-behavior: contain`
   (FR-3) stops the inner box from "catching" the scroll chain at its edge, so
   the same continuous drag keeps scrolling the outer dialog rather than going
   dead.
4. **Close.** Unchanged — tap outside the dialog content (`event.target.id ===
   'detail'`) or existing close affordance; `close` event still clears
   `innerHTML` to tear down the SSE connection (`board.html:44-46`).

No new interaction patterns, no new JS event listeners — the existing `afterSwap`/
`close` handlers in `board.html` are untouched.

## Component design

Two existing template files are touched; no module boundaries in `src/harness/`
change, so `test_architecture.py`'s layering constraints are unaffected (this is a
static-asset change, not Python).

### `src/harness/api/templates/board.html`

- **`<head>`**: add
  `<meta name="viewport" content="width=device-width, initial-scale=1">`
  immediately after `<meta charset="utf-8">`. Responsibility: tells mobile
  browsers to lay out at the device's actual CSS pixel width instead of a virtual
  desktop width — a prerequisite for `vh`/`dvh` units and touch scrolling to
  behave predictably in the dialog.
- **`dialog` rule**: extend the existing declaration
  ```css
  dialog {
    border: none; border-radius: 6px; padding: 16px;
    max-width: 640px; width: 90%;
    max-height: 85vh; max-height: 85dvh;
    overflow-y: auto;
  }
  ```
  Responsibility: the dialog becomes a bounded box that owns its own vertical
  scroll once its content (info + history + artifacts + live output) exceeds 85%
  of the viewport height. The duplicate `max-height` declaration is intentional —
  browsers that don't recognize `dvh` skip that line and keep the `vh` value
  (progressive enhancement, no `@supports` needed).
  No other rule in this file changes; `.board`, `.column`, `.card`, `table` rules
  are unaffected.

### `src/harness/api/templates/_task.html`

- **`#stage-output` inline style**: add `overscroll-behavior: contain` alongside
  the existing `max-height: 240px; overflow: auto`. Responsibility: once the
  dialog itself is scrollable (previous change), this stops a touch-scroll
  gesture that reaches the bottom of the inner live-output box from stalling —
  the scroll chain hands off to the dialog instead of being absorbed by the inner
  box's own overscroll glow/bounce.
  No structural HTML change — same `<h2>`/`<table>`/`<h3>` sequence, same
  `hx-*`/`sse-*` attributes, same Jinja loops over `task.history` / `artifacts`.

### Boundary check against project invariants

- `api/` still touches only `BoardView`/`ArtifactView` (unchanged Python) — this
  change is confined to Jinja/CSS text served by the existing routes.
- No route, no response model, no `BehaviorResult`, no queue/dispatcher code is
  touched, so invariants #1–#23 are not implicated by this change.

## Data schemas

No schema changes. This fix touches only static markup/CSS served by two existing
`GET` routes; it introduces no new fields, endpoints, or payloads.

- **`GET /`** (`board.html`) — response body gains the viewport `<meta>` tag and
  the updated `dialog` CSS text; the Jinja context (`board`) is unchanged.
- **`GET /fragment/board`, `GET /tasks/{id}`** (`_task.html`) — response body
  gains one CSS property on `#stage-output`'s inline style; the Jinja context
  (`task`, `artifacts`) is unchanged — same fields (`ref.step`, `ref.attempt`,
  `ref.name`, `entry.at/actor/from_step/to_step/outcome/reason`, etc.) render as
  before.
- SSE event payloads (`/api/events`, `/api/tasks/{id}/output/events`) are
  untouched — this fix doesn't alter what's streamed, only how the container that
  displays it scrolls.

## Regression coverage note

Per plan-01.md's open question: this repo's test suite (`tests/test_api_html.py`)
asserts on rendered HTML strings via FastAPI `TestClient`, with no browser
rendering/layout engine available. The design accepts string-level assertions as
the regression guard for this fix (e.g. the served `/` response contains the
viewport `<meta>` tag and the `dialog` rule's CSS text contains `max-height` and
`overflow-y: auto`) — these confirm the markup/CSS shipped, not that a real mobile
browser scrolls correctly. The acceptance criterion "mobile responsiveness is
tested on various screen sizes" is satisfied by manual verification (browser
devtools device emulation at 375×667 and 390×844, plus a desktop check at
1280×800), not by new automated tooling — this repo has no Playwright-style
harness to add one, and adding one is out of scope per plan-01.md.
