# Design — unify admin UI with the dashboard design system

Input: `.artifacts/tsk_35fef287ee384ce0/plan-01.md`. This document turns that
plan into a concrete, ready-to-implement design: the exact CSS to add to
`app.css`, the exact markup each of the four admin templates ends up with, and
the outline the dev step should write `docs/design/ui-guide.md` from. It does
not enumerate developer tasks — that belongs to the dev step.

**Worktree note carried over from the plan:** this worktree predates PR #65 and
does not contain `app.css`, `_nav.html`, or the admin templates locally. Every
file shown below (`app.css`, `_nav.html`, `board.html`, `_columns.html`,
`_task.html`, the four `admin/*.html` templates, `admin/_update_result.html`)
was read via `git show origin/main:<path>` so this design is written against
the real, current file contents. Syncing the branch to `origin/main` before
editing is an implementation precondition, not a design decision — carried
forward for the dev step.

## 1. Overview

`app.css` already defines a complete token system (color roles, elevation,
radii) and a component vocabulary (`.btn`, `.card`, `.kv`, `.table-scroll`,
`.banner`-shaped needs partially met by ad hoc admin CSS) — it's just missing
the handful of components admin forms need: text inputs, field validation,
banner variants, a danger button, and a static (non-interactive) panel
container. The design below:

1. Extends `app.css` with those missing components — additive only, no edits
   to any selector the board already uses (FR-4).
2. Retargets the four admin templates (plus `admin/_update_result.html`, see
   §3.5) onto the shared stylesheet, deleting every inline `<style>` block and
   hardcoded hex color.
3. Gives the dev step a ready outline for `docs/design/ui-guide.md`.

One correction to the plan's default: **the admin form/editor wrapper must not
reuse `.card` verbatim.** `.card` (app.css:180-201) is the *board task card* —
it's `cursor: pointer`, has a `:active { transform: scale(.985) }` press
effect, and a 4px status-colored left edge (`::before`, colored via
`.is-working`/`.is-done`/etc.). Wrapping a static form in `.card` would import
a pointer cursor, a press animation, and an idle gray left bar onto a form
that isn't a clickable card. §2.2/§3.2 introduce a new static component,
`.panel`, for this instead. The two list pages have no such conflict — they
wrap directly in `.table-scroll`/`table`, which is already a static container.

## 2. UX/UI

### 2.1 Component hierarchy (shared across all four admin pages)

```
<body>
 └─ _nav.html            (.appbar + .tabbar — already shared, unchanged)
 └─ <main class="page">
     ├─ <a class="hint">  ← back-link (form pages only)
     ├─ <div class="page-header">
     │   ├─ <h1>
     │   └─ <a/button class="btn ... page-header__action">  ← list pages only
     ├─ <div class="panel">                    ← form pages
     │   ├─ <form> → .field* → input/textarea/.field-error
     │   └─ <form> (delete) → .actions → .btn.danger
     │   — or —
     ├─ <div class="table-scroll"><table>      ← list pages
     └─ <div class="banner .success/.error/.warning">   ← result feedback
```

This is exactly `board.html`'s shell (`_nav.html` + `<main class="page">` +
`.page-header`), so an admin page and the board page differ only in what's
inside `<main>`, not in the surrounding chrome.

### 2.2 Wireframes

**Agents / Workflows list** (`agents_list.html`, `workflows_list.html` — identical shape):

```
┌─────────────────────────────────────────────┐
│ ● harness      Board  Agents  Workflows  Upd.│  ← _nav.html .appbar (unchanged)
├─────────────────────────────────────────────┤
│  Agents                          [+ New agent]│ ← .page-header, action right-aligned
│ ┌───────────────────────────────────────────┐│
│ │ reviewer                                   ││ ← .table-scroll > table (reused as-is)
│ │ implementer                                ││
│ │ designer                                   ││
│ └───────────────────────────────────────────┘│
└─────────────────────────────────────────────┘
   (empty state: <p class="hint">No agents defined yet.</p>)
```

**Agent editor** (`agent_form.html`):

