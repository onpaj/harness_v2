# Development — unify admin UI with the dashboard design system

## What was done

**0. Branch sync (prerequisite from architecture-01.md).** Ran
`git fetch origin && git merge origin/main` on the current branch (no new
branch, no worktree). Merge was clean — zero conflicts, as the architecture
step had verified in advance. This pulled in PR #65/#69 (`app.css`,
`_nav.html`, `board.html`, `_columns.html`, `_task.html`, the four
`admin/*.html` templates, `admin/_update_result.html`) that this task's
worktree previously lacked. Confirmed all expected files exist post-merge
before touching anything.

**1. `docs/design/ui-guide.md` (new).** Written from design-01.md's outline,
sourcing token values directly from the real, post-merge `app.css`
`:root`/dark-mode blocks. Covers: principles (tokens → components →
templates, the "no inline `<style>`/no hex" enforcement rule, automatic dark
mode, offline-friendliness), the full token table (colors, radii, shadows,
status semantics), typography, layout primitives (`.page`, `.page-header`,
`.panel`/`.panel.wide`, `.table-scroll`), every shared component (`.btn` incl.
modifiers, `.card` — explicitly marked board-only, `.field`/`.field-error`,
inputs/textareas, `.outcomes`, `.banner` incl. 3 variants, `.badge`, `.kv`,
`.hint`, the update-result color overrides), navigation (`_nav.html`'s
appbar/tabbar as the only nav chrome), and the closing usage rule. States
explicitly that `app.css` is authoritative and this doc is a reading aid, per
architecture's risk note.

**2. `src/harness/api/static/app.css` — additive component styles.** Appended
one new section after the existing `Desktop`/`reduced-motion` blocks (the
literal end of the file): `.panel`/`.panel.wide`, `.field`/`.field label`,
`input[type="text"]`/`textarea` (incl. `.editor` monospace variant and
`:focus` ring), `.field.error`/`.field-error`, `.outcomes`, `.actions`,
`.banner` + `.success`/`.error`/`.warning`, `.btn.danger`,
`.page-header__action`, `.update-result-ok`/`.update-result-error`. Verified
with `git diff origin/main -- app.css`: the diff is 64 insertion-only lines
appended at the end of the file — no existing selector's declaration block
was touched, so the board is provably unaffected by construction, not just by
inspection.

**3. Restyled the four admin templates + `_update_result.html`.** Applied
design-01.md's markup verbatim (byte-identical field names, `id`/`name`
attributes, POST `action` targets, and Jinja context variables — checked
against the real pre-merge templates before editing):

- `admin/agents_list.html`, `admin/workflows_list.html` — added the standard
  mobile-first `<head>` (viewport/theme-color/apple-mobile-web-app meta, same
  as `board.html`), replaced the ad hoc `.nav`/`.card`/`.empty`/`a.new`
  markup with `_nav.html` + `<main class="page">` + `.page-header` (title +
  `.page-header__action` "+ New …" button) + `.table-scroll`/`table` (or
  `.hint` empty state). Removed the inline `<style>` block entirely.
- `admin/agent_form.html` — same head/shell treatment; the "back" link is now
  `<a class="hint">`; the form wrapper is the new `.panel` (not `.card`, per
  design's correction — a static form isn't a clickable board task); fields
  use `.field`/`.field-error`/`.outcomes`; buttons are `.btn.primary`/
  `.btn.danger`; the saved message is `.banner.success`. Inline `<style>`
  block and every hardcoded hex removed.
- `admin/workflow_editor.html` — same treatment with `.panel.wide` (720px)
  and `textarea.editor` for the raw-JSON field; three banner kinds
  (`.error`/`.success`/`.warning`) preserved as separate conditional blocks
  exactly as before.
- `admin/_update_result.html` — swapped the three inline hex colors
  (`#bf2600`/`#006644`/`#5e6c84`) for `class="update-result-error"` /
  `class="update-result-ok"` / no class (inherits the appbar's existing muted
  default). This fragment is rendered on *every* page via `_nav.html`, so
  fixing it also removes a pre-existing hardcoded-color leak from the board
  itself, not just the admin pages.

## Files created

- `docs/design/ui-guide.md`

