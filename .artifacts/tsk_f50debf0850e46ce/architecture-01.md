# Architecture assessment — Artifacts tab in task detail dialog

## Verdict

Approved as designed. This is a template/CSS-only rearrangement with no
architectural surface: no new port, no new route, no driver import, no
routing/decision logic. Nothing in the design or plan touches an invariant.
Proceed to development as written in `design-01.md`.

## What was checked against the actual source (not assumed)

- `src/harness/api/templates/_task.html` (read in full, 87 lines): confirms
  the artifacts block is exactly where the plan/design say — a
  `<h3>artifacts</h3>` + conditional table/hint, appended after the `kv` list
  inside `data-panel="info"`, before the closing `</section>`. The `history`
  and `output` panels are structural siblings with no in-panel heading
  duplicating their own tab name — so the design's choice to omit an
  `<h3>Artifacts</h3>` inside the new panel matches the codebase's existing
  convention exactly, not just a stylistic guess.
- `src/harness/api/templates/board.html:118-128`: the delegated click handler
  reads `event.target.closest('.tabs .tab')`, gets `data-tab`, and toggles
  `.active` on any `.tab-panel` (scoped via `closest('.task-detail')`) whose
  `data-panel` matches. It is a plain attribute-equality toggle over
  `querySelectorAll('.tab-panel')` — genuinely tab-count-agnostic, confirming
  the design's central claim that a 4th `data-tab`/`data-panel` pair needs no
  script change.
- `src/harness/api/static/app.css:295-313`: `.tabs`, `.tab`, `.tab-panel`,
  `.tab-panels` are all generic, already serving 3 panels; `.tabs .tab.active`
  sets `color: var(--on-accent); background: var(--accent)`, which the
  design's `.tab__count` active-state override correctly accounts for.
  `.column__count` at line 175 is the model the new `.tab__count` follows —
  confirmed it exists and has the shape (small pill, muted background/text)
  the design describes.
- `src/harness/api/routes.py:537`: the fragment handler's context is
  `{"task": found, "artifacts": artifacts.list(task_id)}` — unchanged by this
  task, confirmed by reading the handler directly, not inferred.
- `tests/test_api_artifacts.py::test_fragment_task_lists_artifacts_with_links`
  (line 74): asserts only that the artifact name and URL substring appear
  somewhere in the fragment body — no assertion on which panel/section
  contains them. Moving the block to a different `<section>` cannot break
  this test, confirmed by reading it rather than assuming.

## Alignment with invariants

- **#5 / #33** (`api/` imports no driver): untouched — this change is
  Jinja/CSS only, no Python import added anywhere.
- **#11** (`api/` touches only `ArtifactView` for artifacts): untouched — the
  handler already resolves `artifacts` via `ArtifactView.list()`; the template
  only re-renders the same list in a different DOM location.
- No port, route, or wire-format change, so no other invariant is in scope.

## Guidance for development

1. Move the existing block (lines 43-59 of `_task.html`) verbatim into a new
   `<section class="tab-panel" data-panel="artifacts">`, appended after the
   `output` section inside `.tab-panels`. Do not carry the `<h3>artifacts</h3>`
   heading along — drop it, consistent with `history`/`output` having no
   in-panel heading of their own.
2. Add the fourth tab button after `output` in the `<nav class="tabs">` strip,
   with the optional `<span class="tab__count">` badge shown only when
   `artifacts` is non-empty (no "0" badge).
3. Delete the moved block from the `info` panel entirely — no duplicate, no
   dead markup.
4. If implementing the count badge, add `.tab__count` (and the
   `.tabs .tab.active .tab__count` override) to `app.css` near line 306,
   sized to fit inside the existing tab button without changing `.tabs`
   min-height/padding.
5. Verification: `.venv/bin/pytest -q` should pass unchanged; grep to confirm
   `<h3>artifacts</h3>` no longer appears and `data-panel="artifacts"` appears
   exactly once; manually exercise both the populated and empty-artifacts
   cases if a dev server is run.

## Risks

None material. The only latent risk is a stale test asserting artifacts
render inside `info` specifically — checked directly above and no such test
exists, so this is closed, not just assumed.
