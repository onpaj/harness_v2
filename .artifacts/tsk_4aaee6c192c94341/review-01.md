# Review — colored repository badges on task cards

## Verdict: done

## What I checked

- Diff (`git show 7321d57`) against `design-01.md` and `plan-01.md`: the
  implementation matches the design's exact edit points — `_repo_hue` filter
  in `routes.py`, badge markup in `_columns.html`/`_task.html`, `.badge.repo-badge`
  CSS in `app.css` — with no deviation.
- **Acceptance criteria**, one by one:
  - Badge renders as a `.badge` pill (reuses the existing `.badge` base rule,
    only adds a `.repo-badge` color modifier) — met.
  - Color derived deterministically via `zlib.crc32(name) % 360`, not the
    salted builtin `hash()` — met, and correctly avoids the per-process-salt
    trap that would have flipped colors on every restart.
  - Distinct-hues-for-~dozen-repos: hash-based, so not a hard guarantee (I
    found one collision among 17 sample names), but that's an inherent,
    already-accepted tradeoff of a hash-derived-not-hand-authored palette —
    not a regression against the spec's "roughly up to a dozen" wording.
  - Legible in both themes: independently recomputed the WCAG relative-
    luminance contrast sweep over all 360 integer hues for both the light
    (`hsl(H 50% 92%)` bg / `hsl(H 70% 22%)` fg) and dark (`hsl(H 45% 22%)` bg
    / `hsl(H 70% 82%)` fg) pairs myself (not just trusting the design doc's
    numbers) — worst case is hue 60 at **6.03:1** light / **7.03:1** dark,
    both well clear of WCAG AA's 4.5:1. Matches `design-01.md` and the new
    `tests/test_repo_badge_contrast.py` exactly.
  - Short name (`basename`) still shown; worktree segment still renders,
    wrapped in its own `<span class="card__worktree">` with the old
    monospace/ellipsis styling moved onto it specifically — met.
  - No-repository card: `_repo_hue` returns `""` (not `"0"`) for falsy input,
    and both templates guard the badge span with `{% if task.repository %}`,
    so no empty/spurious badge is ever emitted — met, and covered by new
    tests (`test_card_with_no_repository_renders_no_badge`,
    `test_fragment_task_with_no_repository_shows_a_dash`).
  - View-only: no port/projection/router touched — confirmed by the diff
    (only `api/routes.py`, two templates, `app.css`, and tests changed).
  - `api/` imports no driver: unaffected — `zlib` is stdlib, no new imports
    into `dispatcher.py`/`consumer.py`; `tests/test_architecture.py` (27
    tests) passes.
- Ran the full suite fresh: `.venv/bin/pytest -q` → **1521 passed, 1 skipped**
  (the skip is the opt-in real-`claude` smoke, unrelated). No failures.
- Spot-checked the new unit tests (`test_repo_hue_filter.py`,
  `test_repo_badge_contrast.py`) and the new `test_api_html.py` assertions —
  they exercise real behavior (exact expected `--repo-hue` values, not
  tautologies) and passed in the run above, so the hardcoded hue values
  (`192` for `my-repo`, `57` for `app-backend`) are confirmed correct against
  the actual filter, not just asserted.
- Checked `--repo-hue` is consumed via modern space-separated `hsl(var(...) S% L%)`
  syntax (CSS Color 4), consistent with how the rest of `app.css` themes
  purely through `prefers-color-scheme` + custom properties — no new theming
  mechanism introduced.

## Notes (non-blocking)

- The hash-collision possibility for two repo names is inherent to any
  hash-to-hue scheme with a fixed 360-value range and was already an accepted
  tradeoff in the approved design; not a defect.

No functional requirement, architecture conflict, missing required test, or
correctness bug found. Approved as implemented.
