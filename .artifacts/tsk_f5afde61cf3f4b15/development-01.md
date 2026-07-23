# Development: Copy button for task IDs in the board UI

Implements `plan-01.md` / `design-01.md`, applying `architecture-01.md`'s
Decision 2 simplification (single closure variable instead of a `WeakMap`).
No plan/design/architecture deviations otherwise.

## Files changed

1. **`src/harness/api/templates/_task.html`**
   Replaced the bare `<h2>{{ task.id }}</h2>` with:
   ```html
   <h2 class="task-id-row">
     <span>{{ task.id }}</span>
     <button type="button" class="copy-id-btn" data-copy-id="{{ task.id }}"
             aria-label="Copy task id">📋</button>
   </h2>
   ```
   Nothing else in the fragment changed (no route/schema touched — same
   `/fragment/task/{id}` response shape).

2. **`src/harness/api/templates/board.html`**
   - `<style>`: appended `.task-id-row` (flex row) and `.copy-id-btn`
     base/`:hover`/`:active`/`.copied` rules, plus a
     `@media (prefers-color-scheme: dark)` block scoped only to
     `.copy-id-btn`, reusing the board's existing palette
     (`#ebecf0`/`#e3fcef`).
   - `<script>`: appended a new IIFE after the existing revision-dedup
     block. It delegates a `click` listener on the static `#detail` dialog
     (matching the file's existing convention for `afterSwap`/`close`
     listeners), resolves `.copy-id-btn` via `closest()`, calls
     `navigator.clipboard.writeText(btn.dataset.copyId)`, flips the button to
     `✓`/`.copied` on success with a 1.5s revert via a single closure-scoped
     `revertTimer` (cleared/reset on rapid re-click), and silently swallows
     rejection (`.catch(() => {})`) per the plan's non-functional
     requirement.

3. **`tests/test_api_html.py`**
   Added `test_fragment_task_shows_copy_id_button`, a sibling of
   `test_fragment_task_shows_metadata_and_history`, asserting the
   `/fragment/task/tsk_1` response contains `class="copy-id-btn"` and
   `data-copy-id="tsk_1"`.

## Verification

- `.venv/bin/pytest -q` → **473 passed, 1 skipped** (the skip is the
  pre-existing opt-in `HARMESS_SMOKE_CLAUDE` test, unrelated to this change).
  No test file needed to be built from scratch beyond the venv itself, which
  did not previously exist in this worktree and was created with
  `python3.11 -m venv .venv && pip install -e ".[dev]"` before running the
  suite.
- No `api/`→`drivers/` import was added (invariant #5); `test_architecture.py`
  passes unchanged.
- Manual/browser verification of the click → clipboard → ✓ → revert flow and
  the dark-mode styling is out of scope for automated coverage per
  architecture-01.md (no JS/DOM test harness exists in this repo) — reviewer
  or a human should confirm visually if desired, but the HTML-level test
  covers the only server-rendered contract (button + `data-copy-id` present).

## Deviations from architecture-01.md

None. Implemented exactly as specified, including the single-closure-variable
simplification of design-01's `WeakMap`.
