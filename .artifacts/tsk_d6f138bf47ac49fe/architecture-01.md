# Architecture — JSON templates / predefined defaults for steps that require JSON input

## Verdict

Approved as designed. I read `src/harness/cli.py`, `drivers/fs_workflows.py`,
`drivers/fs_agents.py` and `models.py` end to end against `design-01.md`'s code
listings — every function name, signature and behavior the design references
(`AGENT_PERSONAS`, `_agent_persona`, `_agent_tools`, `_allowed_outcomes_for`,
`_write_default_agents`, `HarnessLayout.agents`, `FilesystemWorkflowRepository.get`,
`WorkflowNotFound`, `Workflow.steps()`, `FilesystemAgentCatalog.get`) exists today
exactly as described, with the exact signature the design assumes. There is no
drift between the design and the code it will land on. Development can proceed
directly from `design-01.md`'s listings; this document adds the integration
checks, the one correctness fix, and the ordering that keeps the change safe.

## Alignment with existing patterns and integration points

- **Wiring stays in `cli.py`.** `_agent_definition_template`, the new `agent
  init` subcommand and its handler are pure additions to the file that already
  owns `AGENT_PERSONAS`/`_write_default_agents`/`DEFAULT_DEFINITION`. Nothing
  crosses into `dispatcher.py`, `consumer.py`, `router.py`, or any `drivers/`
  module. Invariant #1 ("you may swap a driver, never its surroundings") and
  invariant #17 (`AgentCatalog`/`RepositoryRegistry` unknown to
  dispatcher/consumer) are both satisfied by construction — this is a CLI-layer
  refactor plus one CLI-layer command, not a new port.
- **Subcommand-group shape matches `service`.** `service install|uninstall|status`
  already establishes the `add_parser("group") → add_subparsers(dest="action",
  required=True) → add_parser("verb")` pattern (`cli.py:797-823`). `agent init`
  reuses it verbatim — no new argparse idiom introduced.
- **Error shape matches `_submit`/`_init`/`_service_install`.** All three
  existing handlers print `error: ...` to `stderr` and return `2` for a
  precondition failure (uninitialized root: `cli.py:314-316`, `540-542`;
  `WorkflowNotFound`: `cli.py:97-101`). `_agent_init` reuses both messages
  character-for-character, so an operator scripting around one error shape
  doesn't need a second one for this command.
- **Read path matches `FilesystemAgentCatalog`/`FilesystemWorkflowRepository`.**
  Both are simple, already-tested filesystem drivers (`<root>/<name>.json`,
  `AgentNotFound`/`WorkflowNotFound` on missing/broken/invalid). The new code
  doesn't reimplement any of their parsing — it only *writes* JSON they already
  know how to read, and FR-4's round-trip test is what proves that.
- **`allowed_outcomes` derivation is unchanged.** `_allowed_outcomes_for`
  (`cli.py:275-281`) is a pure fold over `workflow.transitions` with no I/O —
  reusing it as-is for FR-3 keeps "what outcomes can this step return" defined
  in exactly one place, consistent with invariant #4's spirit (decisions
  derived from data, not duplicated).

## Proposed architecture

No new component. One function extracted, one call site rewired, one CLI verb
added — all inside `cli.py`. Three decisions worth recording:

**1. Where the template function lives — `cli.py`, not a new module.**
Considered: a `templates.py` sibling module. Rejected: `AGENT_PERSONAS`,
`_agent_persona`, `_agent_tools` and `_allowed_outcomes_for` all already live
in `cli.py` and have no reason to be importable from outside it — `AgentSpec`
itself is a `ports/agent.py` concept, but the *personas* are CLI-owned
authoring defaults, not domain data. Moving them would widen the module's
public surface for a function with exactly two callers, both in the same
file. Keep it boring: extract in place.

