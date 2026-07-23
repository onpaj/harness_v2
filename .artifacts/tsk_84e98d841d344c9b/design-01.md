# Design: full-screen tabbed task detail view

Based on `plan-01.md`. Pure front-end change to `board.html` / `_task.html`
(CSS + template + one small inline script) — no port/route/data changes.

## UX/UI

### Wireframes

Full-screen modal, header fixed, tab bar fixed, tab body scrolls independently.
Tab 1 (default) — **Task info**:

```
┌ dialog#detail ────────────────────────────────────────────────────── 96vw ┐
│ tsk_84e98d8…   Redesign the task detail view              [Restart] [✕]  │  ← header, fixed
├────────────────────────────────────────────────────────────────────────┤
│ [ Task info ]   History    Output                                        │  ← tab bar, fixed
├────────────────────────────────────────────────────────────────────────┤
│  repository   harness_v2                                                  │
│  worktree     tsk_84e98d8…                                                │
│  workflow     default                                                     │
│  status       development                                                 │
│  lastOutcome  request_changes                                             │
│  lockId       lck_1                                                       │
│  created      2026-07-21T09:00:00Z                                        │
│  data         { "title": "…" }                                            │
│                                                                            │
│  artifacts                                                                │
│  ┌──────────┬─────────┬──────────────────────┐                           │
│  │ step     │ attempt │ name                 │                           │
│  ├──────────┼─────────┼──────────────────────┤                           │
│  │ design   │ 1       │ design-01.md         │  (scrolls if long)         │
│  └──────────┴─────────┴──────────────────────┘                           │
└────────────────────────────────────────────────────────────────────── 92vh┘
```

Tab 2 — **History** (same shell, body replaced):

```
├────────────────────────────────────────────────────────────────────────┤
│  Task info    [ History ]   Output                                       │
├────────────────────────────────────────────────────────────────────────┤
│  time                  actor        from      to           outcome  ...  │
│  2026-07-21T09:00:05Z  dispatcher   review    development  request…      │
│  …                                                                        │
│  (full tab height, no more 240px-ish cramped embedded table)              │
```

Tab 3 — **Output** (same shell, body replaced, dark live-log panel fills body):

```
├────────────────────────────────────────────────────────────────────────┤
│  Task info    History    [ Output ]                                      │
├────────────────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │ [09:00:03] step 'development' started                              │ │
│ │ [09:00:04] writing plan-01.md                                      │ │
│ │ …live-streamed lines, auto-scrolls to bottom, fills remaining body… │ │
│ └────────────────────────────────────────────────────────────────────┘ │
```

### Component hierarchy

```
board.html
└─ <dialog id="detail">                     (persistent shell, never re-created)
     [content below is the htmx-swapped fragment, _task.html, re-rendered fresh on every open/restart]
     └─ .task-detail
        ├─ header.task-detail__header
        │   ├─ .task-detail__id       ("tsk_…" + task.data.title if present)
        │   ├─ button[Restart]         (only when task.status == "failed")
        │   └─ button.task-detail__close  ("✕", new — see Key interactions #3)
        ├─ nav.tabs[role=tablist]
        │   ├─ button.tab[data-tab=info]    (active by default)
        │   ├─ button.tab[data-tab=history]
        │   └─ button.tab[data-tab=output]
        └─ div.tab-panels
           ├─ section.tab-panel[data-panel=info]      (active by default)
           │   ├─ table.meta            (existing metadata rows, unchanged)
           │   └─ table.artifacts       (moved here from its own top-level <h3>)
           ├─ section.tab-panel[data-panel=history]
           │   └─ table.history         (existing history rows, unchanged)
           └─ section.tab-panel[data-panel=output]
              └─ div[hx-ext=sse]        (existing SSE div, now fills the full panel)
                 └─ #stage-output[sse-swap=line]
```

`board.html` owns two things that must survive across every fragment swap:
the `<dialog>` element itself and the **one** delegated tab-click listener
(registered once, on `#detail`, in `board.html`'s existing `<script>` block —
not inside `_task.html`, which is destroyed and re-parsed on every open/restart
and would otherwise re-register a duplicate listener each time). `_task.html`
stays pure markup: no `<script>` of its own.

### Key interactions

1. **Open a task.** Click card → `hx-get /fragment/task/{id}` → swap into
   `#detail` → existing `htmx:afterSwap` handler calls `showModal()`.
   `_task.html` is rendered server-side with the Task info tab/panel already
   marked active (`class="tab active"` / `class="tab-panel active"`) — this is
   what satisfies FR-3 (default tab) and FR-4 (no leaked state) for free, since
   there is no client-side "remember last tab" state at all.
