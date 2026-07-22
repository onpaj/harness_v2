# Design — Mobile-first board and admin UI redesign

Input: `plan-01.md` (this task has no separate `architecture-*.md` artifact — the
plan already carried the scope correction and file boundaries; this document
turns that into concrete markup, CSS tokens and interaction wiring for the
development step).

Scope, unchanged from the plan: **board** (columns + cards) and **task detail**
(info/history/artifacts/live output) only. No admin/agents/workflows UI, no
workflow-grouped tabs, no port/model/route changes beyond the `shorttime`
filter. Every wireframe, class name and script below is a proposal the
development step should treat as a strong default, not a byte-for-byte
contract — the contract is the acceptance criteria and the pinned tests.

## 1. UX/UI

### 1.1 Phone board (< 768px, e.g. 390×844)

```
┌───────────────────────────────────┐
│ ● harness                         │  appbar, sticky, safe-area-top pad
├───────────────────────────────────┤
│ Board                             │  h1, slim page header
│                                    │
│ TODO                          [2] │  sticky column head + count pill
│ ┌────────────────────────────────┐│
│ │▐ Fix the login bug             ││  card, colored left stripe
│ │▐ my-repo · wt_sacramento       ││  repo/worktree basenames
│ │▐ [processing]        Jul 22,.. ││  badges left, time right
│ └────────────────────────────────┘│
│ ┌────────────────────────────────┐│
│ │▐ tsk_2                         ││  no title → id; no history → no time
│ └────────────────────────────────┘│
│                                    │
│ DEVELOPMENT                   [0] │
│   No tasks                        │  collapsed empty column, one slim row
│                                    │
│ DONE                          [3] │
│ ┌────────────────────────────────┐│
│ │▐ ...                           ││
│ └────────────────────────────────┘│
│                                    │
│ FAILED                        [1] │
│ ┌────────────────────────────────┐│
│ │▐ ...                    [failed]│
│ └────────────────────────────────┘│
├───────────────────────────────────┤
│  ⬛        (Board, highlighted)    │  bottom tab bar, safe-area-bottom pad
└───────────────────────────────────┘
```

Columns are full-width blocks stacked top to bottom — the page itself never
scrolls sideways. Each column's own card list can never overflow horizontally
(cards are block-level, full width); only the History/Artifacts tables inside
the detail sheet get their own horizontal scroller (§1.3).

### 1.2 Desktop board (≥ 768px)