## Files changed

- `src/harness/api/static/app.css` (additive only — see diff verification above)
- `src/harness/api/templates/admin/agent_form.html`
- `src/harness/api/templates/admin/workflow_editor.html`
- `src/harness/api/templates/admin/agents_list.html`
- `src/harness/api/templates/admin/workflows_list.html`
- `src/harness/api/templates/admin/_update_result.html`
- `tests/test_api_update.py` (see "Test updates" below — not anticipated by
  architecture-01.md, discovered while running the suite)

## Test updates (deviation from architecture-01.md's assessment)

Architecture-01.md stated no existing test would need updating, based on a
grep of `test_api_agents.py`/`test_api_workflows.py`. Running the full suite
surfaced two failures architecture's grep didn't cover:
`tests/test_api_update.py::test_update_reports_a_version_change` and
`::test_update_surfaces_an_error_as_a_swappable_fragment` asserted directly on
the hardcoded hex colors (`#006644`, `#bf2600`) that `_update_result.html`
used to emit — colors this task deliberately removed per design-01.md §3.6.
Updated both assertions (plus the adjacent "muted" negative-assertion in
`test_update_reports_no_change`) to check for the new
`class="update-result-ok"` / `class="update-result-error"` markers instead.
This is a mechanical assertion update tracking an intentional, in-scope
markup change (§3.6 was explicitly called into scope by both design and
architecture) — no test behavior or coverage was weakened; the tests still
assert "ok gets a distinguishing marker, muted case doesn't, error gets a
distinguishing marker," just via class name instead of via hex value.

## How to verify

```sh
.venv/bin/pip install -e ".[dev]"   # first time only
.venv/bin/pytest -q                 # 1022 passed, 1 skipped
```

Mechanical checks (all clean):

```sh
grep -n "<style" src/harness/api/templates/admin/*.html            # no output
grep -nE "#[0-9a-fA-F]{3,6}" src/harness/api/templates/admin/*.html # only board-matching <meta theme-color> tags, no CSS hex
git diff origin/main -- src/harness/api/static/app.css             # insertions only, 64 lines, nothing existing modified
```

Manual/behavioral spot-check performed in this step (no browser available in
this environment, so verified via `TestClient` + reading rendered HTML rather
than an actual browser render):

- `GET /`, `/admin/agents`, `/admin/agents/new`, `/admin/workflows`,
  `/admin/workflows/new` all return 200 with no `<style>` tag in the body.
- Rendered `/admin/agents/new` HTML inspected directly: shared `_nav.html`
  appbar/tabbar present, `<main class="page">` shell, `.panel` form wrapper,
  `.field`/`.outcomes`/`.actions` markup, all classes resolving to the new
  `app.css` rules — matches design-01.md's wireframe exactly.
- To eyeball light/dark rendering in an actual browser: `python -m
  harness.cli run` (or the project's `run` skill) and toggle the OS color
  scheme while viewing `/admin/agents/new`, `/admin/workflows/new`, and the
  two list pages alongside `/` (the board) — all five should now read as one
  consistent, theme-aware UI with no light-only holdouts.

## Acceptance criteria check

- [x] Design guide exists (`docs/design/ui-guide.md`) covering tokens,
      typography, and shared components with usage rules.
- [x] Tokens/component styles live only in `app.css` (additive), not
      duplicated per template.
- [x] Agent editor, workflow editor, agents list, workflows list restyled
      onto the shared stylesheet; every inline `<style>` block and hardcoded
      color removed (incl. the "bonus" `_update_result.html` fragment).
- [x] Admin screens get light/dark automatically via the same
      `prefers-color-scheme` tokens as the board — no new dark-mode CSS
      needed anywhere in this change, verified by construction (every new
      rule is `var(--...)`-driven).
- [x] Nav/back-link and page shell now match the board's appbar/nav/`.page`/
      `.page-header` treatment exactly (byte-identical head block and
      `_nav.html` include).
- [x] No visual regression on the board — `app.css` diff against
      `origin/main` is insertion-only; full test suite (1022 tests, including
      board-rendering tests) passes unmodified except the two update-fragment
      assertions tracking the intentional `_update_result.html` change.
