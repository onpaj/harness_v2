# Plan — Artifacts tab in task detail dialog

## Summary

`templates/_task.html` renders the task detail dialog with three tabs (Info,
History, Output). The artifacts table currently sits at the bottom of the Info
panel, below a `<pre>` dump of `task.data`, which for a real task pushes it
below the fold. This is a template/CSS-only change: add a fourth "Artifacts"
tab using the exact same `data-tab`/`data-panel` switching mechanism already
driving the other three, move the existing artifacts markup into it verbatim,
and leave the Info panel's `data` `<pre>` untouched.

## Context

- `artifacts` is already passed into the `_task.html` render context as a
  plain list of `ArtifactRef`-shaped dicts by `routes.py`'s task-fragment
  handler (`routes.py:537`, `context={"task": found, "artifacts":
  artifacts.list(task_id)}`) — confirmed by reading the handler. No signature
  or route change is implied by this task.
- The tab switching itself is driven by existing JS keyed on `data-tab` /
  `data-panel` attributes (not investigated line-by-line here since the task
  says explicitly to reuse it unchanged — the History/Output tabs already
  prove the mechanism generalizes past two tabs).
- `.tabs`, `.tab-panel`, `.tab-panels`, `.table-scroll` are all existing CSS
  classes (`app.css`) already used by the History/Output tabs — the Artifacts
  panel needs no new styling to satisfy the scrollable-table requirement.
- There is no existing `.tab__count` class anywhere in the codebase (the
  board's workflow tabs use `.column__count` for the per-column task count,
  not a tab-level count). The nice-to-have "Artifacts 4" badge is new CSS,
  modeled on `.column__count`'s look (small pill, `--text-3`/`--surface-2`),
  not a rename of something that already exists.

## Functional requirements

**FR-1 — Fourth tab in the tab strip.**
Add `<button class="tab" data-tab="artifacts">Artifacts</button>` to the
`<nav class="tabs" role="tablist">` strip in `_task.html`, after Output.
Acceptance: clicking it activates a panel with `data-panel="artifacts"` and
deactivates the previously active tab/panel, using the same script already
wired to Info/History/Output (verified by exercising the existing pattern —
each tab button's `data-tab` value matches a panel's `data-panel` value 1:1,
and the JS presumably does a plain attribute-match toggle with no
tab-specific logic. Do not add a new script path.).

**FR-2 — Artifacts panel holds the table.**
Add `<section class="tab-panel" data-panel="artifacts">` inside
`.tab-panels`, containing exactly the artifacts block currently at the tail
of the `info` panel (the `{% if artifacts %} ... table-scroll ... {% else %}
No artifacts yet. {% endif %}` block), moved verbatim — same table headers
(step/attempt/name), same link target
(`/api/tasks/{{ task.id }}/artifacts/{{ ref.step }}/{{ ref.attempt }}/{{ ref.name }}`),
same `.table-scroll` wrapper.
Acceptance: the artifacts table and its "No artifacts yet." fallback render
identically to today, just under the new panel instead of under Info.

**FR-3 — Info panel loses the artifacts block.**
Remove the `<h3>artifacts</h3>` heading and the artifacts
table/empty-state block from the `info` panel entirely (not duplicated, not
left as dead markup). The Info panel's `<ul class="kv">` (ending with the
`data` row) is otherwise unchanged — reducing what's below the fold in Info is
explicitly out of scope beyond this removal.
Acceptance: `grep -c 'artifacts' _task.html` shows the artifacts block appears
once (in the new panel), the string `<h3>artifacts</h3>` appears only inside
the `data-panel="artifacts"` section.

**FR-4 — Empty state uses the existing hint.**
No behavior change needed here beyond the move: the `{% else %}<p
class="hint">No artifacts yet.</p>{% endif %}` branch already exists and
moves along with the rest of the block (FR-2). Acceptance criterion is
satisfied by FR-2's verbatim move, not separate work.

**FR-5 (nice-to-have) — Artifact count on the tab.**
Add a small badge to the Artifacts tab button showing the count, e.g.:
```html
<button class="tab" data-tab="artifacts">
  Artifacts{% if artifacts %} <span class="tab__count">{{ artifacts|length }}</span>{% endif %}
</button>
```
New CSS class `.tab__count` in `app.css`, visually modeled on
`.column__count` (small pill, muted text/background) but scoped to `.tabs
.tab` sizing so it doesn't blow out the tab strip's `min-height: 44px` /
`padding: 8px 14px` sizing. Omit the badge (or render nothing) when
`artifacts` is empty — an empty pill reading "0" is more clutter than signal
for a hint that already reads "No artifacts yet." Acceptance: with N
artifacts the tab reads "Artifacts N"; with zero it reads "Artifacts" (no
stray badge).

