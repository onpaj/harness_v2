# Plan — colored repository badges on task cards

## Summary

Turn a task card's plain-grey `.card__repo` line into a colored pill badge,
consistent with the board's existing `.badge` vocabulary. The color is
computed deterministically from the repository name (hash → hue) so it needs
no per-repo configuration, stays stable across reloads, and works in both
light and dark themes.

## Context

The board already teaches the eye to read outcome via `.badge.done`,
`.badge.working`, `.badge.failed`, etc. — fixed enum, hand-authored palette.
Repository names aren't an enum (`repos.json` is operator-defined and grows),
so the same hand-authored approach doesn't scale: the color has to be
*derived* from the name itself, not assigned. This is a template + CSS +
one small filter change; no port, projection, or routing change is needed
(the router/dispatcher never read `repository` — invariant #8 — and this
stays true; `api/` still touches no driver — invariant #5/#33).

## Functional requirements

**FR-1 — Deterministic hue derivation from repository name.**
A pure function maps a repository name string to a hue in `[0, 360)`, via a
stable hash (e.g. Python's `zlib.crc32` or `hashlib.sha256` digest mod 360 —
not `hash()`, which is salted per-process by default and would make the color
flip on every server restart).
- Acceptance: given the same name, the function returns the same hue across
  repeated calls, across process restarts, and independent of dict/set
  ordering.
- Acceptance: two visibly different names generally land far enough apart in
  hue to read as distinct — verified informally by hashing every name in a
  sample `repos.json` (up to ~12) and checking no two adjacent hues are
  within ~15° of each other for that sample. (No algorithm guarantees this
  for arbitrary adversarial inputs; a hash is not required to *space* colors,
  only to be *stable* — see Open questions.)

**FR-2 — A Jinja filter (or template-local computation) exposes the hue.**
Following the existing `_basename`/`_shorttime` pattern in
`src/harness/api/routes.py`, add a filter (e.g. `repo_hue`) registered on
`TEMPLATES.env.filters`, so the template can write
`{{ task.repository | repo_hue }}` and get back an integer degree value (or
empty string when there's no repository).
- Acceptance: `_repo_hue(None)` / `_repo_hue("")` returns `""` (or some falsy
  sentinel) so the template's existing `{% if task.repository or
  task.worktree %}` guard is untouched and no empty badge is emitted.
- Acceptance: `_repo_hue("acme/widgets")` and `_repo_hue("widgets")` are
  intentionally allowed to differ (the filter hashes whatever string it's
  given — the *display* name, from FR-3's `basename`, is a separate
  question the implementer resolves per FR-3's note) — pin down which string
  is hashed in the plan's Open Questions.

**FR-3 — Card markup renders the repository as a `.badge`, colored via an
inline CSS custom property.**
In `templates/_columns.html`'s `board_column` macro, replace the plain
`.card__repo` div's repository segment with a `<span class="badge repo-badge"
style="--repo-hue: {{ task.repository | repo_hue }}">{{ task.repository |
basename }}</span>`, keeping the worktree segment (today's `· {{ task.worktree
| basename }}`) either inside or alongside the badge — implementer's call,
but the combined `task.repository or task.worktree` guard must still suppress
the whole line when both are empty (today's behavior for a repository-less
task, FR-6).
- Acceptance: a task with `repository` set gets a colored pill with the repo's
  basename as its text.
- Acceptance: a task with no `repository` but a `worktree` still renders the
  worktree text plainly (no badge, no hue) — matches current fallback
  behavior line-for-line.
- Acceptance: a task with neither renders nothing (no empty `.card__repo` div
  emitted) — this already works via the existing `{% if %}` guard and must
  not regress.

**FR-4 — CSS: a `.badge.repo-badge` (or equivalent) that derives background
and text color from `--repo-hue` via `hsl()`, honoring both themes.**
Add to `static/app.css`, near the existing `.badge` block (~line 211-219):
```css
.badge.repo-badge {
  background: hsl(var(--repo-hue) 60% 88%);
  color: hsl(var(--repo-hue) 70% 28%);
}
@media (prefers-color-scheme: dark) {
  .badge.repo-badge {
    background: hsl(var(--repo-hue) 45% 22%);
    color: hsl(var(--repo-hue) 70% 82%);
  }
}
```
(Exact L/S numbers are the implementer's call — must pass FR-5's contrast
check in both branches of the existing `@media (prefers-color-scheme: dark)`
block, i.e. two fixed lightness pairs, one per theme, not one that
"happens to work" via `currentColor` tricks.)
- Acceptance: badge renders with visibly tinted background and matching
  legible text color in a light-theme browser/OS and in a dark-theme one.
- Acceptance: the `::before` bullet dot inherited from `.badge` (`background:
  currentColor`) still renders in the derived text color, not left as a
  stale fixed color from another `.badge.*` variant.
- Acceptance: no hardcoded hex bypasses the two theme branches — the same
  hue variable, two different lightness pairs, mirroring how `--done-bg`/
  `--done-fg` etc. are set per theme today.

**FR-5 — Contrast check.**
For the chosen S/L pair in each theme, WCAG contrast ratio between background
and text color must be ≥ 4.5:1 across the full hue range (0–359°), not just
for a couple of sample hues — HSL lightness/saturation, unlike hex picks,
apply uniformly across hue so this is a one-time check of the two chosen
(S%, L%) pairs, not a per-repo check.
- Acceptance: compute (or spot-check with a contrast calculator) the ratio at
  a few representative hues (e.g. 0°, 60°, 120°, 180°, 240°, 300°) for both
  theme branches' chosen S/L — all pass ≥ 4.5:1.

**FR-6 — No regression to the repository-less card path.**
Unchanged from current behavior: a task with no `repository` and no
`worktree` shows nothing where `.card__repo` used to be.

**FR-7 — Reuse in the task detail dialog (`_task.html`).**
Apply the same badge treatment to the `repository` row in the info panel
(`_task.html:28`, currently `<span class="v">{{ task.repository or "—"
}}</span>`), reusing the same filter/CSS class so the color association is
learned once and stays consistent between the board and the detail view.
- Acceptance: opening a task's detail dialog shows the same repo name in the
  same hue as its card on the board.
- Acceptance: a task with no `repository` still renders `—` (the existing
  fallback), not an empty badge.

## Non-functional requirements

- **Performance**: the hash computation is O(len(name)) per render, negligible
  at board scale (tens of cards); no caching needed.
- **No JS dependency**: derivation happens server-side in the Jinja filter,
  so it keeps working with JS disabled and survives the SSE fragment swap
  for free (per the task notes) — this rules out a client-side hash-to-hue
  script as the primary mechanism, though nothing stops also computing it
  client-side later if a non-templated code path needs it.
- **No new external dependency**: use only stdlib (`zlib.crc32` or
  `hashlib`) for the hash — no new package for this.

## Data model

No changes. `task.repository` (already a plain string name, invariant #15)
is the only input. No new fields, no persistence change — this is a pure
presentational derivation at render time.

## Interfaces

- New Jinja filter `repo_hue` (or similar name), registered in
  `src/harness/api/routes.py` next to `_basename`/`_shorttime`, following
  the same defensive-on-falsy-input pattern.
- Template changes: `templates/_columns.html` (`board_column` macro's
  `.card__repo` block) and `templates/_task.html` (the `repository` `<li>`
  in the info panel).
- CSS changes: `static/app.css`, a new `.badge.repo-badge` rule plus its
  dark-theme override, sitting next to the existing `.badge.*` outcome
  rules and the `:root` / `prefers-color-scheme: dark` custom-property
  blocks already present.
- No HTTP endpoint, event, or port changes.

## Dependencies and scope

- Depends on: existing `.badge` CSS vocabulary, existing `basename` filter,
  existing theme custom-property split (`:root` vs
  `@media (prefers-color-scheme: dark)`).
- Out of scope (per task notes): operator-configurable per-repo colors;
  coloring anything other than the repository (outcome badges keep their own
  palette); the board repository *filter* (#137) — independent, no overlap
  in files beyond both touching board templates.
- No port/projection/routing change — confirmed by re-reading invariant #8
  and invariant #5/#33: this stays a template+CSS+filter change, `api/`
  imports no driver.

## Rough plan

1. Add `_repo_hue(value: str | None) -> str` to `routes.py`, hashing the
   input with `zlib.crc32(value.encode()) % 360`, returning `""` for
   falsy input; register as `TEMPLATES.env.filters["repo_hue"]`.
2. Update `_columns.html`'s card repo block to wrap the repository segment
   in `<span class="badge repo-badge" style="--repo-hue: {{ task.repository
   | repo_hue }}">{{ task.repository | basename }}</span>`, keeping the
   worktree segment's existing text and the `{% if task.repository or
   task.worktree %}` guard intact.
3. Update `_task.html`'s repository row to use the same badge span (falling
   back to plain `—` when `task.repository` is falsy, matching today).
4. Add `.badge.repo-badge` + its dark-theme override to `app.css`, near the
   existing `.badge` rules; remove or repurpose the now-superseded plain
   `.card__repo` color styling if it no longer applies to the repo text
   itself (it may still apply to the worktree segment's spacing/margin).
5. Manually verify: multiple repos in a fake board render distinct hues; a
   task with no repository is unaffected; toggle OS theme and check
   contrast; confirm the same repo shows the same color on card vs. detail
   dialog.
6. Run `.venv/bin/pytest -q` to confirm no existing template/architecture
   test breaks (in particular anything asserting on `_columns.html`/`_task.html`
   markup or on `api/`'s import set).

## Open questions

- **Which string does the filter hash — the raw `task.repository` or its
  `basename`?** Default: hash the raw `task.repository` value (matches what
  `RepositoryRegistry` actually distinguishes — two registry entries could in
  principle share a basename but differ in full name/path prefix, unlikely
  in practice but the raw value is the more correct identity to key color on).
  Implementer may hash the basename instead if that reads more intuitively
  for the common case; either satisfies the acceptance criteria as long as
  it's consistently the same value in both `_columns.html` and `_task.html`.
- **Exact hash function.** Default: `zlib.crc32` (stdlib, fast, stable across
  runs — Python's built-in `hash()` is deliberately excluded since it's
  randomized per-process for strings unless `PYTHONHASHSEED` is fixed).
- **Exact HSL saturation/lightness pair per theme.** Default values given in
  FR-4's sample CSS; the only hard requirement is passing the ≥4.5:1
  contrast check (FR-5) across the full hue range in both themes — the
  implementer may tune the numbers.
- **Worktree segment placement relative to the badge.** Default: keep the
  worktree text plain (outside/after the badge, following the current
  `·`-separated layout), since only the repository gets the colored-badge
  treatment per the acceptance criteria; badging the worktree too is
  explicitly not required and would conflict with "coloring anything other
  than the repository" being out of scope.
