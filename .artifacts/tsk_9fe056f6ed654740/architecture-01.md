# Architecture assessment — per-process target repository selection

Reviews `plan-01.md` (FR-1..FR-7) and `design-01.md` against the actual code on
`main`. Verdict: **the design is correct and buildable as written.** I traced
every function/line it cites — `compile_process`, `ScheduledTrigger._task_for`,
`FilesystemProcessRepository.build`/`_build_one`, `FilesystemProcessAdmin`,
`ProcessAdmin`/`ProcessFields`, `routes.py`'s `_process_fields_*`/
`_process_form_*` helpers, `app.build()`, `cli.serve()`/`cli._run()`, and the
actual `process_form.html` template — and the design's assumed shapes,
signatures and precedents match reality exactly. This document does not
re-derive the design; it confirms the seams, flags the two things I'd correct,
and gives the dev step an ordered path with the verification points that
matter.

## Alignment with existing patterns

This is a textbook "extend an existing seam" change, not new architecture:

- **`compile_process`/`ScheduledTrigger` composition is already exactly
  right for the precedence rule.** `ScheduledTrigger._task_for`
  (`drivers/scheduled_trigger.py:104`) already computes
  `obs.repository or self._repository` — that line needs zero changes. The
  entire three-way precedence (`obs.repository or file_repository or
  repository_kwarg`) is achieved purely by what `compile_process` passes as
  `self._repository`'s source value at construction time. This is the same
  trick `dedup`/`sink` already use: `ScheduledTrigger` stays a dumb composer,
  `compile_process` is the only place that reads the file. Good — no new
  concept, no new port, no runtime module learns "process" or "repository
  selection" (invariant #39 holds as stated).
- **The validator shape (`_parse_repository`) matches `_parse_dedup`/
  `_parse_sink`/`_parse_target` exactly** — same signature shape
  (`where, raw_value, ...) -> validated | raises ProcessValidationError(msg,
  field=...)`, called from `compile_process` at the same call-site cluster
  (`fs_processes.py:131-136`, right where `dedup`/`sink` are parsed today).
  `ProcessValidationError.field`'s docstring enum
  (`interval|cron|check|params|target|dedup|sink|trigger`, line 80) is the one
  place that must literally gain the new value — small, mechanical, easy to
  miss in review.
- **`ProcessAdmin.repository_names()` mirrors `check_names()`/`sink_kinds()`
  precisely** (`ports/process_admin.py:87-100`): same "tuple of sorted
  strings, read by `api/` instead of importing a driver" shape. `api/` today
  imports zero driver modules for process admin (verified: `routes.py`'s only
  process-related imports are from `harness.ports.process_admin`) — this
  stays true after the change, satisfying invariant #33 unchanged.
- **`FilesystemProcessAdmin.__init__` already takes one optional
  dependency (`checks`) the same shape the new `registry` param would take**
  (`drivers/fs_processes.py:389-394`) — adding `registry:
  RepositoryRegistry | None = None` is additive, not a signature break.
- **`_process_fields_from`/`_process_fields_from_form`/`_process_fields_dict`
  are flat, repetitive field lists** (`routes.py:237-297`) — adding
  `repository` is one line in each, following the existing `.strip()`
  convention. The error-redisplay reconstruction at `routes.py:926-928`
  (`_process_fields_from({key: form.get(key) for key in form.keys()})`)
  already forwards *every* posted key including a hypothetical `repo_scope`,
  so the design's claim that redisplay-after-a-params-error keeps the right
  scope "for free" is correct — I checked this is a generic dict
  reconstruction, not a hardcoded key list.
- **The template's section-numbering idiom
  (`{{ N if is_new else N-1 }}`) and inline-`<script>`-only pattern are
  exactly as the design assumes** — I read the live template
  (`src/harness/api/templates/admin/process_form.html`, not
  `templates/admin/...` as the design's path implies — see Corrections
  below) end to end. Every toggle (cadence, target-kind, dedup, sink) lives in
  one inline `<script>` block with the same `sync*()` / `current*()` /
  `updateSummary()` shape the design's snippets extend. There is no
  precedent anywhere on this page for a separately-loaded JS file — confirms
  the plan's read that PR #108's `process_form.js` was always the wrong
  shape, not an unfinished-but-correct one.
- **`FilesystemProcessRepository.build`'s existing `known_targets` threading
  is the direct precedent for `known_repositories`** — same
  `Optional[set[str]]`, same "the real run supplies it, the admin driver's
  `write()` today passes `known_targets=None` unconditionally" split. The
  design's resolution of open question #2 (repository validation is strict
  at admin-save time, target validation stays lenient) is justified by a real
  asymmetry: `known_targets` needs the *served* workflow set, which a
  filesystem-only `ProcessAdmin` genuinely cannot compute; `repos.json` has
  no such runtime dependency. Endorsed.

## Corrections to the design (small, do not change its substance)

