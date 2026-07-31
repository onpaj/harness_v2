# Design — colored repository badges on task cards

Resolves the open questions left by `plan-01.md` with concrete values and
exact edit points. No port/projection/routing change; `api/` still imports
no driver (invariants #5/#8/#33 untouched).

## UX/UI

### Card, today

```
┌────────────────────────────────────┐
│ Fix the login bug                  │
│ my-repo · wt_sacramento            │  ← plain grey monospace line
│ [working]                  Jul 22  │
└────────────────────────────────────┘
```

### Card, after

```
┌────────────────────────────────────┐
│ Fix the login bug                  │
│ ╭──────────╮ wt_sacramento         │  ← pill badge (hue from "my-repo"),
│ │ my-repo  │                       │    worktree stays plain monospace
│ ╰──────────╯                      │
│ [working]                  Jul 22  │
└────────────────────────────────────┘
```

- Two repos on the board (`app-backend`, `my-repo`) render two visibly
  different pill colors, stable across columns, workflow tabs and reloads,
  because the color is a pure function of the repository string.
- A task with `worktree` but no `repository` is unchanged: plain text, no
  badge (nothing to derive a color from).
- A task with neither renders nothing where the line used to be (unchanged).

### Task detail dialog, today

```
repository   /Users/x/repos/my-repo
worktree     /Users/x/worktrees/wt_sacramento
```

### Task detail dialog, after

```
repository   ╭──────────╮
             │ my-repo  │   ← same hue as the card badge
             ╰──────────╯
worktree     /Users/x/worktrees/wt_sacramento
```

Only the `repository` row changes (to the same badge, same hue-derivation
input, same basename display text as the card). `worktree`'s row is untouched
— badging is scoped to the repository only, per the task's "out of scope."
A task with no `repository` keeps showing `—`.

### Component hierarchy / interactions

No new interactive behavior — the badge is inert markup, not a control.
It rides the two existing render paths unchanged:
- `GET /fragment/board` → `_columns.html` (`board_column` macro) → per-card
  `.card__repo` block.
- `GET /fragment/task/{id}` → `_task.html` → the `repository` `<li>`.

Both are server-rendered Jinja fragments (HTMX swaps `innerHTML`, SSE-driven
board refresh re-renders the same macro), so the badge is present the moment
either fragment is served — no client JS, no FOUC, no extra request.

## Component design

### 1. `_repo_hue` filter — `src/harness/api/routes.py`

Sits next to `_basename`/`_shorttime` (same file, same registration pattern:
a small defensive function + a `TEMPLATES.env.filters[...]` line). Add
`import zlib` to the existing import block (stdlib, alphabetical with `html`,
`json`, `re`).

```python
def _repo_hue(value: str | None) -> str:
    """Deterministic hue in [0, 360) for a repository name, as a plain
    decimal string ready for a CSS custom property. `zlib.crc32`, not the
    builtin `hash()` — the latter is salted per-process for strings, which
    would flip every badge's color on each server restart.

    Falsy input passes through as "" (not "0") so a template checking
    `{% if task.repository %}` never emits a badge span with an empty-but-
    present --repo-hue for a repository-less task.
    """
    if not value:
        return ""
    return str(zlib.crc32(value.encode("utf-8")) % 360)


TEMPLATES.env.filters["repo_hue"] = _repo_hue
```

**Contract:** input is `task.repository`'s raw string (the registry name,
invariant #15) — not its `basename`. Two registry entries that happened to
share a basename but differ in the full name still get independent colors;
this is the more correct identity to key on, and it's what both call sites
(card + detail dialog) pass, so they can never disagree. Output is either
`""` or a decimal integer string in `"0"`..`"359"` — safe to interpolate
directly into an inline `style="--repo-hue: {{ ... }}"` attribute with no
further escaping (Jinja autoescaping still applies to the surrounding HTML
attribute as normal; the value itself is guaranteed pure `[0-9]` by
construction, never attacker-influenced markup, since a repository name is
operator-authored `repos.json` config, not end-user input).

### 2. Card markup — `src/harness/api/templates/_columns.html`

Replace lines 26–30 (the `.card__repo` block) with:

```html
{% if task.repository or task.worktree %}
<div class="card__repo">
  {% if task.repository %}<span class="badge repo-badge" style="--repo-hue: {{ task.repository | repo_hue }}">{{ task.repository | basename }}</span>{% endif %}{% if task.repository and task.worktree %} · {% endif %}{% if task.worktree %}<span class="card__worktree">{{ task.worktree | basename }}</span>{% endif %}
</div>
{% endif %}
```

- Outer `{% if %}` guard is untouched (FR-6 — no regression for a
  repository-less, worktree-less task).
- `task.repository` alone → badge only, no `·` separator.
- `task.worktree` alone (no `repository`) → plain `.card__worktree` text
  only, no badge, no separator — identical text output to today.
- Both present → badge, ` · `, plain worktree text — same visual grouping
  as today, just with the repository segment now a pill.
- `test_card_shows_repo_and_worktree_basename_not_path` keeps passing
  unmodified: `"my-repo"` and `"wt_sacramento"` still appear verbatim in the
  fragment body (now inside a `<span>` instead of bare text), and the full
  path `"/Users/x/"` still never appears (only `basename` output is ever
  interpolated, same as before).

### 3. Detail dialog markup — `src/harness/api/templates/_task.html`

Replace line 28:

```html
<li><span class="k">repository</span><span class="v">{{ task.repository or "—" }}</span></li>
```

with:

```html
<li><span class="k">repository</span><span class="v">{% if task.repository %}<span class="badge repo-badge" style="--repo-hue: {{ task.repository | repo_hue }}">{{ task.repository | basename }}</span>{% else %}—{% endif %}</span></li>
```

- `task.repository` set → same badge, same hue, same basename text as the
  card (FR-7 — color association learned once).
- `task.repository` unset → `—`, unchanged from today.
- `test_fragment_task_shows_metadata_and_history`'s `assert "app-backend" in
  body` keeps passing (repository `"app-backend"` has no slash, so
  `basename` is a no-op on it and the substring still appears, now inside
  the badge span).

### 4. CSS — `src/harness/api/static/app.css`

**a. Split `.card__repo`'s two segments.** Lines 203–207 currently style the
whole repo+worktree text as one monospace grey line; now only the worktree
segment (plain text) should keep that treatment, while the container becomes
a flex row so the badge and the worktree text sit on one line:

```css
.card__repo {
  display: flex; align-items: center; gap: 6px; flex-wrap: nowrap;
  color: var(--text-2); font-size: 13px; margin-top: 3px;
  overflow: hidden;
}
.card__repo .card__worktree {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
```

**b. Add `.badge.repo-badge`, right after the existing `.badge.*` outcome
rules (after line 219)** — same selector shape as `.badge.done` /
`.badge.working` / `.badge.failed`, but reading its color from the
per-instance `--repo-hue` custom property instead of a fixed `var(--*-fg)`:

```css
.badge.repo-badge {
  background: hsl(var(--repo-hue) 50% 92%);
  color: hsl(var(--repo-hue) 70% 22%);
}
@media (prefers-color-scheme: dark) {
  .badge.repo-badge {
    background: hsl(var(--repo-hue) 45% 22%);
    color: hsl(var(--repo-hue) 70% 82%);
  }
}
```

No change needed to `.badge` or `.badge::before` — the inherited flex
layout, padding, pill radius and `background: currentColor` bullet dot all
apply as-is and the dot correctly follows `repo-badge`'s own derived text
color, exactly as it already does for `.badge.done` etc.

**Contrast, verified (not estimated).** Computed WCAG relative-luminance
contrast ratio between the chosen background/text HSL pair, swept across
every integer hue 0–359° (script in the Verification section below):

| Theme | background | text | worst-case ratio (hue) | threshold |
|---|---|---|---|---|
| Light | `hsl(H 50% 92%)` | `hsl(H 70% 22%)` | **6.03:1** (hue 60, yellow — always the tightest hue for both pairs since it's the highest-luminance hue at fixed S/L) | ≥ 4.5:1 |
| Dark | `hsl(H 45% 22%)` | `hsl(H 70% 82%)` | **7.03:1** (hue 60) | ≥ 4.5:1 |

Both pairs clear WCAG AA (4.5:1) with margin across the full hue range, not
just at sampled points — satisfies FR-5 exactly (a lightness/saturation pair
applies uniformly across hue, so this one-time sweep stands in for "every
possible repository name").

## Data schema / interfaces

No wire format, DB schema, or event payload changes — this is a pure
render-time derivation with no new persisted or transmitted shape. The only
"schema" introduced is the filter's own input/output contract, already
specified in full above:

```
repo_hue: str | None -> str      # "" | "0".."359"
```

consumed solely as an inline CSS custom property (`--repo-hue`) scoped to
one `<span>` per badge instance — no global CSS variable, no JS, no server
state keyed by repository (the hash is recomputed per render, which is fine
at board scale — negligible cost, same reasoning as `plan-01.md`'s NFR).

## Verification

- Contrast sweep (light and dark pairs above) run via a small standalone
  script (`colorsys.hls_to_rgb` + the standard WCAG relative-luminance
  formula), confirming ≥ 4.5:1 at every integer hue 0–359° for both pairs —
  this is a one-off design check, not a new test to add to the suite (no
  existing test asserts on computed color values; template tests assert on
  text content, per the existing pattern in `tests/test_api_html.py`).
- Existing tests that must keep passing unmodified, confirmed by re-reading
  them against the markup above: `test_card_shows_repo_and_worktree_basename_not_path`,
  `test_fragment_task_shows_metadata_and_history`, and every other
  `test_api_html.py` case that doesn't touch `.card__repo`/`repository`
  markup (`test_architecture.py`'s driver-import checks are untouched since
  no import changes anywhere).
- `.venv/bin/pytest -q` to run before landing, per `plan-01.md`'s rough
  plan step 6 — unaffected by this design step (no code written yet).

## Open questions from `plan-01.md`, now resolved

- **Which string is hashed:** the raw `task.repository` value (not its
  basename) — §"Component design, 1" above.
- **Hash function:** `zlib.crc32(value.encode("utf-8")) % 360` — stdlib,
  stable across restarts.
- **HSL saturation/lightness pair per theme:** light `50%/92%` bg,
  `70%/22%` fg; dark `45%/22%` bg, `70%/82%` fg — both verified ≥ 4.5:1
  across the full hue range.
- **Worktree segment placement:** stays plain text, after the badge,
  separated by ` · ` when both are present — unchanged from today's visual
  grouping, only the repository segment becomes a pill.
