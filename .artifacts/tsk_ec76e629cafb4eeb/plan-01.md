# Plan — triage Process: generalized `github-issues` params + `label-issue` finisher

## Grounding note

This worktree (`harness/tsk_ec76e629cafb4eeb`) is checked out **74 commits behind
`origin/main`** and contains none of the files this task assumes exist —
`drivers/github_issues_check.py`, `drivers/fs_processes.py`, `ports/triggers.py`
(`Check`/`Observation`), the finisher registry in `app.py`, ADR-0015/0016, or
`CLAUDE.md` invariants #24–#41. Everything below is grounded by reading those
files directly off `origin/main` (`git show origin/main:<path>`), not off this
worktree. **The first implementation step must merge/rebase onto `origin/main`
before anything else** — this is FR-0, not a footnote.

## Summary

Two small, orthogonal additions let a "triage" Process sit upstream of the
existing `github-issues` ingestion Process: (1) the `github-issues` action
already supports a scan `label` and a claim-swap `claimed_label` in code
(`GithubIssuesCheck`) but only `label` is exposed through `processes/*.json`;
expose `claimed_label` too. (2) today a step's *finisher* (ADR-0016) fully
**replaces** the step's behavior (e.g. `land` never runs an agent) — but triage
needs the opposite: a persona (`product-manager`) runs and returns a verdict,
and *then* the worker applies a label to the source GitHub issue based on that
verdict. This requires generalizing the finisher registry from "kind → fixed
behavior" to "kind → factory that can wrap the step's own agent behavior."

## Context

The ProductManager scenario: a periodically-run Process scans issues carrying
a triage label (e.g. `harness:triage`), claims each with the existing
label-swap mechanics (`GithubIssuesCheck`, reused verbatim), runs a
`product-manager` persona against it, and — depending on the verdict — either
relabels it `harness:todo` (so the *existing*, untouched `github-issues`
ingestion Process picks it up next) or `harness:needs-info` (parked for a
human). The PM step is purely upstream of ingestion: no downstream step,
router edge, or dispatcher logic changes.

The two gaps blocking this: the process-JSON schema for `github-issues` only
threads `label` through to the check (`cli.py::_process_sources`), and there is
no mechanism today for a finished step to write an outbound label — the
closest thing, `GithubLabelReflector`, maps *harness lifecycle states*
(queued/in-progress/done/failed) to labels, never a step's *verdict*.

## Functional requirements

**FR-0 — Merge/rebase onto `origin/main` first.**
This worktree predates all of processes, checks, and finishers. Acceptance:
`git merge origin/main` (or equivalent rebase) completes with the whole
Check/Process/finisher machinery present and `pytest -q` green, before any new
code is written.

