# Architecture: Copy button for task IDs in the board UI

Reviewed against `plan-01.md`, `design-01.md`, and the current state of
`src/harness/api/templates/_task.html`, `board.html`, and `tests/test_api_html.py`.
Verdict: **the design is architecturally sound and matches the live code
exactly** (line numbers, class names and the absence of any dark-mode/JS-test
precedent all check out against the actual files, not stale assumptions). One
simplification is recommended before implementation (see Decision 2); no other
changes to the plan/design are needed.

## Alignment with existing patterns and integration points

- **Layer:** this change lives entirely in `api/templates/` — markup, one
  `<style>` block, one inline `<script>` block. It does not touch
  `api/routes.py`, `projection.py`, `ports/board.py`, or any driver. Invariant
  #5 (`api/` never imports `drivers/`) and invariant #11 (`ArtifactStore`/
  `Forge`/`Workspace` unknown to `api/`) are trivially preserved because
  nothing here talks to Python at all beyond the template already rendering
  `task.id`. `tests/test_architecture.py` has nothing new to guard.
- **Fragment/dialog lifecycle:** confirmed against `board.html:33-46` — `#detail`
  is a single static `<dialog>` created once; its `innerHTML` is replaced on
  every `htmx:afterSwap` and cleared on `close`. The design's choice to
  delegate the click listener from `#detail` (not `document`, not the button)
  is not just correct but is the *only* pattern already in use in this file —
  the existing backdrop-click-to-close and `afterSwap`/`close` listeners are
  all bound to `#detail` directly. Following that exactly is the right call;
  binding to `document` would work too but would be an unjustified deviation
  from the file's one established convention.
- **Styling:** the proposed palette (`#ebecf0`, `#e3fcef`) is lifted directly
  from `board.html`'s existing `.column`/`.badge.done` rules — correct reuse,
  no new hue introduced.
- **Testing:** `tests/test_api_html.py:152` (`test_fragment_task_shows_metadata_and_history`)
  is the right and only precedent to extend — it drives `/fragment/task/{id}`
  through FastAPI's `TestClient` and asserts on the rendered HTML string.
  Confirmed there is no JS/DOM/Playwright test harness anywhere in this repo
  (`pyproject.toml`, `tests/`) — the plan's assumption is correct, not a
  guess. Click/clipboard/timer behavior stays a manual verification step;
  don't introduce a new test toolchain for one button.