```
┌──────────────────────────────────────────────────────────────────────┐
│ ● harness                                    Board                   │  appbar, inline links
├──────────────────────────────────────────────────────────────────────┤
│  Board                                                                │
│  ┌─ TODO [2] ─────┐ ┌─ DEVELOPMENT [1] ┐ ┌─ DONE [3] ──┐ ┌ FAILED [1]┐│
│  │ ▐ card          │ │ ▐ card           │ │ ▐ card      │ │ ▐ card    ││
│  │ ▐ card          │ │                  │ │ ▐ card      │ │           ││
│  └─────────────────┘ └──────────────────┘ └─────────────┘ └───────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

Identical markup to the phone layout — `.board` switches from
`flex-direction: column` to `row` at `min-width: 768px`; columns become fixed
cards in a row instead of full-width blocks. `overflow-x: auto` on `.board`
stays as the existing fallback for many columns (open question 3 in the plan;
unchanged behaviour).

### 1.3 Task detail — phone (full-screen sheet) vs desktop (dialog)

Phone:

```
┌───────────────────────────────────┐
│ ← tsk_1                [Restart]✕│  header, safe-area-top pad
├───────────────────────────────────┤
│ [ Info ]   History     Output     │  segmented control, 3 buttons
├───────────────────────────────────┤
│ id            tsk_1                │
│ repository    app-backend          │  stacked key/value list
│ worktree      —                    │  (label over/left of value)
│ workflow      default              │
│ status        development          │
│ lastOutcome   request_changes      │
│ lockId        lck_1                │
│ created       Jul 19, 10:00        │  shorttime text, title="…Z" ISO
│ data          {}                   │
│                                     │
│ ARTIFACTS                          │
│ ┌─ step │ attempt │ name ────────┐ │  scrolls inside this box only
│ └────────────────────────────────┘ │
└───────────────────────────────────┘
```

Tapping **History** or **Output** swaps which panel is visible (CSS only —
all three panels stay mounted, see §3.3); History and Artifacts render inside
their own `overflow-x: auto` container so a wide table never widens the page.
Output keeps its live-output `<div>` mounted from the moment the sheet opens,
regardless of which tab is active, so the SSE stream is already flowing when
the user switches to it.

Desktop: the same `.task-detail` markup renders inside a centered `<dialog>`
(≈860px wide, ≈88vh tall) instead of covering the viewport; header/tabs/panels
are unchanged, just laid out with more breathing room.

### 1.4 Component hierarchy

```
board.html                                   (full page, GET /)
├─ <head>: viewport/theme-color/apple-* meta, <link app.css>, htmx+sse <script>
├─ _nav.html                                  (partial, included, not fragment-served)
│   ├─ <header class="appbar">  — brand + inline links (desktop)
│   └─ <nav class="tabbar">     — icon+label links (phone)
├─ <main class="page">
│   ├─ page header (h1 "harness board")
│   └─ #board  [hx-get=/fragment/board, hx-trigger=sse:board]
│       └─ _columns.html                      (partial, ALSO served standalone
│            └─ .board                          at GET /fragment/board)
│                └─ .column × N
│                    ├─ .column__head (h2 name + .column__count)
│                    └─ .column__list (.card × N) | .column__empty
└─ <dialog id="detail">                       (empty shell; filled by swap)
     └─ _task.html                            (partial, served standalone at
          └─ .task-detail                       GET /fragment/task/{id} and
               ├─ .task-detail__header           POST /tasks/{id}/restart)
               ├─ .tabs (3 × button.tab)
               └─ .tab-panels
                   ├─ [data-panel=info]    → .kv list + artifacts table-scroll
                   ├─ [data-panel=history] → table-scroll
                   └─ [data-panel=output]  → sse div > #stage-output.terminal
```

`_columns.html` and `_task.html` remain pure data-render partials (no
`<script>`), exactly as today — they are re-rendered server-side on every
htmx swap, so any client state (e.g. which tab is active) must not live in
markup that gets replaced. That's why tab-switching JS is delegated from a
container that survives the swap (`#detail`, not `.task-detail` — see §3.3).

### 1.5 Key interactions

| # | Trigger | Effect |
|---|---|---|
| 1 | SSE `sse:board` frame arrives | revision de-dup (unchanged) → `hx-get /fragment/board` → `#board` innerHTML swap |
| 2 | Tap a card | `hx-get /fragment/task/{id}` → `#detail` innerHTML swap → `htmx:afterSwap` → `dialog.showModal()` |
| 3 | Tap Info/History/Output | delegated click on `#detail` → toggle `.tab.active` + `.tab-panel.active` by matching `data-tab`/`data-panel` (pure CSS visibility, no re-fetch) |
| 4 | Tap ✕, tap backdrop, or Esc | `dialog.close()` → native `close` event → `#detail.innerHTML = ''` (unchanged; tears down the Output SSE connection) |
| 5 | Tap Restart (failed tasks only) | `hx-post /tasks/{id}/restart` → `#detail` innerHTML swap (refreshed sheet) → same `afterSwap` → `showModal()` again (idempotent, already open) |
| 6 | Page load | `_nav.html`'s inline script compares `location.pathname` to each nav link's `href` and marks the match `aria-current="page"` / `.active` |

No new endpoints, no new triggers — everything above already exists today
except #3 (net-new markup, nothing to preserve) and #6 (trivial, one page).

## 2. Component design