```
┌─────────────────────────────────────────────┐
│ ● harness      Board  Agents  Workflows  Upd.│
├─────────────────────────────────────────────┤
│  ← back to agents                            │ ← <a class="hint">
│  Agent: reviewer                              │ ← .page-header > h1
│ ┌─ .panel ──────────────────────────────────┐│
│ │ Prompt *                                   ││ ← .field > label
│ │ ┌─────────────────────────────────────────┐││ ← textarea
│ │ │                                         │││
│ │ └─────────────────────────────────────────┘││
│ │ Model            [____________________]    ││ ← .field > input[type=text]
│ │ Fallback model   [____________________]    ││
│ │ Allowed tools    [____________________]    ││
│ │   comma-separated                          ││ ← .hint
│ │ Allowed outcomes  [x] done  [ ] request_… ││ ← .outcomes
│ │ [Save]                                     ││ ← .actions > .btn.primary
│ │ [Delete]                                   ││ ← .actions > .btn.danger
│ └─────────────────────────────────────────────┘│
│  ✔ saved — picked up on the next run…         │ ← .banner.success
└─────────────────────────────────────────────┘
```

**Workflow editor** (`workflow_editor.html`): identical shell, `.panel.wide`
(720px vs 640px), a single `.field` holding `textarea.editor` (monospace JSON,
20 rows), and up to three banner kinds simultaneously possible (`error`,
`success`, N × `warning`).

### 2.3 Key interactions

- **Dark mode is automatic.** Every new/changed rule reads a `var(--...)`
  token, so the existing `@media (prefers-color-scheme: dark)` block in
  `app.css` re-colors admin pages the same way it already re-colors the board
  — no new dark-mode block needed anywhere in this design.