1. **Template path.** The design's snippets say `templates/admin/
   process_form.html`; the real path is
   `src/harness/api/templates/admin/process_form.html`. Same for
   `processes_list.html` etc. Purely a doc typo in `design-01.md` — the dev
   step should edit the real path, no different content implied.
2. **`FilesystemProcessAdmin.write()`'s current call already passes
   `known_targets=None` explicitly** (`fs_processes.py:432-434`) — the design's
   snippet reproduces this correctly; just noting there is no separate
   "lenient by omission" vs "lenient by explicit None" distinction to worry
   about, both already read as `None` today.

Neither correction changes an interface, a precedence rule, or a test
boundary. Proceed with the design as written, using the corrected path.

## Proposed architecture (confirmed, not modified)

No new port, no new driver, no new invariant. The change is four small,
additive extensions to existing seams, wired through three existing wiring
functions:

```
processes/<name>.json
  └─ "repository": "<name>"                          [NEW optional key]
         │
         ▼
FilesystemProcessRepository.build(known_repositories=...)   [NEW param, threaded]
         │
         ▼
compile_process(known_repositories=...)              [NEW param]
  └─ _parse_repository(where, raw.get("repository"), known_repositories)  [NEW fn]
         │
         ▼
ScheduledTrigger(repository=file_repository or repository_kwarg)  [ZERO changes]
         │
         ▼
ScheduledTrigger._task_for: obs.repository or self._repository    [ZERO changes,
                                                                     already correct]
```

Mirrored on the admin-editor side:

```
ProcessFields.repository: str = ""                    [NEW field]
         │
   _raw_from_fields / _fields_from_raw                 [NEW round-trip lines]
         │
FilesystemProcessAdmin(registry=...)                   [NEW ctor param]
  ├─ repository_names()                                [NEW port method]
  └─ write() → compile_process(known_repositories=...)  [threaded through]
         │
routes.py: _process_fields_dict / _process_fields_from /
           _process_fields_from_form / _process_form_context   [NEW field/param]
  └─ _repository_from_payload(payload)                  [NEW helper, resolves
                                                           repo_scope → repository]
         │
process_form.html: new "Repository" section             [segmented All/Specific
                                                           + <select>, inline JS]