2. **Switch tab.** Click a `.tab` button → the delegated listener (matches
   `event.target.matches('.tab')`) reads `data-tab`, toggles `.active` on that
   button and its sibling buttons, and toggles `.active` on the `.tab-panel`
   whose `data-panel` matches. Hiding is CSS (`.tab-panel:not(.active) {
   display: none; }`), never `innerHTML` removal or conditional re-render —
   the Output panel's SSE-connected div is never detached, so the connection
   and any already-rendered lines survive switching away and back (FR-2).
   No network round-trip.
3. **Close.** Three equivalent triggers — clicking the new `✕` button
   (`detail.close()`), clicking the backdrop (existing `click` listener,
   unchanged), or pressing Escape (native `<dialog>` behavior, already
   implicit today) — all funnel into the native `close` event, where the
   existing handler clears `innerHTML`, tearing down the SSE connection via
   htmx's `htmx:beforeCleanupElement`. The `✕` button is a new addition to the
   design: at 96vw × 92vh there's only a thin backdrop margin left to
   click, so an explicit close affordance is needed for usability that the
   plan didn't call out but the full-screen size requires.
4. **Restart.** Unchanged mechanically (`hx-post /tasks/{id}/restart` →
   swaps `#detail`) — the response is the same `_task.html`, so it re-renders
   with Task info active again, satisfying FR-6 without special-casing.

## Component design

**`board.html`**
- CSS: `dialog#detail` grows from `max-width: 640px; width: 90%` to a
  full-screen layout. Since a native `<dialog>`'s default open display is
  `block`, override to `flex` only while open:
  ```css
  dialog#detail { max-width: none; width: 96vw; height: 92vh; padding: 0; }
  dialog#detail[open] { display: flex; flex-direction: column; }
  ```
  `.task-detail` is the flex child filling that box (`flex: 1; min-height: 0;
  display: flex; flex-direction: column;`), so the header and tab bar stay
  fixed height and `.tab-panels` gets the remaining space
  (`flex: 1; min-height: 0; overflow: auto;`) — this is what lets the Output
  panel's log div fill available height instead of the current fixed
  `max-height: 240px`.
- JS: one new delegated `click` listener added next to the existing two
  (`afterSwap` → `showModal()`, dialog `click` → backdrop-close), plus the
  `close` handler unchanged. No new listener is added per fragment render —
  it lives at the `#detail` level in the page's static script, registered once
  at page load.

**`_task.html`**
- Restructured into the hierarchy above. No `{% include %}` split into
  separate partials — three `<div>`s in one file is enough (per the plan's
  "Open questions", kept as a single file to minimize new templates).
- Content moves, not logic: the metadata `<table>`, history `<table>`,
  artifacts `<table>` and SSE `<div>` are the exact same Jinja loops as today,
  just re-homed under their tab panel and wrapped with `.active` on the first
  one only.
- The restart `{% if task.status == "failed" %}` guard is unchanged, only its
  position moves into the new header.

**Boundary respected:** neither template gains a new data dependency —
`task` (with `.history`) and `artifacts` are the same two context variables
`routes.py::_task_fragment` already passes in. `StageOutputView`'s SSE
endpoint (`/api/tasks/{id}/output/events`) is untouched.

## Data schemas

No new or changed schemas — `Task`, `HistoryEntry`, `ArtifactRef`, and the SSE
`line`/`end` event frames are all unchanged (see `routes.py:37-69`). The only
"schema" this design adds is a markup/class contract internal to the two
templates, for the dev step to implement literally:

| Element | Attribute | Values | Purpose |
|---|---|---|---|
| `.tab` button | `data-tab` | `info` \| `history` \| `output` | which panel it activates |
| `.tab-panel` section | `data-panel` | `info` \| `history` \| `output` | matched against `data-tab` |
| both | `class` | base class + `active` (present only on the initial/current one) | server sets `active` once on render; JS moves it |

This table is the only "interface" introduced by this design — a client-side
convention, not a network contract. `GET /fragment/task/{task_id}` and
`POST /tasks/{task_id}/restart` keep their existing request/response shapes.

## Test impact (informational, for the dev step)

`tests/test_api_html.py` only asserts on substrings present anywhere in the
returned HTML (e.g. `"review" in body`, `"Restart" in body`) — it never
parses table structure or checks CSS visibility. Since hidden tab panels stay
in the DOM (`display: none`, not removed), all existing assertions keep
passing unmodified against the new markup. No test changes are required by
this design; the dev step should still run the suite to confirm.
