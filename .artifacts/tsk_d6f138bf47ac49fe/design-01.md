# Design — JSON templates / predefined defaults for steps that require JSON input

## Overview

The only place in the harness today where an operator authors step-shaped JSON
from a blank slate is `agents/<step>.json` (`AgentSpec`, read by
`FilesystemAgentCatalog`). `harness init` already writes a valid one for every
step of the workflow it initializes, using a hardcoded persona table
(`AGENT_PERSONAS`) plus a generic fallback for unknown step names. This design
pulls that "compute a valid definition for a step" logic out of
`_write_default_agents` into one small, named function, and adds a CLI verb —
`harness agent init <step>` — that calls it on demand for a single step of an
already-initialized root. No new file format, no new module, no runtime
dependency: this is a refactor of `cli.py` plus one new subcommand, entirely
inside the existing wiring layer.

This is a CLI-only feature — there is no graphical or web surface. The board
(`api/`) stays read-only per invariant #5/#11; the "interaction" is a terminal
command and its stdout, covered under Component design/Interfaces below.

## Component design

### `_agent_definition_template` — the template function (FR-1)

A new pure function in `src/harness/cli.py`, extracted from the dict literal
currently inlined in `_write_default_agents`:

```python
def _agent_definition_template(step: str, allowed_outcomes: list[str]) -> dict:
    """The full, valid AgentSpec-JSON dict for `step`.

    Known steps (AGENT_PERSONAS) get their carried-over persona and tool list;
    any other step name gets the generic fallback. `allowed_outcomes` is the
    caller's responsibility (derived from a workflow via
    `_allowed_outcomes_for`) — this function has no knowledge of workflows.
    """
    return {
        "prompt": _agent_persona(step),
        "model": None,
        "fallback_model": None,
        "allowed_tools": _agent_tools(step),
        "allowed_outcomes": allowed_outcomes,
    }
```

It has no I/O and no dependency on `HarnessLayout`/`Workflow` — it takes the
two things that vary (`step`, `allowed_outcomes`) and returns a dict, matching
the shape `FilesystemAgentCatalog.get` already parses. `_agent_persona` and
`_agent_tools` (existing, unchanged) supply the two branches:
`AGENT_PERSONAS.get(step)` hit → the persona/tools tuple; miss → the generic
one-paragraph instruction / empty tool list.

Placement stays `cli.py`, not a new module: `AGENT_PERSONAS`, `_agent_persona`,
`_agent_tools` and `_allowed_outcomes_for` already live there and the function
has no reason to be reachable from outside the CLI layer.

### `_write_default_agents` — rewired, not rewritten (FR-2)

```python
def _write_default_agents(layout: HarnessLayout, workflow) -> None:
    layout.agents.mkdir(parents=True, exist_ok=True)
    for step in workflow.steps():
        if step == LANDING_STEP:
            continue
        path = layout.agents / f"{step}.json"
        if path.exists():
            continue
        definition = _agent_definition_template(
            step, _allowed_outcomes_for(workflow, step)
        )
        path.write_text(
            json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8"
        )
```

Only the dict-construction line changes; loop, skip-`LANDING_STEP`,
skip-if-exists and the write itself are untouched. Byte-identical output to
today for every existing test.

### `harness agent init <step>` — on-demand scaffolding (FR-3)

A new subcommand group, argparse-wired alongside `service`:

```python
agent = subparsers.add_parser("agent", help="manage per-step agent definitions")
agent_actions = agent.add_subparsers(dest="action", required=True)

agent_init = agent_actions.add_parser(
    "init", help="scaffold agents/<step>.json from the built-in template"
)
agent_init.add_argument("step")
agent_init.add_argument("--root", default=None)
agent_init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
agent_init.add_argument("--force", action="store_true")
agent_init.set_defaults(handler=_agent_init)
```

Handler, mirroring the checks and error shapes `_init`/`_submit` already use:

