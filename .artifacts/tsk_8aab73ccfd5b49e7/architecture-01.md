# Architecture review: `known_steps`/`known_workflows` split for Process/Trigger targets

## Verdict

**Approved to implement, with two scope corrections required before FR-6 is
considered done** (both are completeness gaps in design-01.md's test surface
and docstrings, not architectural defects — see §3). No invariant is
violated; no port changes; no new dependency crosses a layer boundary.

## 1. Alignment with existing patterns and invariants

Verified directly against the current tree (not just the plan/design's
prose), since line numbers and behavior claims must hold before this is
handed to implementation:

- **Module map / layering (invariant re: dispatcher/consumer ports-only,
  module map's "Orchestration" row).** Confirmed at `dispatcher.py:76-78`:
  the only change is the literal string passed to `self._fail(...)`. No new
  import, no new constructor argument, no new method. `dispatcher.py` stays
  exactly as ports-only as it is today. This is the right amount of restraint
  for FR-5 — a `Dispatcher` that started distinguishing "unknown step" from
  "known step, stale queue set" would need `AgentCatalog`/`RepositoryRegistry`
  visibility, which is explicitly and correctly ruled out by the plan.
- **No port changes.** Confirmed `ports/*.py` is untouched by this design —
  `compile_process`/`FilesystemProcessRepository.build`/
  `FilesystemTriggerRepository.build`/`FilesystemProcessAdmin` are all
  driver-layer signatures. This is a validation-logic and wiring fix, not a
  new architectural capability, matching the plan's own framing (open
  question 4, resolved as "short ADR, not a new invariant").
- **`invariant #33` (`ProcessAdmin` is UI-facing, unknown to
  dispatcher/consumer).** Untouched — `FilesystemProcessAdmin` gains two
  constructor kwargs, still wired only in `cli.py::serve()`.
- **`invariant #39` (a Process is compile-time only, never a runtime
  object).** Untouched — the fix only tightens what `compile_process`
  validates before producing a `ScheduledTrigger`; nothing new crosses into
  `dispatcher`/`consumer`/`router`.
- **The `known_*=None` escape-hatch convention** (`known_repositories=None`
  in `_parse_repository`, today's `known_targets=None` in `_parse_target`).
  Confirmed in `drivers/fs_processes.py:240,257-279`: this convention is
  real, consistent, and the design's choice to keep `known_steps=None` /
  `known_workflows=None` as two *independently* toggleable escape hatches
  (design §1.1, plan open question 3) is the correct continuation of an
  existing idiom, not a new one.
- **`Harness.workflows` is already public** (`app.py:152`, `self.workflows =
  workflows`) and is exactly the served-workflow-name-keyed dict the design
  needs for `set(harness.workflows)` — confirmed, no new attribute required
  for that half of FR-4's admin wiring.
- **`app.py:420-426`'s `known_steps` computation and `app.py:699`'s
  `known_targets = set(known_steps) | set(resolved)` union** are exactly
  where the design says they are. The design's replacement (pass
  `known_steps=known_steps, known_workflows=set(resolved)` instead of the
  union) is a minimal, surgical diff — confirmed no other logic in that
  function needs to move.
- **ADR numbering.** `docs/adr/` currently ends at `0021-...md`; `0022` is
  free. The design's proposed ADR-0022 content (validate against the exact
  runtime dispatch surface, never a broader superset merging two namespaces)
  is a genuinely reusable principle worth recording — endorsed.
- **Dispatcher message-text change is safe.** Grepped for every consumer of
  the literal `"has no queue"` string: `tests/test_api_html.py:67` embeds a
  hand-built `HistoryEntry(reason="step 'plan' has no queue")` fixture for
  HTML-rendering tests — it does not call the real dispatcher, so it is
  unaffected by FR-5's wording change. No other test or driver pattern-matches
  this string. FR-5 is free to change the message's content.
- **Existing tests assert on substrings, not exact validation-error text**
  (`tests/test_fs_processes.py:474`, `tests/test_fs_triggers.py:201` both
  assert only `"unknown-wf" in str(excinfo.value)`), so `_parse_target`'s
  message wording is free to change per-branch (step vs. workflow) without
  breaking today's tests beyond the `known_targets=` kwarg rename itself.

The component design in §1.1–§1.6 of design-01.md is architecturally sound:
it reshapes two derived, transient, in-memory sets, threads them through
existing call sites, and adds one read-only property (`Harness.known_steps`)
computed from state that already exists (`self._step_queues`) rather than a
second stored copy that could drift. That "derive, don't duplicate" choice is
the single most important correctness property of this design — the whole
bug class the task describes is a validate-against-the-wrong-set problem, so
a fix that introduces a *second* place `known_steps` is computed and stored
would be self-defeating. Confirmed there is no such duplication anywhere in
the design.

