# Architecture assessment — triage Process: `github-issues` params + `label-issue` finisher

## Verdict

**Approved, with two blocking corrections, one moderate correction, and one
test-coverage correction.** `plan-01.md` and `design-01.md` are well-grounded
overall — I re-fetched `origin/main` (`e4485d6`) and independently re-read
every file both documents quote (`models.py`, `app.py`, `cli.py`,
`fs_processes.py`, `fs_workflows.py`, `fs_agents.py`, `github_issues_check.py`,
`github_client.py`, `ports/agent.py`, `ports/triggers.py`,
`scheduled_trigger.py`, `checks.py`, `consumer.py`, `claude_cli.py`,
`test_app.py`, `test_cli.py`, `test_models.py`, `test_fs_workflows.py`,
`test_architecture.py`) rather than trusting the quotes. FR-1, FR-2's target
location, FR-3's parser shape, the verdict-vocabulary decision, the
named-workflow decision, and the repo-attachment-for-free finding all hold up
byte-for-byte. But **FR-4's central artifact — `behaviors/label_issue.py`
importing `GithubClient`** — violates a test that already exists on
`origin/main` and that CLAUDE.md calls load-bearing
(`test_architecture.py` guards invariant #41's shape). This is not a
style nit; as designed, `pytest -q` fails at the architecture-test layer the
moment `LabelIssueBehavior` is added. The fix is small (change one directory),
but it must happen before implementation starts, not be discovered mid-PR.

## Alignment with existing patterns

Most of this task is exactly the shape ADR-0015/0016 already established:
`github-issues` gaining a second param is one line in an existing closure
(`cli.py:786`, confirmed unchanged since the design read it); the finisher
registry generalizing from "kind → fixed behavior" to "kind → factory" is a
natural continuation of ADR-0016's own stated scope (its Consequences section
already anticipated "a new finishing action... is a registry entry", just not
one that needed to *see* the step's own behavior). Nothing here asks for a new
port class hierarchy or a change to `dispatcher.py`/`consumer.py`/`router.py` —
confirmed by reading `consumer.py:79-84` (the `isinstance(result.outcome,
Outcome)` gate is exactly as strict as design describes) and
`test_architecture.py`'s `test_consumer_has_no_branch_on_outcome_value` (an
AST-level check, not just a naming convention — `LabelIssueBehavior` reading
`result.outcome.value` to index a dict is fine because that happens in a
*behavior*, not in `consumer.py`).

Where this task's architecture genuinely earns its own review (not just a
rubber stamp of the design) is the one place it introduces something the
codebase hasn't needed before: **a `ConsumerBehavior` that must depend on a
driver type with no existing port**. Every other `ConsumerBehavior` today
either depends on nothing driver-shaped (`DummyBehavior`) or depends on a
*port* (`LandingBehavior` → `ports.forge.Forge`; `ClaudeCliBehavior` →
`ports.agent.AgentRunner`; `ResolveConflictBehavior` → same). `label-issue` is
the first finisher whose entire reason to exist is one driver verb
(`GithubClient.add_label`) that the task's own notes correctly judge doesn't
deserve a new port. That combination — "needs a driver, but not a new port" —
has no precedent in `behaviors/`, and the design's claim that it does
(comparing to `landing.py` importing `Forge`) is the one place it
misclassified a port as a driver. See the correction below; the resolution
(move the file to `drivers/`) is itself precedented — `github_issues_check.py`
already imports `GithubClient` from a sibling driver file
(`github_issues_check.py:20`), because `drivers/` importing `drivers/` was
never restricted, only `behaviors/` importing `drivers/` was.

## Proposed architecture — corrections to `design-01.md`

### Correction 1 (blocking): `LabelIssueBehavior` must live in `drivers/`, not `behaviors/`

`test_architecture.py:263-269` (present on `origin/main` today, unmodified by
this task):