**2. `land` is rejected, not silently scaffolded.**
`_write_default_agents` already skips `LANDING_STEP` in its loop, because
`land` runs `LandingBehavior` and never reaches `ClaudeCliBehavior` — there is
no `AgentSpec` for it to consume (invariant #12: "landing is a step, not
magic"). `harness init` silently omitting a file is fine, since the operator
never asked for `land` specifically. But `harness agent init land` is a
targeted, explicit request; silently doing nothing there is a worse failure
mode than skip-with-no-feedback in `init`'s loop, because the operator has no
signal anything happened. Design's choice to hard-error is correct and is the
one place FR-3 goes beyond a literal reading of the plan — call this out
explicitly in the PR description so the review step doesn't flag it as scope
creep.

**3. `--force` still prints the file either way.**
This is a UX choice, not a correctness one, but it's worth keeping: it means
`harness agent init <step>` is safe to run repeatedly to *inspect* a step's
current template (no `--force`, existing file, prints it, exit 0) as well as
to *write* it. One command serves both "show me the template" and "give me
the template" — don't split it into two commands later without a concrete
need.

## Correctness fix required before implementation

The design's `_agent_init` listing (`design-01.md:129-131`) has an ordering
bug relative to its own stated goal ("no partial-write state to clean up",
`design-01.md:169-171`):

```python
layout.agents.mkdir(parents=True, exist_ok=True)   # <- side effect ...
path = layout.agents / f"{args.step}.json"
text = path.read_text(encoding="utf-8") if path.exists() else None
```

`layout.agents.mkdir()` runs *before* the `land`/step-not-in-workflow checks
that follow it in the same listing (`design-01.md:115-127` are written above
this block, but the listing's actual line order in the design doc places the
mkdir call in the same paragraph without re-stating the checks run first —
confirm in implementation that the four checks execute in this order: root →
workflow → land → step-in-workflow → *then* `mkdir`/write). If `mkdir` runs
before the `land` or step-not-in-workflow check, a rejected call
(`harness agent init land` or `harness agent init bogus-step`) still creates
an empty `agents/` directory as a side effect of the error path. That's a
minor leak, not a data-corruption risk (an empty directory is harmless and
`init`/future calls handle a pre-existing `agents/` fine), but it contradicts
the design's own "no exception path... no partial-write state" claim and is
trivial to avoid. **Implementation guidance: perform `layout.agents.mkdir(...)`
only immediately before the write, after every validation check has passed —
mirror `_init`'s ordering, where `layout.workflows.mkdir()` happens early
because workflows are that command's whole subject, but here `agents/` is a
side effect of a step-scoped command and shouldn't appear before the step is
known to be valid.**

This is the only deviation from the design worth flagging; everything else in
`design-01.md`'s two code listings can be implemented as written.

## Implementation guidance

**Where new code goes** (all in `src/harness/cli.py`):

1. `_agent_definition_template(step: str, allowed_outcomes: list[str]) -> dict`
   — placed directly below `_agent_tools` (near its two dependencies), above
   `_allowed_outcomes_for` or below it, doesn't matter — keep the four
   template-related functions (`_agent_persona`, `_agent_tools`,
   `_allowed_outcomes_for`, `_agent_definition_template`) contiguous.
2. `_write_default_agents` — one-line change: replace the inline dict literal
   at `cli.py:292-298` with a call to `_agent_definition_template(step,
   _allowed_outcomes_for(workflow, step))`. Everything else in the function
   (mkdir, loop, `LANDING_STEP` skip, exists-skip, write) is untouched.
3. `_agent_init(args: argparse.Namespace) -> int` — new handler, placed near
   `_submit` (same "single-entity CRUD-ish" shape: resolve root, validate,
   write, print). Ordering inside it, corrected per the fix above:
   root-initialized check → `FilesystemWorkflowRepository(layout.workflows).get(args.workflow)`
   (catch `WorkflowNotFound`) → `land` rejection → step-in-`workflow.steps()`
   check → **then** `layout.agents.mkdir(parents=True, exist_ok=True)` →
   exists/`--force` branch → write + print.
4. Argparse wiring — new `agent` subparser group in `main()`, placed after
   `run` and before `service` (keeps the top-level command list in roughly
   the order operators reach for them: init → submit → run → agent → service
   → update). Wire `agent_init.set_defaults(handler=_agent_init)` exactly as
   `service_install`/`service_uninstall`/`service_status` already do.

**Data flow:** operator invokes `harness agent init <step> [--root]
[--workflow] [--force]` → `_agent_init` resolves `HarnessLayout(root)` and
`FilesystemWorkflowRepository(layout.workflows).get(workflow)` (the same two
objects `_init`/`_run` already construct) → validates → calls
`_agent_definition_template(step, _allowed_outcomes_for(workflow, step))` →
serializes with `json.dumps(..., indent=2, ensure_ascii=False)` (matching
every other JSON write in this file: `DEFAULT_DEFINITION`, `_write_default_agents`,
`_write_default_repos`, `_submit`'s task write — stay consistent) → writes
`layout.agents / f"{step}.json"` → prints path + content to stdout.

**Test placement:** `tests/test_cli.py`, alongside the existing `test_init_*`
tests, driven the same way (`main([...])` against `tmp_path`, assertions on
the written file and on `capsys.readouterr()`), not via new fakes. FR-4's
round-trip test additionally imports `FilesystemAgentCatalog` from
`drivers.fs_agents` (already imported by other test modules — reuse, don't
reinvent) and calls `.get(step)` on the written file.

## Risks and mitigations

- **Risk: silent scope creep into `submit --data` or `repos.json`
  templating.** The issue's phrasing ("steps that require JSON input") could
  be read to include the free-form task payload. Mitigation: the plan
  explicitly scopes this out with a stated rationale (payload shape is
  workflow/source-specific, not step-owned) and `repos.json` already defaults
  to a valid `{}` — no blank-field problem exists there. Development should
  not touch `_submit` or `_write_default_repos` beyond what FR-2 requires.
- **Risk: `--force` overwrite is destructive to a hand-tuned persona.**
  Mitigation: already designed as opt-in (default is quiet no-op on an
  existing file, matching `init`'s idempotency), consistent with this
  project's general stance that destructive behavior is never the default.
  No further mitigation needed.
- **Risk: `mkdir`-before-validation ordering bug** (see Correctness fix
  above) — low blast radius (an empty directory), but worth fixing before
  merge since a test (`land`/unknown-step failure path) can assert
  `layout.agents` does *not* exist afterward, turning this into a regression
  guard rather than a one-time fix.
- **Risk: divergence between `_write_default_agents`'s and `_agent_init`'s
  JSON serialization** (e.g. one gains a field later and the other doesn't).
  Mitigation: both go through the same `_agent_definition_template` function
  and the same `json.dumps(..., indent=2, ensure_ascii=False)` call shape —
  this is exactly what FR-1/FR-2 are for. No further action needed as long as
  development doesn't duplicate the serialization line instead of sharing it.

## Prerequisites before implementation begins

None outstanding. All dependencies named in the plan's "Depends on" section
(`AGENT_PERSONAS`, `_agent_persona`, `_agent_tools`, `_allowed_outcomes_for`,
`HarnessLayout.agents`, `FilesystemAgentCatalog`, `build()`/`WorkflowNotFound`
handling) exist in the codebase today, verified by direct read during this
step. Development can start directly from `design-01.md`'s two code listings,
applying the single ordering fix above.
