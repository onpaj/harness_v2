# Architecture — Mobile-first board and admin UI redesign

Input: `plan-01.md` (scope correction: admin agents/workflows UI dropped — it
doesn't exist in this repo) and `design-01.md` (wireframes, component
hierarchy, CSS tokens, filter spec). Both are already unusually complete for
this stage. This document is the go/no-go check against the actual code
(`src/harness/api/routes.py`, `app.py`, `templates/board.html`,
`_columns.html`, `_task.html`, `tests/test_api_html.py`,
`tests/test_architecture.py`) plus the file-level sequencing and risk list
the development step needs. It does not repeat the design's wireframes or
token table — read `design-01.md` alongside this.

## 1. Alignment with existing patterns and integration points

I read the live files, not just the plan/design's paraphrase of them. Findings:

- **The scope cut is correct and necessary, not optional.** `src/harness/api/templates/`
  contains exactly `board.html`, `_columns.html`, `_task.html` — no `admin/`
  directory, no agents/workflows routes, no such ports. `tests/test_api_agents.py`
  and `tests/test_api_workflows.py` do not exist anywhere under `tests/`. Building
  toward them here would mean inventing ports and routes inside a task whose own
  brief says "no port, model, or driver changes" — a direct contradiction. The
  plan's decision to scope to board + task-detail only is the only reading of the
  brief that doesn't violate itself, and I am carrying it forward unchanged.