| File | Responsibility | Notes |
|---|---|---|
| `static/app.css` (new) | Every rule for every page. Design tokens as CSS custom properties on `:root`; full dark palette under `@media (prefers-color-scheme: dark)`; one `@media (min-width: 768px)` block flips board/nav/dialog from phone to desktop layout. | Self-contained: system font stack, no `@import`, no external URL. Loaded once via `<link rel="stylesheet" href="/static/app.css">`. |
| `templates/_nav.html` (new partial) | Renders the same nav data twice — as a top `appbar` (desktop) and a bottom `tabbar` (phone) — from one Jinja-local list, plus the active-section script. | Data-driven per the plan (`{% set nav_items = [("Board", "/", "board")] %}`), so a future entry is one tuple, not a rewrite. Included by `board.html` only; not served as its own fragment. |
| `templates/board.html` | Page shell: `<head>` meta/link/scripts, includes `_nav.html`, hosts `#board` and `dialog#detail`, owns the one `<script>` block with all client behaviour (dialog open/close, delegated tab switching, revision de-dup). | Behavioural surface is a superset of today's — same three existing handlers, plus the new delegated tab-switch handler on `#detail`. |
| `templates/_columns.html` | Renders `Board` → `BoardColumn` → `Task` as the responsive column/card markup. Pure template, no script. Served both inline (via `{% include %}` in `board.html`) and standalone at `GET /fragment/board`. | Behavioural contract (title fallback, repo/worktree basenames, working/last-outcome badges, since-timestamp-only-with-history) is unchanged — see §3.1 for the exact guard placement. |
| `templates/_task.html` | Renders one `Task` + its `ArtifactRef` list as the segmented sheet. Pure template, no script — its tab buttons carry `data-tab`/`data-panel` attributes that `board.html`'s delegated listener reads. Served standalone at `GET /fragment/task/{id}` and `POST /tasks/{id}/restart`. | Output panel's `hx-ext="sse"` / `sse-connect` / `sse-close="end"` / `id="stage-output"` / `sse-swap="line"` / `hx-swap="beforeend scroll:bottom"` attributes are carried over byte-for-byte; only the surrounding class changes (`style="…"` → `class="terminal"`). |
| `routes.py` | Adds one Jinja filter, `shorttime`, registered next to the existing `basename` filter. | The only non-template, non-CSS change in this task. No route, no context, no signature changes. |

Architecture invariants this design respects (from `CLAUDE.md`): `api/` still
imports only `ports/board.py`, `ports/artifacts.py`, `ports/logs.py`,
`ports/control.py`, `ports/clock.py` — nothing new is added to that import
list, so invariant #11 (`api/` and `projection.py` import no drivers) holds
by construction; no template gains a new context variable that isn't already
produced by `BoardView.snapshot()/get()`, `ArtifactView.list()` or
`Task`/`HistoryEntry`/`ArtifactRef`'s existing fields.

## 3. Data schemas

No port, model or JSON-response schema changes. This section pins the exact
shapes the templates consume (unchanged) and the two genuinely new schemas
this task introduces: the `shorttime` filter and the nav item tuple.

### 3.1 Template render contexts (unchanged from today)

| Route | Template | Context |
|---|---|---|
| `GET /` | `board.html` | `{"board": Board}` |
| `GET /fragment/board` | `_columns.html` | `{"board": Board}` |
| `GET /fragment/task/{id}` | `_task.html` | `{"task": Task, "artifacts": tuple[ArtifactRef, ...]}` |
| `POST /tasks/{id}/restart` | `_task.html` | same as above, post-restart |

`Board`, `BoardColumn`, `Task`, `HistoryEntry`, `ArtifactRef` are exactly as
defined in `ports/board.py`, `models.py`, `ports/artifacts.py` today — no
field is added, renamed or removed by this task.

Card-level guards the new markup must preserve exactly (pinned by
`tests/test_api_html.py`):

```
title        := task.data.title or task.id
repo line    := present only if task.repository or task.worktree
                 rendered as "{repository|basename}[ · ][{worktree|basename}]"
working badge:= present only if task.lock_id
outcome badge:= present only if task.last_outcome, class == last_outcome value
since/time   := present only if task.history is non-empty; sourced from
                 task.history[-1].at; the raw ISO string from that field must
                 still appear somewhere in the rendered card (design puts it
                 in a `title="{{ ... }}"` attribute on the same element that
                 shows the `shorttime`-formatted text)
```

Detail-sheet guards to preserve (pinned by the same test file):

```
every optional field renders `or "—"` — never a bare Python `None`
history/artifact rows keep their existing column sets
restart button rendered only when task.status == "failed", with the
  existing hx-post/hx-target/hx-swap attributes
output panel keeps hx-ext="sse", sse-connect, sse-close="end",
  id="stage-output", sse-swap="line", hx-swap="beforeend scroll:bottom"
  verbatim
```

