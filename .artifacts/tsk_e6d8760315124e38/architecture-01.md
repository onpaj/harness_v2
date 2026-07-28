# Architecture review — repository select filter + fulltext filter on the board UI

Reviewed against `plan-01.md`, `design-01.md`, and the current state of
`src/harness/{ports/board.py,projection.py,app.py,api/routes.py,api/templates/
board.html,api/templates/_columns.html,api/static/app.css}`, plus the cited
precedent (`ports/process_admin.py`, `drivers/fs_processes.py::
FilesystemProcessAdmin`).

## Verdict

**Approved, build as designed.** The design is a faithful, invariant-respecting
transcription of the plan, and every claim it makes about the current codebase
was verified against the actual source (not assumed). One documentation nit
and two implementer-facing gaps are noted below — neither blocks
implementation, both are cheap to close while transcribing the design into
code.

## Alignment with existing invariants and patterns

- **Invariant #5/#33 (`api/`/`projection.py` import no driver).** Verified
  live: `projection.py` today imports only `harness.models`,
  `harness.ports.board`, `harness.ports.queue`. The design's
  `BoardProjection.__init__(steps, workflows=(), repository_names=())` adds a
  plain `Sequence[str]` parameter — no new import at all, let alone a driver
  one. This is more conservative than it needed to be: even importing
  `harness.ports.repos.RepositoryRegistry` into `projection.py` would satisfy
  the invariant (it's a port), but the design correctly recognizes
  `BoardProjection` already treats `steps`/`workflows` as plain constructor-time
  data, not live port references, and keeps `repository_names` on the same
  footing. `routes.py` needs zero changes — confirmed both `index()` (line 541)
  and `fragment_board()` (line 553) already pass `board=view.snapshot()` into
  the template, so `board.repository_names` simply exists once `app.py` wires
  it. `test_architecture.py::test_projection_does_not_import_drivers` and
  `::test_api_does_not_import_drivers` cover this mechanically.
- **Invariant #8 (`repository` read only by the behavior for routing
  purposes).** The design reads `task.repository` in exactly two places:
  Jinja (`data-repository` attribute) and the plain-data `Board` field. Neither
  is consulted by `route()`/the dispatcher — confirmed no changes touch
  `router.py`/`dispatcher.py`/`consumer.py`. Filtering is 100% a rendering
  concern.
- **Precedent match confirmed, not assumed.** `ports/process_admin.py` +
  `drivers/fs_processes.py::FilesystemProcessAdmin.repository_names()` (line
  531) is real and does exactly what the design claims:
  `tuple(sorted(self._registry.names())) if self._registry else ()`. The
  design's `BoardProjection`/`app.py` plumbing mirrors this shape faithfully —
  same sorted-tuple-or-empty leniency, same "`None` registry → `()`" fallback
  already established for `known_repos` in `app.py` (line 701:
  `set(repository_registry.names()) if repository_registry is not None else
  None`).
- **`app.py::build()` already has the dependency in hand.** `repository_registry:
  RepositoryRegistry | None = None` is an existing parameter (line 370),
  already imported (line 52: `from harness.ports.repos import
  RepositoryRegistry`) and already used once (line 701, for `VerifyBehavior`/
  `known_repos`). Threading `repository_registry.names() if ... else ()` into
  the existing `BoardProjection(...)` call (line 428) is a one-line, in-place
  change — no new parameter, no new port method.
- **`Board` field addition is backward-compatible.** Verified every existing
  `Board(...)` construction site across `src/` and `tests/` (18 call sites)
  uses keyword arguments exclusively; appending `repository_names: tuple[str,
  ...] = ()` as the dataclass's last field with a default breaks nothing and
  needs no call-site update.
- **SSE-swap survival mechanism matches the codebase's own established
  pattern**, and the design correctly identifies *why* it diverges from
  `applyActiveTab`'s in-`#board` `data-` attribute idiom: the filter bar sits
  **outside** `#board` (a sibling, before it), so `hx-swap="innerHTML"` on
  `#board` never touches it — the `<select>`/`<input>`'s live DOM values need
  no round-trip. Only the *effect* of filtering needs reapplying, via
  `applyFilters()` called from the same `htmx:afterSwap` handler that already
  calls `applyActiveTab()` (confirmed present at `board.html:95-97`). This is
  the right call, not a cargo-culted copy of the tab pattern.
- **`.column--empty` CSS reuse is correct, verified against the actual rule**,
  not assumed: `app.css:181` — `.column--empty .column__head { opacity: .55;
  }` — is purely a header-dimming affordance, not a "No tasks" text
  replacement (that text, `.column__empty`, is a separate element
  `_columns.html` renders only when `column.tasks` is server-side empty,
  `_columns.html:40`). The design's actual pseudocode (in "Client-side filter
  algorithm") correctly only toggles the `column--empty` class and does *not*
  attempt to inject a "No tasks" placeholder for an all-filtered column — it
  reuses exactly the dimming affordance and nothing more. This is architecturally
  sound: no new DOM element type, no duplicated empty-state string.

