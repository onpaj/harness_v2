# Development — colored repository badges on task cards

Implements `design-01.md` exactly: a stable, deterministic per-repository hue
rendered as a `.badge` pill on both the task card and the detail dialog,
consistent with the existing `.badge.done`/`.badge.working`/`.badge.failed`
vocabulary. View-only change — no port, projection, or routing touched
(invariants #5, #8, #33 all hold).

## Files changed

- **`src/harness/api/routes.py`**
  - Added `import zlib` (stdlib, alongside `html`/`json`/`re`).
  - Added `_repo_hue(value: str | None) -> str`, registered as the Jinja
    filter `repo_hue`, right after the `basename` filter registration —
    same pattern/placement as `_basename`/`_shorttime`. Derives
    `zlib.crc32(value.encode("utf-8")) % 360` (a decimal string `"0"`–`"359"`)
    from the raw `task.repository` name; falsy input passes through as `""`
    (not `"0"`), so a repository-less task never gets a spurious
    `--repo-hue` custom property. `zlib.crc32`, not the builtin `hash()`,
    because Python salts `hash()` for strings per-process — that would flip
    every badge's color on every server restart.

- **`src/harness/api/templates/_columns.html`**
  - The `.card__repo` block (inside `board_column`) now renders
    `task.repository` as `<span class="badge repo-badge" style="--repo-hue: {{ task.repository | repo_hue }}">{{ task.repository | basename }}</span>`
    instead of bare text, and wraps the worktree segment in
    `<span class="card__worktree">`. The `·` separator and both individual
    presence checks are unchanged — a worktree-only task (no repository)
    still renders identical plain text to before, with no badge and no
    separator.

- **`src/harness/api/templates/_task.html`**
  - The `repository` `<li>` in the info panel now renders the same badge
    (same filter, same basename display) when `task.repository` is set, and
    falls back to `—` exactly as before when it isn't.

- **`src/harness/api/static/app.css`**
  - `.card__repo` becomes a flex row (`display: flex; align-items: center;
    gap: 6px`); the monospace/ellipsis treatment that used to apply to the
    whole line now applies only to the new `.card__repo .card__worktree`
    child, so the badge itself isn't force-monospaced.
  - Added `.badge.repo-badge` (light) and a
    `@media (prefers-color-scheme: dark)` override, both reading color
    purely from the per-instance `--repo-hue` custom property:
    - light: `background: hsl(var(--repo-hue) 50% 92%)`,
      `color: hsl(var(--repo-hue) 70% 22%)`
    - dark: `background: hsl(var(--repo-hue) 45% 22%)`,
      `color: hsl(var(--repo-hue) 70% 82%)`
    No changes to `.badge`/`.badge::before` — the shared pill shape, padding
    and `currentColor` dot are inherited as-is.

## Tests added

- **`tests/test_repo_hue_filter.py`** — unit tests for `_repo_hue` directly
  (mirrors the existing `tests/test_shorttime_filter.py` per-filter pattern):
  `None`/`""` pass through as `""`; a real name yields the expected
  `crc32 % 360` value in `[0, 360)`; same input is always the same output;
  two different repo names yield different hues; a full registry-style path
  (`/Users/x/repos/my-repo`) yields a different hue than its bare basename
  (`my-repo`) — confirms the filter is fed the raw repository string, not
  its basename, as the design specifies.

- **`tests/test_repo_badge_contrast.py`** — a computed WCAG contrast sweep
  over every integer hue 0–359° for both the light and dark
  `.badge.repo-badge` background/text HSL pairs (hand-kept in sync with
  `app.css`'s literal values), using the standard sRGB relative-luminance
  formula. Asserts the worst-case ratio in each theme is still ≥ 4.5:1
  (WCAG AA). This turns design-01.md's one-off verification script into a
  regression test: if either HSL pair is later tweaked without re-checking
  contrast, this test catches a regression at any hue, not just the
  originally-sampled ones.

- **`tests/test_api_html.py`** — added to the existing fragment-rendering
  suite:
  - `test_card_renders_repository_as_a_badge_with_a_derived_hue` — the
    `TITLED` fixture's card (`/Users/x/repos/my-repo`) renders the exact
    expected `<span class="badge repo-badge" style="--repo-hue: 192">my-repo</span>`.
  - `test_card_with_no_repository_renders_no_badge` — the `WAITING` fixture
    (no repository, no worktree) renders neither `repo-badge` nor
    `card__repo` in its own card slice.
  - `test_fragment_task_shows_repository_as_a_badge_with_same_hue_as_the_card`
    — the detail dialog for `tsk_1` (`app-backend`) renders
    `<span class="badge repo-badge" style="--repo-hue: 57">app-backend</span>`
    — same hue-derivation input and same basename display as the card, so
    the color association is visibly the same object across both surfaces.
  - `test_fragment_task_with_no_repository_shows_a_dash` — `tsk_4`
    (`WORKFLOW_LESS`, no repository) still shows `—`, no badge markup.
  - Pre-existing `test_card_shows_repo_and_worktree_basename_not_path` and
    `test_fragment_task_shows_metadata_and_history` pass unmodified, exactly
    as design-01.md predicted (basename text still appears verbatim, now
    inside a `<span>`; the full path is never interpolated).

## Verification

```
.venv/bin/pytest -q
```

Full suite: **1521 passed, 1 skipped** (the skip is the opt-in
`HARNESS_SMOKE_CLAUDE=1`-gated real-`claude` smoke, unrelated to this
change). `tests/test_architecture.py` (the driver-import / no-branching
guards) passes unchanged — no new imports into `dispatcher.py`/`consumer.py`,
`api/` still imports no driver.

To see it manually: `harness serve` (or any run exposing the board), open a
column with tasks across more than one registered repository — each
repository now renders as a distinct-colored pill badge, stable across page
reloads, columns and workflow tabs, and the same color reappears in a task's
detail dialog under "repository". A task with no repository is unchanged
(plain `—` in the dialog, no badge/line on the card).