```python
def _agent_init(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"error: {root} is not initialized, run `harness init`", file=sys.stderr)
        return 2

    workflows = FilesystemWorkflowRepository(layout.workflows)
    try:
        workflow = workflows.get(args.workflow)
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.step == LANDING_STEP:
        print(
            f"error: {args.step!r} is the landing step, driven by the built-in "
            "landing behavior, not an agent",
            file=sys.stderr,
        )
        return 2
    if args.step not in workflow.steps():
        print(
            f"error: step {args.step!r} is not part of workflow {args.workflow!r}",
            file=sys.stderr,
        )
        return 2

    layout.agents.mkdir(parents=True, exist_ok=True)
    path = layout.agents / f"{args.step}.json"
    text = path.read_text(encoding="utf-8") if path.exists() else None

    if text is not None and not args.force:
        print(f"{path} already exists, not overwritten (use --force to replace it)")
        print(text)
        return 0

    definition = _agent_definition_template(
        args.step, _allowed_outcomes_for(workflow, args.step)
    )
    text = json.dumps(definition, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    print(str(path))
    print(text)
    return 0
```

Design choices, keyed to the plan's acceptance criteria:

- **Root/workflow checks reuse existing idioms** — the "not initialized"
  message is character-for-character what `_submit` already prints;
  `WorkflowNotFound` is caught the same way `_init` catches it. No new error
  vocabulary for the operator to learn.
- **`land` is rejected explicitly**, not silently skipped. `_write_default_agents`
  skips `LANDING_STEP` because `land` runs `LandingBehavior`, never
  `ClaudeCliBehavior` — there is no valid agent template for it. Scaffolding
  `agents/land.json` on request would produce a file `FilesystemAgentCatalog`
  can parse but the dispatcher never reads, which is a worse trap than an
  error. This is the one place the design goes beyond the plan's listed
  checks, and it's a direct consequence of invariant #12 ("Landing is a step,
  not magic").
- **Step-not-in-workflow** is checked against `workflow.steps()` — the same
  method `_write_default_agents`'s loop already uses, so "part of the
  workflow" means exactly what it means everywhere else in the codebase.
- **Existing-file/`--force`** prints the current file's content either way
  (existing, untouched, or freshly written) — satisfying "prints the file's
  path and its JSON content to stdout" for both outcomes, and letting a
  scripted caller diff old vs. new without a second `cat`.
- **No exception path**: the four failure checks are structural (root,
  workflow, step name) and all resolved before any file write, so there's no
  partial-write state to clean up.

### Unaffected components

`FilesystemAgentCatalog`, `AgentSpec`, `ClaudeCliBehavior`, `build()`,
`dispatcher.py`, `consumer.py`, `router.py` — none of these change. This is
entirely inside `cli.py`'s existing wiring role (invariant #1), so
`test_architecture.py`'s guarded imports are unaffected.

## Data schemas

No new schema. The template's output is exactly today's `AgentSpec` JSON
encoding (documented in `fs_agents.py`, unchanged):

```jsonc
{
  "prompt": "string, required",
  "model": null,                 // or a model name string
  "fallback_model": null,        // or a model name string
  "allowed_tools": [],           // list[str], Claude Code tool names
  "allowed_outcomes": ["done"]   // list[str], must parse as Outcome
}
```

Two computed instances of that shape, both already valid on arrival
(round-trips through `FilesystemAgentCatalog.get` with no exception — FR-4):

- **Known step** (`plan`/`design`/`architecture`/`development`/`review`):
  `prompt`/`allowed_tools` from `AGENT_PERSONAS[step]` verbatim;
  `allowed_outcomes` from the target workflow's transitions leaving `step`.
- **Unknown step** (any other name declared in a custom workflow):
  `prompt` = `f"You are the agent for the '{step}' step. Read the artifacts of the previous steps in your working directory, do the step's work, and write the output where the task prompt directs you."`,
  `allowed_tools` = `[]`, `allowed_outcomes` as above.

`allowed_outcomes` is never hardcoded in the template — it's always derived
from the live workflow definition via `_allowed_outcomes_for(workflow, step)`,
so a template for a step with a custom transition set (e.g. a `triage` step
with `on: "escalate"`) reflects the real edges, not a guess.

### CLI request/response shape

`harness agent init <step>` has no JSON request; its "response" is stdout:

```
<path-to-agents-dir>/<step>.json
{
  "prompt": "...",
  "model": null,
  "fallback_model": null,
  "allowed_tools": [...],
  "allowed_outcomes": [...]
}
```

preceded by a one-line notice when the file already existed and `--force` was
not given. Exit codes: `0` success (written or left untouched), `2` on every
checked failure (uninitialized root, unknown workflow, `land`, step not in
workflow) — matching the `0`/`2` convention already used by `_init`/`_submit`.
