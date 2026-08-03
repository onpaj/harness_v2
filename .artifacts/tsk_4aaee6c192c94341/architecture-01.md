# Architecture review — colored repository badges on task cards

## Verdict

**Approved as designed.** `design-01.md` (and the `plan-01.md` it refines) is
a template + CSS + one pure Jinja filter change, entirely inside `api/`. It
does not touch a port, the projection, the router or the dispatcher, and it
introduces no new dependency. Verified line-for-line against the current
source, not just against the design's own prose.

## Alignment with existing patterns and invariants

Checked against the actual files, not assumed:

- **`src/harness/api/routes.py`** (read in full around the filter block):
  `_basename`/`_shorttime` are small defensive functions registered via
  `TEMPLATES.env.filters["name"] = _fn`. The proposed `_repo_hue` follows the
  identical shape — same file, same registration idiom, same
  falsy-in/falsy-out defensiveness. `import zlib` added to the existing
  stdlib import block (`html`, `json`, `re`) is consistent style; no new
  third-party dependency, matching the plan's NFR.
- **`_columns.html`** (read in full): the `.card__repo` block the design
  targets is exactly lines 26–30 as claimed, including the
  `{% if task.repository or task.worktree %}` guard the design keeps
  unmodified. The proposed replacement preserves that guard verbatim and
  only restructures what's *inside* it.
- **`_task.html`** (read in full): line 28 is exactly the
  `{{ task.repository or "—" }}` span the design targets, unchanged
  everywhere else.
- **`app.css`** (read the relevant regions): `.card__repo` (line 203),
  `.badge`/`.badge::before`/`.badge.done`/`.badge.request_changes`/
  `.badge.working`/`.badge.failed` (lines 211–219), and the
  `:root` / `@media (prefers-color-scheme: dark)` custom-property split
  (lines 12, 49–50) are exactly where the design says. The proposed
  `.badge.repo-badge` sits as a new sibling of `.badge.done` etc., reusing
  `.badge`'s shared layout/pill/`::before` styling and only supplying its own
  `background`/`color`, which is the same shape every existing outcome
  variant already uses — no new visual mechanism introduced.
- **Existing tests re-verified, not just cited:**
  `test_card_shows_repo_and_worktree_basename_not_path` (asserts
  `"my-repo"` and `"wt_sacramento"` present, `"/Users/x/"` absent) and
  `test_fragment_task_shows_metadata_and_history` (asserts `"app-backend"`
  present) both keep passing under the proposed markup — the basename text
  still appears verbatim, now one level deeper inside a `<span>`, and no full
  path is ever interpolated (only `| basename` output is used, unchanged from
  today).
- **Invariant #8** (router/dispatcher decide only on `(status, lastOutcome)`
  and workflow presence, never on `repository`/`worktree`/`step`/`data`):
  untouched — this change is entirely inside two Jinja templates and a CSS
  file plus a pure string→string filter that touches nothing but
  `task.repository`'s display. No code path here is reachable from
  `router.py`/`dispatcher.py`.
- **Invariant #5/#33** (`api/` imports no driver): `_repo_hue` uses only
  stdlib `zlib`; nothing under `drivers/` is imported. Confirmed against
  `test_api_does_not_import_drivers` (`tests/test_architecture.py:361`),
  which this change does not challenge — no new import statement in
  `routes.py` reaches outside stdlib/`ports`/`models`.
- **No projection change**: `task.repository` already reaches the template
  today; this is a pure rendering change of an existing field, not a new one.

## Proposed architecture (as specified by design-01.md)

1. **Derivation** — `_repo_hue(value: str | None) -> str` in `routes.py`,
   `zlib.crc32(value.encode()) % 360`, `""` on falsy input. Correctly
   rejected: Python's built-in `hash()` (per-process salt for strings) as
   unsuitable for a value that must be stable across server restarts and
   independent processes — this is the right call and the only one of the
   two stdlib options that satisfies the stated requirement.
2. **Exposure** — registered as `TEMPLATES.env.filters["repo_hue"]`, mirroring
   `basename`/`shorttime` exactly; no new port or capability, just a template
   convenience function, same trust boundary as the existing filters.