## 2. Proposed architecture — assessment of the key decision

**Decision under review:** split one merged `known_targets: set[str]` into
two independently-checked sets (`known_steps`, `known_workflows`), each
validated only against the namespace it actually governs, rather than (a)
keeping one merged set and special-casing the check, or (b) introducing a
new `TargetResolver`-style abstraction.

**Assessment: correct choice.** Options (a)/(b) were implicitly considered
and rejected by the plan/design, and rightly so:
- Keeping one merged set and special-casing risks re-introducing exactly this
  bug the next time a third target-shaped namespace appears (e.g. a future
  "trigger a Check by name" target) — the root cause is structural (one set,
  two namespaces), so the fix must be structural, not a patched special case.
- A new abstraction (a `TargetResolver` object, a registry pattern) is not
  warranted — the two sets are simple, already-computed `set[str]` values
  with a 5-line lifetime; this project's own style (module map, ADR habit)
  favors data-shaped fixes over new machinery for a bug at this scale.

The two-set split is also the option that makes the fix trivially testable
per FR-6/AC1/AC2 (independent True/False matrix over 2×2 target-kind ×
set-membership), which the merged-set alternative would not offer as cleanly.

## 3. Gaps found — required before FR-6 is complete

These are **completeness gaps in the design's stated test surface and
documentation**, not defects in the proposed logic. Both are mechanical to
close, but neither is currently listed in design-01.md §5, so an
implementation that follows §5 literally will leave the suite red.

### 3.1 Test surface: two files call the changing production signature and are not listed

FR-3's own acceptance criterion — `grep -rn known_targets src/harness` shows
no remaining merged-set call site — is deliberately scoped to `src/harness`
only. That means it will pass even though the following **test** files
directly call the exact production functions being renamed
(`FilesystemProcessRepository.build()` / a thin wrapper around it,
`FilesystemTriggerRepository.build()`) with the old `known_targets=` keyword,
and will fail with `TypeError: unexpected keyword argument 'known_targets'`
the moment the rename lands:

- **`tests/test_cli.py`** — a shared helper, `_compile_processes(tmp_path,
  *, checks, known_targets, clock)` (`tests/test_cli.py:1580`), forwards
  `known_targets=known_targets` straight into
  `FilesystemProcessRepository.build()`. It is called from **8** tests
  (`known_targets={"default"}` or `{"resolver"}`, lines 1614, 1640, 1662,
  1688, 1721, 1750, 1775, 1798) — all workflow-only targets, so each
  updates mechanically to `known_workflows={...}, known_steps=None`.
- **`tests/test_processes_e2e.py`** — three direct calls to
  `FilesystemProcessRepository(...).build(..., known_targets={...})`
  (lines 243, 313, 471; the last passes `{"default", "resolver"}` for a
  process/workflow pair spanning two files) — same mechanical update.

Design-01.md §5 ("Test surface") names only `tests/test_fs_processes.py`,
`tests/test_fs_triggers.py`, `tests/test_app.py`, `tests/test_fs_process_admin.py`
and `tests/test_dispatcher.py`. Neither `test_cli.py` nor
`test_processes_e2e.py` is mentioned, even though both call the exact
signatures FR-3 renames.