## Non-functional requirements

- No new JS, no new route, no new port method — purely template + CSS.
- Must not regress mobile layout: `.tabs .tab { flex: 1 0 auto; }` already
  wraps to 4 items the same way it already handles 3; verify by eyeballing
  at a narrow width if a dev server is run, but no explicit media-query
  change is anticipated since `.tabs` already scrolls horizontally
  (`overflow-x: auto`) when tabs don't fit.

## Data model

No change. `ArtifactRef` (`step`, `attempt`, `name`) is already the shape
iterated in the table; nothing here touches `ArtifactView`, `ArtifactStore`,
or the artifact URL scheme, satisfying the "no port change" acceptance
criterion directly.

## Interfaces

- No endpoint changes. `GET /tasks/{id}` (the fragment route rendering
  `_task.html`) already resolves and passes `artifacts` — untouched.
- UI flow: task detail dialog → click "Artifacts" tab → see the table (or the
  empty hint) → click an artifact name → navigates to
  `/api/tasks/{id}/artifacts/{step}/{attempt}/{name}` exactly as before.

## Dependencies and scope

- Depends on: existing tab-switching script (unnamed/unlocated in this plan
  since it's explicitly out of scope to alter — the design step should
  confirm its exact selector logic before implementation touches markup, to
  be sure a 4th `data-tab`/`data-panel` pair "just works").
- In scope: `_task.html` tab strip + panel markup, `app.css` if the
  nice-to-have badge is implemented.
- Out of scope (per task notes): trimming/collapsing the `data` `<pre>` in
  Info; inline artifact preview; any change to artifact storage/layout in the
  worktree; `ArtifactView`/routes/URL scheme.
- Invariants to preserve: #5/#33 (`api/` imports no driver) and #11 (`api/`
  touches only `ArtifactView` for artifacts) — both already hold today and
  this change touches no Python import, only Jinja/CSS, so they hold
  trivially; the development step should still confirm no accidental import
  crept in (e.g. don't add a driver-touching helper to satisfy the count
  badge — `artifacts|length` is a pure Jinja filter over the list already in
  context).

## Rough plan

1. In `_task.html`, add the fourth tab button (FR-1) and, if doing the
   nice-to-have, the count badge (FR-5).
2. Add the new `<section class="tab-panel" data-panel="artifacts">` and move
   the artifacts table/empty-state block into it verbatim (FR-2).
3. Delete the `<h3>artifacts</h3>` block from the `info` panel (FR-3).
4. If implementing FR-5, add `.tab__count` to `app.css` near `.tabs`/`.tab`
   rules, sized to fit inside the existing tab button without changing
   `.tabs` height.
5. Run the app locally (or via the project's `run`/dev-server pattern) and
   open a task's detail dialog to confirm: tab switches correctly, artifacts
   render under the new tab, Info no longer shows them, a task with zero
   artifacts shows "No artifacts yet." under Artifacts (not an empty table),
   and a task with many attempts scrolls within `.table-scroll` instead of
   growing the dialog.
6. Run the existing test suite (`.venv/bin/pytest -q`) — no test changes are
   anticipated since no route/port signature changes, but confirm nothing
   asserts on the artifacts block's prior location inside the Info panel's
   HTML (grep tests for `<h3>artifacts</h3>` or similar before assuming this).

## Open questions

- Exact tab-switching JS was not located/read in this step (out of scope for
  planning, but design/development should verify it before relying on
  "the existing script — no new switching mechanism" holding for a 4th tab
  with zero changes needed).
- Whether any existing test asserts on the artifacts table living inside the
  `info` panel specifically (vs. just being present in the rendered HTML
  fragment) — if such a test exists it needs updating to point at the new
  panel; assumed absent by default pending a grep in the design/dev step.
- The FR-5 badge is optional per the acceptance criteria ("nice-to-have, not
  required"); default is to implement it since it's cheap and directly
  serves the stated goal ("see at a glance whether there is anything to
  open").
