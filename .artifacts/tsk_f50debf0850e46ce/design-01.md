# Design — Artifacts tab in task detail dialog

This is a template/CSS-only change to `src/harness/api/templates/_task.html`
(and `src/harness/api/static/app.css` for the optional count badge). No
backend, port, or route change. There is no new interaction pattern to design
from scratch — the task detail dialog already has a working 3-tab UI (Info /
History / Output); this adds a 4th tab reusing the identical mechanism.

## Confirmed mechanics (read from source, not assumed)

- Tab switching is one delegated click listener in `board.html`
  (`document.getElementById('detail').addEventListener('click', ...)`,
  `board.html:118-128`). It reads `event.target.closest('.tabs .tab')`, toggles
  `.active` on the clicked tab among its siblings, then toggles `.active` on
  whichever `.tab-panel` inside the same `.task-detail` has a matching
  `data-panel`. It is generic over the *number* of tabs — nothing hardcodes
  "3" or names Info/History/Output — so a 4th `data-tab`/`data-panel` pair
  needs zero script changes.
- CSS: `.tabs` (flex row, horizontal scroll if it overflows), `.tabs .tab`
  (pill button, `flex: 1 0 auto`), `.tab-panel { display: none }` /
  `.tab-panel.active { display: flex; ... }`, `.tab-panels` (scroll
  container). All already generic, already used by 3 panels, need no
  modification for a 4th (`app.css:295-313`).
- `.table-scroll` (`app.css:333`) is the existing scrollable-table wrapper,
  already used both by the artifacts table today and by the History table —
  reused verbatim, no new class.
- `routes.py`'s task-fragment handler already puts `artifacts` (a list of
  `ArtifactRef`-shaped objects with `.step`/`.attempt`/`.name`) into the Jinja
  context for `_task.html` — untouched by this change.
- No existing test asserts the artifacts block lives inside the `info` panel
  specifically (`tests/test_api_artifacts.py::test_fragment_task_lists_artifacts_with_links`
  only checks that `"plan.md"` and the artifact URL are somewhere in the
  fragment body) — moving the block to a different panel doesn't break it.

## UX — tab strip and panel layout

