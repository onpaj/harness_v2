# Design — triage Process: `github-issues` params + `label-issue` finisher

## Grounding note

Like `plan-01.md`, this design is grounded by reading `origin/main` directly
(`git show origin/main:<path>`), not this worktree — this worktree is still
74 commits behind and lacks every file named below. Paths are given relative
to `src/harness/` (the actual package root on `origin/main`; the plan's paths
omitted this prefix). Two things changed since the plan was written that this
design corrects:

1. **`github-issues` is not wired through `cli.py::_process_sources`'s
   `github_issues_factory` as a flat function anymore** — it's wired the same
   way, but alongside a second action, `github-conflicts`
   (`GithubConflictsCheck`), both merged over `BUILTIN_CHECKS` into one `checks`
   dict. FR-1's change is a one-line addition to the existing
   `github_issues_factory` closure — same shape the plan assumed, just
   confirmed against the real function (`cli.py:778-787`).
2. **The `Outcome` enum is closed and enforced, not just a naming convention.**
   `models.py::Outcome(str, Enum)` has exactly two members, `DONE` /
   `REQUEST_CHANGES`. `Consumer.tick()` (`consumer.py:79-81`) hard-rejects any
   `BehaviorResult` whose `outcome` is not an `Outcome` instance
   (`_fail(task, "behavior returned an invalid result")`), and
   `claude_cli.py`'s verdict parser does `Outcome(verdict["outcome"])` —
   constructing the enum from the agent's JSON string, which raises
   `ValueError` (→ `VerdictError`) for anything outside the two members. The
   task notes and plan both assume the PM persona returns `"approve"`/
   `"reject"` as if outcomes were open vocabulary; they aren't. **§"The verdict
   vocabulary decision" below is the one real design call this document makes**
   — it resolves this without touching `Outcome`, `AgentSpec`, or verdict
   parsing at all.

No UI section: this is a CLI/config/backend feature with no visible surface
beyond process/agent/workflow JSON files and the existing board (which already
renders arbitrary steps and outcomes generically).

## The verdict vocabulary decision

**Decision: the PM persona reuses `Outcome.DONE` / `Outcome.REQUEST_CHANGES`
verbatim — it does not introduce `"approve"`/`"reject"` as new outcome
literals.** `AgentSpec.allowed_outcomes` for the `triage` step is
`(Outcome.DONE, Outcome.REQUEST_CHANGES)`, exactly like `reviewer`'s spec
today. The persona's *prompt* — not a new enum member — is what tells it
`done` means "this issue is well-defined, in scope, and worth building" and
`request_changes` means "not yet — needs more definition before it's
actionable." This is not a workaround; it's the existing pattern for exactly
this shape of decision (`reviewer` already returns `done`/`request_changes` to
mean "I approve this diff" / "I don't, here's why" — the PM step is the same
gate one level upstream, over an issue instead of a diff).