- **`routes.py` today registers exactly one filter** (`_basename`, routes.py:23-34),
  tolerant of `None`/no-slash input, installed via `TEMPLATES.env.filters[...]`.
  `shorttime` (design §3.2) is a drop-in sibling of the same shape — same file,
  same registration pattern, same defensive-on-bad-input contract. No new import,
  no new context passed into any `TemplateResponse` call (routes.py:119-135 — the
  three call sites already pass exactly `{"board": ...}` or `{"task": ..., "artifacts": ...}`;
  the design's nav item list and status→color mapping must be computed in the
  template, not routes.py, or FR-5's "one routes.py change" boundary breaks).
- **`test_api_does_not_import_drivers`** (`test_architecture.py:205`) walks every
  `.py` under `api/` via AST import inspection — it says nothing about templates
  or CSS. Since this task adds zero `.py` files to `api/` (only `app.css`,
  `_nav.html`, restyled templates, and the one filter function), invariant #5/#11
  is satisfied by construction; there is nothing to check at runtime beyond "don't
  add a new `.py` file that imports something from `drivers/`."
- **`create_app`'s optional-port pattern must keep working.** `app.py` wires
  `_EmptyArtifactView`/`_EmptyStageOutputView`/`_NullTaskControl` as fallbacks
  when a board is built without those ports (app.py:19-50). The redesigned
  `_task.html` still renders with an empty artifacts tuple and a `status!="failed"`
  task and must not error — verify this explicitly since the new detail sheet
  touches every field these fallbacks produce.
- **The exact test-pinned contract is narrower than the plan implies in one spot.**
  `test_card_shows_time_in_state_only_when_history_exists` (test_api_html.py:144-149)
  asserts the literal substring `"since"` is **absent** from a card with no
  history — it does not assert `"since"` is present when history exists, and it
  does not care what class name backs the time element. This means design-01.md's
  `card__time` class (design §3.2) is safe to use instead of keeping a literal
  `class="since"` — I confirmed this so development doesn't second-guess it or
  keep a needless `since` class name "just in case." The only hard constraints
  are: (a) the raw ISO string is present in the response body when history
  exists, and (b) no time element renders at all when history is empty.
- **`_columns.html`/`_task.html` render fragments standalone** (routes.py:131-139)
  as well as via `{% include %}` from `board.html`. Any CSS class introduced by
  the redesign must resolve from the single shared `app.css`, since these
  fragments carry no `<head>`/`<link>` of their own — confirming FR-1's "linked
  from every page" must actually mean "linked once, from `board.html`, since the
  fragments never render outside that page's DOM." No fragment template should
  gain its own `<style>` or `<link>`.
- **Reference branch is guidance only, confirmed non-mergeable.** I did not
  re-diff it — the plan already established it's ~250 files / ~38k lines ahead
  with unrelated features (self-healing, docs site, admin CRUD). Development
  should read its `app.css`/`_nav.html`/`_task.html` for markup/CSS ideas but
  never attempt a merge or cherry-pick from it.

## 2. Proposed architecture

No new architectural component is introduced — this is a presentation-layer
change inside the existing `api/` boundary, and the design's component table
(design-01.md §2) already reflects the right shape. My job is to make three
decisions the design left implicit or slightly underspecified, plus pin the
build order.

**Decision 1 — Where does the status→color/stripe mapping live?**
Options: (a) compute it in `routes.py` and pass a precomputed `status_class`
per task into the template context, (b) compute it inline in Jinja per card
using `{% if %}` chains, (c) a Jinja macro/filter local to `_columns.html`.
Chosen: **(b)/(c) — template-local, not routes.py.** Adding a `status_class`
field to the context is a route/context change (however small), and FR-5
explicitly reserves that budget for `shorttime` alone. A tiny Jinja macro
(`{% macro status_class(task) %}`) in `_columns.html`, called from both the
card's stripe and its badge, keeps the derivation in one place without
touching `routes.py` a second time or duplicating the four-way `if` at each
call site.

**Decision 2 — Phone sheet: `<dialog>` resized vs. a new element.**
The design leaves this as an open question resolved toward `<dialog>`. Chosen,
confirmed: **keep `<dialog id="detail">` unconditionally**, restyle it to
cover the viewport under the phone media query
(`inset: 0; width: 100%; height: 100%; max-width: none; border-radius: 0;`)
and reservce the centered/max-width treatment for `≥768px`. Rationale: every
existing JS hook (`showModal()`, native `close` event, backdrop-click-to-close,
the `innerHTML = ''` teardown that stops the SSE connection) is wired to
`#detail` as a `<dialog>` element (board.html:33-46) and none of it is
touched by this task's scope. Introducing a second, non-`<dialog>` phone-only
sheet element would require either duplicating that JS or branching it by
viewport — both directly contradict "CSS-only layout switch, no new JS for
layout." One element, two CSS shapes, zero new listeners.

**Decision 3 — Tab switching (Info/History/Output) implementation.**
The design proposes a delegated click listener on `#detail` reading
`data-tab`/`data-panel` attributes (design §1.5 #3). Confirmed as the right
shape, with one addition worth being explicit about: the listener must be
registered **once**, on `document.body` or `#detail`'s static parent (i.e. in
`board.html`'s existing `<script>` block, alongside the other three
`document.body`/`document.getElementById('detail')` listeners already there),
**not** inside `_task.html`. `_task.html` is swapped wholesale on every
`hx-get`/`hx-post` (routes.py:115-123, 137-149) — a listener attached inside it
would be silently discarded on refresh (e.g. after Restart) and need
re-registration, which is exactly the class of bug the design's own note "tab
state must not live in markup that gets replaced" (design §1.4) is warning
against. Delegation from a container that survives the swap is not optional
polish, it's the only correct place for this listener to live.

No other component decisions are open. `app.css` is a single new static file
served by the existing `StaticFiles` mount (app.py:72-76) — no wiring change.
`_nav.html` is a new template partial included only from `board.html` — no new
route, no new fragment endpoint (confirmed against design §2's own note that
it's "not served as its own fragment").

## 3. Implementation guidance

**File-level build order** (mirrors the plan's rough plan, made concrete
against the real files):

1. `src/harness/api/static/app.css` — new file. Port the current inline rules
   from `board.html:8-26` as the starting point (they're the only existing
   style source — there's nothing in `_columns.html`/`_task.html` to extract,
   both are style-free today aside from `_task.html`'s one inline `style=`
   on the output `<div>`, line 47-50, which must become a class, e.g.
   `.terminal`, in `app.css`). Add tokens, dark-mode block, responsive board
   rule, nav rule, dialog/sheet rule, table-scroll-container rule, tap-target
   sizing. Link it from `board.html`'s `<head>` only — fragments never carry
   their own `<head>`.
2. `src/harness/api/routes.py` — add `_shorttime` next to `_basename`
   (routes.py:23-34) and register it the same way (routes.py:34). This is the
   only `.py` diff in the whole task; keep it that way so
   `test_api_does_not_import_drivers` and the "presentation only" acceptance
   criterion stay trivially true.
3. `src/harness/api/templates/_columns.html` — restructure `.column`/`.card`
   markup: sticky header + count pill, empty-state branch (`{% if column.tasks
   %}...{% else %}<div class="column__empty">No tasks</div>{% endif %}`),
   status macro, stripe, pill badges, `shorttime`-formatted time with
   `title="{{ ... }}"` carrying the raw ISO value. Preserve every guard listed
   in design §3.1 exactly (title fallback, repo/worktree basename-join,
   working-badge-iff-lock_id, outcome-badge-iff-last_outcome,
   time-iff-history). Run `tests/test_api_html.py` after this step alone
   before touching `_task.html` — it isolates card/column regressions from
   detail-sheet regressions.
4. `src/harness/api/templates/_nav.html` — new partial, the data-driven
   single-item list (design §3.3), rendered twice (appbar/tabbar) from one
   Jinja loop.
5. `src/harness/api/templates/board.html` — add viewport/theme-color/apple-*
   meta tags, `<link rel="stylesheet" href="/static/app.css">`, `{% include
   "_nav.html" %}`, and extend the existing `<script>` block with the one new
   delegated tab-switch listener (Decision 3) — do not touch the three
   existing listeners' logic, only their surrounding markup/CSS.
6. `src/harness/api/templates/_task.html` — restructure into the segmented
   sheet: tab buttons + three panels, stacked key/value Info list, table-scroll
   wrappers around History and Artifacts, `.terminal` class replacing the
   inline `style=` on the output `<div>`. Copy the SSE/output attributes
   (`hx-ext="sse"`, `sse-connect`, `sse-close="end"`, `id="stage-output"`,
   `sse-swap="line"`, `hx-swap="beforeend scroll:bottom"`) verbatim —
   character-for-character, not "equivalent" — since nothing in this task
   should touch that wiring.
7. Full suite (`.venv/bin/pytest -q`), then a manual/visual pass at 390px and
   ≥768px in both color schemes (the `verify` skill, per the plan's step 4).

**Contracts to keep pinned** (do not weaken, only satisfy with new markup):
all of `tests/test_api_html.py` as it exists today, `test_api_json.py`,
`test_api_sse.py`, `test_api_artifacts.py`, `test_architecture.py`'s
`test_api_does_not_import_drivers` / `test_api_reads_artifacts_only_through_view`.
New tests may be added for new markup (nav active-state, tab-strip
data-attributes, `title`-attribute ISO timestamps) but must not replace or
loosen an existing assertion.

**Data flow**: unchanged from today. `BoardView.snapshot()`/`.get()` →
`Board`/`Task` dataclasses → template render, no new field anywhere. The one
net-new piece of "data" is presentation-only and never leaves the template
layer: the status macro's output (a CSS class name) and the nav item tuples
are Jinja-local, not Python objects crossing the routes.py boundary.

## 4. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Tab-switch listener re-registered or lost across fragment swaps (Decision 3), causing tabs to silently stop working after a Restart | Register once in `board.html`'s persistent `<script>`, delegated from a node that is never replaced by an `hx-swap` (`document.body` or `#detail`'s parent, not `#detail` itself if `hx-swap="innerHTML"` replaces its children — verify the listener target survives the specific swap target used) |
| `<dialog>` resized to full-screen on phone interacts badly with `showModal()`'s native centering/backdrop, or breaks Esc-to-close | Test Esc-to-close and backdrop-tap-to-close explicitly at 390px during the manual/verify pass — this is unchanged JS but new CSS geometry, and dialog backdrop hit-testing can behave surprisingly at `inset: 0` |
| Extracting `_task.html`'s inline `style=` on the output `<div>` into a class accidentally changes rendered attribute order/whitespace in a way some test string-matches on | Grep `tests/` for the literal inline `style=` string before removing it (none currently found in the test files read, but re-check before deleting) |
| New `app.css` accidentally introduces an external URL (a copy-pasted font `@import`, a CDN icon reference) and silently breaks `"https://" not in body` | `app.css` itself isn't in the response body, but if any template ever inlines a `<style>` referencing an external URL that check still applies to the rendered HTML — keep `app.css` the sole styling source and grep it for `://` before considering it done |
| `shorttime` filter mis-parses a timestamp format not seen in tests (e.g. one without `Z`/offset) and raises instead of degrading | Follow `_basename`'s tolerance pattern exactly — catch `ValueError` and return the input unchanged (already specified in design §3.2); add a unit test for at least one malformed/empty input, not just the happy path |
| Scope creep back toward the admin agents/workflows UI mentioned in the original brief | Explicitly out of scope per plan-01.md; do not add routes, ports, or `admin/` templates in this task under any circumstance — that would violate the "presentation only" boundary this whole design rests on |

**Prerequisite before development starts:** none outstanding. Plan and design
are implementation-ready; no port/model change, no external dependency, no
unresolved question blocks starting at step 1 above.
