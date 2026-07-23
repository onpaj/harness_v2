# Plan — unify admin UI with the dashboard design system

## Summary

The board (`board.html` + `_columns.html` + `_task.html`) is styled by a single
shared, theme-aware stylesheet, `src/harness/api/static/app.css` — an
iOS-flavoured language with CSS custom-property tokens, pill buttons, rounded
cards, and full light/dark support via `prefers-color-scheme`. The four admin
pages (`admin/agent_form.html`, `admin/workflow_editor.html`,
`admin/agents_list.html`, `admin/workflows_list.html`) each carry their own
inline `<style>` block with a hardcoded, light-only Atlassian-style palette
(`#f4f5f7`, `#172b4d`, `#0052cc`, `#de350b`, …). This task documents the
board's design language as a design guide and ports the four admin pages onto
`app.css`, deleting their bespoke styles so the whole product reads as one UI
in both color schemes.

## Context

`_nav.html` (the shared appbar/tabbar) is already `{% include %}`-ed by all
four admin pages, so navigation chrome is technically shared already — but
each page's inline `<style>` block still defines a dead, parallel `.nav`
selector left over from an older nav design, alongside the real culprits:
hardcoded `.card`, `.field`, `button`, `.banner`, and table styles that never
adopted `app.css`'s tokens. The visible result is a light-only "admin tool"
bolted onto a dark-mode-capable "dashboard" — jarring when a user with a dark
system theme jumps from the board to editing an agent.

`app.css` already has most of the vocabulary this task needs (`--surface`,
`--border`, `--radius`, `--accent`, `.btn`, `.btn.primary`, `.page`,
`.page-header`, `.kv`, `.table-scroll`/`table`), just not the pieces specific
to forms and status banners (text inputs, textareas, field-level validation
errors, success/error/warning banners, a "danger" button variant, a simple
list-page card). This task adds those as new shared component styles, then
retargets the four templates to use them instead of inventing their own.

**⚠️ Worktree note (see Open Questions):** as of this writing, this task's
worktree is checked out from an older point on `main` (commit `0c8027b`) that
predates PR #65 ("Redesign the board and admin UI to be mobile-first"), which
is the commit that introduced `app.css`, `_nav.html`, and the four admin
templates in the first place. `origin/main` is currently 46 commits ahead.
None of the files this task references exist in the worktree yet. The design
step and dev step must sync the branch with `origin/main` before design/
implementation can proceed — the content below is written against the
`origin/main` version of these files (fetched via `git show origin/main:<path>`)
since that's the code the merged PR will actually land on top of.

## Functional requirements

**FR-1 — Design guide document**
A new document, `docs/design/ui-guide.md`, describes the design system
extracted from `app.css`: color tokens (light + dark values), radii, shadows,
spacing/sizing conventions (44px tap targets, 16px page padding), typography
scale (`h1`/`h2`/`h3`, body, monospace for code/paths), and every shared
component class with a usage rule and a minimal HTML snippet.
- Acceptance: doc exists, covers at minimum `--bg/--surface/--surface-2/
  --text/--text-2/--text-3/--border/--border-strong/--accent/--accent-weak/
  --on-accent/--shadow/--shadow-lg/--radius/--radius-sm/--radius-pill` plus the
  status tokens (`--done-*`, `--changes-*`, `--working-*`, `--failed-*`).
- Acceptance: doc documents every component this task adds or reuses (`.btn`
  incl. `.primary`/`.danger`/`.small`, `.page`/`.page-header`, `.card`,
  `.field`, `.banner` incl. `.success`/`.error`/`.warning`, `.kv`,
  `.table-scroll`, `.hint`, `.field-error`).
- Acceptance: doc states the one rule that matters for enforcement — tokens
  and shared component styles live only in `app.css`; a template must not
  declare its own `<style>` block or hex colors.

**FR-2 — Shared component styles added to `app.css`**
Add the CSS needed to cover admin-form and admin-list use cases that don't
already exist on the board: text `input`/`textarea` styling (incl. a
monospace variant for the JSON editor), `.field`/`.field-error` (validation
state), `.banner` with `.success`/`.error`/`.warning` variants, `.btn.danger`,
and a simple non-kanban list/table "card" wrapper for the two list pages
(`agents_list.html`/`workflows_list.html` can reuse `.card`/`.table-scroll`
already on the board rather than inventing a new list style).
- Acceptance: every new selector is namespaced under existing conventions
  (BEM-ish `.field`, `.field-error`, modifier classes on `.btn`/`.banner`) and
  uses only existing custom properties — no new hardcoded colors, no new
  tokens invented without a stated reason.
