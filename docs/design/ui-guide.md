# UI design guide

harness's UI is one product, not a board plus a separate admin tool. Every
page — the board, the agent editor, the workflow editor, and their list
pages — is rendered through a single shared, theme-aware stylesheet:
`src/harness/api/static/app.css`.

`app.css` is the source of truth. This guide is a reading aid over it, not a
second spec — if this document and `app.css` ever disagree, `app.css` is
right and this document is stale.

## 1. Principles

- **Tokens → components → templates.** Colors, radii, shadows and spacing are
  defined once as CSS custom properties (§2). Components (`.btn`, `.card`,
  `.panel`, `.banner`, …) reference only those tokens, never a literal color
  (§4-5). Templates consume component classes and contribute no styling of
  their own.
- **The one enforcement rule:** a template must not declare its own
  `<style>` block or a hardcoded hex color. Every page's `<head>` links only
  `/static/app.css`. If a page needs a visual it doesn't have, add a token or
  component to `app.css` — don't reach for an inline style.
- **Dark mode is automatic, not opt-in.** Because every rule reads a
  `var(--...)` token, the single `@media (prefers-color-scheme: dark)` block
  in `app.css` re-colors the entire app — board and admin alike. A new page
  that follows this guide needs zero extra work to support dark mode.
- **No external resources.** System fonts, inline SVG icons, no CDN
  stylesheets or web fonts — the app must keep working offline.

## 2. Design tokens

All defined in `app.css`'s `:root` block (light) and overridden in the
`@media (prefers-color-scheme: dark)` block (dark) — see `app.css:12-67`.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#f2f2f7` | `#000000` | page background |
| `--surface` | `#ffffff` | `#1c1c1e` | cards, panels, inputs |
| `--surface-2` | `#f7f7fa` | `#2c2c2e` | recessed surfaces (desktop column bg, count pill) |
| `--text` | `#1c1c1e` | `#ffffff` | primary text |
| `--text-2` | `#55555b` | `#aeaeb4` | secondary text, field labels |
| `--text-3` | `#8e8e93` | `#8e8e93` | tertiary text, hints, timestamps |
| `--border` | `#e3e3e8` | `#38383a` | hairline borders |
| `--border-strong` | `#d1d1d6` | `#48484a` | input borders, `.btn` border |
| `--accent` | `#007aff` | `#0a84ff` | links, primary buttons, focus ring |
| `--accent-weak` | `#e5f0ff` | `#12325a` | active nav link background |
| `--on-accent` | `#ffffff` | `#ffffff` | text/icons on an accent-filled surface |
| `--shadow` | soft, `rgba(9,30,66,…)` | soft, `rgba(0,0,0,.5)` | card/panel elevation |
| `--shadow-lg` | `rgba(9,30,66,.18)`, larger spread | `rgba(0,0,0,.6)`, larger spread | dialog elevation (desktop task detail) |
| `--radius` | `16px` | same | cards, panels, big surfaces |
| `--radius-sm` | `11px` | same | inputs, banners |
| `--radius-pill` | `999px` | same | buttons, badges, tab strips |

Status semantics — one pair of tokens per outcome, used consistently for
badges, the board card's left edge, and admin banners/validation state:

| Token pair | Light | Dark | Meaning |
|---|---|---|---|
| `--done-bg` / `--done-fg` | `#e7f7ee` / `#1b7a43` | `#10361f` / `#57d98a` | success |
| `--changes-bg` / `--changes-fg` | `#fff3df` / `#9a5b00` | `#3d2c0a` / `#f0b45a` | warning / request_changes |
| `--working-bg` / `--working-fg` | `#e5f0ff` / `#0a63d6` | `#10305c` / `#6aa8ff` | in progress |
| `--failed-bg` / `--failed-fg` | `#ffe8e5` / `#c0362c` | `#3d1512` / `#ff6f61` | error / destructive action |

Layout constants: 44px minimum tap target (`.btn`, inputs, tabbar links),
16px page padding (`.page`), safe-area insets on phone (`--safe-top`,
`--safe-bottom`) for the fixed appbar/tabbar.

## 3. Typography

- `h1` — 22px/700, tight letter-spacing (`app.css:85`). Page titles, inside
  `.page-header`.
