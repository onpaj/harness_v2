# Review — Read-only node-graph preview for the workflow editor

Reviewed against `plan-01.md`, `design-01.md`, `architecture-01.md`, and the
issue's spec/acceptance criteria. Verified independently (not just re-reading
`development-01.md`'s claims).

## Verification performed

- Fresh venv (`python3.11 -m venv` + `pip install -e ".[dev]"`), full suite:
  `1350 passed, 1 skipped` — matches the development doc's claim, confirmed
  from a clean environment rather than trusting the log.
- `git show --stat b935a3c` — exactly the three files named in the
  plan/architecture were touched (`workflow_preview.js` new,
  `workflow_editor.html`, `app.css`), plus the artifact file. No route, port,
  model, or driver file changed, so `tests/test_architecture.py` is
  unaffected by construction.
- Served the actual page through `TestClient` (`create_app` +
  `FilesystemWorkflowAdmin`, mirroring `tests/test_api_workflows.py`'s
  fixtures) with a real workflow definition: `GET /admin/workflows/default`
  returns 200 with `id="workflow-preview-svg"`, `id="workflow-preview-notice"`,
  and the `/static/workflow_preview.js` script tag present; the textarea's
  content, `html.unescape`d, is byte-identical to the source JSON.
  `GET /static/workflow_preview.js` serves 200.
- Exercised `parseWorkflow`/`buildGraph`/`computeLayout`/`render` directly
  under Node (own harness, not reusing the development doc's) against: the
  issue's exact example workflow (7 nodes/7 edges, columns
  `plan=0..land=5,end=6`, back-edge ignored for placement); invalid JSON;
  missing `start`; a single-step/zero-transition workflow (1×1 layout, no
  divide-by-zero); an orphan node unreachable from `start` (placed in a
  trailing column, not dropped); a self-loop plus duplicate `(from,on,to)`
  edges (both rendered, not deduped); a step with a string-form `finishers`
  entry and a `descriptions` entry (both surfaced on the node). All matched
  the documented behavior.
- Confirmed the read-only/XSS guarantees mechanically: no `.innerHTML =` in
  the file, no `textarea.value =`, no `fetch(`/`htmx` call — the module's
  only DOM write target is the SVG's children, built exclusively via
  `createElementNS`/`setAttribute`/`.textContent`.
- Checked every CSS custom property used in the new `app.css` block
  (`--surface`, `--surface-2`, `--border`, `--border-strong`, `--accent`,
  `--accent-weak`, `--on-accent`, `--text`, `--text-2`, `--text-3`,
  `--radius-sm`) exists and is defined in both the light (`:root`) and dark
  (`prefers-color-scheme: dark`) blocks — no new variables, no hardcoded
  colors, theming is inherited for free.
- Compared the JS shape-validator (`parseWorkflow`) against the server's
  `_parse_workflow` (`drivers/fs_workflows.py`): required `start`/`transitions`
  shape matches; `maxParallel`/`finishers`/`descriptions` are tolerated
  best-effort on the client (falls back to `{}`) rather than strictly
  validated like the server does — acceptable, since these are display-only
  extras here (badges/tooltips) and the spec never asks the preview to
  duplicate full server-side validation, only to degrade gracefully on
  invalid/incomplete JSON, which it does.

## Acceptance criteria check

- [x] Node-graph preview renders when JSON is valid — confirmed via
  `TestClient` + direct JS exercise.
- [x] Nodes = steps (`Workflow.steps()`-equivalent dedup) + reserved `end`;
  edges = transitions labelled by `on` — confirmed by direct inspection of
  `buildGraph`/`render` output.
- [x] `start` visually distinguished (`.wf-node--start`, badge), `end` marked
  terminal (`.wf-node--end`, dashed border, badge) — confirmed in CSS + render
  logic.
- [x] Invalid/incomplete JSON degrades gracefully — the notice element is
  shown, SVG hidden, no exception path (every function gated by `ok` before
  any DOM write); confirmed with several malformed inputs.
- [x] Strictly read-only — no write to the textarea, no network call
  anywhere in the file; save/validation flow untouched (`text`/`id`/`name`
  attributes and `{{ text }}` interpolation on the textarea are unmodified).
- [x] Theming honored via existing custom properties, no new variables.
- [x] Nice-to-have hint/description/finisher tooltips implemented via native
  `<title>` elements, as the design specified.

## Assessment

No automated JS test was added, but this matches the architecture doc's
explicit, reasoned scoping (this project has no JS test runner, and manual
verification was the agreed mitigation for that stated risk) — not a gap
against what was asked. The implementation is a straightforward, contained
rendering of the existing model shapes with no server/port/model changes,
consistent with every invariant in `CLAUDE.md` (`api/` unaffected, no driver
touched, router/dispatcher untouched). All acceptance criteria are met.

## Verdict

`done`