```python
def test_behaviors_import_only_ports_not_drivers():
    """Behaviors (behaviors/) reach for ports and models, not for other drivers."""
    for path in (SOURCE / "behaviors").glob("*.py"):
        assert not any(
            module.startswith("harness.drivers")
            for module in imported_modules(path)
        ), f"{path.name} imports a driver"
```

`design-01.md`'s §4 places `LabelIssueBehavior` at `behaviors/label_issue.py`
and has it `from harness.drivers.github_client import GithubClient` (its own
listed import line). That import alone fails this test — it is a blanket rule
over every file under `behaviors/`, not conditioned on whether a new port was
introduced. Design's justification ("`GithubClient` is a driver type, so
`behaviors/label_issue.py` importing it is exactly the same layering
`behaviors/landing.py` already has importing `Forge`") is incorrect:
`landing.py:12` imports `from harness.ports.forge import Forge` — a **port**
(`ports/forge.py`), not a driver. `GithubClient` lives at
`drivers/github_client.py` (confirmed: `class GithubClient(ABC)` at
`github_client.py:63`) — it is a driver by the same definition the module map
uses everywhere else in this codebase. There is no existing behavior that
imports a driver; this would be the first, and the test that would catch it
already exists and is green today.

**Fix — relocate, don't re-abstract.** Move the class to
`drivers/label_issue.py` (naming symmetric with `drivers/github_issues_check.py`,
which already imports `GithubClient` cleanly — `github_issues_check.py:20`).
Everything else in design's §4 is unchanged:

- It still implements `ConsumerBehavior` (a port, `ports/behavior.py`) and
  `Task`/`BehaviorResult` (`models.py`) — the only imports it needs besides
  `GithubClient`.