**FR-1 — `claimed_label` becomes a `github-issues` process param.**
`cli.py::_process_sources`'s `github_issues_factory` reads
`params.get("claimed_label", "harness:queued")` (mirroring the existing
`params.get("label", args.github_label)`) and passes it to `GithubIssuesCheck`.
Acceptance:
- A process file `{"action": {"check": "github-issues", "params": {"label":
  "harness:triage", "claimed_label": "harness:validating"}}}` compiles and, on
  fire, swaps `harness:triage` → `harness:validating` (not the ingestion
  process's `harness:queued`).
- Omitting `claimed_label` keeps today's default (`harness:queued`) —
  no behavior change for the existing ingestion process.
- A non-string `label`/`claimed_label` fails `compile_process` with a
  `ProcessValidationError(field="params")`, the same path a malformed `label`
  would already have to go through (today's factory doesn't type-check
  `label` either — add the check for both, consistently).

**FR-2 — Cheap cross-process label-collision guard.**
`FilesystemProcessRepository.build()` already loads every `processes/*.json`
file in one call; while it does, collect `(file, label, claimed_label)` for
every compiled `github-issues` action and reject the batch (naming both
offending files) when:
- two `github-issues` processes share the same `label`, or the same
  `claimed_label` (either would double-claim or leave "claimed" ambiguous
  between two scanners), or
- one process's `claimed_label` equals another's `label` (the second
  scanner would immediately re-claim what the first just claimed, an infinite
  hand-off).

Acceptance: a `harness-todo.json` (`label: harness:todo`) and `triage.json`
(`label: harness:triage, claimed_label: harness:todo`) combination fails
`FilesystemProcessRepository.build()` with a `ProcessValidationError` naming
both files; `triage.json` alone with `claimed_label: harness:validating` (no
collision with `harness-todo.json`'s `label: harness:todo` /
`claimed_label: harness:queued`) builds cleanly.
Document explicitly (in `fs_processes.py`'s module docstring and the processes
spec) that this is a **static, exact-string check only** — it cannot catch a
label typo, a collision mediated through an *agent*-authored label, or two
processes that are logically incompatible without sharing a literal string.
That residual footgun is accepted, not solved, in this increment.

**FR-3 — `finishers` binding grows a structured form; plain string stays valid.**
`Workflow.finishers` today is `dict[str, str]` (step → kind). A finisher that
needs per-binding data (the label mapping) can't fit a bare string. Parsing in
`fs_workflows.py::_parse_workflow` (and the identical contract used by
`FilesystemWorkflowAdmin`'s write path) accepts, per step:
- a plain string `"open-pr"` → `FinisherBinding(kind="open-pr", config={})`
  (unchanged meaning, unchanged JSON for every existing workflow file), or
- an object `{"kind": "label-issue", "labels": {"approve": "harness:todo",
  "reject": "harness:needs-info"}}` → `FinisherBinding(kind="label-issue",
  config={"labels": {...}})` (everything except `"kind"` becomes `config`,
  so a future finisher kind needing different fields adds no parser change).

Acceptance: existing workflow fixtures with `"finishers": {"publish":
"open-pr"}` parse identically to today (`test_models.py`,
`test_fs_workflows.py` unchanged); a new fixture with the object form round-trips
through `FilesystemWorkflowAdmin.write`/`read`; an unknown extra key inside the
object is preserved as opaque `config` (validated by the *finisher factory* at
build time, not by the workflow parser, which doesn't know finisher-specific
shapes — mirroring how `_parse_action` doesn't validate check-specific params
either).

**FR-4 — `label-issue` finisher kind: applies an outcome→label mapping to
`task.data.source`, wrapping (not replacing) the step's own agent behavior.**
This is the core new mechanic. Unlike `open-pr` (which fully replaces the
`land` step's behavior — no agent runs there), `label-issue` must let the
step's persona run **and then** act on the outcome it returned. Concretely:

- `app.build()`'s finisher registry changes shape from `dict[str,
  ConsumerBehavior]` (kind → one fixed, step-agnostic instance) to `dict[str,
  Callable[[str, dict, ConsumerBehavior], ConsumerBehavior]]` (kind → factory
  taking the step name, that binding's `config`, and the `inner` behavior
  `behavior_for` would otherwise have returned for that step — the real
  `ClaudeCliBehavior` built from `catalog.get(step)` when a catalog is wired,
  a `dummy`/`work` fallback otherwise). The default registry's `"open-pr"`
  entry becomes `lambda step, config, inner: landing` (ignores `step`/`config`/
  `inner`, unchanged observable behavior).
- `behavior_for(step)` is restructured so the *existing* resolution chain
  (`RESOLVE_STEP` special case → catalog agent → dummy `work` fallback) always
  computes first, unconditionally; **then**, if `step_finishers` binds a kind
  for this step, the resolved behavior is handed through that kind's factory.
  This is what makes composition possible without a name-keyed branch: the
  registry is consulted by `kind` exactly as before, it just gets more to
  work with.
- A new `harness/behaviors/label_issue.py::LabelIssueBehavior(ConsumerBehavior)`
  wraps an `inner: ConsumerBehavior`, a `client: GithubClient`, and `labels:
  dict[str, str]` (outcome value → label string). `run(task)`:
  1. `result = await self._inner.run(task)` — the wrapped persona/agent step
     runs exactly as it would unbound; the LLM never sees or touches GitHub
     (invariants #9/#26 shape).
  2. If `task.data.get("source")` is missing (no GitHub provenance — e.g. a
     `harness submit` task accidentally routed through this step), skip the
     label call and return `result` with a note appended to `summary`
     (`"no data.source — label not applied"`) — a clear no-op, not a crash.
  3. Otherwise, look up `labels.get(result.outcome.value)`; if present, call
     `self._client.add_label(source["repo"], source["issue"], label)`. An
     outcome with no mapped label (there's a third possible outcome the
     workflow allows but the finisher config doesn't cover) is *also* a no-op
     with a summary note — never an exception, since the label mapping is a
     strict subset of `allowed_outcomes` by construction but a mismatch must
     not fail the task.
  4. Return `result` (the original `BehaviorResult`, `outcome` and routing
     untouched — the finisher labels the issue, it does not reroute the task;
     the dispatcher still routes purely on `(status, lastOutcome)`, invariant #8).
- The concrete `GithubClient` for this factory is supplied by the **caller**
  (`cli.py`'s `_run`, alongside where it already builds a client for
  `GithubForge`/`GithubIssuesCheck`), passed into `build(finishers={...})`
  exactly like any other caller override — `app.py` itself never imports
  `GithubClient` and gains no new parameter. When no `GITHUB_TOKEN` is
  configured, `cli.py` simply doesn't register the `"label-issue"` factory; a
  workflow that binds a step to it then fails at `build()` with today's
  existing "unknown finisher kind" `ValueError` — no new error-handling code.

Acceptance:
- A `triage` step bound to `{"kind": "label-issue", "labels": {"approve":
  "harness:todo", "reject": "harness:needs-info"}}`, backed by a
  `product-manager` `AgentSpec` returning `approve`, ends with `add_label`
  called with `harness:todo` on the task's source issue (a `FakeGithubClient`
  in tests) — and the task still routes via the workflow's `approve` edge
  exactly as an unbound `ClaudeCliBehavior` would have.
- The `reject` path calls `add_label` with `harness:needs-info`.
- A task with no `data.source` reaches `done`/routes normally; `add_label` is
  never called; the summary carries the no-op note.
- Conflicting `label-issue` bindings across served workflows on the same step
  (different `labels` config) fail at build — extending the existing
  kind-conflict check in `app.build()` to compare the whole binding
  (kind *and* config), not just the kind string.

**FR-5 — `product-manager` persona template.**
Ship a documented (not `init`-seeded) `agents/triage.json` template — the file
name must be `triage` because `AgentCatalog.get(name)` looks up by the *step*
name, with no separate step→persona indirection (confirmed: `_agent_persona`/
`_write_default_agents` key off `workflow.steps()` directly). Mirrors the
precedent already set for `processes/autoresolver.json`
(commit `a70512e`'s plan: documented, not auto-seeded, because seeding it
unconditionally at `harness init` would break a token-less `harness run` per
existing `test_cli.py` assertions) — `triage` is opt-in, not every deployment
runs a triage Process, so it must not appear in every fresh `harness init`.
`allowed_outcomes: ["approve", "reject"]`; the prompt is vision/fit-check
content (the PM persona from the task description), not code — it lives in
the template's `prompt` field only, `behaviors/agent.py` gains no branch.

Acceptance: the documented template is valid `AgentSpec` JSON (round-trips
through `FilesystemAgentCatalog.get`); a fresh `harness init` does **not**
write it (existing `test_cli.py` init assertions keep passing unmodified).

**FR-6 — `processes/triage.json` end-to-end template.**
Documented alongside FR-5 (same non-seeded treatment): `action: {"check":
"github-issues", "params": {"label": "harness:triage", "claimed_label":
"harness:validating"}}`, `target: {"step": "triage"}`, and the triage workflow
(or a workflow-less `step` target, per the existing "target any queue"
capability, invariant #35) binds `finishers: {"triage": {"kind":
"label-issue", "labels": {"approve": "harness:todo", "reject":
"harness:needs-info"}}}`.

## Non-functional requirements

- **No new port.** `GithubClient.add_label` already exists; `label-issue` is a
  driver/wiring-level concern (`behaviors/label_issue.py` + `cli.py`), never a
  new abstraction under `ports/`. `dispatcher.py`/`consumer.py` gain no new
  import — `test_architecture.py` must stay green.
- **Fail fast, not silent.** Every new failure mode (bad params, colliding
  labels, unknown finisher kind, conflicting bindings) surfaces at
  `compile_process`/`build()`, never mid-run. This matches ADR-0015/0016's
  existing posture and is the main reviewable property of this change.
- **No regression to existing finishers.** Every existing workflow file
  (plain-string `finishers`, `"open-pr"` only) must parse and build byte-for-byte
  as before — covered by not touching `_parse_workflow`'s existing tests,
  only adding new ones.
- **The LLM never calls GitHub.** `LabelIssueBehavior.run` performs the
  `add_label` call itself, after the wrapped persona returns — invariants
  #9/#26's shape, extended from "the worker commits"/"the worker opens the
  issue" to "the worker applies the label."

## Data model

- `Workflow.finishers: dict[str, FinisherBinding]` (was `dict[str, str]`).
  `FinisherBinding(kind: str, config: dict = {})`, frozen dataclass in
  `models.py`. `Workflow.finisher_for(step) -> FinisherBinding | None` replaces
  today's kind-only accessor (or a thin `finisher_kind_for` shim can keep the
  old call sites compiling — the design step should decide which).
- `processes/triage.json`:
  ```json
  {
    "trigger": {"interval": "5m"},
    "action": {
      "check": "github-issues",
      "params": {"label": "harness:triage", "claimed_label": "harness:validating"}
    },
    "target": {"step": "triage"},
    "dedup": "per-state",
    "sink": {"kind": "none"}
  }
  ```
- `task.data.source` — unchanged shape (`{kind: "github", repo, issue, url}`),
  read (never written) by `LabelIssueBehavior`.
- `agents/triage.json` (template) — standard `AgentSpec` JSON,
  `allowed_outcomes: ["approve", "reject"]`.

## Interfaces

- No new HTTP/API routes. `FilesystemProcessAdmin`'s existing `check_names()`/
  `sink_kinds()`-style discovery methods are untouched by this task (the
  process admin UI's finisher-editing support, if any, is out of scope — see
  Scope below).
- CLI: no new flags. `_process_sources`'s `github_issues_factory` gains one
  more `params.get(...)` line; no new `argparse` argument.
- Internal seam: `app.build(finishers: dict[str, Callable[[str, dict,
  ConsumerBehavior], ConsumerBehavior]] | None)` — a breaking signature change
  to an existing parameter (today `dict[str, ConsumerBehavior]`). Both call
  sites that exist today (`tests/test_app.py`'s `finishers={"record":
  RecordingFinisher()}` style) will need updating to the factory form; this is
  the one place existing tests must change, and should be called out plainly
  in the PR.

## Dependencies and scope

Depends on: `origin/main`'s Check/Process/finisher machinery (FR-0),
`GithubClient.add_label` (exists, unchanged), `GithubIssuesCheck`'s existing
claim mechanics (exists, unchanged — reused verbatim by both the ingestion and
triage processes).

Out of scope (explicitly, per the task notes):
- Posting an issue *comment* with PM reasoning — `GithubClient` has no comment
  verb today. Leave a one-line seam note in `label_issue.py`'s docstring;
  don't build it.
- Whether the triage task needs a worktree — reuses whatever the heal-as-process
  precedent lands on; default to repo-attached (acceptable, and lets the PM
  persona optionally read code for implementation-fit judgment) unless that
  other task's resolution says otherwise.
- Any change to the *ingestion* `github-issues` process/workflow — it keeps
  scanning `harness:todo` → `harness:queued` exactly as today; the triage
  process is purely upstream and invisible to it.
- Process-admin UI support for editing the structured `finishers` object or
  `claimed_label` — the admin's write path only needs to keep accepting
  whatever `compile_process`/`_parse_workflow` accept; a dedicated form field
  is a follow-up, not blocking this increment.

## Rough plan

1. **FR-0**: merge/rebase this worktree onto `origin/main`; confirm
   `pytest -q` green before touching anything.
2. **FR-1 + FR-2**: `cli.py::github_issues_factory` reads `claimed_label`;
   `fs_processes.py`'s `FilesystemProcessRepository.build()` gains the
   cross-file label-collision check. Unit tests: claim-swap with custom
   labels (extend `test_github_issues_check.py`'s existing pattern with a
   `claimed_label` override), collision-rejected/collision-clear process
   pairs (`test_fs_processes.py`).
3. **FR-3**: `models.py` gains `FinisherBinding`; `fs_workflows.py::_parse_workflow`
   accepts both finisher shapes. Tests: plain-string back-compat
   (`test_fs_workflows.py`, `test_models.py` unchanged), new structured-form
   fixture, `FilesystemWorkflowAdmin` round-trip.
4. **FR-4**: `behaviors/label_issue.py::LabelIssueBehavior`; `app.build()`'s
   finisher registry becomes factory-shaped; `behavior_for` reordered to
   compute the inner behavior unconditionally, then apply a bound finisher
   factory. Update the two existing `finishers=` call sites in
   `tests/test_app.py` to the factory form. New tests: approve→label,
   reject→label, no-`data.source`→no-op-with-note, unmapped-outcome→no-op,
   cross-workflow config-conflict fails build.
5. **FR-5 + FR-6**: write (not seed) `agents/triage.json` and
   `processes/triage.json` templates — likely alongside the
   `autoresolver.json` precedent's documentation location; confirm `harness
   init` is untouched by re-running its existing test suite.
6. **Docs**: `CLAUDE.md` — extend invariant #39/#41's text (or add a short
   clause) to name `claimed_label` and the `label-issue` kind; module map gains
   `behaviors/label_issue.py`; the processes spec
   (`docs/superpowers/specs/2026-07-22-processes-design.md`) gains a section;
   consider a short new ADR (next available: **ADR-0018**) if the finisher
   registry's factory-shape change is judged architecturally significant
   enough to warrant one (recommended — it changes an existing port-adjacent
   contract, which is exactly what past ADRs here document).
7. Full `pytest -q`, then an end-to-end test mirroring
   `tests/test_processes_e2e.py`'s style: a `triage` process + step wired with
   a `ScriptedBehavior`/fake agent returning `approve` then `reject` across two
   tasks, a `FakeGithubClient` asserting final labels, and a re-scan after
   approval proving no re-claim (the issue no longer carries `harness:triage`).

## Open questions

- **Finisher registry signature** (FR-4) is the one real design decision in
  this task — I've picked a concrete shape (kind → factory taking
  `(step, config, inner)`) because it's the minimal change that lets
  `label-issue` *compose* with an agent step rather than replace it like
  `open-pr` does, but the **design** step should confirm this exact signature
  (e.g., whether `inner` should instead be lazily-provided via a callable to
  avoid building an unused `ClaudeCliBehavior` when no finisher is bound —
  probably unnecessary, since `ClaudeCliBehavior` construction is cheap and
  side-effect-free) and whether `Workflow.finisher_for` should return
  `FinisherBinding | None` directly or keep a `str`-returning shim for
  existing call sites.
- **`agents/triage.json` vs `agents/product-manager.json` naming** — the task
  notes name the file `product-manager.json`, but `AgentCatalog.get(step)`
  requires the filename to equal the *step* name (`"triage"`), with no
  separate mapping layer. Default assumed here: ship it as `agents/triage.json`
  with the product-manager persona as its *content*; if a distinct step name
  is preferred, the workflow's step must literally be renamed, not the agent
  file.
- **Does the label-collision guard (FR-2) belong in `fs_processes.py` or in a
  separate cross-file validator?** Assumed inline in
  `FilesystemProcessRepository.build()` since it already loads every file in
  one pass; `FilesystemProcessAdmin.write` (single-file validation) can't run
  it at all (no visibility into sibling files) — document that a
  hand-edited/admin-written triage file only gets the collision check at the
  next full `harness run` startup, not at admin-save time. Flagging this
  gap explicitly rather than silently narrowing the guard's coverage.
- **Repo-attachment for the triage task** — deferred to whatever the
  heal-as-process task decides, per the task notes; default assumed is
  repo-attached (worktree exists, PM persona may read code).