**Required fix to the plan before implementation starts:** widen FR-6 (or
FR-3's own AC) to `grep -rn known_targets tests/` as well as `src/harness` —
every one of the 19 hits (confirmed count) must be either updated to the new
kwarg or, for the 2 hits in `test_app.py` that are prose in docstrings
referencing the old name conceptually (lines 968, 1118), simply not code and
safe to leave, updated for accuracy if convenient. All 8 `_compile_processes`
call sites in `test_cli.py` and all 3 in `test_processes_e2e.py` pass
workflow-only sets today, so every update is `known_targets={X}` →
`known_workflows={X}` with `known_steps=None` — no test needs new logic, only
the rename. Low risk, but must be in scope or the suite goes red mid-implementation.

### 3.2 Stale docstring: `FilesystemProcessAdmin`'s own class docstring contradicts the FR-4 fix

`drivers/fs_processes.py:436-441` (current code) states, of `registry`:

> "the same `RepositoryRegistry` ... available at both compile sites
> (**unlike the served-workflow set `known_targets` depends on**)."

This sentence is the admin-side gap FR-4 closes. Once
`FilesystemProcessAdmin` gains `known_steps`/`known_workflows` and `serve()`
wires them from `harness.known_steps`/`harness.workflows`, this docstring
becomes actively wrong (it says the opposite of the new reality) rather than
merely outdated. Design-01.md §1.5 shows the new `__init__`/`write()` body
but does not mention updating the surrounding class docstring.

**Required fix to the plan:** add "update `FilesystemProcessAdmin`'s class
docstring" as an explicit line item under FR-4 / the design's §1.5, not left
to incidental cleanup. Same applies to the one-line comment in `cli.py`'s
`_scheduled_sources` docstring (`cli.py:817`, "`known_targets` (served
workflow names ∪ known step names) lets the repository reject...") — should
read `known_steps`/`known_workflows` after the rename.

## 4. Implementation guidance

Recommended order (matches the plan's own rough-plan, confirmed against the
real call graph — no reordering needed):

1. `drivers/fs_processes.py::_parse_target` + `compile_process` +
   `FilesystemProcessRepository.build`/`_build_one` — split the parameter.
   Mirror in `drivers/fs_triggers.py`'s twin. Do these two together since
   they are structural twins (module map already documents this pairing) —
   a divergence between them here would itself be a small architecture bug.
2. `app.py:699` — replace the union with the two already-in-scope values
   (`known_steps`, `set(resolved)`); delete the now-stale comment at
   `app.py:694-698`.
3. Add `Harness.known_steps` as the read-only property over `self._step_queues`
   (design §1.3) — do this *before* FR-4's wiring, since `serve()` needs it.
4. `cli.py::_scheduled_sources` + its caller's local computation
   (`cli.py:1876-1886`) — split the same way as `app.py`, independently (the
   plan correctly does not ask to deduplicate this pre-existing duplicate
   loop against `app.py`'s — out of scope, agreed).
5. `FilesystemProcessAdmin.__init__`/`write` (FR-4) — add the two kwargs,
   thread through in `cli.py::serve()`'s construction call
   (`cli.py:2033-2035`), **and update the class docstring** (§3.2 above).
6. `dispatcher.py:78` — FR-5's message text, isolated one-line change,
   can land independently of 1–5 (no dependency either direction).
7. Tests, per FR-6 **as widened by §3.1** — include `test_cli.py` and
   `test_processes_e2e.py` in the mechanical rename sweep, not only the five
   files design-01.md §5 names.
8. ADR-0022 + any docstring/comment updates named in §3.2.

Step 6 (dispatcher message) and steps 1–5 (validation split) are fully
independent and can be implemented/tested in either order or in parallel by
two passes over the diff; nothing in this design creates a sequencing
dependency between the "reject earlier" fix and the "explain better when it
still happens" fix, which is itself a sign the two are correctly separated
concerns (compile-time prevention vs. runtime defence-in-depth).

## 5. Residual risk — explicitly accepted, not a defect

FR-1 through FR-4 do **not** prevent the literal incident that opened this
task (`tsk_936d9920688e414f`, `nanoclaw-sweep`): that task's
`agents/nanoclaw-sweep.json` and `processes/nanoclaw-sweep.json` were valid
and would have passed both the current check *and* the tightened one — the
failure came from being added against an already-running instance with no
live-reload, not from a target naming the wrong namespace. Only FR-5 (an
actionable message) touches that path, and it does not prevent the failure,
only makes it legible with a concrete remedy ("restart the service").

This is already correctly scoped as "out of scope" in the plan (live-reload
of `agents/`/`processes/`/`triggers/` is explicitly deferred as "materially
larger"). Flagging it here only so it is a **conscious acceptance**, not an
oversight: after this change ships, a config added to a running instance
without a restart will still fail — just with a message that tells the
operator what to do about it. If that residual gap is unacceptable, the
right follow-up is a live-reload feature, not a re-scope of this fix; nothing
in this design should be stretched to half-solve that separately.

## 6. Prerequisites before implementation begins

None blocking. The two items in §3 (test-surface scope, docstring updates)
should be folded into the plan/design before the dev step starts, since both
are cheap to add now and expensive to discover as red tests mid-implementation.
No open question from plan-01.md or design-01.md remains unresolved that
would block starting code.

```json
{"outcome": "done", "summary": "Verified design-01.md's known_steps/known_workflows split against the real codebase (app.py, cli.py, dispatcher.py, fs_processes.py, fs_triggers.py, test_architecture.py) — layering, ports-only dispatcher, ADR numbering and the known_*=None convention all check out, and the derive-don't-duplicate Harness.known_steps property is the right call. Found two completeness gaps to fold into the plan before FR-6 is done: test_cli.py (8 call sites via a shared _compile_processes helper) and test_processes_e2e.py (3 call sites) call the exact known_targets= signature FR-3 renames but aren't listed in design-01.md's test surface, and FilesystemProcessAdmin's class docstring plus a cli.py docstring line will read backwards once FR-4 lands unless explicitly updated. Also flagged as an accepted (not new) residual risk that FR-1-4 don't prevent the original nanoclaw-sweep incident's literal restart-ordering path — only FR-5 makes it legible, per the plan's own explicit scoping."}
```
