# Plan — JSON templates / predefined defaults for steps that require JSON input

## Summary

Every workflow step's behaviour is driven by an `AgentSpec` read from
`agents/<step>.json` (`FilesystemAgentCatalog`). Today the harness only ever
writes that file for the five built-in default-workflow steps, and only once,
at `harness init` time (`_write_default_agents` in `src/harness/cli.py`). The
moment an operator adds a custom step to a workflow, or re-initializes an
already-initialized root, there is no way to get a valid starting point for
that step's JSON — they must hand-author `agents/<step>.json` from scratch,
inferring the required shape (`prompt`, `model`, `fallback_model`,
`allowed_tools`, `allowed_outcomes`) from a docstring in `fs_agents.py`. This
plan turns the existing "write a sensible default" logic into a reusable
template mechanism, and exposes it on demand via a new CLI command so any
step — built-in or custom — can be scaffolded with valid, pre-filled JSON at
any time, not only during `init`.

## Context

The harness already solved this problem once, implicitly: `AGENT_PERSONAS`
plus `_write_default_agents` write a full, valid `agents/<step>.json` for
every step of the workflow passed to `harness init`. That's exactly the
"template / predefined default" behaviour this issue asks for — but it's
wired to a single call site and a single point in time. It doesn't help an
operator who:

- adds a new step to `workflows/<name>.json` after `init` has already run
  (there is no re-scaffold path — the new step's agent file simply doesn't
  exist, and the task fails with `AgentNotFound` the first time the
  dispatcher reaches it), or
- wants to see/regenerate one step's template without touching the rest of
  an already-configured `agents/` directory.

Generalizing the existing pattern into a small, named mechanism — reusable by
both `init` and a new on-demand command — closes that gap without inventing a
new JSON dialect or a new authoring surface.

## Functional requirements

**FR-1 — A reusable agent-definition template.**
A single function computes the full, valid `AgentSpec` JSON dict for a given
step: `{"prompt", "model", "fallback_model", "allowed_tools",
"allowed_outcomes"}`. For one of the five known steps (`plan`, `design`,
`architecture`, `development`, `review`) it returns the existing persona and
tool list (`AGENT_PERSONAS`); for any other step name it returns the existing
generic-but-valid fallback (persona describing the step name + no default
tools). `allowed_outcomes` is derived from the workflow's transitions
(`_allowed_outcomes_for`), exactly as today.
- Acceptance: calling the function for `"review"` returns the current
  `_REVIEW_PERSONA` verbatim; calling it for an unknown step name `"triage"`
  returns a dict whose `prompt` mentions `"triage"` and whose
  `allowed_outcomes` matches the edges leaving `"triage"` in the workflow.

**FR-2 — `harness init` keeps working unchanged, sourced from FR-1.**
`_write_default_agents` is rewritten to call the FR-1 template function per
step instead of building the dict inline. No behavioural change: same files,
same content, same idempotency (an existing `agents/<step>.json` is left
untouched).
- Acceptance: `test_init_creates_layout_and_default_workflow` and
  `test_init_is_idempotent_and_keeps_edits` continue to pass unmodified.

**FR-3 — On-demand scaffolding: `harness agent init <step>`.**
A new CLI subcommand writes `agents/<step>.json` from the FR-1 template for
one step of an already-initialized root, at any time — not only during
`harness init`. Flags: `--root` (as elsewhere), `--workflow` (default
`"default"`, used to look up the step's `allowed_outcomes` and to confirm the
step belongs to that workflow), `--force` (overwrite an existing file;
without it, an existing file is left untouched, matching `init`'s
idempotency rule). On success the command prints the file's path and its
JSON content to stdout, so the operator sees the pre-filled template without
opening the file.
- Acceptance:
  - Missing file: `harness agent init triage --workflow default` (where
    `triage` is a step of that workflow) creates
    `agents/triage.json` with FR-1's template and prints it.
  - Existing file, no `--force`: file is left byte-for-byte unchanged, exit
    code reports "already exists" without error noise (mirrors `init`'s
    quiet idempotency).
  - Existing file, `--force`: file is overwritten with the fresh template.
  - Uninitialized root: fails cleanly (`error: ... run \`harness init\``),
    same message shape as `_submit`'s existing check.
  - Unknown workflow name: fails cleanly, same `WorkflowNotFound` handling
    already used by `_init`/`run`.
  - Step not part of the given workflow: fails cleanly with a clear error
    rather than silently writing an orphaned agent file.

**FR-4 — The template is valid on arrival.**
Every file FR-2/FR-3 write must parse and round-trip through
`FilesystemAgentCatalog.get(step)` into an `AgentSpec` without raising
`AgentNotFound` — i.e. the operator never has to fix the generated JSON
before the step can run.
- Acceptance: a test writes the template via FR-3, then loads it through
  `FilesystemAgentCatalog` and asserts no exception and the expected
  `prompt`/`allowed_outcomes`.

## Non-functional requirements

- **No new runtime dependencies.** Templates stay Python literals in
  `cli.py` (or a small sibling module), consistent with how
  `DEFAULT_DEFINITION` and `AGENT_PERSONAS` are defined today.