3. **Markup** — the repository segment becomes
   `<span class="badge repo-badge" style="--repo-hue: {{ ... }}">`, wrapping
   the same `| basename` text already shown today; the worktree segment stays
   plain text. Both `_columns.html` and `_task.html` use the identical filter
   call on the identical input (`task.repository`, not its basename) so the
   two views can never disagree on a repo's color — this consistency
   guarantee is exactly what FR-7 asks for and is achieved by construction
   (same input, same pure function), not by convention.
4. **Styling** — `.badge.repo-badge` derives `background`/`color` from
   `hsl(var(--repo-hue) S% L%)` with one fixed `(S, L)` pair per theme
   branch (light vs. `prefers-color-scheme: dark`), the same "hue varies,
   lightness/saturation fixed per theme" trick that makes a *single* CSS rule
   correct for every possible hash output — no per-repo CSS is ever written,
   which is the actual mechanism that satisfies "a newly registered repo
   needs no CSS or config change."

## Key decisions and rationale

- **Hash the raw `task.repository`, not its basename.** Correct default:
  `RepositoryRegistry` (invariant #15) keys repos by name, and two entries
  could in principle share a basename. Hashing the full name is the more
  correct identity and costs nothing since both call sites already have that
  string in scope.
- **Server-side derivation (Jinja filter) over a client-side script.**
  Correct given the module map's own framing of `api/` as
  server-rendered Jinja fragments swapped by HTMX/SSE — a client-side hash
  would need to re-run on every SSE fragment swap and would break with JS
  disabled, for zero benefit (the computation is a single `crc32` call, not a
  reason to move logic client-side).
- **Fixed lightness/saturation pair per theme, hue as the only free
  variable.** This is what makes contrast a one-time, hue-independent proof
  rather than a per-repository judgment call — verified below.

## Verification performed in this review

The design claims specific worst-case contrast ratios (6.03:1 light,
7.03:1 dark) computed by a sweep script rather than a couple of spot checks.
Re-ran that computation independently to confirm the claim rather than take
it on faith:

```
worst light ratio: 6.03 @ hue 60
worst dark  ratio: 7.03 @ hue 60
```

Both clear WCAG AA (4.5:1) with real margin, confirming FR-5. Hue 60
(yellow) is the correct worst case for both pairs, since at fixed S/L it's
the highest-luminance hue in HSL space — the design's explanation for *why*
that's the tightest point is accurate, not just the number.

No implementation exists yet on this branch (confirmed:
`git status`/current tree still has the original plain `.card__repo` markup
and no `repo_hue` filter), so there is nothing further to verify by running
the test suite at this step — that is the next step's job, per
`plan-01.md`'s rough-plan step 6 and `design-01.md`'s own verification
section.

## Risks and mitigations

- **Modern `hsl(H S% L%)` space-separated syntax** (CSS Color 4, no commas)
  is new to this stylesheet — every existing color function in `app.css` is
  `rgba(...)` with commas. Not a real risk: this is a locally-served
  operator dashboard (no cross-browser/legacy-support requirement is stated
  anywhere in the docs), and the syntax is supported by all current
  evergreen browsers. No mitigation needed, but flagging it since it's a
  "first of its kind" in this file.
- **`--repo-hue` as a bare unitless number.** `hsl()` interprets a unitless
  hue argument as degrees per spec — correct as designed, no `deg` suffix
  needed and none should be added.
- **Adjacent-hue readability for ~12 repos** is explicitly *not* guaranteed
  by construction (a hash doesn't space colors) and the plan says so plainly
  in its own open-questions/FR-1 acceptance note. This is an accepted,
  documented risk rather than a gap — a fixed-per-name hash is what "no
  config, no per-repo assignment" requires, and the acceptance criteria only
  ask for "roughly up to a dozen" to read as distinct, not a guarantee for
  adversarial inputs. No architectural mitigation is warranted; if a future
  need arises for guaranteed separation, that would be a deliberate new
  requirement (e.g. golden-angle hue spacing keyed by registration order),
  out of scope here per the task notes.

## Prerequisites before implementation begins

None outstanding. The design is fully resolved (all of `plan-01.md`'s open
questions have concrete answers in `design-01.md`), verified against the
current state of every file it touches, and requires no new port, driver,
dependency, or test-suite structural change. Implementation may proceed
directly per `design-01.md`'s "Component design" sections 1–4.
