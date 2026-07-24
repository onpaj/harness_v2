# Design — Harness dashboard: local-timezone timestamps

Implements `.artifacts/tsk_e22b195369cc4469/plan-01.md`. Purely a client-side
presentation change: three template edits, one new static JS file, one new
script-tag + two-line wiring change in `board.html`. No route, port, or
projection change.

## UX/UI

The dashboard's visual layout is unchanged — no new controls, no timezone
picker, no layout shift. The only visible difference is the *text* of every
rendered timestamp.

**Before → after, per surface:**

```
Card ("since" line, _columns.html:16)
  before: 2026-07-23T18:00:00Z
  after:  Jul 23, 2026, 2:00:00 PM GMT-4     (title="2026-07-23T18:00:00Z")

Detail dialog, "created" row (_task.html:14)
  before: 2026-07-23T18:00:00Z
  after:  Jul 23, 2026, 2:00:00 PM GMT-4     (title="2026-07-23T18:00:00Z")

Detail dialog, history table "time" column (_task.html:23)
  before: 2026-07-23T18:05:12Z
  after:  Jul 23, 2026, 2:05:12 PM GMT-4     (title="2026-07-23T18:05:12Z")
```

ASCII sketch of the detail dialog's affected rows (unchanged structure, only
cell content differs — `[…]` marks a `<time>` element):

```
┌ dialog#detail ──────────────────────────────────────────┐
│ tsk_e22b195369cc4469                        [Restart]   │
│ ┌───────────┬─────────────────────────────────────────┐ │
│ │ repository│ harness_v2                              │ │
│ │ ...       │ ...                                     │ │
│ │ created   │ [Jul 23, 2026, 2:00:00 PM GMT-4]  <- <time>, title=raw UTC │
│ └───────────┴─────────────────────────────────────────┘ │
│ history                                                  │
│ ┌──────────────────────────┬────────┬...                │
│ │ time                     │ actor  │...                │
│ │ [Jul 23, 2026, 2:05:12…] │ dispatcher │...             │
│ └──────────────────────────┴────────┴...                │
└───────────────────────────────────────────────────────────┘
```

Hovering (or, on touch, long-pressing) any timestamp shows the exact UTC
instant in a native tooltip via `title`, satisfying FR-2's "cross-checkable
against server logs" requirement without adding UI chrome.

If `Intl.DateTimeFormat` throws for a given node (malformed `datetime`
attribute), that node's text is left as whatever the server rendered — the
raw UTC string — so the page never shows a blank or broken cell.

### Component hierarchy (template composition, not JS components)

```
board.html
├── #board  (hx-get="/fragment/board", swapped by SSE and on nav)
│   └── _columns.html          → card.since  → <time>            [NEW markup]
└── dialog#detail  (hx-get="/fragment/task/{id}")
    └── _task.html             → created row → <time>            [NEW markup]
                                → history rows → <time> (×N)      [NEW markup]
```

No new client-side framework/component model is introduced — the "component"
here is a plain DOM-scanning function invoked at two call sites already
present in `board.html`.

### Key interactions
- **Page load**: server renders `<time datetime="...">` with the raw UTC
  string as fallback text; a `DOMContentLoaded` listener immediately rewrites
  all such nodes under `document` to local time. No flash is guaranteed
  because the script is synchronous and runs before paint completes on a cold
  load in practice, but even in the worst case the fallback is a valid,
  readable UTC timestamp — not broken markup.
- **Live SSE board refresh**: htmx swaps in a fresh `_columns.html` fragment;
  the existing `htmx:afterSwap` listener (`board.html:35`) is extended to
  also re-run the conversion, scoped to `event.detail.target` so only the
  swapped subtree is touched.
- **Opening task detail**: same `htmx:afterSwap` listener fires for the
  `#detail` swap (it already does — see the `showModal()` call there);
  conversion runs on that subtree too.
- **Restart**: the restart button's response re-swaps `#detail`, going
  through the same `afterSwap` path — no separate wiring needed.

## Component design

### 1. `format-local-time.js` (new file, `src/harness/api/static/`)

A single small script, no build step, no dependency — consistent with
`htmx.min.js`/`sse.js` already being vendored flat files in that directory.

Responsibility: given a DOM subtree, find every `time[datetime]` element and
replace its text content with an `Intl.DateTimeFormat`-formatted rendering of
its `datetime` attribute, in the browser's local timezone. Nothing else —
it does not know about tasks, boards, or htmx; it operates purely on the
`<time>` contract.

```js
(function () {
  function localizeTimes(root) {
    var nodes = (root || document).querySelectorAll('time[datetime]');
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      try {
        var date = new Date(node.getAttribute('datetime'));
        if (isNaN(date.getTime())) { continue; }
        node.textContent = new Intl.DateTimeFormat(undefined, {
          dateStyle: 'medium',
          timeStyle: 'medium',
          timeZoneName: 'short',
        }).format(date);
      } catch (error) {
        // Leave the server-rendered raw-UTC fallback text in place.
      }
    }
  }
  window.localizeTimes = localizeTimes;
})();
```

