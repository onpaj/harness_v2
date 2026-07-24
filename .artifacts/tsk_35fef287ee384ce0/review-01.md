# Review — unify admin UI with the dashboard design system

## Verdict: done

## What was checked

1. **Spec conformance.** All six acceptance criteria from the task are met:
   - `docs/design/ui-guide.md` exists and covers tokens, typography, layout
     primitives, components (with usage rules) and navigation. Spot-checked
     every token value in the guide's §2 table against the real `app.css`
     `:root`/dark-mode blocks (`app.css:12-67`) — all correct, no invented
     tokens.
   - New component CSS (`.panel`, `.field`, inputs/textarea, `.outcomes`,
     `.actions`, `.banner` + 3 variants, `.btn.danger`, `.page-header__action`,
     `.update-result-*`) lives solely in `app.css`, not duplicated per
     template.
   - All four admin templates + `admin/_update_result.html` are restyled onto
     the shared stylesheet. Verified with `grep -n "<style"` (no hits) and
     `grep -nE "#[0-9a-fA-F]{3,6}"` (only hits are the `<meta theme-color>`
     tags, which byte-for-byte match `board.html`'s own head block — not a
     CSS hex leak).
   - Dark mode: every new rule is `var(--...)`-driven, confirmed by reading
     the appended CSS block directly — no new dark-mode-specific rule was
     needed or added.
   - Nav/shell parity: admin pages now use `_nav.html` + `<main class="page">`
     + `.page-header`, identical to `board.html`'s shell.
   - No board regression: `git diff origin/main -- src/harness/api/static/app.css`
     is 64 insertion-only lines appended at the end of the file; `board.html`
     and `_nav.html` are untouched (`git diff origin/main` on both is empty).

2. **Architecture/invariants.** This task touches only `api/templates` and
   `api/static` — no dispatcher/consumer/router/port changes, so none of the
   repo's numbered invariants are implicated. Not applicable here.

3. **Correctness.** Diffed all four templates plus `_update_result.html`
   against `design-01.md`'s prescribed markup (§3.3–3.6) — matches verbatim,
   including preserved field names, POST targets, and Jinja context
   variables. The `_update_result.html` change (hex → class) is correctly
   scoped: it's rendered via `_nav.html` on *every* page including the board,
   and removing its hardcoded colors is a pure win, not scope creep, since
   `_nav.html` itself was flagged in-scope by prior steps.

4. **Test coverage.** Ran `.venv/bin/pytest -q`: 1022 passed, 1 skipped — full
   suite green. The two `test_api_update.py` assertion updates (hex →
   `class="update-result-ok/error"`) are mechanical, tracking the intentional
   markup change; they preserve the same behavioral assertions (ok gets a
   marker, muted case doesn't, error gets a marker) rather than weakening
   coverage.

No functional requirement is unmet, no architecture conflict, no missing
required test, no correctness bug found.

## Non-binding notes

- None — the implementation is a clean, verbatim execution of design-01.md.