- It is still constructed only in `cli.py`'s wiring closure and handed to
  `app.build(finishers={...})` exactly as designed — `app.py` still never
  imports `GithubClient` or `harness.drivers.label_issue` (the factory
  signature `Callable[[str, dict, ConsumerBehavior], ConsumerBehavior]` is
  driver-agnostic by construction; `app.py`'s only obligation is to *call*
  whatever factory it's handed).
- `test_only_app_and_cli_wire_drivers` (`test_architecture.py:272-279`) does
  **not** block this: its `SOURCE.glob("*.py")` is non-recursive (`pathlib`
  glob semantics — `*` does not cross a `/`), so it only inspects files
  directly under `src/harness/` (`app.py`, `cli.py`, `models.py`, `router.py`,
  `consumer.py`, `dispatcher.py`, `projection.py`, `source_poller.py`,
  `healer.py`, `pr_watcher.py`, `merge_reconciler.py`,
  `issue_reconciler.py`) — it never walks `drivers/` or `behaviors/` at all.
  The only guard that matters here is `test_behaviors_import_only_ports_not_drivers`,
  and relocating the file out of `behaviors/` satisfies it trivially.
- The task notes' "no new port" instruction is fully honored — this is a
  directory correction, not an abstraction change. It does mean the module map
  table in CLAUDE.md gets a new `drivers/label_issue.py` entry instead of a
  `behaviors/label_issue.py` one; flag this in the PR description since it's a
  visible deviation from what the task notes assumed.
- One documentation consequence worth stating in the new ADR (see below): this
  is the first `ConsumerBehavior` implementation that lives in `drivers/`
  rather than `behaviors/`, breaking the file-location symmetry with
  `LandingBehavior`/`ClaudeCliBehavior`/`ResolveConflictBehavior`. That's a
  deliberate, narrow exception (driver dependency, no port), not a precedent
  for moving other behaviors — worth one sentence so a future reader doesn't
  read it as "behaviors/ is deprecated."

### Correction 2 (blocking): `cli.py`'s `_run` has no `GithubClient` in scope to close over

Design's wiring section says the factory is registered "alongside where a
`GithubClient` is already built for `GithubForge`/`GithubIssuesCheck`
(`_process_sources`, `_run`)" — implying `_run` already has one. It does not.
Re-reading `cli.py` directly: **every** GitHub-touching helper independently
builds its own `HttpGithubClient(token)` when not handed one, and none of them
return it to the caller:

- `_github_sources` (`cli.py:602-646`) — local `client`, not returned.
- `_github_reflectors` (`cli.py:649-682`) — same.
- `_mergeability_sources` (`cli.py:685-718`) — same.
- `_process_sources` (`cli.py:749-813`) — same; its `client` is a closure
  variable inside `_process_sources` itself, invisible to `_run`.
- `_build_forge` (`cli.py:1448-1461`) — builds yet another one, inline.
- `_build_merge_checker`/`_build_issue_checker` (`cli.py:1463-1482`) — two more.

`_run` (`cli.py:1485-...`) itself never constructs a `GithubClient` — it only
ever calls these helpers, each of which is independently offline-safe
(returns `[]`/`None` without a token). This is a real, established pattern
(five independent client constructions per `run` invocation when a token is
set) — cheap and stateless, not a bug, just not what design assumed when it
said `_run` already has a client to close the finisher factory over.

**Fix — one more client, built the same way the other five are.** In `_run`,
before calling `_process_sources`, add:

```python
token = os.environ.get("GITHUB_TOKEN")
client = HttpGithubClient(token) if token else None
```

then thread it two places:
1. `_process_sources(args, root, registry, clock=SystemClock(),
   known_targets=known_targets, client=client)` — `_process_sources` already
   accepts `client: GithubClient | None = None` (used today by
   `test_process_sources_builds_a_github_issues_process`,
   `cli.py:756`/`test_cli.py:951-958`), so this is a one-argument addition at
   the call site, not a signature change. It also removes a redundant
   sixth-client construction that would otherwise happen inside
   `_process_sources` itself.
2. The `finishers` dict built just before `build(...)` is called:
   ```python
   finishers: dict[str, Callable[[str, dict, ConsumerBehavior], ConsumerBehavior]] = {}
   if client is not None:
       finishers["label-issue"] = lambda step, config, inner: LabelIssueBehavior(
           inner=inner, client=client, labels=config.get("labels", {})
       )
   ```
   passed as `build(..., finishers=finishers or None, ...)`.

This is a two-line, purely-additive change to `_run` — no existing call site
of `_github_sources`/`_build_forge`/etc. changes, and `fake_build(*args,
**kwargs)` in `test_cli.py` (used by every `_run`-level test that stubs
`build`, e.g. `test_cli.py:87`, `:111`) accepts arbitrary kwargs, so adding
`finishers=` to the real `build(...)` call risks nothing there.

### Correction 3 (moderate): FR-2's collision guard needs the CLI's `label` default, which `fs_processes.py` cannot see

`github_issues_factory` resolves an omitted `label` to `args.github_label`
(`cli.py:786`), an operator-configurable flag (`--github-label`, default
`"harness:todo"`, `cli.py:1772-1776`) — **not** a literal `fs_processes.py`
can hardcode. Design's FR-2 pseudocode collects `(label, claimed_label)` per
file without naming where the effective default for an *omitted* `label`
comes from; as written, a triage file that (mis)omits `label` would silently
compare against the wrong default inside `FilesystemProcessRepository.build()`,
undermining the exact footgun FR-2 exists to catch.

**Fix — thread the CLI's default through, mirroring `repository`/
`worktree_root`/`known_targets`.** `FilesystemProcessRepository.build()`
already takes four caller-supplied parameters with sane defaults
(`fs_processes.py:228-236`); add a fifth:

```python
def build(
    self,
    *,
    clock: Clock,
    checks: dict[str, CheckFactory] = BUILTIN_CHECKS,
    repository: str | None = None,
    worktree_root: str | None = None,
    known_targets: set[str] | None = None,
    default_github_issues_label: str = "harness:todo",
) -> list[ScheduledTrigger]:
```

`_process_sources` passes `default_github_issues_label=args.github_label`.
Confirmed safe: `FilesystemProcessRepository.build()` has exactly one
production caller (`cli.py:805-813`) — `git grep FilesystemProcessRepository\(`
on `origin/main` finds three test call sites
(`test_cli.py:548`, `test_fs_process_admin.py:31`, `test_fs_processes.py:31`,
`test_processes_e2e.py:72`), all of which call `.build(clock=...)` with no
positional/keyword collision, so a new defaulted keyword-only parameter is
fully backward compatible.

**Also simplify design's `_build_one` return-signature change.** Design
proposes `_build_one` return `(ScheduledTrigger, dict | None)` so `build()`
can see each file's raw `action` for the collision check. That's an unforced
internal-contract change — `_build_one` is private and untested directly
(confirmed: zero hits for `_build_one` in `test_fs_processes.py`), but there's
a simpler shape with an identical test surface: have `build()` **re-parse
each file's raw JSON itself**, independently of `_build_one`, purely to
extract `(action.get("check"), action.get("params", {}))` for files where
`action.get("check") == "github-issues"`:

```python
def build(self, *, clock, checks=BUILTIN_CHECKS, repository=None,
          worktree_root=None, known_targets=None,
          default_github_issues_label="harness:todo"):
    if not self._root.exists():
        return []
    triggers = []
    seen: dict[str, str] = {}
    for path in sorted(self._root.glob("*.json")):
        triggers.append(self._build_one(path, clock=clock, checks=checks,
                                          repository=repository,
                                          worktree_root=worktree_root,
                                          known_targets=known_targets))
        raw = json.loads(path.read_text(encoding="utf-8"))
        action = raw.get("action") if isinstance(raw, dict) else None
        if isinstance(action, dict) and action.get("check") == "github-issues":
            params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}
            label = params.get("label", default_github_issues_label)
            claimed = params.get("claimed_label", "harness:queued")
            for value in (label, claimed):
                if isinstance(value, str):
                    if value in seen and seen[value] != path.name:
                        raise ProcessValidationError(
                            f"processes {seen[value]!r} and {path.name!r} both use "
                            f"the github-issues label {value!r} (label or "
                            f"claimed_label) — this would double-claim or leave "
                            f"claiming ambiguous",
                            field="params",
                        )
                    seen[value] = path.name
    return triggers
```

This re-reads each file's JSON a second time (already validated to be
well-formed by `_build_one` at that point in the loop — if it weren't,
`_build_one` already raised before this code runs), which is a startup-time,
one-file-at-a-time cost, not a hot path — trivial. It costs nothing in test
risk (`_build_one`'s signature and every existing caller of it stay
untouched) versus design's proposal, at the cost of one redundant
`json.loads` per process file at startup. I recommend this over design's
tuple-return change purely for surface-area minimization; either is
architecturally sound, but this one touches less.