- `h2` — 17px/600 (`app.css:86`). Section headings, e.g. task detail tabs.
- `h3` — 13px/600, uppercase, letter-spaced, `--text-3` (`app.css:87-90`).
  Group labels (e.g. column headers).
- Body — system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI",
  system-ui, sans-serif`), 16px/1.45.
- Monospace — `ui-monospace, SFMono-Regular, Menlo, monospace`, used for
  repo paths, key/value technical values, and the JSON `textarea.editor`.

## 4. Layout primitives

- **`.page`** — the page-level container (`max-width: 1200px`, centered,
  16px padding). Every page's content sits inside one `<main class="page">`.
- **`.page-header`** — a flex row for the page title plus an optional
  right-aligned action (`.page-header__action`, e.g. "+ New agent").
- **`.panel` / `.panel.wide`** — a static (non-interactive) content
  container for a form: 640px max-width by default, 720px with `.wide`. Not
  the same component as `.card` (below) — a panel has no press effect and no
  status-colored edge, because it isn't a clickable board task.
- **`.table-scroll`** — a bordered, rounded, horizontally-scrollable wrapper
  around a `<table>`. Used by both the board's future tabular views and the
  admin list pages.

## 5. Components

- **`.btn`** — the pill button. Modifiers: `.primary` (accent-filled, for the
  main affirmative action), `.danger` (outlined in `--failed-fg`, for
  destructive actions like Delete), `.small` (tighter padding, still 44px tap
  target). Plain `.btn` with no modifier is a secondary/neutral action.
- **`.card`** — the **board task card only**. It is `cursor: pointer`, has a
  press animation (`:active { transform: scale(.985) }`) and a 4px
  status-colored left edge (`.is-working`/`.is-done`/`.is-changes`/
  `.is-failed`). Do not reuse `.card` for static content (forms, panels) —
  use `.panel` instead.
- **`.field` / `.field-error`** — a form field wrapper. `.field.error` reddens
  its `input`/`textarea` border (`--failed-fg`); `.field-error` is the inline
  validation message below it.
  ```html
  <div class="field {{ 'error' if errors.name }}">
    <label for="name">Name</label>
    <input type="text" id="name" name="name">
    {% if errors.name %}<div class="field-error">{{ errors.name }}</div>{% endif %}
  </div>
  ```
- **`input[type="text"]` / `textarea`** — shared text-entry styling: 44px min
  height, `--border-strong` border, `--accent` focus ring. `textarea.editor`
  is the monospace variant for raw JSON/code (used by the workflow editor).
- **`.outcomes`** — a flex row for a group of checkbox options inside a
  `.field` (e.g. "Allowed outcomes").
- **`.banner`** — an inline feedback message, with `.success` / `.error` /
  `.warning` modifiers mapped to the `--done-*` / `--failed-*` / `--changes-*`
  token pairs. `.banner.error` preserves whitespace (`white-space: pre-wrap`)
  for multi-line error text.
- **`.badge`** — a small pill status indicator (`.done` / `.request_changes` /
  `.working` / `.failed`), used on board task cards.
- **`.kv`** — a key/value list (label stacked over or beside a value),
  used in the task detail sheet.
- **`.hint`** — quiet secondary text (`--text-3`, 13px). Also used for
  low-emphasis navigation, e.g. an admin form's "&larr; back to agents" link.
- **`.update-result-ok` / `.update-result-error`** — color overrides for the
  appbar's Update button feedback span; the "already up to date" case needs
  no class since it already inherits the muted `--text-3` default.

## 6. Navigation

`_nav.html` is the shared appbar (top, desktop link row) + tabbar (bottom,
phone icons) — included by every page, board and admin alike, via
`{% include "_nav.html" %}` right after `<body>`. It is the only navigation
chrome a page should have; don't add a second nav bar or back-link row above
it. A page-local "back" affordance (e.g. an editor returning to its list) is
a `.hint`-styled anchor placed inside `<main class="page">`, not a
replacement for `_nav.html`.

## 7. Usage rule

Every page's `<head>` includes exactly one stylesheet:

```html
<link rel="stylesheet" href="/static/app.css">
```

No inline `<style>` block, no hardcoded hex color, in any template. If a page
needs a look `app.css` doesn't have yet, extend `app.css` with a new,
token-driven component — don't work around it locally. This is what keeps
every future page consistent by construction rather than by discipline.