## Documentation nit (non-blocking)

`design-01.md`'s prose in "Key interactions" step 1 and in the "`_columns.html`"
component-design section says the algorithm "shows the `.column__empty`
('No tasks') placeholder" for an all-filtered column. That's not what the
pseudocode two sections later actually does (it only toggles `column--empty`,
never touches/creates `.column__empty`). The pseudocode is correct and is what
should be transcribed; the prose oversells it. Worth a one-line fix to the
design doc for future readers, but doesn't change what to build — flagging so
the implementer transcribes the pseudocode, not the prose.

## Implementation guidance (gaps in the design worth resolving up front)

1. **`select` styling has no existing precedent to "reuse".** The design's CSS
   section says `.filter-bar select, .filter-bar input[type="search"] { /*
   reuse the existing input[type="text"] sizing rule rather than a new one */
   }`. Verified: `app.css` has **no** bare `select` rule anywhere (confirmed by
   grep) — every existing `<select>` in the codebase (e.g.
   `admin/process_form.html:198`, the repository scope select) renders with
   browser-default sizing, with only ad-hoc container rules around it. So
   there is nothing to "reuse" for the `<select>` half of this design; the
   implementer must write an explicit sizing rule for `#filter-repo` (can
   mirror `input[type="text"]`'s `min-height: 44px` and border/radius tokens
   from `app.css:415-420` by value, not by selector reuse). The `input[type=
   "search"]` half genuinely can share `input[type="text"], textarea`'s
   existing rule by adding `, input[type="search"]` to that selector list, or
   by giving `#filter-text` a matching explicit rule — either is fine; just
   don't assume the browser default `<select>` will look consistent with the
   rest of the form controls without an explicit rule.
2. **`data-search` attribute construction needs a null-safe Jinja
   expression.** The design's snippet concatenates `task.data.title or
   task.id`, `task.id`, `task.repository or ''`, `task.worktree or ''`,
   `task.last_outcome or ''` with `~` and applies `| lower` once at the end.
   Confirmed against `_columns.html`'s existing idiom (`task.data.title or
   task.id`, `task.repository | basename`) — this is consistent Jinja style
   already used in the file, just longer. No blocking issue, just confirming
   the pattern compiles against the real `Task` model fields used elsewhere in
   the same template (`task.data`, `task.repository`, `task.worktree`,
   `task.last_outcome` are all already dereferenced in `_columns.html` today).

Neither gap changes the shape of the design — both are CSS/template polish the
implementer resolves while transcribing, not an architectural decision to
revisit.

## Risks and mitigations

- **Risk: `.card` count grows (two new `data-*` attributes per card,
  concatenated search blob).** Mitigated by design: the blob is built once
  server-side per render (not per keystroke), and the filter script does a
  plain `indexOf` substring check — O(query length) per card per keystroke,
  no re-normalization of card content client-side. Acceptable for a board-scale
  (tens to low hundreds of cards) UI.
- **Risk: no JS test harness exists to mechanically verify the actual
  hide/show/count behavior.** Confirmed: `grep -rl
  "playwright\|selenium\|puppeteer" tests/` is empty, as both `plan-01.md` and
  `design-01.md` already note. This is a real gap but not one this feature can
  close alone — the test plan correctly scopes automated coverage to the data
  plumbing (`Board.repository_names`, `BoardProjection` construction,
  `build()` wiring, and the rendered `<select>`/`data-*` attributes via
  `test_api_html.py`) and explicitly calls out that the interactive
  hide/show/count/AND-compose/survive-SSE-swap behavior must be verified by
  hand (e.g. via the `run` skill) once implemented. Carry this forward
  explicitly into the implementation step — don't let "tests pass" stand in
  for "the filter actually filters."
- **Risk: a future column-count consumer (e.g. any other view reading
  `BoardColumn`) never sees the filtered count**, since the filter is 100%
  client-side and `Board.to_dict()`/the JSON API report unfiltered totals.
  This is intentional per the acceptance criteria (filtering is a view
  concern only) and correctly out of scope — flagging only so it's not
  mistaken for an oversight later.

## Prerequisites before implementation begins

None outstanding. `RepositoryRegistry`, `Board`, `BoardProjection`, `app.py`'s
`repository_registry` parameter, and the `board.html`/`_columns.html`/`app.css`
templates all already exist in the shape the design assumes — verified by
direct reading, not inference. The implementer can proceed straight to the
plan's "Rough plan" steps 1-8.

## Test plan sign-off

The design's test plan (`tests/test_board_port.py`, `tests/test_projection.py`,
`tests/test_app.py`, `tests/test_api_html.py`, plus a full
`tests/test_architecture.py` run) is appropriately scoped and targets files
that all exist today. No additions needed.