Keep FR-2's acceptance criteria and the "static, exact-string only" footgun
documentation exactly as design specifies — that reasoning is sound and
unaffected by which of the two implementation shapes is chosen.

## Correction 4 (test coverage): two existing unit tests assert the pre-`FinisherBinding` shape and will fail, contrary to design's "unchanged" claim

Design's FR-3 acceptance criteria state "existing workflow fixtures... parse
identically to today (`test_models.py`, `test_fs_workflows.py` unchanged)".
That's true for the **JSON acceptance** claim (a plain string in a workflow
file still compiles) but not for two specific existing assertions that
construct or inspect `Workflow.finishers`/`finisher_for` directly and compare
against a bare string — these **will** fail once `Workflow.finishers` becomes
`dict[str, FinisherBinding]`, and both must be updated in the same commit that
changes `models.py`:

- `test_models.py:230-241`
  (`test_workflow_finisher_for_reads_configured_kind`) constructs
  `Workflow(..., finishers={"publish": "open-pr"})` directly (bypassing
  `_parse_workflow` entirely — this is a raw dataclass construction) and
  asserts `workflow.finisher_for("publish") == "open-pr"`. Update to
  `finishers={"publish": FinisherBinding(kind="open-pr")}` and
  `finisher_for("publish") == FinisherBinding(kind="open-pr")`.
