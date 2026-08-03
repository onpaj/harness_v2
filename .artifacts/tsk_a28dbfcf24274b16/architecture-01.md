# Architecture assessment: board UI doesn't use full width on wide desktop screens

Reviewed against `plan-01.md`, `design-01.md`, the current `src/harness/api/static/app.css`,
and this project's invariants (`CLAUDE.md`).

## Verdict

**Approved as designed, no changes requested.** This is a pure CSS-only change confined
to `src/harness/api/static/app.css`. It touches none of the orchestration surfaces
(`dispatcher`/`consumer`/`router`/ports/drivers) the numbered invariants in `CLAUDE.md`
govern, and none of the module-map layers beyond `api/` — and even within `api/`, it
only touches the static stylesheet, not `routes.py`, `app.py`, or any port
(`BoardView`/`ArtifactView`/`TaskControl`/the admin ports). Invariants #5 and #33
("neither `api/` nor `projection.py` imports `drivers/`", "`api/` touches only
`ArtifactView`/admin ports") are about Python import boundaries; a stylesheet edit
cannot violate them. There is no invariant in this codebase about CSS layout, so the
only bar this change needs to clear is "don't regress an existing test or an existing
visual contract" — which the design pass already verified concretely (see below).

## Alignment with existing patterns and integration points

- **File boundary matches the codebase's own layering.** `app.css` is the one shared
  stylesheet for every page (`board.html`, admin pages, dialogs) — confirmed by
  `grep` showing every `.page`-using template pulls the same
  `<link rel="stylesheet" href="/static/app.css">`. A layout fix at this shared
  boundary is the correct place; there is no per-page stylesheet to fragment it into,
  and inventing one would be scope creep the task doesn't call for.
- **No markup change is required or proposed.** `board.html`/`_columns.html`'s
  structure (`.page` → `.board` → `.column` → `.card`) stays untouched — verified by
  reading the plan/design, which both explicitly confirm this. Good: a CSS-only bug
  (a hard-coded cap) should get a CSS-only fix; reaching into templates would be an
  unjustified surface-area increase.
- **Placement respects the existing cascade contract.** I read `app.css:145-146`
  (current `.page { max-width: 1200px; ... }`) and the desktop block at
  `@media (min-width: 768px) { ... .column { flex: 1 1 0; min-width: 260px;
  max-width: 360px; ... } ... }` (lines ~367-394) directly — the design doc's line
  numbers and rule content match the file on disk exactly. The proposed two new
  `@media (min-width: 1400px)` / `@media (min-width: 1800px)` blocks, appended
  *after* the existing 768px block, only override `max-width` on `.page`/`.column`
  and touch no other property (`flex`, `min-width`, `flex-direction`, spacing,
  color tokens) — so there is no specificity fight, only "which media query
  matches," and later-declared blocks winning at wider viewports is standard,
  unsurprising CSS cascade behavior, not a fragile trick.
- **Existing test contract verified, not just assumed.** I checked
  `tests/test_api_html_mobile.py::test_stylesheet_switches_board_layout_at_768px` —
  it asserts `"flex-direction: column" in css`, `"@media (min-width: 768px)" in css`,
  and `"flex-direction: row" in css` as independent substring checks against the
  whole served CSS text, not scoped to a particular block boundary. I also grepped
  both `test_api_html.py` and `test_api_html_mobile.py` for `max-width`/`1200px`/
  `280px`/`220px`/`.column`/`.page` and found zero hits — no test pins the current
  cap or column width numerically. The plan/design's claim that this is safe for
  the test suite is independently confirmed, not just asserted.

## Proposed architecture (unchanged from design-01.md — endorsed)

Two additive breakpoints, values as specified:

```css
@media (min-width: 1400px) {
  .page { max-width: 1600px; }
  .column { max-width: 420px; }
}

@media (min-width: 1800px) {
  .page { max-width: 2200px; }
  .column { max-width: 500px; }
}
```

Placed immediately after the existing `@media (min-width: 768px)` block (i.e. after
the current line ~394, before `@media (prefers-reduced-motion: reduce)`).

**Why this shape and not an alternative:**
- *Fluid `max-width: min(2000px, 100%)` instead of stepped breakpoints* — considered
  implicitly by the plan's FR-1 wording ("either drop the cap ... or raise it
  substantially") and rejected in the design in favor of discrete steps. The stepped
  approach is the better fit here specifically because `.column` also needs to grow
  in lockstep with `.page` (FR-2) — a single fluid `.page` rule would still leave
  `.column` capped at 360px, so columns wouldn't visibly widen even as the shell
  grows. Tying both to the same breakpoint keys keeps them visibly synchronized and
  keeps the diff readable as "at width X, page and column both step up," which is
  easy to verify by eye against the acceptance criteria's named widths
  (1400/1800/1920/2560). This also directly satisfies FR-3's explicit requirement
  for "at least one breakpoint above 768px" with margin — two, not one.
- *Editing the existing 768px block in place instead of appending new blocks* —
  rejected correctly. Appending is what makes the change a pure addition: the
  768px block's exact text is preserved byte-for-byte, so the substring-based test
  assertions keep matching regardless of what's appended after them, and a reviewer
  can diff this change as "N new lines" with zero risk of a fat-fingered edit to
  the existing row-layout rule.
- *Monotonic growth (1200→1600→2200 for `.page`, 360→420→500 for `.column`)* — the
  design explicitly checked that no tier produces a smaller cap than a narrower
  tier, avoiding a shrink-on-resize discontinuity. Correct and worth keeping as a
  standing constraint if these numbers are ever revisited.

## Implementation guidance

- **Where the new code belongs:** `src/harness/api/static/app.css` only, the two
  blocks verbatim as shown above, inserted directly after the closing brace of the
  existing `@media (min-width: 768px) { ... }` block and before the
  `@media (prefers-reduced-motion: reduce)` block that currently follows it. No
  other file needs a change.
- **Contract to preserve:** do not modify any existing selector's rule body inside
  the 768px block. The new blocks may only add `max-width` overrides for `.page`
  and `.column` — introducing any other property, or touching `flex-direction`,
  `min-width`, or `flex`, would be outside this design's verified-safe surface and
  should trigger a second look at the test suite before landing.
- **Data flow:** none — this is static CSS served as-is (`app.css` has no build
  step per the module map notes), so "implementation" is the literal edit, and
  "deployment" is just serving the changed file.
- **Verification the development step must actually run** (not skip):
  1. `.venv/bin/pytest -q`, in particular `tests/test_api_html_mobile.py` and
     `tests/test_api_html.py`, to confirm the substring assertions still hold.
  2. A real visual check (via the `run`/browser tooling this repo already has) of
     the board — plus Agents/Workflows/Processes and both dialogs — at 375, 768,
     1200, 1400, 1800, 1920 and 2560px. This is a visual bug with no pixel-level
     automated test; the acceptance criteria are inherently eyeball criteria
     (gutters, wrap count, no `body` horizontal scrollbar), so this check is not
     optional busywork — it's the only verification that actually covers FR-1/FR-2/FR-4.

## Risks and mitigations

- **Risk: a workflow with very few columns (2-3) could make columns look
  disproportionately wide at the 500px cap on a 2560px screen.** Mitigation: this
  is already bounded by `flex: 1 1 0` plus the `max-width` cap — columns fill
  available space up to 500px each, they don't stretch unbounded, so the worst
  case is "columns are exactly 500px wide with more gutter than a 4+ column board,"
  not an overflow or an absurd single-column stretch. Acceptable; matches the
  design's own caveat under the wide-viewport ASCII sketch.
- **Risk: the design's claimed line numbers drift if another change lands in
  `app.css` between this artifact and implementation.** Mitigation: low —
  I independently re-read the live file and confirmed the current line numbers and
  rule bodies match the design doc's citations exactly as of this review. The
  development step should still anchor its edit on the block boundary (end of the
  768px block, start of `prefers-reduced-motion`), not on hardcoded line numbers.
- **Risk: none identified around the invariants file** — this change has no
  interaction with any of the 43 numbered invariants; they govern task/queue/agent
  orchestration, not presentation layout.

## Prerequisites before implementation begins

None. The design is fully specified (exact rule text, exact placement, exact
values), the test surface has been independently confirmed clear, and no other
file or port needs to change first. Implementation can proceed directly from
`design-01.md`'s rule block.