- **Dark mode:** confirmed `board.html` has zero `prefers-color-scheme`
  handling today. Scoping the new dark rule to `.copy-id-btn` only (not a
  board-wide dark theme) is correctly out of scope per the plan and is the
  right boundary — a full dark mode is a separate, much larger effort with
  its own design questions (does it follow OS preference or a toggle? does it
  cover the SSE log panel's hardcoded `#091e42`?).

## Proposed architecture

No new components. Three touch points, all inside existing files:

1. `_task.html` — `<h2>{{ task.id }}</h2>` becomes
   `<h2 class="task-id-row"><span>{{ task.id }}</span><button ... data-copy-id="{{ task.id }}">📋</button></h2>`.
2. `board.html` `<style>` — `.task-id-row` (flex row) + `.copy-id-btn` base/
   `:hover`/`:active`/`.copied` + one `@media (prefers-color-scheme: dark)`
   block scoped to `.copy-id-btn`.
3. `board.html` `<script>` — one delegated `click` listener on `#detail`,
   added as a new IIFE alongside the existing revision-dedup IIFE.

### Decision 1 — delegated listener on `#detail`, data on the button via `data-copy-id`

Options considered: (a) attach listener to the button at render time — rejected,
the button is destroyed/recreated on every swap, listener would be a no-op
after the first close; (b) delegate from `document` — works, but is a new
binding target not used anywhere else in the file; (c) delegate from
`#detail` — matches the file's only existing convention. **Chosen: (c).**
Reading the id from `data-copy-id` rather than `textContent` of the `<span>`
is correct — it decouples the copied string from whatever markup/whitespace
ends up inside the heading.

### Decision 2 — single shared timer, not a `WeakMap` (simplification of design-01)

`design-01.md` proposes a `WeakMap<button, timeoutId>` to track the pending
revert-to-📋 timer. Worth checking against the actual invariant: **at most
one `.copy-id-btn` element can ever exist in the DOM at a time.** `#detail`
holds exactly one fragment (one task's detail) at once; there is no list
view, no multi-select, no second dialog. A collection keyed by "which button"
is solving a problem that cannot occur here.

Recommendation: replace the `WeakMap` with a single closure-scoped variable,
e.g.:

```js
(function () {
  var detail = document.getElementById('detail');
  var revertTimer = null;

  detail.addEventListener('click', function (event) {
    var btn = event.target.closest('.copy-id-btn');
    if (!btn) { return; }
    navigator.clipboard.writeText(btn.dataset.copyId).then(function () {
      if (revertTimer) { clearTimeout(revertTimer); }
      btn.textContent = '✓';
      btn.classList.add('copied');
      revertTimer = setTimeout(function () {
        btn.textContent = '📋';
        btn.classList.remove('copied');
        revertTimer = null;
      }, 1500);
    }).catch(function () {});
  });
})();
```

This is strictly fewer moving parts for identical observable behavior
(FR-2/FR-3 acceptance criteria are unaffected — a rapid second click still
clears and resets the pending revert), and matches this project's stated
convention of not carrying generality nothing here needs. If a future task
adds a second simultaneous copy target inside the same fragment (e.g. a
repository-path copy button next to the id button), *that* task is exactly
when a per-button map earns its keep — not before. Everything else in
design-01's script (the `.catch(() => {})` swallow, reading `dataset.copyId`,
`classList.add('copied')`) is unchanged and correct.

### Decision 3 — `aria-label`, `type="button"`

Both are zero-cost correctness the design already got right: `type="button"`
prevents accidental form submission if this fragment is ever wrapped in a
`<form>` later (it isn't today), and `aria-label="Copy task id"` gives the
emoji-only button an accessible name. Keep both as specified in design-01.

## Implementation guidance

- **`_task.html`** (`src/harness/api/templates/_task.html:1`): replace line 1
  exactly as shown in design-01's markup section. Nothing else in this file
  changes — the `<table>` below stays untouched (per plan-01's Open Questions,
  no redundant "id" row is added).
- **`board.html`** (`src/harness/api/templates/board.html`):
  - `<style>` block (currently lines 8-26): append the `.task-id-row` /
    `.copy-id-btn*` rules and the `@media (prefers-color-scheme: dark)` block
    from design-01, verbatim.
  - `<script>` block (currently lines 34-73): append the click-delegation
    IIFE using the simplified single-timer version from Decision 2, placed
    after the existing `close` cleanup listener and before or after the
    revision-dedup IIFE — order between the two IIFEs doesn't matter, they
    share no state.
- **`tests/test_api_html.py`**: extend `test_fragment_task_shows_metadata_and_history`
  (or add a sibling test next to it, following the file's existing style of
  one assertion-dense test per fragment concern) to assert the response body
  for `/fragment/task/tsk_1` contains `class="copy-id-btn"` and
  `data-copy-id="tsk_1"`. No new fixtures needed — `client` and `WORKING`
  already exist in the file.
- **Manual verification** (no automated coverage possible without a new JS
  toolchain, which is out of scope): open a task card, click 📋, confirm the
  clipboard contents and the ✓→📋 flip after 1.5s, once in normal color
  scheme and once with the OS/browser forced into
  `prefers-color-scheme: dark`.

## Risks and mitigations

- **Clipboard API requires a secure context** (HTTPS or `localhost`). The
  harness board is typically served on `localhost` during dev/ops use, so
  this is low risk in practice; the design's `.catch(() => {})` already
  degrades silently rather than throwing, satisfying the plan's NFR without
  needing a fallback `document.execCommand('copy')` path — not worth the
  added surface for an internal ops tool.
- **Detached-node timer fire** (dialog closed mid-revert): confirmed harmless
  — mutating a detached `<button>` after `#detail.innerHTML = ''` has no
  visible effect and no error. No mitigation needed, already correctly
  reasoned in design-01.
- **Emoji rendering across platforms**: 📋/✓ are common enough (already same
  risk class as other emoji-free-icon UIs) that no icon font/SVG dependency
  is justified for this scope.

## Prerequisites before implementation begins

None. No schema, port, or route changes; no new dependency; no environment
setup beyond the existing dev loop (`.venv/bin/pytest -q`). Implementation can
start directly from this document plus design-01.md, applying Decision 2's
simplification in place of the `WeakMap` version.