Interface: one global function, `window.localizeTimes(root?: Element)`.
`root` defaults to `document` so the same function serves both the
whole-page pass and a scoped post-swap pass.

### 2. `board.html` wiring (edit)

- Add `<script src="/static/format-local-time.js"></script>` next to the
  existing `htmx.min.js`/`sse.js` tags (before the inline `<script>` block,
  so `localizeTimes` exists when the inline block runs).
- Call `localizeTimes()` once, unscoped, for the initial server-rendered
  content (covers `#board`'s inline `{% include "_columns.html" %}`).
- Inside the existing `htmx:afterSwap` listener, add
  `localizeTimes(event.detail.target)` as the first statement — reuses the
  handler that already exists for the detail-dialog `showModal()` logic, and
  now also covers the `#board` fragment swap (SSE-driven) and the restart
  response, since all three go through `htmx:afterSwap` on `document.body`.

No new event listeners are registered; the revision-dedup IIFE is untouched
(it operates on the raw SSE payload before htmx swaps anything, so it never
needs to see formatted times).

### 3. Template edits (`_columns.html`, `_task.html`)

Each raw timestamp interpolation becomes a `<time>` element carrying the
untouched UTC string as both `datetime` and (for the tooltip) `title`, with
the raw string repeated as element text so the field is meaningful even with
JS disabled or before the script runs:

```jinja
{# _columns.html:16 #}
{% if task.history %}
<div class="since">
  <time datetime="{{ task.history[-1].at }}" title="{{ task.history[-1].at }} UTC">{{ task.history[-1].at }}</time>
</div>
{% endif %}
```

```jinja
{# _task.html:14 #}
<tr><th>created</th><td>
  <time datetime="{{ task.created }}" title="{{ task.created }} UTC">{{ task.created }}</time>
</td></tr>
```

```jinja
{# _task.html:23 #}
<td><time datetime="{{ entry.at }}" title="{{ entry.at }} UTC">{{ entry.at }}</time></td>
```

No other rows change — `status`, `lastOutcome`, `lockId`, `outcome`, `actor`
etc. are not timestamps.

### Why this boundary (component responsibility)

- **Server (Jinja templates)**: unchanged data, new markup shape only —
  emits the `<time>` element with both a machine-readable (`datetime`) and a
  human raw-UTC fallback (element text + `title`). Stays agnostic of any
  client timezone, consistent with invariant #5 (`api/`/`projection.py`
  don't know what the harness runs on — and by the same logic, don't know
  who's looking at the browser).
- **Client (`format-local-time.js`)**: the only place that knows the
  viewer's timezone, because it's the only place that *can* — `Intl` reads it
  from the OS via the browser. It's a pure DOM-rewrite utility, not tied to
  the board's data model, so it composes with any current or future template
  that emits `<time datetime>`.
- **`board.html`**: wiring only — decides *when* to invoke the utility,
  reusing the single `htmx:afterSwap` chokepoint that already exists for
  every fragment-swap path in the app (board refresh, task detail, restart).
  This is why FR-3 is satisfied for all four refresh paths without adding any
  new event listener.

## Data schemas

No schema changes. For completeness, the shapes that remain untouched:

**`Task.to_dict()` / `/api/board`, `/api/tasks/{id}` (JSON)** — unchanged:
```json
{
  "id": "tsk_...",
  "created": "2026-07-23T18:00:00Z",
  "history": [
    {"at": "2026-07-23T18:05:12Z", "actor": "dispatcher", "from_step": "start", "to_step": "design", "outcome": null, "reason": null}
  ],
  "...": "..."
}
```

**HTML fragment output (`_columns.html`, `_task.html`)** — the only
"schema" that changes, and only in shape of markup, not in the underlying
value:
```html
<!-- was -->
<div class="since">2026-07-23T18:00:00Z</div>

<!-- becomes -->
<div class="since">
  <time datetime="2026-07-23T18:00:00Z" title="2026-07-23T18:00:00Z UTC">2026-07-23T18:00:00Z</time>
</div>
```
The `datetime` attribute value is always exactly `Task.created` /
`HistoryEntry.at` as emitted today (`%Y-%m-%dT%H:%M:%SZ`), so any external
consumer scraping the JSON API is unaffected, and even an HTML scraper that
previously read `.since` text still finds the same raw string as the
element's `title` and (until the script runs) initial text content.

No event payload changes — this feature touches no `EventSink`/projection
path; the SSE `sse:board` event already carries only `{revision}` (per
`board.html`'s existing revision-dedup code) and is not affected.