- **Idempotent by default, destructive only opt-in.** Matches the existing
  `init` convention (`if path.exists(): continue`) so a repeated or
  scripted call never silently discards an operator's hand-edited persona;
  `--force` is the explicit, deliberate override.
- **Offline.** No network calls; works with the in-memory/filesystem test
  doubles already used across `tests/test_cli.py`.
- **No architecture-invariant changes.** This is additive within `cli.py`'s
  existing wiring role — it doesn't touch `dispatcher`/`consumer`/`router`,
  and doesn't require changes to `test_architecture.py`'s guarded imports.

## Data model

No new persisted entities. This formalizes an existing one:

- **Agent-definition template** — a function of `(step: str, allowed_outcomes:
  list[str]) -> dict`, keyed conceptually by step name, with two branches:
  a **known-step template** (one of the five `AGENT_PERSONAS` entries) and a
  **generic template** (current `_agent_persona`/`_agent_tools` fallback).
  Its output shape is exactly `AgentSpec`'s JSON encoding, already documented
  in `fs_agents.py`'s module docstring:
  `{prompt: str, model: str|null, fallback_model: str|null, allowed_tools:
  list[str], allowed_outcomes: list[str]}`.

## Interfaces

- **CLI:** `harness agent init <step> [--root PATH] [--workflow NAME]
  [--force]` — new subcommand alongside `init`/`submit`/`run`/`service`/
  `update` in `main()`'s `subparsers`.
- **Internal function** (name illustrative,
  e.g. `_agent_definition_template(step, allowed_outcomes) -> dict`) — called
  by both `_write_default_agents` (existing, FR-2) and the new `_agent_init`
  handler (FR-3). No public API change outside `cli.py`.

## Dependencies and scope

**Depends on:** `AGENT_PERSONAS`, `_agent_persona`, `_agent_tools`,
`_allowed_outcomes_for`, `HarnessLayout.agents`, `FilesystemAgentCatalog`,
`build()`/`WorkflowNotFound` handling already used by `_init`.

**In scope:** templating/defaults for per-step agent JSON
(`agents/<step>.json`) only — this is the one place in the codebase today
where an operator must author step-shaped JSON from a blank slate with no
mechanism at all to fall back on.

**Explicitly out of scope** (flagged as candidate follow-ups, not required
by this issue's acceptance criteria, which asks for *the mechanism*, not
coverage of every JSON surface):
- `harness submit --data`'s free-form task-data payload (its shape is
  workflow/source-specific and not owned by a single "step").
- `repos.json`'s repo-name → path mapping (already defaults to a valid empty
  `{}`; no invalid-blank-field problem exists there today).
- Any interactive/web authoring UI — the board (`api/`) is read-only by
  invariant #5/#11 (`api/` touches only `BoardView`/`ArtifactView`); adding a
  write path for authoring would be a separate, larger change.
- Changing `AgentSpec`'s JSON schema itself, or adding schema validation
  beyond what `FilesystemAgentCatalog.get` already enforces.

## Rough plan

1. Extract the per-step template construction currently inlined in
   `_write_default_agents` into a standalone function (FR-1), covering both
   the known-persona and generic-fallback branches.
2. Rewire `_write_default_agents` to call it (FR-2) — pure refactor, existing
   `init` tests must pass unchanged.
3. Add the `agent` subcommand group and `init` action
   (`harness agent init <step>`) to `main()`'s argparse wiring, plus a
   handler `_agent_init` implementing FR-3's checks (root initialized,
   workflow known, step belongs to the workflow, existing-file/`--force`
   handling, stdout output).
4. Tests in `tests/test_cli.py`: known-step template content, generic-step
   template content + derived `allowed_outcomes`, missing-file scaffold,
   existing-file-not-overwritten, `--force` overwrite, uninitialized-root
   failure, unknown-workflow failure, step-not-in-workflow failure, and a
   round-trip through `FilesystemAgentCatalog` (FR-4).
5. Update `CLAUDE.md`'s "What is responsible for what" bullet on
   `harness init`'s agent scaffolding to mention the new on-demand
   `harness agent init` path, keeping the doc in sync with behaviour.

## Open questions

- **Command name.** Chosen default: `harness agent init <step>`, mirroring
  the existing `harness init` verb and its write-if-missing semantics.
  Alternatives (`agent template`, `agent scaffold`) were considered but
  `init` keeps the vocabulary consistent with the top-level command that
  already does this for every step at once.
- **Scope of "steps that require JSON input".** Chosen default: this plan
  covers only per-step agent definitions (the concrete, currently-broken
  case). `submit --data` and `repos.json` are noted as out of scope; if the
  operator meant those too, they should come back as separate follow-up
  issues once this mechanism's shape has proven out.
- **Generic (non-persona) template content.** Chosen default: reuse the
  existing `_agent_persona`/`_agent_tools` fallback text verbatim rather than
  inventing new copy, so behaviour for already-working custom steps (created
  via `init` today) doesn't change.
