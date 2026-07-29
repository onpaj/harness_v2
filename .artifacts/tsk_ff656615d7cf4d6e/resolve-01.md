# Resolve merge conflict on PR #143

## Conflict

Merging `origin/main` into the `board-ui` branch produced a single conflicted
file: `src/harness/api/templates/_columns.html`.

Both sides touched the same region of the template for different reasons:

- **`origin/main`** refactored column rendering into a shared `board_column(col)`
  macro and grouped columns into three labelled sections (Waiting / `<workflow>`
  workflow / Finished) per `docs/design/ui-guide.md`'s column-kind grouping.
  The macro (already present above the conflict, lines 8–67) still had the
  *pre*-repo-badge `card__repo` markup (plain basename text, no badge).
- **`board-ui` (HEAD)** added the per-repo hue badge feature (`repo_hue` Jinja
  filter, `.badge.repo-badge` CSS) on top of the *old*, ungrouped per-column
  markup — duplicating the card rendering inline inside the "inbox" section
  instead of going through a macro (which didn't exist yet on this branch).

So the two branches disagreed both on *where* card markup lives (macro vs.
inline) and on *what* the repo/worktree markup looks like (badge vs. plain
text).

## Resolution

1. Kept `origin/main`'s macro-based, grouped-by-kind structure (`board_column`,
   the Waiting/`<workflow>` workflow/Finished sections) — it's the newer,
   shared rendering path and every column (inbox, step, terminal) goes through
   it.
2. Ported the repo-hue badge markup from HEAD's inline block into the
   `board_column` macro's `card__repo` div, replacing the plain-text
   basenames:
   ```
   {% if task.repository %}<span class="badge repo-badge" style="--repo-hue: {{ task.repository | repo_hue }}">{{ task.repository | basename }}</span>{% endif %}{% if task.repository and task.worktree %} · {% endif %}{% if task.worktree %}<span class="card__worktree">{{ task.worktree | basename }}</span>{% endif %}
   ```
   This is byte-for-byte what `tests/test_api_html.py`'s
   `test_card_renders_repository_as_a_badge_with_a_derived_hue` and the
   already-merged `static/app.css` (`.badge.repo-badge`, `.card__repo
   .card__worktree`) expect.
3. Deleted HEAD's duplicated inline per-task loop entirely (it was fully
   superseded by the macro, now applied uniformly to every column) and removed
   all conflict markers.

Since the badge now lives in the one shared macro, it renders identically in
every column kind (inbox/step/terminal), not just the inbox section HEAD's
inline code happened to touch.

## Verification

- `grep -n '<<<<<<<\|=======\|>>>>>>>' src/harness/api/templates/_columns.html`
  — no markers remain.
- Full suite: `1684 passed, 1 skipped` (the skip is the opt-in
  `HARNESS_SMOKE_CLAUDE` smoke test, expected without a live `claude` call).
- Targeted re-run of `tests/test_api_html.py`, `tests/test_repo_hue_filter.py`,
  `tests/test_repo_badge_contrast.py`, `tests/test_architecture.py` — all pass,
  including the exact-badge-markup and repo/worktree-basename assertions and
  the port/driver-import architecture checks.
- `git add` the resolved file; `git status` shows no unmerged paths remaining.