Why not extend `Outcome` with `APPROVE`/`REJECT` members instead (closer to
the task notes' wording)? Rejected:
- It would touch `models.py` (a file the project's module map calls out as
  importing nothing from the package — foundational), `claude_cli.py`'s
  verdict parser, and every place that enumerates `Outcome` for display —
  for a rename with no behavioral difference from reusing `done`/
  `request_changes`.
- `Consumer`'s `isinstance(result.outcome, Outcome)` check and the router's
  pure `(status, last_outcome)` matching both stay byte-for-byte unchanged
  either way — the only thing that would change is the *label reader's*
  spelling, not any mechanism.
- The task's own framing calls this a "small and generic" pair of additions;
  widening a closed, foundational enum for one persona's vocabulary is the
  opposite of that.

Consequence for the rest of this design: `LabelIssueBehavior`'s `labels`
mapping (§Component design) is keyed by `Outcome.value` strings (`"done"`,
`"request_changes"`), and `processes/triage.json`'s companion workflow maps
`{"done": "harness:todo", "request_changes": "harness:needs-info"}`. The task
notes' `"approve"`/`"reject"` survive only as English words in the persona's
prompt and in this document's prose — never as JSON literals the harness
parses.

## Component design

### 1. `github_issues_factory` gains `claimed_label` (FR-1)

`cli.py:778-787`, today:

```python
def github_issues_factory(params: dict) -> GithubIssuesCheck:
    if client is None:
        raise ProcessValidationError(
            "github-issues action requires GITHUB_TOKEN", field="check"
        )
    return GithubIssuesCheck(
        client=client,
        registry=registry,
        label=params.get("label", args.github_label),
    )
```

Changes to exactly one line, plus a type guard shared by both params:

```python
def github_issues_factory(params: dict) -> GithubIssuesCheck:
    if client is None:
        raise ProcessValidationError(
            "github-issues action requires GITHUB_TOKEN", field="check"
        )
    label = params.get("label", args.github_label)
    claimed_label = params.get("claimed_label", "harness:queued")
    if not isinstance(label, str) or not isinstance(claimed_label, str):
        raise ProcessValidationError(
            "github-issues action requires label/claimed_label to be strings",
            field="params",
        )
    return GithubIssuesCheck(
        client=client,
        registry=registry,
        label=label,
        claimed_label=claimed_label,
    )
```

`GithubIssuesCheck.__init__` already accepts `claimed_label` (default
`"harness:queued"`) — no change needed there at all. This closure is the only
touch point; `BUILTIN_CHECKS` (client-free) is untouched.

### 2. Cross-process label-collision guard (FR-2)

Lives in `drivers/fs_processes.py::FilesystemProcessRepository.build()`
(`fs_processes.py:228-252`), which already loops over every `*.json` in one
call — the only place with visibility across files (`_build_one` compiles one
file in isolation and can't see siblings; `compile_process`, shared with
`FilesystemProcessAdmin.write`, is deliberately single-file too).

Shape: after `_build_one` returns a `ScheduledTrigger` per file, `build()`
additionally inspects — **only for triggers whose action is `github-issues`**
— the `(label, claimed_label)` pair. `ScheduledTrigger` doesn't expose these
today (it holds an opaque `Check`), so the collision check needs the raw
`(path, action_check_name, params)` alongside the compiled trigger. Cleanest
seam: `build()` collects `(path.name, params.get("label", ...), params.get
("claimed_label", ...))` for every file whose `action.check ==
"github-issues"` in the *same* loop that already reads `raw` in `_build_one` —
requires `_build_one` to hand back its `raw["action"]` too (or `build()`
re-reads `action` from the same `raw` dict it already parses; either is a
small, local change, not a new pass over the filesystem). Concretely,
`_build_one` returns `(ScheduledTrigger, dict | None)` — the second element is
`raw.get("action")` when `action.get("check") == "github-issues"`, else
`None` — and `build()` unpacks the pair, appending non-`None` entries to a
list before checking for collisions:

```python
seen: dict[str, str] = {}  # label-or-claimed-label -> file name
def _claim(value: str, file: str) -> None:
    if value in seen and seen[value] != file:
        raise ProcessValidationError(
            f"processes {seen[value]!r} and {file!r} both use the "
            f"github-issues label {value!r} (label or claimed_label) — "
            f"this would double-claim or leave claiming ambiguous",
            field="params",
        )
    seen[value] = file
```
called once per file for its `label` and once for its `claimed_label` — the
three collision cases the plan names (`label==label`, `claimed_label==
claimed_label`, `claimed_label==label` across two files) all reduce to "the
same string used as a `github-issues` label/claimed_label in two files,"
because `_claim` is symmetric: it doesn't distinguish which role a string
plays, only that it's claimed by two different files. That is deliberately
coarser than the plan's three bullets — it also catches a fourth accidental
case (two processes sharing the *same* `label` **and** *same*
`claimed_label`, e.g. a copy-pasted file) for free, at no extra cost.

The module docstring (already present, `fs_processes.py:1-31`) gains one
paragraph documenting the residual footgun verbatim per the plan's FR-2:
static, exact-string only; an agent-authored label, a typo, or a
logically-incompatible-but-textually-different pair are not caught.
`FilesystemProcessAdmin.write` (single-file) cannot run this check — its
docstring gains a one-line cross-reference saying so.

### 3. `Workflow.finishers` grows a structured form (FR-3)

`models.py:166-198`, `Workflow.finishers: dict[str, str]` becomes
`dict[str, FinisherBinding]`:

```python
@dataclass(frozen=True)
class FinisherBinding:
    """A step's bound finisher: a kind plus that kind's own config, opaque to
    everything except the finisher factory that reads it at build time."""

    kind: str
    config: dict[str, Any] = field(default_factory=dict)
```

`Workflow.finisher_for(step)` today returns `str | None`; it becomes:

```python
def finisher_for(self, step: str) -> FinisherBinding | None:
    return self.finishers.get(step)
```

No `str`-returning shim is kept — the only caller is `app.build()`
(confirmed: `git grep finisher_for` on `origin/main` has exactly one call
site, `app.py`), so there is nothing else to keep compiling. Introducing a
shim for a single, already-known call site would be exactly the kind of
premature back-compat surface the project's own conventions warn against.

`drivers/fs_workflows.py::_parse_workflow` (`fs_workflows.py:81-102`) accepts,
per step, either shape:

```python
finishers: dict[str, FinisherBinding] = {}
for step, raw_binding in raw_finishers.items():
    if step not in known_steps:
        raise ValueError(
            f"workflow {name!r} has a finisher for unknown step {step!r}"
        )
    if isinstance(raw_binding, str):
        if not raw_binding:
            raise ValueError(
                f"workflow {name!r} has an invalid finisher for step {step!r}: {raw_binding!r}"
            )
        finishers[step] = FinisherBinding(kind=raw_binding)
    elif isinstance(raw_binding, dict):
        kind = raw_binding.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError(
                f"workflow {name!r} has an invalid finisher kind for step {step!r}: {kind!r}"
            )
        config = {k: v for k, v in raw_binding.items() if k != "kind"}
        finishers[step] = FinisherBinding(kind=kind, config=config)
    else:
        raise ValueError(
            f"workflow {name!r} has an invalid finisher for step {step!r}: {raw_binding!r}"
        )
```

`config` is *not* validated here — same posture as `_parse_action` not
validating check-specific params (`fs_processes.py:141-157`): the parser
knows the shape is either "a kind string" or "a kind plus opaque config," it
does not know what a given finisher kind's config must contain. That's the
finisher factory's job, at `app.build()` time (§4).

`FilesystemWorkflowAdmin.write_raw`/`read_raw` (`fs_workflows.py:146-205`)
keep passing raw JSON text through unmodified end-to-end (they already do —
`write_raw` calls `_parse_workflow` only to *validate*, never to
re-serialize) — so the structured form round-trips for free, with zero admin
changes beyond what `_parse_workflow` already does.

### 4. Finisher registry becomes factory-shaped; `LabelIssueBehavior` (FR-4)

This is the composition mechanic. Today, `app.py:520-583`:

```python
finisher_registry: dict[str, ConsumerBehavior] = {"open-pr": landing}
finisher_registry.update(finishers or {})

step_finishers: dict[str, str] = {}
for workflow in resolved.values():
    for step, kind in workflow.finishers.items():
        ...
        step_finishers[step] = kind
if landing_step not in step_finishers:
    step_finishers[landing_step] = "open-pr"
for step, kind in step_finishers.items():
    if kind not in finisher_registry:
        raise ValueError(...)

def behavior_for(step: str) -> ConsumerBehavior:
    kind = step_finishers.get(step)
    if kind is not None:
        return finisher_registry[kind]          # <-- returns EARLY, replaces
    if step == RESOLVE_STEP and catalog is not None:
        return ResolveConflictBehavior(...)
    if catalog is not None:
        return ClaudeCliBehavior(...)
    return work
```

`open-pr` *replaces* the step's behavior outright — `land` never runs an
agent. `label-issue` must *wrap* the step's own agent behavior: the PM
persona runs, then the worker labels the issue based on what it returned.
Restructured:

```python
FinisherFactory = Callable[[str, dict, ConsumerBehavior], ConsumerBehavior]

finisher_registry: dict[str, FinisherFactory] = {
    "open-pr": lambda step, config, inner: landing,
}
finisher_registry.update(finishers or {})

step_bindings: dict[str, FinisherBinding] = {}
for workflow in resolved.values():
    for step, binding in workflow.finishers.items():
        if step in step_bindings and step_bindings[step] != binding:
            raise ValueError(
                f"step {step!r} is bound to conflicting finisher bindings "
                f"{step_bindings[step]!r} and {binding!r} across served workflows"
            )
        step_bindings[step] = binding
if landing_step not in step_bindings:
    step_bindings[landing_step] = FinisherBinding(kind="open-pr")
for step, binding in step_bindings.items():
    if binding.kind not in finisher_registry:
        raise ValueError(
            f"step {step!r} names unknown finisher kind {binding.kind!r} "
            f"(known: {', '.join(sorted(finisher_registry))})"
        )

def _inner_behavior_for(step: str) -> ConsumerBehavior:
    """Exactly today's behavior_for body, minus the finisher branch — always
    computed, whether or not a finisher is bound, so a finisher can wrap it."""
    if step == RESOLVE_STEP and catalog is not None:
        return ResolveConflictBehavior(...)
    if catalog is not None:
        spec = catalog.get(step)
        effective_timeout = spec.timeout if spec.timeout is not None else agent_timeout
        return ClaudeCliBehavior(..., spec=spec, ...)
    return work

def behavior_for(step: str) -> ConsumerBehavior:
    inner = _inner_behavior_for(step)
    binding = step_bindings.get(step)
    if binding is None:
        return inner
    return finisher_registry[binding.kind](step, binding.config, inner)
```

Notes on this shape:
- `FinisherBinding` needs `__eq__` for the conflict check above (`!=`
  comparison across workflows) — it's a frozen `@dataclass`, which gets
  structural equality for free; no extra code.
- The conflict check now compares the *whole binding* (kind and config), not
  just the kind string, per the plan's FR-4 acceptance criterion — two served
  workflows binding the same step to `label-issue` with *different* `labels`
  maps is now also a build-time conflict, whereas under the old string-only
  check it would have silently picked whichever workflow's dict iteration
  landed last.
- `open-pr`'s factory ignores all three arguments and returns the
  already-built `landing` singleton — this is observably identical to today
  (`open-pr` still fully replaces, never wraps), so `test_workflow_without_
  finishers_key_still_binds_land_to_landing` and
  `test_custom_step_bound_to_open_pr_lands_through_the_forge` pass unmodified.
- `_inner_behavior_for` is unconditionally called for *every* step now,
  including `land` and any other step exclusively finished by `open-pr` — a
  `ClaudeCliBehavior`/`ResolveConflictBehavior` gets constructed there even
  though `open-pr`'s factory discards it. Both are cheap, side-effect-free
  constructions (no I/O in `__init__` for either — confirmed by reading both
  classes: they just store references), so this is a non-issue, exactly as
  the plan's open question anticipated. No lazy/callable-`inner` wrapper is
  needed.
- Callers passing `finishers={"record": RecordingFinisher()}` (a bare
  `ConsumerBehavior`, `tests/test_app.py:937,950`) must become
  `finishers={"record": lambda step, config, inner: recorder}` — this is the
  one breaking call-site change the plan flagged, and it is confined to two
  lines in `test_app.py`.

**`behaviors/label_issue.py::LabelIssueBehavior`** — new module:

```python
class LabelIssueBehavior(ConsumerBehavior):
    """Wraps a step's own behavior and, after it returns, applies an
    outcome -> label mapping to the task's source GitHub issue.

    The wrapped behavior (typically ClaudeCliBehavior, built from the step's
    AgentSpec) runs exactly as it would unbound — this class never touches
    the agent, the prompt, or the verdict parsing. It only reads the outcome
    the inner behavior already produced and, if data.source is present and the
    outcome has a mapped label, calls GithubClient.add_label. The label call
    never changes routing: the original BehaviorResult (outcome, summary,
    data) is returned as-is, so the dispatcher routes purely on what the inner
    behavior decided (invariant #8).

    Seam note: a natural companion is posting an issue comment with the
    persona's reasoning (e.g. "needs: acceptance criteria"). GithubClient has
    no comment verb today — deliberately out of scope here; add_comment would
    be a new GithubClient method plus one more line in run(), nothing else
    would need to change.
    """

    def __init__(
        self,
        *,
        inner: ConsumerBehavior,
        client: GithubClient,
        labels: dict[str, str],
    ) -> None:
        self._inner = inner
        self._client = client
        self._labels = labels

    async def run(self, task: Task) -> BehaviorResult:
        result = await self._inner.run(task)

        source = task.data.get("source")
        if not source:
            return replace(
                result, summary=f"{result.summary} (no data.source — label not applied)"
            )

        label = self._labels.get(result.outcome.value)
        if label is None:
            return replace(
                result,
                summary=f"{result.summary} (outcome {result.outcome.value!r} has no "
                f"mapped label — label not applied)",
            )

        self._client.add_label(source["repo"], source["issue"], label)
        return result
```

- `labels: dict[str, str]` is keyed by `Outcome.value` (`"done"` /
  `"request_changes"`), per §"The verdict vocabulary decision" — not by
  arbitrary step-specific words. This keeps the finisher generic across any
  future step it might be bound to, not just `triage`.
- No new port (`ports/behavior.py`, `ports/agent.py`, `ports/triggers.py` all
  untouched) — `GithubClient` is a driver type, so `behaviors/label_issue.py`
  importing it is exactly the same layering `behaviors/landing.py` already has
  importing `Forge`. `test_architecture.py`'s guard is "dispatcher/consumer
  import only ports" — `behaviors/*` importing driver-adjacent types is
  established precedent, not a new exception.
- The two "no-op, not a crash" branches both mutate only `summary` via
  `dataclasses.replace` — `outcome`/`data` pass through untouched, so routing
  is never affected by a missing `data.source` or an unmapped outcome.

**Wiring in `cli.py`** — alongside where a `GithubClient` is already built for
`GithubForge`/`GithubIssuesCheck` (`_process_sources`, `_run`): when a client
exists, register `"label-issue"` in the `finishers` dict passed to
`build()`:

```python
finishers = {}
if client is not None:
    finishers["label-issue"] = lambda step, config, inner: LabelIssueBehavior(
        inner=inner, client=client, labels=config.get("labels", {})
    )
```

When `client is None` (no `GITHUB_TOKEN`), `"label-issue"` is simply never
registered — a workflow binding a step to it then fails at `build()` through
the *existing* "unknown finisher kind" `ValueError`, no new error path.
`app.py` gains no new parameter and never imports `GithubClient` — the
factory closure is entirely `cli.py`'s concern, matching how `github_issues_
factory` closes over the same client today.

### 5. `product-manager` persona template (FR-5)

Documented template, not `harness init`-seeded — same precedent as
`processes/autoresolver.json` in the sibling "heal-as-process" task (still
in-flight as of this writing; not yet on `origin/main`), for the identical
reason: `harness init` must keep working with no `GITHUB_TOKEN`
(`test_cli.py`'s init assertions), and not every deployment runs a triage
Process. File name is **`agents/triage.json`** (not `product-manager.json`)
because `AgentCatalog.get(name)` is keyed by the workflow *step* name with no
separate persona-name indirection (confirmed: both `_agent_persona`-style
lookups and `_write_default_agents` key off `workflow.steps()` directly, and
`app.build()`'s `_inner_behavior_for` calls `catalog.get(step)` — never a
persona name). The PM identity lives in the file's *content*, not its
filename:

```json
{
  "name": "triage",
  "prompt": "You are the harness's product-manager gatekeeper for inbound GitHub issues. An issue reaches you carrying its title and body under task.data. Judge it against three questions: (1) is it clearly and completely defined — could someone start work without asking clarifying questions? (2) is it understandable — is the problem and desired outcome stated in plain language? (3) does it fit the application's vision and have a plausible implementation path in this repository (you may read the checked-out code to judge fit)? Return outcome 'done' if all three hold — this issue is ready to become harness:todo work. Return 'request_changes' if any one doesn't, and say in your summary specifically what's missing (e.g. 'needs: acceptance criteria', 'needs: scope narrowed to one repo').",
  "allowed_outcomes": ["done", "request_changes"]
}
```

Round-trips through `FilesystemAgentCatalog.get` (standard `AgentSpec` JSON,
no new fields). A fresh `harness init` does not write it — verified by
re-running `test_cli.py`'s existing init assertions unmodified.

### 6. `processes/triage.json` + a `triage` workflow (FR-6)

**Design call not fully pinned by the plan: the triage target is a named
workflow, not a workflow-less `{"step": "triage"}`.** Reason: a finisher
binding (`label-issue`) lives in `Workflow.finishers`, and `app.build()`
builds `step_bindings` exclusively from `resolved.values()` — i.e. from
*served, named* workflows. A workflow-less task (`target: {"step": ...}`)
never contributes a `Workflow` at all, so there would be no way to bind
`label-issue` to it under the mechanism §4 builds. A minimal one-step
workflow costs nothing extra (`router.py:27-28`: for a task with `workflow is
None` route already returns `Finished()` unconditionally regardless of
outcome — a *named* single-step workflow with explicit `done`/
`request_changes` → `end` transitions behaves identically in terms of where
the task ends up, but is the only shape that can carry a `finishers` entry).

`agents/triage.json` template's companion `workflows/triage.json` (also
documented, not seeded):

```json
{
  "name": "triage",
  "start": "triage",
  "transitions": [
    {"from": "triage", "on": "done", "to": "end"},
    {"from": "triage", "on": "request_changes", "to": "end"}
  ],
  "finishers": {
    "triage": {
      "kind": "label-issue",
      "labels": {"done": "harness:todo", "request_changes": "harness:needs-info"}
    }
  }
}
```

`processes/triage.json`:

```json
{
  "trigger": {"interval": "5m"},
  "action": {
    "check": "github-issues",
    "params": {"label": "harness:triage", "claimed_label": "harness:validating"}
  },
  "target": {"workflow": "triage"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

`--workflow` (or however `harness run` names served workflows) must include
`triage` alongside the primary/ingestion workflow for this to be live — a
deployment operator's wiring choice, not code; documented alongside the
template, mirroring how the ingestion process/workflow pairing is documented
today.

End-to-end flow: `GithubIssuesCheck` (scanning `harness:triage`) claims an
issue by swapping to `harness:validating` (invariant: at-most-once, reused
verbatim), emits a task with `data.source` + `repository` set, workflow-less
routing places it at `triage`'s start. `ClaudeCliBehavior` (built from
`agents/triage.json`) runs the PM persona; `LabelIssueBehavior` wraps it,
reads the returned `done`/`request_changes`, calls `add_label` with
`harness:todo`/`harness:needs-info` on the same issue (the `harness:
validating` label is *not* removed — labels are additive here, matching how
`GithubIssuesCheck` itself only ever adds/removes the two labels it manages
and never touches a third). The task finishes to `done/` either way (both
transitions target `end`). The *existing*, untouched ingestion Process
(scanning `harness:todo`) picks up an approved issue on its own next tick —
no code path between the two Processes; the hand-off is entirely a GitHub
label, exactly as the task's design goal states.

## Data schemas

### `Workflow` / `FinisherBinding` (models.py)

```python
@dataclass(frozen=True)
class FinisherBinding:
    kind: str
    config: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class Workflow:
    name: str
    start: str
    transitions: tuple[Transition, ...]
    max_parallel: dict[str, int] = field(default_factory=dict)
    finishers: dict[str, FinisherBinding] = field(default_factory=dict)

    def finisher_for(self, step: str) -> FinisherBinding | None:
        return self.finishers.get(step)
```

### `processes/*.json` — `github-issues` action params (extended)

```json
{
  "action": {
    "check": "github-issues",
    "params": {
      "label": "harness:triage",
      "claimed_label": "harness:validating"
    }
  }
}
```
`label` (str, default: CLI's `--github-label`) — the scan label.
`claimed_label` (str, default `"harness:queued"`) — the swap-to label on
claim. Both validated as strings at `compile_process` time; a non-string
value is a `ProcessValidationError(field="params")`.

### `workflows/*.json` — `finishers` (extended shape)

```json
{
  "finishers": {
    "publish": "open-pr",
    "triage": {"kind": "label-issue", "labels": {"done": "harness:todo", "request_changes": "harness:needs-info"}}
  }
}
```
Plain string → `FinisherBinding(kind=<string>, config={})` (unchanged
meaning). Object → every key except `"kind"` becomes `config` verbatim.

### `agents/triage.json` — `AgentSpec` (no schema change)

```json
{
  "name": "triage",
  "prompt": "<PM persona prompt>",
  "allowed_outcomes": ["done", "request_changes"]
}
```

### `task.data.source` (unchanged, read-only here)

```json
{"kind": "github", "repo": "onpaj/harness_v2", "issue": 42, "url": "https://github.com/..."}
```
Read by `LabelIssueBehavior.run`; never written by it.

### Internal seam: `app.build()`'s `finishers` parameter (breaking signature)

Before: `finishers: dict[str, ConsumerBehavior] | None`.
After: `finishers: dict[str, Callable[[str, dict, ConsumerBehavior],
ConsumerBehavior]] | None` — kind name → factory `(step, config, inner) ->
ConsumerBehavior`. Both existing call sites in `tests/test_app.py`
(`finishers={"record": RecordingFinisher()}`, two occurrences) change to
`finishers={"record": lambda step, config, inner: recorder}`.

## Interfaces

- No new HTTP/API routes. `ProcessAdmin.check_names()`/`sink_kinds()` are
  untouched. A `ProcessAdmin`/`WorkflowAdmin` UI editing `claimed_label` or
  the structured `finishers` object works today by construction — both admins
  validate through the same `compile_process`/`_parse_workflow` this design
  extends, so the write path needs zero admin-specific code; a dedicated
  form field for either is a UI polish follow-up, not blocking.
- CLI: no new flags, no new `argparse` arguments. `github_issues_factory`
  gains one `params.get(...)` line.
- `harness init`: unmodified — neither `agents/triage.json` nor
  `workflows/triage.json` nor `processes/triage.json` is seeded.

## Non-functional properties preserved

- **No new port** — `label-issue` lives entirely in `behaviors/` (driver-
  adjacent) + `cli.py` (wiring); `dispatcher.py`/`consumer.py` gain no new
  import, `test_architecture.py` stays green.
- **Fail fast** — bad params, label collisions, unknown finisher kind,
  conflicting bindings (kind *and* config) all surface at `compile_process`/
  `build()`, never mid-run.
- **No regression** — every existing plain-string `finishers` workflow file
  parses and builds identically; `open-pr`'s factory is behaviorally
  identical to the old fixed-instance registry entry.
- **The LLM never calls GitHub** — `LabelIssueBehavior.run` performs
  `add_label` itself, strictly after the wrapped persona/agent returns.

## Out of scope (unchanged from the plan)

- Issue comments carrying PM reasoning — `GithubClient` has no comment verb;
  left as a one-line seam note in `LabelIssueBehavior`'s docstring.
- Any change to the ingestion `github-issues` process/workflow.
- Process-admin UI form fields specific to `claimed_label` or the structured
  `finishers` object (the write path already accepts both without change;
  a dedicated form control is a follow-up).
- Repo-attachment policy for triage tasks beyond "whatever `GithubIssuesCheck`
  already does" (it stamps `repository=name` per issue via the registry —
  the triage task is repo-attached automatically, no new code needed; this
  also resolves the plan's open question — no dependency on the sibling
  heal-as-process task after all, since `GithubIssuesCheck`'s existing
  `Observation.repository` field already supplies it).

## Documentation follow-ups (for the implementation step, not designed here)

- `CLAUDE.md` invariant #41 gains a clause naming the factory-shaped registry
  and `label-issue`; invariant #39 gains a clause naming `claimed_label`.
  Module map gains `behaviors/label_issue.py`.
- `docs/superpowers/specs/2026-07-22-processes-design.md` gains a section for
  `claimed_label`.
- A new ADR (next available number at implementation time — confirm by
  listing `docs/adr/` fresh, since the sibling heal-as-process task may have
  claimed one) documenting the finisher-factory generalization: it changes an
  existing port-adjacent contract (`app.build()`'s `finishers` parameter
  shape) exactly as past ADRs here (0007, 0016) document that class of
  change.