- **Back-link.** Kept (per plan's default), demoted to a quiet text link:
  `<a class="hint" href="...">← back to agents</a>`. `.hint` already exists
  (`color: var(--text-3); font-size: 13px`) and combined with the browser's
  default anchor underline-on-hover this reads as "secondary navigation," not
  a second nav bar competing with `_nav.html`'s appbar.
- **Validation state.** `.field.error` reddens the input border
  (`var(--failed-fg)`) and shows `.field-error` text below it — same pattern
  as today, same visual language as the board's failed-status red.
- **Delete confirmation.** Unchanged — still a plain `onsubmit="return
  confirm(...)"`, no new JS.
- **Focus/tap targets.** New `input[type="text"]` gets `min-height: 44px` and
  a visible `:focus` outline (`2px solid var(--accent)`), matching `.btn`'s
  existing 44px tap target and the board's accent color for interactive
  affordance.
- **Mobile meta parity.** The four admin templates currently ship no
  `<meta name="viewport">` at all, so none of `app.css`'s mobile-first rules
  (tabbar, safe-area insets, responsive breakpoints) actually engage on a
  phone today. §3.2 adds the same `<head>` meta block `board.html` has
  (viewport, theme-color ×2, apple-mobile-web-app) so the admin pages get the
  tabbar on phones and the appbar's link row on desktop, identically to the
  board.

## 3. Component design

### 3.1 Design tokens

No new tokens. Every rule below consumes the existing `:root` custom
properties (`app.css:12-41`, dark overrides `app.css:44-66`):

| Token | Light | Dark | Used by (new rules) |
|---|---|---|---|
| `--surface` | `#ffffff` | `#1c1c1e` | `.panel`, inputs |
| `--border` / `--border-strong` | `#e3e3e8` / `#d1d1d6` | `#38383a` / `#48484a` | `.panel`, input borders |
| `--text` / `--text-2` / `--text-3` | `#1c1c1e`/`#55555b`/`#8e8e93` | `#fff`/`#aeaeb4`/`#8e8e93` | field labels, hints |
| `--accent` | `#007aff` | `#0a84ff` | `.btn.primary`, focus ring |
| `--radius` / `--radius-sm` | `16px` / `11px` | same | `.panel` / inputs, banners |
| `--shadow` | see app.css:24 | see app.css:56 | `.panel` |
| `--done-bg`/`--done-fg` | `#e7f7ee`/`#1b7a43` | `#10361f`/`#57d98a` | `.banner.success`, `.update-result-ok` |
| `--changes-bg`/`--changes-fg` | `#fff3df`/`#9a5b00` | `#3d2c0a`/`#f0b45a` | `.banner.warning` |
| `--failed-bg`/`--failed-fg` | `#ffe8e5`/`#c0362c` | `#3d1512`/`#ff6f61` | `.banner.error`, `.field.error`, `.btn.danger`, `.update-result-error` |

This directly satisfies FR-2's acceptance rule: no new hardcoded colors, no
new tokens invented.

### 3.2 New `app.css` sections (additive — append after the existing `Desktop`/`reduced-motion` blocks, or wherever the file's section-comment convention prefers; nothing existing is edited)

```css
/* --- Forms (admin editors) ------------------------------------------------- */

.panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 18px 20px; max-width: 640px;
}
.panel.wide { max-width: 720px; }

.field { margin-bottom: 16px; }
.field label {
  display: block; font-size: 13px; font-weight: 600;
  color: var(--text-2); margin-bottom: 6px;
}

input[type="text"], textarea {
  width: 100%; box-sizing: border-box; font: inherit; font-size: 15px;
  padding: 10px 12px; border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong); background: var(--surface); color: var(--text);
}
input[type="text"] { min-height: 44px; }
textarea { min-height: 44px; resize: vertical; }
textarea.editor {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px; line-height: 1.5;
}
input[type="text"]:focus, textarea:focus {
  outline: 2px solid var(--accent); outline-offset: 1px;
}

.field.error input, .field.error textarea { border-color: var(--failed-fg); }
.field-error { color: var(--failed-fg); font-size: 12px; margin-top: 6px; }

.outcomes { display: flex; flex-wrap: wrap; gap: 4px 16px; }
.outcomes label {
  font-weight: 400; font-size: 14px; color: var(--text);
  display: inline-flex; align-items: center; gap: 6px; margin: 0;
}

.actions { margin-top: 20px; display: flex; gap: 10px; }

/* --- Banners ---------------------------------------------------------------- */

.banner {
  padding: 10px 14px; border-radius: var(--radius-sm);
  margin-top: 16px; font-size: 14px; max-width: 720px;
}
.banner.success { background: var(--done-bg); color: var(--done-fg); }
.banner.error   { background: var(--failed-bg); color: var(--failed-fg); white-space: pre-wrap; }
.banner.warning { background: var(--changes-bg); color: var(--changes-fg); }

/* --- Buttons: danger variant ------------------------------------------------- */

.btn.danger { color: var(--failed-fg); border-color: var(--failed-fg); background: var(--surface); }

/* --- Page header action slot ------------------------------------------------- */

.page-header__action { margin-left: auto; }

/* --- Update result (appbar "Update" button feedback) ------------------------- */

.update-result-ok { color: var(--done-fg); }
.update-result-error { color: var(--failed-fg); }
```

Rationale for the few markup-shape decisions baked into this CSS:

- **`.btn.danger`, not a bare `.danger` class** — mirrors the existing
  `.btn.primary`/`.btn.small` modifier pattern (`app.css:149-150`), so the
  danger button gets `.btn`'s sizing/font/44px tap target for free and only
  overrides color.
- **`.update-result-ok`/`.update-result-error`, no "muted" class** — the
  parent `#update-result` span (`_nav.html`) is already styled
  `color: var(--text-3); font-size: 12px` by `app.css:118`
  (`.appbar__actions #update-result`). The "already up to date" case needs no
  class at all; it inherits that muted color already. Only the two
  attention-worthy outcomes (ok/error) need an override.
- **`.outcomes` as its own flex row inside `.field`** — the original inline
  CSS applied spacing directly to `.field.outcomes label`; here the two
  checkboxes are wrapped in a nested `<div class="outcomes">` so the field's
  own `<label>Allowed outcomes</label>` (the field-level caption) stays
  visually distinct from the two checkbox option-labels. A markup addition,
  not a functional one — field name/values are unchanged.

### 3.3 Template markup — `admin/agents_list.html` / `admin/workflows_list.html`

Identical shape for both (`agents`/`workflows`, `name`/`name` swapped):

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#f8f8fa" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#141416" media="(prefers-color-scheme: dark)">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <title>harness board — agents</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  {% include "_nav.html" %}
  <main class="page">
    <div class="page-header">
      <h1>Agents</h1>
      <a class="btn small primary page-header__action" href="/admin/agents/new">+ New agent</a>
    </div>
    {% if names %}
    <div class="table-scroll">
      <table>
        {% for name in names %}
        <tr><td><a href="/admin/agents/{{ name }}">{{ name }}</a></td></tr>
        {% endfor %}
      </table>
    </div>
    {% else %}
    <p class="hint">No agents defined yet.</p>
    {% endif %}
  </main>
</body>
</html>
```

`workflows_list.html` is the same with `Agents`→`Workflows`, `agent`→`workflow`,
`/admin/agents*`→`/admin/workflows*`.

### 3.4 Template markup — `admin/agent_form.html`

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#f8f8fa" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#141416" media="(prefers-color-scheme: dark)">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <title>harness board — agent {{ name }}</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  {% include "_nav.html" %}
  <main class="page">
    <a class="hint" href="/admin/agents">&larr; back to agents</a>
    <div class="page-header">
      {% if is_new %}<h1>New agent</h1>{% else %}<h1>Agent: {{ name }}</h1>{% endif %}
    </div>
    <div class="panel">
      <form method="post" action="{{ '/admin/agents' if is_new else '/admin/agents/' + name }}">
        {% if is_new %}
        <div class="field {{ 'error' if errors.name }}">
          <label for="name">Name</label>
          <input type="text" id="name" name="name" value="{{ name }}" autofocus>
          {% if errors.name %}<div class="field-error">{{ errors.name }}</div>{% endif %}
        </div>
        {% endif %}

        <div class="field {{ 'error' if errors.prompt }}">
          <label for="prompt">Prompt *</label>
          <textarea id="prompt" name="prompt" rows="10">{{ prompt }}</textarea>
          {% if errors.prompt %}<div class="field-error">{{ errors.prompt }}</div>{% endif %}
        </div>

        <div class="field">
          <label for="model">Model</label>
          <input type="text" id="model" name="model" value="{{ model }}">
        </div>

        <div class="field">
          <label for="fallback_model">Fallback model</label>
          <input type="text" id="fallback_model" name="fallback_model" value="{{ fallback_model }}">
        </div>

        <div class="field {{ 'error' if errors.allowed_tools }}">
          <label for="allowed_tools">Allowed tools</label>
          <input type="text" id="allowed_tools" name="allowed_tools" value="{{ allowed_tools }}">
          <div class="hint">comma-separated</div>
          {% if errors.allowed_tools %}<div class="field-error">{{ errors.allowed_tools }}</div>{% endif %}
        </div>

        <div class="field {{ 'error' if errors.allowed_outcomes }}">
          <label>Allowed outcomes</label>
          <div class="outcomes">
            <label><input type="checkbox" name="allowed_outcomes" value="done" {{ 'checked' if 'done' in checked_outcomes }}> done</label>
            <label><input type="checkbox" name="allowed_outcomes" value="request_changes" {{ 'checked' if 'request_changes' in checked_outcomes }}> request_changes</label>
          </div>
          {% if errors.allowed_outcomes %}<div class="field-error">{{ errors.allowed_outcomes }}</div>{% endif %}
        </div>

        <div class="actions">
          <button class="btn primary" type="submit">Save</button>
        </div>
      </form>
      {% if not is_new %}
      <form method="post" action="/admin/agents/{{ name }}/delete"
            onsubmit="return confirm('Delete agent {{ name }}?');" class="actions">
        <button class="btn danger" type="submit">Delete</button>
      </form>
      {% endif %}
    </div>
    {% if saved %}
    <div class="banner success">saved — picked up on the next run, no restart</div>
    {% endif %}
  </main>
</body>
</html>
```

All field names, POST targets, and Jinja context variables (`errors`,
`checked_outcomes`, `is_new`, `saved`, `name`, `prompt`, `model`,
`fallback_model`, `allowed_tools`) are untouched, per the plan's non-functional
constraint.

### 3.5 Template markup — `admin/workflow_editor.html`

Same shell, `.panel wide`, single JSON field, three banner kinds:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#f8f8fa" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#141416" media="(prefers-color-scheme: dark)">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <title>harness board — workflow {{ name }}</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  {% include "_nav.html" %}
  <main class="page">
    <a class="hint" href="/admin/workflows">&larr; back to workflows</a>
    <div class="page-header">
      {% if is_new %}<h1>New workflow</h1>{% else %}<h1>Workflow: {{ name }}</h1>{% endif %}
    </div>
    <div class="panel wide">
      <form method="post" action="{{ '/admin/workflows' if is_new else '/admin/workflows/' + name }}">
        {% if is_new %}
        <div class="field">
          <label for="name">Name</label>
          <input type="text" id="name" name="name" value="{{ name }}" autofocus>
        </div>
        {% endif %}

        <div class="field">
          <label for="text">Definition (raw JSON)</label>
          <textarea class="editor" id="text" name="text" rows="20">{{ text }}</textarea>
        </div>

        <div class="actions">
          <button class="btn primary" type="submit">Save</button>
        </div>
      </form>
      {% if not is_new %}
      <form method="post" action="/admin/workflows/{{ name }}/delete"
            onsubmit="return confirm('Delete workflow {{ name }}?');" class="actions">
        <button class="btn danger" type="submit">Delete</button>
      </form>
      {% endif %}
    </div>
    {% if error %}
    <div class="banner error">{{ error }}</div>
    {% endif %}
    {% if saved %}
    <div class="banner success">saved</div>
    {% endif %}
    {% for warning in warnings %}
    <div class="banner warning">{{ warning }}</div>
    {% endfor %}
  </main>
</body>
</html>
```

### 3.6 `admin/_update_result.html` — in scope (resolves an Open Question)

This fragment is not admin-only: `_nav.html` includes the `#update-result`
target on **every** page, board included, so its three hardcoded hex colors
(`#bf2600`, `#006644`, `#5e6c84`) are a live inconsistency on the board today,
not just the admin pages. It is small enough that fixing it costs one line and
closes that gap:

```html
{#- Swapped into #update-result by the nav's Update button. Colour keyed by
    outcome: ok = version moved, error = it failed, otherwise muted (inherits
    #update-result's default color, no class needed). -#}
<span{% if kind == 'error' %} class="update-result-error"{% elif kind == 'ok' %} class="update-result-ok"{% endif %}>{{ message }}</span>
```

## 4. Data / schema

No database, request/response, or event-payload changes — this task is
templates + CSS only, and every route's context variables listed in §3.4/§3.5
are consumed unchanged. The only "schema" this task introduces is the CSS
component contract added to `app.css`, cataloged here for `docs/design/ui-guide.md`
to document in full:

| Selector | Role | Consumes tokens |
|---|---|---|
| `.panel` / `.panel.wide` | static (non-clickable) content container for a form, 640/720px | `--surface --border --radius --shadow` |
| `.field` / `.field.error` | form field wrapper + validation state | `--text-2 --failed-fg` |
| `.field-error` | inline validation message | `--failed-fg` |
| `input[type="text"]`, `textarea`, `textarea.editor` | text entry, monospace variant for JSON | `--surface --border-strong --text --accent` (focus) |
| `.outcomes` | checkbox option row | `--text` |
| `.actions` | form action button row | — (layout only) |
| `.banner` + `.success`/`.error`/`.warning` | inline feedback message | `--done-* --failed-* --changes-*` |
| `.btn.danger` | destructive-action button (modifier on existing `.btn`) | `--failed-fg --surface` |
| `.page-header__action` | right-aligns a button/link inside `.page-header` | — (layout only) |
| `.update-result-ok` / `.update-result-error` | appbar update-feedback color override | `--done-fg --failed-fg` |

### `docs/design/ui-guide.md` outline (for the dev step to author)

1. **Principles** — tokens → components → templates layering; the one
   enforcement rule: no template declares its own `<style>` or hex color.
2. **Tokens** — the table in §3.1 of this document, extended with the full
   `:root`/dark-mode value list already in `app.css:12-66`.
3. **Typography** — `h1`/`h2`/`h3` rules (`app.css:85-90`), body font stack,
   monospace usage (`ui-monospace, SFMono-Regular, Menlo, monospace`) for
   paths/code/JSON.
4. **Layout primitives** — `.page`/`.page-header`, `.panel`/`.panel.wide`,
   `.table-scroll`.
5. **Components** — `.btn` (`.primary`/`.danger`/`.small`), `.card` (board-only
   — explicitly note it is *not* for static content, per §1), `.field`/
   `.field-error`, `.banner` (3 variants), `.badge`, `.kv`.
6. **Navigation** — `_nav.html`'s appbar/tabbar, shared by every page.
7. **Usage rule** — every new page includes `/static/app.css` and nothing
   else; component classes only, no inline styles, no new hex colors.