- `test_fs_workflows.py:180-189`
  (`test_finishers_are_parsed_and_exposed`) reads a real JSON file through
  `FilesystemWorkflowRepository.get` (i.e., through `_parse_workflow`, the
  actual read path) and asserts `workflow.finishers == {"review": "open-pr"}`
  and `finisher_for("review") == "open-pr"`. Update both assertions to compare
  against `FinisherBinding(kind="open-pr")`.

Everything else in both files (`test_workflow_finisher_for_defaults_to_none`,
`test_definition_without_finishers_defaults_to_empty`,
`test_finishers_not_an_object_raises`, `test_finisher_for_unknown_step_raises`,
`test_finisher_invalid_kind_raises`) is unaffected — confirmed by reading each:
they assert `{}`/`None`/error paths that don't depend on the value type. Also
worth noting, since it slightly undercuts the plan's stated risk profile:
`app.py`'s actual production code (`app.py:534`,
`for step, kind in workflow.finishers.items():`) never calls
`Workflow.finisher_for` at all — it walks `.finishers.items()` directly.
`finisher_for` is exercised only by the two tests above; it has no other
production caller (confirmed: `git grep finisher_for` on `origin/main` returns
exactly its definition plus these five test lines). This makes design's
"no str-shim needed" call even easier to ratify than design itself argued —
there is no runtime code path depending on `finisher_for`'s return type at
all, only tests, and both are enumerated above.

## Ratified decisions (design's open questions)

- **Finisher factory signature `(step, config, inner) -> ConsumerBehavior`** —
  ratified. I independently verified the cost claim: `ClaudeCliBehavior.__init__`
  (`agent_behavior.py:26-41`) and `ResolveConflictBehavior.__init__`
  (`resolve_conflict.py:25-40`) both only assign constructor arguments to
  `self`, no I/O, no side effect — so computing `_inner_behavior_for(step)`
  unconditionally for every step (including ones exclusively finished by
  `open-pr`, which discards it) is free. No lazy/callable-`inner` wrapper
  needed, exactly as design's own open question anticipated.
- **No `str`-returning shim for `Workflow.finisher_for`** — ratified, and for
  a stronger reason than design gave (see Correction 4: it has zero
  production callers today).
- **`agents/triage.json`, not `agents/product-manager.json`** — ratified;
  confirmed `FilesystemAgentCatalog.get(name)` (`fs_agents.py:94-111`) reads
  `<root>/<name>.json` with `name` supplied by the caller (the step name), and
  never reads a `"name"` key out of the JSON body itself. One correction to
  design's example template: drop the `"name": "triage"` key from the shipped
  JSON — `_parse_agent_spec` (`fs_agents.py:36-78`) never reads it, and
  neither does the real seeded template (`_agent_definition_template`,
  `cli.py:463-470`, emits only `prompt`/`model`/`fallback_model`/
  `allowed_tools`/`allowed_outcomes`/`timeout`). Match that shape exactly, no
  extra key, so the shipped template is indistinguishable in structure from
  what `harness agent init` would generate.
- **`triage` as a named workflow, not a workflow-less step target** —
  ratified; confirmed `app.build()`'s `step_finishers` map is built
  exclusively from `resolved.values()` (`app.py:532-534`, iterating served,
  *named* workflows) — a workflow-less task never contributes a `Workflow`
  object, so there is genuinely no other way to bind `label-issue` to it.
- **Repo-attachment "for free"** — ratified and independently re-derived:
  `GithubIssuesCheck.evaluate()` stamps `Observation(repository=name, ...)`
  (`github_issues_check.py:62-77`), and
  `ScheduledTrigger._task_for` sets `repository=obs.repository or
  self._repository` and `worktree=f"{worktree_root}/{task_id}"` whenever
  `worktree_root` is non-`None` (`scheduled_trigger.py:81-97`) —
  `_process_sources` always passes a non-`None` `worktree_root`
  (`cli.py:806`). So a triage task is both repo- and worktree-attached with
  zero new code, exactly as design's closing paragraph concludes. No
  dependency on the sibling heal-as-process task.
