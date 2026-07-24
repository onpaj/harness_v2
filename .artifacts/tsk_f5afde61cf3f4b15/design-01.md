# Design: Copy button for task IDs in the board UI

Follows `plan-01.md`. This is a template/style/script-only change to
`src/harness/api/templates/_task.html` and `board.html`; no port, driver, or
route is touched.

## UX/UI

### Wireframe — task detail panel header, before/after

Before:
```
┌───────────────────────────────────────────┐
│ tsk_f5afde61cf3f4b15                       │  <h2>
│ [Restart]                (only if failed)  │
│ ┌─────────────┬─────────────────────────┐ │
│ │ repository  │ app-backend             │ │
│ │ worktree    │ …                       │ │
```

After:
```
┌───────────────────────────────────────────┐
│ tsk_f5afde61cf3f4b16 [📋]                  │  <h2> + button, same line
│ [Restart]                (only if failed)  │
│ ┌─────────────┬─────────────────────────┐ │
│ │ repository  │ app-backend             │ │
```

Button states (same position, content/color swap only — no layout shift):

```
resting     hover        active        confirmed (1.5s)
 [📋]        [📋]          [📋]           [✓]
 gray bg    darker bg    darkest bg    green-tinted bg
```

### Component hierarchy

```
board.html
└── <dialog id="detail">            (persists across opens; innerHTML swapped)
      └── _task.html fragment       (swapped in via hx-swap="innerHTML")
            └── <h2 class="task-id-row">
                  ├── task id text node
                  └── <button class="copy-id-btn" data-copy-id="{id}">
```

The button is *inside* the swapped fragment, so it is created and destroyed
on every open/close. Its click handler is therefore **not** attached to the
button itself (would be a no-op after the first swap) but delegated from
`#detail`, which is a static node created once at page load and never
replaced (only its `innerHTML` is cleared/repopulated).

### Key interaction

1. User opens a task card → htmx swaps `_task.html` into `#detail` →
   `htmx:afterSwap` calls `showModal()` (existing code, unchanged).
2. User clicks the 📋 button.
3. Delegated handler on `#detail` catches the click, resolves the button via
   `event.target.closest('.copy-id-btn')`, reads `data-copy-id`, calls
   `navigator.clipboard.writeText(id)`.
4. On promise resolve: button's `textContent` → `✓`, `classList.add('copied')`.
   A `setTimeout(1500)` reverts `textContent` → `📋`,
   `classList.remove('copied')`. Any previous pending timeout *for that same
   button element* is cleared first (`clearTimeout`), so rapid re-clicks
   reset the 1.5s window instead of stacking reverts.
5. On promise reject (insecure context, permission denied): no state change,
   no thrown error surfaces past the `.catch(() => {})`.
6. Dialog close (existing `close` handler) clears `#detail.innerHTML`,
   which discards the button node; any pending timeout referencing it is
   harmless (it still fires but touches a detached node — see Gotchas).

No new dialogs, routes, or navigation. Purely additive to the existing
detail panel.

## Component design

Three touch points, each a narrow, additive change to an existing file. No
new module, no new abstraction — this is markup + ~15 lines of vanilla JS +
a style block, consistent with `board.html`'s existing single-file inline
script pattern.

### 1. `_task.html` — markup

Replace the bare heading with a heading wrapped alongside the button, so
flex layout can align them on one line without disturbing the table below:

```html
<h2 class="task-id-row">
  <span>{{ task.id }}</span>
  <button type="button" class="copy-id-btn" data-copy-id="{{ task.id }}"
          aria-label="Copy task id">📋</button>
</h2>
```

- `type="button"` — this fragment sits outside any `<form>` today, but
  explicit typing is cheap insurance against a future form wrapping it.
- `data-copy-id` carries the *exact* id string the JS must copy — reading
  it from an attribute (not by parsing `textContent`) avoids any coupling
  to whitespace/markup inside the `<h2>`.
- `aria-label` — the button's only visible content is an emoji glyph; the
  label gives it an accessible name (`📋`/`✓` swap wouldn't otherwise
  announce sensibly to a screen reader). Not in the acceptance criteria but
  free to add correctly the first time.
- No `hx-*` attributes — this button never talks to the server (non-
  functional requirement: 100% client-side).

### 2. `board.html` — behavior (extends the existing inline `<script>`)