### 3.2 `shorttime` Jinja filter

```python
def _shorttime(value: str | None) -> str:
    """ISO-8601 UTC ("...Z" or "+00:00") -> compact display form, e.g.
    "Jul 22, 09:03". Defensive like `_basename`: anything it can't parse
    (None, already-short text, garbage) passes through unchanged."""
    if not value:
        return value or ""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return value
    return parsed.strftime("%b %d, %H:%M")

TEMPLATES.env.filters["shorttime"] = _shorttime
```

Applied consistently everywhere a timestamp is shown — card `.since`/time
element, the detail sheet's `created` row, and the History table's `time`
column — always paired with the untouched raw ISO string in a `title="{{ … }}"`
attribute on the same element, e.g.:

```html
<span class="card__time" title="{{ task.history[-1].at }}">{{ task.history[-1].at | shorttime }}</span>
...
<td title="{{ entry.at }}">{{ entry.at | shorttime }}</td>
```

This resolves the plan's open question 4: apply `shorttime` uniformly
(including the History table), since the pinned test only asserts the raw ISO
string is present *somewhere* in the response body (`"2026-07-19T10:00:05Z"
in body`) — a `title` attribute satisfies that exactly as well as visible
text, and a single rule ("always short text + ISO title") is simpler to keep
correct than two different rules for card vs. table.

### 3.3 Nav items (new, template-local — not a route/context change)

```jinja2
{# _nav.html #}
{% set nav_items = [
  {"label": "Board", "href": "/", "section": "board", "icon": "board"},
] %}
```

One entry today (per the plan's scope cut), looped identically for both the
`appbar` (desktop links) and `tabbar` (phone icons+labels) renderings, so
adding "Agents"/"Workflows" later — once their ports/routes exist — is a
one-line addition to this list, not a template rewrite. `section` is matched
against `location.pathname` client-side (§1.5 #6) since there's exactly one
route today and no server-side "current page" context variable to add
(adding one would be a `routes.py` context change, outside this task's
scope).

### 3.4 CSS design tokens

Custom properties on `:root`, overridden wholesale under
`@media (prefers-color-scheme: dark)` — values below are the light-mode
defaults; adapted from the reference branch's palette, which already passes
a manual contrast check in both modes:

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--bg` | `#f2f2f7` | `#000000` | page background |
| `--surface` | `#ffffff` | `#1c1c1e` | card/dialog/table background |
| `--surface-2` | `#f7f7fa` | `#2c2c2e` | column background (desktop), input background |
| `--text` / `--text-2` / `--text-3` | `#1c1c1e` / `#55555b` / `#8e8e93` | `#ffffff` / `#aeaeb4` / `#8e8e93` | primary / secondary / tertiary text |
| `--border` / `--border-strong` | `#e3e3e8` / `#d1d1d6` | `#38383a` / `#48484a` | hairlines, input borders |
| `--accent` / `--on-accent` | `#007aff` / `#fff` | `#0a84ff` / `#fff` | links, active tab, primary button |
| `--done-bg`/`--done-fg`, `--changes-bg`/`--changes-fg`, `--working-bg`/`--working-fg`, `--failed-bg`/`--failed-fg` | paired bg/fg per status | — | badges + card left-stripe colour |
| `--radius` / `--radius-sm` / `--radius-pill` | `16px` / `11px` / `999px` | — | card/dialog / input / badge-button corner radii |
| `--safe-top` / `--safe-bottom` | `env(safe-area-inset-top, 0px)` / `env(safe-area-inset-bottom, 0px)` | — | notch/home-indicator padding |
| `--tabbar-h` | `56px` | — | bottom tab bar height, reserved via `body`'s `padding-bottom` |

Status → colour mapping (drives both the badge and the card's left stripe):
`lock_id set → working`, `last_outcome == "done" → done`,
`last_outcome == "request_changes" → changes`, `status == "failed" → failed`,
else neutral (`--border-strong`). This is presentation-only derived state —
no new field, computed the same way the existing `.badge.working`/
`.badge.{{ last_outcome }}` conditionals already are.