```

Wiring (`app.build`, `cli.serve`, `cli._run`) gains one new optional parameter
each, purely to compute/forward `known_repositories`/`registry` — no change to
any existing parameter's meaning.

### Key decisions, restated with rationale

- **Single repository per process, not multi-repo fan-out.** Correct scope
  cut. `Task.repository`/`ScheduledTrigger` are singular by construction;
  fan-out would require an `Observation`-per-repo expansion at compile time —
  a materially different (and unrequested) feature. Confirmed out of scope in
  both plan and design; I agree with deferring it.
- **Precedence: `obs.repository or file_repository or repository_kwarg`.**
  This is not just "the design's choice" — it is the *only* choice consistent
  with `ScheduledTrigger._task_for`'s existing code, which the design
  correctly identifies needs no edit. Any alternative (e.g. process file
  wins over observation) would require touching `scheduled_trigger.py`,
  which is otherwise untouched by every other Process feature to date
  (dedup, sink) — a strong signal this file should stay closed.
- **Strict validation wherever a registry is reachable.** Right call: unlike
  `known_targets` (needs live served-workflow state), `repos.json` is static
  and already read by both compile sites today. Leniency here would only
  hide operator typos that are trivially catchable.
- **Segmented All/Specific toggle + `<select>`, not the stub's checkbox
  list.** Right call, and the only call consistent with the single-repository
  decision above — a checkbox list implies multi-select, which this design
  correctly does not build. Reusing the page's existing inline-script idiom
  rather than reviving a separately-loaded file is also the right
  call: `process_form.js` was dead from the moment it shipped, not a
  reusable partial implementation.

## Implementation guidance

Follow the plan's numbered steps in this order; the ordering matters because
later steps' tests depend on earlier steps' shapes existing:

1. **`ports/process_admin.py`** — add `ProcessFields.repository: str = ""`
   and abstract `ProcessAdmin.repository_names()`. This alone will break
   nothing (dataclass field with a default; ABC method addition breaks any
   *other* concrete `ProcessAdmin` implementation not updated in the same
   commit — grep confirms `FilesystemProcessAdmin` is the only one, plus a
   possible `_EmptyProcessAdmin` in `api/app.py` per the design; update both
   in this commit).
2. **`drivers/fs_processes.py`** — `_parse_repository`, thread
   `known_repositories` through `compile_process` → `build` → `_build_one`;
   extend the `ProcessValidationError` docstring's field enum; round-trip in
   `_raw_from_fields`/`_fields_from_raw`; `FilesystemProcessAdmin.__init__`
   gains `registry`, implements `repository_names()`, passes
   `known_repositories` into its `write()`'s `compile_process` call.
3. **`app.py`** — `build(..., repository_registry=None)`, compute
   `known_repositories` once, pass into the existing
   `process_repo.build(...)` call (`app.py:643-649`).
4. **`cli.py`** — `serve(..., registry=None)` → forwarded into
   `FilesystemProcessAdmin(...)`; `_run()` passes its already-constructed
   `registry` (line 1587) into both the `build(...)` call (~line 1723) and
   the `serve(...)` call (~line 1755).
5. **`api/routes.py`** — `_repository_from_payload`, extend the four
   `_process_fields_*`/`_process_form_context` functions, add
   `repository_options=process_admin.repository_names()` at both
   `_process_form_response` construction and wherever `_EmptyProcessAdmin`
   is substituted in tests.
6. **Template** — new Repository section (correct path:
   `src/harness/api/templates/admin/process_form.html`), renumber Options'
   header, extend the inline `<script>`.
7. **Delete `src/harness/api/static/process_form.js`.** Confirmed zero
   references anywhere in templates, routes, or tests — safe, unconditional
   delete, no follow-up wiring needed.
8. **Tests**, in this order so each layer is provable independently before
   the next depends on it:
   - `test_fs_processes.py`: `_parse_repository` unit cases (absent / valid /
     unknown-with-registry / non-string / empty-string), plus a
     `FilesystemProcessRepository.build(known_repositories=...)` case.
   - `test_fs_process_admin.py`: `repository_names()` (with/without a
     registry), `write()` round-trip and rejection-on-unknown (follow the
     existing `test_write_validates_against_the_wired_registry` /
     `test_check_names_reflects_the_wired_registry` pattern at lines
     305-341 — same shape, s/check/repository/).
   - `test_process_admin_api.py` / routes-level tests: JSON body and HTML
     form both carrying `repository`/`repo_scope`.
   - `test_processes_e2e.py`: the precedence proof — a `github-issues`-backed
     process with both a process-level `repository` and an
     observation-supplied one asserts the observation wins; a
     non-observation-repository check (e.g. `always`) with a declared
     `repository` asserts it comes through; a process with neither behaves
     exactly as every pre-existing process file in the test suite already
     does (regression guard — this is the one that would catch a precedence
     mistake immediately).
   - `test_app.py` / `test_cli.py`: extend the existing
     `test_build_compiles_processes_root_targeting_a_served_workflow_by_name`
     -style wiring tests (lines 966+) with a `repository_registry=` case, and
     add a `cli.serve(registry=...)` wiring test alongside the existing
     `FilesystemProcessAdmin` construction assertions in `test_cli.py`.
9. **Docs** — one-line docstring additions only, as the plan says; no new
   numbered invariant is warranted (confirmed: this touches no dispatcher/
   consumer/router code path, and reuses `ScheduledTrigger`'s existing
   composition rule rather than introducing a new one).

## Risks and mitigations

- **Breaking the `ProcessAdmin` ABC for any un-updated implementation.**
  Mitigation: grep for `ProcessAdmin` subclasses before step 1 lands
  (`FilesystemProcessAdmin` plus any test-only fake/`_EmptyProcessAdmin` in
  `api/app.py`) and update all in the same commit — an abstract method
  addition is a hard break for anything missed.
- **Silent precedence regression** (process-level `repository` accidentally
  overriding an observation's own, e.g. if a future edit reorders the `or`
  chain or moves logic into `ScheduledTrigger`). Mitigation: the
  `test_processes_e2e.py` precedence test (item 8 above) is not optional —
  it is the single test that would catch this, and per plan FR-7 it must
  exist before this is considered done.
- **Form redisplay losing `repo_scope` on a validation error.** The design
  correctly identifies that the existing generic-dict reconstruction at
  `routes.py:926-928` already forwards every form key, so this "just works" —
  but it's worth an explicit test (submit an invalid `params` JSON alongside
  `repo_scope=specific`, assert the redisplayed page still shows "Specific"
  checked) since it's a non-obvious interaction between two features that
  weren't co-designed.
- **`repos.json` absent or empty at admin-save time.** Already handled by the
  design's `registry is None → known_repositories=None → lenient` fallback,
  consistent with every other `known_*=None` escape hatch in this module —
  no new failure mode, but worth one explicit test (`write()` succeeds with
  an arbitrary repository name when no registry was wired) so a future
  refactor can't accidentally make this strict-by-default.

## Prerequisites before implementation begins

None outstanding. `RepositoryRegistry.names()` already exists and returns
`list[str]` (`ports/repos.py:24`, `drivers/fs_repos.py:42`) — no upstream
port work needed. All three call sites this design threads through
(`app.build`, `cli.serve`, `cli._run`) already exist with the exact shapes
the design assumes. The dev step can start directly at plan step 1.

```json
{"outcome": "done", "summary": "Verified design-01.md's assumed shapes/signatures against the live code (compile_process, ScheduledTrigger._task_for's existing obs.repository-or-self._repository, FilesystemProcessAdmin, ProcessFields, routes.py's process helpers, app.build()/cli.serve()/cli._run(), the real process_form.html, and test_fs_process_admin.py's precedent tests) — all confirmed accurate. Endorsed the architecture as-is: no new port/driver/invariant, four additive extensions to existing seams, zero changes needed to ScheduledTrigger itself. Flagged one doc-only path correction (templates live under src/harness/api/templates/, not templates/) and gave an ordered implementation path, risk list (ABC-break on ProcessAdmin subclasses, precedence-regression test as a hard requirement, repo_scope redisplay interaction) and confirmed no prerequisites are outstanding."}
```