Added as a new block inside the existing `<script>`, alongside the other
`#detail` listeners already there (`htmx:afterSwap`, the backdrop-click
`close`, the `close` cleanup handler). One delegated listener, no new
top-level script tag:

```js
(function () {
  var detail = document.getElementById('detail');
  var revertTimers = new WeakMap();  // button element -> timeout id

  detail.addEventListener('click', function (event) {
    var btn = event.target.closest('.copy-id-btn');
    if (!btn) { return; }
    var id = btn.dataset.copyId;
    navigator.clipboard.writeText(id).then(function () {
      var pending = revertTimers.get(btn);
      if (pending) { clearTimeout(pending); }
      btn.textContent = '✓';
      btn.classList.add('copied');
      var timer = setTimeout(function () {
        btn.textContent = '📋';
        btn.classList.remove('copied');
        revertTimers.delete(btn);
      }, 1500);
      revertTimers.set(btn, timer);
    }).catch(function () {
      // Clipboard write failed (insecure context, permission denied, …).
      // No confirmation shown; no error surfaced — see plan's NFR.
    });
  });
})();
```

Design choices:
- **Listener target is `#detail`**, not `document`. `#detail` is the
  smallest static ancestor that survives every swap (matches the existing
  `close`/backdrop-click listeners' pattern of binding directly to
  `#detail` rather than `document`), so this stays consistent with the
  file's established convention instead of introducing a new one.
- **`WeakMap` keyed by button element** for the pending-revert timer, not a
  single module-level `var currentTimer`. A single shared timer variable
  would be correct for "one button visible at a time" (true today — only
  one task detail is open at once) but a `WeakMap` costs nothing extra,
  needs no manual cleanup (entries drop when the button node is GC'd after
  the fragment is discarded), and removes the implicit one-button-at-a-time
  assumption from the code. Ordinary `Map` would leak entries for buttons
  whose revert never fires (e.g. dialog closed mid-timer); `WeakMap` doesn't
  need that guard.
- **IIFE wrapper** to scope `revertTimers` without adding a new global,
  matching the existing revision-dedup block's own IIFE below it.

### 3. `board.html` — styling (extends the existing `<style>` block)

```css
.task-id-row { display: flex; align-items: center; gap: 8px; }
.copy-id-btn {
  font: inherit;
  border: none;
  border-radius: 4px;
  padding: 2px 6px;
  background: #ebecf0;
  cursor: pointer;
  line-height: 1.4;
}
.copy-id-btn:hover { background: #dfe1e6; }
.copy-id-btn:active { background: #c1c7d0; }
.copy-id-btn.copied { background: #e3fcef; }

@media (prefers-color-scheme: dark) {
  .copy-id-btn { background: #3b3f45; color: #c1c7d0; }
  .copy-id-btn:hover { background: #4a4f57; }
  .copy-id-btn:active { background: #5a5f68; }
  .copy-id-btn.copied { background: #1c3829; }
}
```

- Color choices reuse the board's existing Trello-like palette already in
  the file (`#ebecf0` column background, `#e3fcef`/`#006644` from
  `.badge.done`) so the button reads as belonging to the same system rather
  than introducing a new hue.
- The dark-mode block is scoped to `.copy-id-btn` only, per the plan's
  explicit out-of-scope call: the rest of the board has no dark-mode
  handling today and this task doesn't add it.
- No layout shift on state change: `📋`/`✓` are both single-glyph text
  content inside a fixed-padding button, so the confirmation flip doesn't
  reflow the `<h2>` line.

## Data schemas

None. No request/response shape changes (`/fragment/task/{id}` is
unmodified — same route, same Jinja context, same status codes), no event
payload changes, no DB/model changes. The task id copied is the same
`task.id` string already rendered in the existing `<h2>`.

## Gotchas carried into implementation

- **Detached-node timer fire is harmless.** If the dialog is closed while
  the 1.5s revert timer is pending, `#detail.innerHTML = ''` (existing
  `close` handler) discards the button node; the timer still fires 1.5s
  later but mutates a detached element — no visible effect, no error, and
  the `WeakMap` entry is naturally unreachable afterward.
- **Only one task id per open dialog** — the delegated selector matches any
  `.copy-id-btn` under `#detail`, so the design already generalizes if a
  future change adds more copy buttons inside the same fragment (e.g.
  repository path), though that stays explicitly out of scope for this task.