Tab strip, four items, order Info / History / Output / Artifacts (append at
the end — least disruptive to an operator's existing muscle memory, and it
mirrors the notes' framing of Artifacts as the newly-added surface):

```
┌───────────────────────────────────────────────────┐
│ [ Info ] [ History ] [ Output ] [ Artifacts 4 ]    │  ← .tabs (nav)
├───────────────────────────────────────────────────┤
│                                                     │
│   (active panel content, scrollable)               │
│                                                     │
└───────────────────────────────────────────────────┘
```

Artifacts panel, populated state:

```
┌───────────────────────────────────────────────────┐
│  step        │ attempt │ name                      │
├──────────────┼─────────┼───────────────────────────┤
│  plan        │    0    │ plan.md  (link)            │  ← .table-scroll
│  design      │    0    │ design.md (link)           │
│  development │    0    │ ...                        │
│  ...         │         │  (scrolls within panel)    │
└───────────────────────────────────────────────────┘
```

Artifacts panel, empty state: a single `<p class="hint">No artifacts yet.</p>`
— identical markup/copy to today's empty state, just under its own panel.

Tab badge (nice-to-have, FR-5): `Artifacts 4` when `artifacts` is non-empty,
plain `Artifacts` when empty — no "0" pill, since the hint text inside the
panel already communicates emptiness once opened; a "0" badge adds visual
noise for no extra information the operator doesn't get anyway from a plain
label.

## Component design

Single component touched: the Jinja template `_task.html`, which is a pure
view over `{task, artifacts}` — no new component boundary, no new JS module.

**Tab strip (`<nav class="tabs">`)** — add one `<button class="tab"
data-tab="artifacts">`. Structural twin of the existing three buttons; the
only difference is the conditional badge span inside it:

```html
<button class="tab" data-tab="artifacts">
  Artifacts{% if artifacts %} <span class="tab__count">{{ artifacts|length }}</span>{% endif %}
</button>
```

**Artifacts panel (`<section class="tab-panel" data-panel="artifacts">`)** —
placed as the fourth `<section>` inside `.tab-panels`, after `output`. Body is
the artifacts block moved out of `info` **verbatim** (same conditional, same
table markup, same link target, same wrapper class):

```html
<section class="tab-panel" data-panel="artifacts">
  {% if artifacts %}
  <div class="table-scroll">
  <table>
    <tr><th>step</th><th>attempt</th><th>name</th></tr>
    {% for ref in artifacts %}
    <tr>
      <td>{{ ref.step }}</td>
      <td>{{ ref.attempt }}</td>
      <td class="wrap"><a href="/api/tasks/{{ task.id }}/artifacts/{{ ref.step }}/{{ ref.attempt }}/{{ ref.name }}">{{ ref.name }}</a></td>
    </tr>
    {% endfor %}
  </table>
  </div>
  {% else %}
  <p class="hint">No artifacts yet.</p>
  {% endif %}
</section>
```

No `<h3>artifacts</h3>` heading is needed inside the new panel — the tab
label ("Artifacts") already names the surface, and none of the other three
panels (History, Output) repeats their own tab name as an in-panel heading
either; keeping that convention is more consistent than carrying the old
Info-panel heading along.

**Info panel** — delete the `<h3>artifacts</h3>` line and the entire
`{% if artifacts %}...{% else %}...{% endif %}` block that follows it. The
`<ul class="kv">` (ending in the `data` row) is the whole remaining panel
body; nothing else in Info changes.

## Data schema

No change to any schema, port, or wire format:

- `ArtifactRef` (`step: str`, `attempt: int`, `name: str`) — unchanged, still
  the type iterated by the moved table.
- `GET /fragment/task/{id}` response — still HTML, same `artifacts` context
  key, same URL scheme for each link
  (`/api/tasks/{id}/artifacts/{step}/{attempt}/{name}`).
- `ArtifactView` interface and its routes — untouched. `api/` continues to
  touch only `ArtifactView` for artifact data (invariant #11) and imports no
  driver (invariants #5/#33) — this change is confined to `.html`/`.css`.

## CSS addition (`app.css`, nice-to-have FR-5)

Add `.tab__count` near the existing `.tabs .tab` rules (`app.css:295-306`),
modeled on `.column__count` (`app.css:175-179`) but sized down to sit inside
a tab button without changing the tab strip's height:

```css
.tab__count {
  font-size: 11px; font-weight: 700;
  background: var(--surface); color: var(--text-3);
  border-radius: var(--radius-pill); padding: 1px 7px; margin-left: 2px;
}
.tabs .tab.active .tab__count { background: var(--on-accent); color: var(--accent); }
```

The active-state override keeps the badge legible against the accent-colored
active tab background (`.tabs .tab.active` sets `color: var(--on-accent);
background: var(--accent)`), mirroring how `.badge` variants already pair a
light background with a matching foreground color elsewhere in this file.

## Verification plan for the development step

1. `grep -c '<h3>artifacts</h3>' _task.html` → 0 (heading dropped, not
   duplicated into the new panel).
2. `grep -c 'data-panel="artifacts"'` → 1.
3. `.venv/bin/pytest -q` — expect no failures; specifically
   `test_api_artifacts.py::test_fragment_task_lists_artifacts_with_links`
   keeps passing since it only checks substring presence, not panel nesting.
4. Manual check via the project's `run` pattern: open a task with artifacts →
   Artifacts tab shows the badge count and the table; open a task with none →
   tab shows no badge, panel shows "No artifacts yet."; confirm Info panel no
   longer shows the artifacts heading/table.

## Out of scope (unchanged from the plan)

Trimming the `data` `<pre>` in Info, inline artifact preview, and any change
to artifact storage/layout or the `ArtifactView` port remain untouched.