- Acceptance: dark mode is automatic — because these rules reference the same
  `var(--...)` tokens as the board, no separate dark-mode block is needed
  beyond what already exists in `app.css`'s `@media (prefers-color-scheme:
  dark)` block.

**FR-3 — Restyle the four admin templates**
Retarget `admin/agent_form.html`, `admin/workflow_editor.html`,
`admin/agents_list.html`, `admin/workflows_list.html` to the shared
stylesheet:
- Remove every inline `<style>` block and every hardcoded hex color.
- Remove the dead `.nav`/`.back` selectors (superseded by `_nav.html`'s
  `.appbar`); replace the `.back` link's markup with whatever the board uses
  for equivalent "back" navigation (or drop it if `_nav.html` already covers
  wayfinding — confirm during design).
- Replace `.card`/`.field`/`button`/`.banner`/table markup with the new
  shared classes from FR-2.
- Wrap page content in `.page`/`.page-header` the way `board.html` does, so
  padding, max-width and heading treatment match.
- Acceptance: `grep -n "<style"` and `grep -nE "#[0-9a-fA-F]{3,6}"` return
  nothing in these four templates (only `<link rel="stylesheet"
  href="/static/app.css">` remains for styling).
- Acceptance: each page visually matches the board's card/typography/spacing
  language in both light and dark system themes (manual check — see Rough
  plan's verification step).

**FR-4 — No visual regression on the board**
Because component styles are added to a shared file consumed by the board
too, additions must be purely additive (new selectors) — no edits to
existing board-only selectors' values.
- Acceptance: `git diff` on the pre-existing portion of `app.css` shows only
  insertions, not modifications, to selectors already used by
  `board.html`/`_columns.html`/`_task.html`.
- Acceptance: the board renders unchanged before/after (manual comparison via
  the `run` skill or a local server).

## Non-functional requirements

- **Offline-friendly**: no external stylesheets, fonts, or CDN assets — same
  constraint the board already meets (system fonts, inline SVG icons).
- **No new build step**: `app.css` is hand-authored and served as a static
  file today; this task must not introduce a CSS preprocessor, bundler, or
  build pipeline.
- **Accessibility parity**: form fields keep visible `<label>` associations;
  focus states and tap targets (44px min-height) match the board's existing
  `.btn` sizing; error banners remain readable in both color schemes (reuse
  `--failed-fg`/`--failed-bg` rather than introducing new red tones).
- **No functional change**: form field names, POST targets, validation logic,
  and server-side template context are untouched — this is a template/CSS
  change only, not a route/behavior change.

## Data model

Not applicable in the usual sense (no persisted entities change). The
relevant "model" here is the design token graph the guide documents:

- **Token layer** (`:root` custom properties in `app.css`): color roles
  (`--bg`, `--surface`, `--surface-2`, `--text*`, `--border*`, `--accent*`,
  `--on-accent`), elevation (`--shadow`, `--shadow-lg`), shape
  (`--radius`, `--radius-sm`, `--radius-pill`), and status semantics
  (`--done-*`, `--changes-*`, `--working-*`, `--failed-*`). Each token has a
  light value and a dark override under `@media (prefers-color-scheme:
  dark)`.
- **Component layer**: classes (`.btn`, `.card`, `.field`, `.banner`, `.kv`,
  `.table-scroll`, …) reference only token-layer variables, never literal
  colors.
- **Template layer**: each `.html` file consumes component-layer classes and
  contributes no styling of its own.

This layering (tokens → components → templates) is the rule the design guide
must state explicitly, since it's what makes future pages consistent by
default rather than by discipline.

## Interfaces

No API/event surface changes. UI flows affected (all server-rendered via
Jinja2, routes unchanged, from `src/harness/api/routes.py`):

- `GET /admin/agents` → `admin/agents_list.html`
- `GET /admin/agents/new`, `GET /admin/agents/{name}`, `POST /admin/agents`,
  `POST /admin/agents/{name}` → `admin/agent_form.html`
- `GET /admin/workflows` → `admin/workflows_list.html`
- `GET /admin/workflows/new`, `GET /admin/workflows/{name}`,
  `POST /admin/workflows`, `POST /admin/workflows/{name}` →
  `admin/workflow_editor.html`

New file: `docs/design/ui-guide.md` (documentation only, not served by the
app).

## Dependencies and scope

**Depends on:**
- The worktree/branch must be brought up to date with `origin/main` (see
  Open Questions) so `app.css`, `_nav.html`, and the four admin templates
  exist to work from.
- `app.css`'s existing token set — this task must not redesign the palette,
  only extend it with missing component rules.

**In scope:**
- `docs/design/ui-guide.md` (new).
- `src/harness/api/static/app.css` (additive component rules).
- `src/harness/api/templates/admin/agent_form.html`,
  `workflow_editor.html`, `agents_list.html`, `workflows_list.html`
  (markup + class changes, style-block removal).

**Out of scope:**
- Any functional/behavioral change to the admin forms (validation rules,
  field set, POST handling) or to `routes.py`.
- Any change to board behavior/markup beyond additive CSS.
- New JS behavior — the JSON editor stays a plain `<textarea>`, no syntax
  highlighting or client-side validation added.
- Restyling `admin/_update_result.html` unless it turns out to need new
  classes from FR-2 to render correctly inside `_nav.html`'s update banner
  (check during design; likely already fine since it's a small HTMX
  fragment, not a full page).

## Rough plan

1. **Sync the branch.** Rebase/merge this task's worktree onto current
   `origin/main` so the referenced files actually exist locally (flagged
   under Open Questions — needs an explicit decision on how, since this repo's
   normal task flow is "worktree per task, PR at the end," and this worktree
   was cut before PR #65 landed).
2. **Write the design guide first** (`docs/design/ui-guide.md`), extracting
   tokens and existing component conventions directly from the current
   `app.css` and board templates — this becomes the checklist for step 3.
3. **Extend `app.css`** with the missing component rules identified in FR-2,
   guided by what the four admin templates currently hand-roll (inputs,
   textareas, field errors, banners, danger button, list card). Add these as
   new sections, following the file's existing section-comment style.
4. **Update the design guide** with the finalized component list/snippets
   (steps 2 and 3 will likely go back and forth once real component rules
   exist).
5. **Restyle the four templates** one at a time (start with `agents_list.html`
   as the simplest, then `workflows_list.html`, then the two form pages),
   removing inline styles and swapping in shared classes.
6. **Verify no regression**: run the app locally (`run` skill or equivalent),
   compare the board before/after, and check all four admin pages in both a
   light and dark system theme.
7. **Check existing tests** (`tests/test_smoke.py` and any template/route
   tests) still pass — this is a template-only change so no new test coverage
   is expected, but existing HTML-content assertions (if any check for
   specific class names) must be updated to match.

## Open questions

- **Worktree/branch staleness (blocking).** This task's worktree is 46
  commits behind `origin/main` and doesn't contain any of the files the task
  is supposed to modify. Default assumption for this plan: a later step syncs
  the branch with `origin/main` before design/dev work starts (standard
  "rebase before implementing" — not this task's own decision to make, so
  flagging rather than resolving). If the harness's normal flow doesn't
  support that, this needs to be raised back to the operator before
  proceeding past this plan.
- **`.back` link fate.** The admin pages currently have both `_nav.html`'s
  appbar (with a "Workflows"/"Agents"/"Board" links) and a separate
  hand-rolled "&larr; back to agents/workflows" link. Default: keep the back
  link but restyle it as a plain `app.css`-styled anchor (`.hint`-weight text
  link) rather than removing it, since it's a one-level-up affordance the
  appbar doesn't provide (appbar links go to the list root, not "back from
  form to list" specifically, which is the same destination here but reads
  differently). Noted as a default choice — the design step can override.
- **List-page styling: dedicated component or reuse?** `agents_list.html`/
  `workflows_list.html` are simple name-only tables. Default: reuse the
  board's existing `.table-scroll` + `table` styles verbatim rather than
  inventing a new "list card" component, since the visual need (a bordered,
  rounded table) is already met. Confirm during design.
- **`admin/_update_result.html` scope.** Small HTMX-swapped fragment shown in
  the appbar; likely needs no changes since it renders inline text, not a
  card/form. Confirm it doesn't carry its own hardcoded colors during
  implementation; if it does, it's in scope under FR-3's spirit even though
  not listed as one of the "four admin templates."