- **Verdict vocabulary reuses `done`/`request_changes`, no new `Outcome`
  members** — ratified; confirmed `Outcome` is a closed two-member
  `(str, Enum)` (`models.py:30-34`), `Consumer.tick` hard-rejects anything
  else (`consumer.py:79-84`, `isinstance(result.outcome, Outcome)`), and
  `claude_cli.py`'s verdict parser does `Outcome(verdict["outcome"])`
  (line 147) — constructing the enum from the agent's raw string, raising
  `VerdictError` for any value outside the two members. Widening this for one
  persona would be a foundational-file change for no behavioral gain; design's
  rejection reasoning is correct as written.
- **New ADR-0018 for the finisher-factory generalization** — ratified. The
  next available number is confirmed free (`origin/main`'s `docs/adr/` tops
  out at `0017-landing-syncs-base-before-proposing.md`). Its text should also
  record Correction 1 above (the `drivers/`-not-`behaviors/` file placement)
  as a deliberate, narrow exception — this is exactly the kind of fact a
  future reader would otherwise have to re-derive from `test_architecture.py`
  by trial and error.

## Implementation guidance — sequencing

1. **FR-0**: merge/rebase onto `origin/main`. Confirm `pytest -q` green
   before any further edit, so any later red is attributable to this task,
   not to drift. Since more commits may land on `main` before implementation
   starts, re-run the specific `git grep`/line-number checks this document and
   `design-01.md` rely on (`_process_sources`, `app.py`'s finisher block,
   `test_architecture.py`'s two behavior-location tests) rather than trusting
   the line numbers quoted here blindly.
2. **FR-1 + FR-2**, together (`cli.py::github_issues_factory` gains
   `claimed_label` + the type guard; `fs_processes.py::build()` gains
   `default_github_issues_label` and the inline collision check per
   Correction 3). Unit tests: claim-swap with custom labels
   (extend `test_github_issues_check.py`'s existing pattern), collision-clear
   and collision-rejected process-file pairs in `test_fs_processes.py`
   (include a case that specifically exercises the *omitted*-`label` default
   path, since that's exactly the gap Correction 3 closes).
3. **FR-3**: `models.py` gains `FinisherBinding`; `fs_workflows.py::_parse_workflow`
   accepts both shapes. In the **same commit**, apply Correction 4's two test
   updates (`test_models.py:230-241`, `test_fs_workflows.py:180-189`) — do not
   let `models.py` change land with those tests still red, even briefly.
4. **FR-4**: create `drivers/label_issue.py::LabelIssueBehavior` (Correction 1
   — not `behaviors/`); `app.build()`'s finisher registry becomes
   factory-shaped exactly per `design-01.md`'s §4 pseudocode (that part of the
   design is correct once the class's location is fixed); apply Correction 2's
   client-threading fix in `cli.py::_run`. Update the two existing
   `finishers=` call sites in `test_app.py` (lines 937, 950) to the factory
   lambda form in the same commit as the `app.py` signature change — a
   partial commit here leaves `test_app.py` red. New tests: approve→label,
   reject→label, no-`data.source`→no-op-with-note, unmapped-outcome→no-op,
   cross-workflow config-conflict fails build (comparing the whole
   `FinisherBinding`, not just `kind`).
5. **FR-5 + FR-6**: write `agents/triage.json` (without the extraneous `name`
   key, per the ratified correction above) and `workflows/triage.json` +
   `processes/triage.json` as documented-not-seeded templates. Confirm
   `harness init`'s existing test suite (`test_cli.py`) is untouched by
   re-running it as-is.
6. **Docs**: `CLAUDE.md` — module map gains `drivers/label_issue.py` (not
   `behaviors/label_issue.py`); invariant #39-region gains `claimed_label`;
   invariant #41-region gains the factory-shaped registry and `label-issue`,
   plus a one-line note on why this one finisher lives under `drivers/`. Add
   `docs/adr/0018-...md` per the ratified decision above. Extend
   `docs/superpowers/specs/2026-07-22-processes-design.md` with
   `claimed_label`.
7. Full `pytest -q`, with explicit attention to `test_architecture.py` (all
   of it, not just the two tests this review focused on — a passing full
   architecture-test run is the actual acceptance bar, not a targeted subset).
8. End-to-end test mirroring `tests/test_processes_e2e.py`'s existing style
   (`test_github_issues_process_ingests_a_labelled_issue_once_per_bucket` is
   the closest existing template): a `triage` process + workflow wired with a
   scripted/fake agent returning `done` then `request_changes` across two
   tasks, a `FakeGithubClient` asserting final labels, and a re-scan after
   approval proving no re-claim (the issue no longer carries the scan label).

## Risks and mitigations

- **Risk: implementing FR-4 literally as `design-01.md` wrote it** (file under
  `behaviors/`, importing `GithubClient` directly) **fails
  `test_behaviors_import_only_ports_not_drivers` immediately.** This is the
  single highest-value catch of this review — low effort to avoid (one
  directory), high cost to discover late (would look like a working feature
  until the full suite runs). **Mitigation**: Correction 1 above, applied at
  the point the file is created, not as a follow-up fix.
- **Risk: the finisher-registry signature change lands without both
  `test_app.py` call sites updated in the same commit**, leaving a red build
  window. **Mitigation**: called out explicitly in step 4 above; this is the
  one place `design-01.md` itself already flagged the breaking change, so the
  risk is knowing about it, not forgetting it — the mitigation is discipline
  in commit sequencing, not new design work.
- **Risk: `_run`'s finisher wiring silently never activates** if the
  `client`-threading fix (Correction 2) is skipped and someone instead tries
  to reach into `_process_sources`'s local `client` variable (which is not
  possible in Python without restructuring the function) — the likely
  fallback under time pressure is building a **second, independent**
  `HttpGithubClient` just for the finisher factory. That would technically
  work (the client is stateless) but silently duplicates the established
  "one client per wiring site" pattern in a way that's harder to reason about
  later. **Mitigation**: Correction 2's exact fix (single client built once in
  `_run`, threaded into both `_process_sources` and the finisher factory) is
  strictly better and no more code — use it as written, don't improvise a
  second client.
- **Risk: FR-2's collision guard is implemented against a hardcoded
  `"harness:todo"` literal instead of the threaded `default_github_issues_label`**,
  making it silently wrong for any deployment that overrides
  `--github-label`. **Mitigation**: Correction 3's test guidance explicitly
  calls for a test case that omits `label` and relies on the default, which
  would catch this at review time if the parameter isn't actually threaded
  through from `args.github_label`.
- **Risk: the shipped `agents/triage.json` template drifts from the real
  agent-spec schema** (e.g., keeping the `"name"` key, or a field order that
  doesn't match `_agent_definition_template`'s output) — low severity (the
  parser ignores unknown keys, so it wouldn't break anything today) but adds
  confusion for a reader comparing the doc template to what `harness agent
  init` generates. **Mitigation**: the ratified correction above (drop
  `"name"`) — cheap to apply now, before the file exists anywhere.

## Prerequisites before implementation begins

1. `git merge origin/main` (FR-0), full suite green before any further edit.
2. Re-confirm (quick `git grep`) that `origin/main`'s `_process_sources`,
   `app.py`'s finisher block, and `test_architecture.py`'s
   `test_behaviors_import_only_ports_not_drivers`/
   `test_only_app_and_cli_wire_drivers` still read as quoted here — if time
   has passed since this assessment, more commits may have landed.
3. No other prerequisite blocks starting. `GithubClient.add_label`,
   `GithubIssuesCheck`'s claim mechanics, and the finisher-as-data machinery
   (ADR-0016) are all already shipped and require no changes beyond what
   Corrections 1-4 specify.
