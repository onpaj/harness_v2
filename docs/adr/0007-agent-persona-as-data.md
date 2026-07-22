# ADR-0007: Agent persona as data

Status: Accepted

## Context

Phase 3 replaces `DummyBehavior` with a real agent invocation for every step —
`plan`, `design`, `architecture`, `development`, `review`, `land` and any other
step a workflow defines. Each of those steps needs a different prompt, and the
`reviewer` step needs to be allowed to return `request_changes` where the others
don't. The naive way to build this is one behavior subclass per step name, with
the prompt and allowed outcomes hard-coded into each subclass — which would mean
adding a new step to a workflow always means adding a new Python class.

## Decision

`behaviors/agent.py`'s `ClaudeCliBehavior` is the single behavior class for
every agent-driven step. It has no branch on the step's name or the agent's
identity (invariant #14) — what distinguishes one persona from another is
entirely the content of the `AgentSpec` dataclass it was constructed with
(`ports/agent.py`): the prompt, the model, the allowed tools, and
`allowed_outcomes`. `AgentCatalog.get(name)` maps a step's name to its spec
(`drivers/fs_agents.py` reads `agents/<step>.json`, written by `harness init`);
the shared `AgentRunner` (`drivers/claude_cli.py`'s `ClaudeCliRunner`) is the one
thing that actually shells out to `claude -p`, and `ClaudeCliBehavior` knows
nothing about subprocesses or CLI flags (invariant #13) — a test drives it with
`FakeAgentRunner` instead.

## Consequences

- Adding a new agent-backed step to a workflow is a new `agents/<step>.json`
  file in the catalog, not a new behavior class — the model, prompt and
  allowed outcomes are per queue (per step), not per class.
- Because the agent is behind `AgentRunner`, the entire behavior can be
  unit-tested with a fake that returns a scripted `AgentRun` — no real `claude`
  process, no network, no timeout to wait out.
- `reviewer` (or any step configured with a broader `allowed_outcomes`) is the
  only place `request_changes` can come from; every other step's `AgentSpec`
  restricts it to `(Outcome.DONE,)`, so a misbehaving prompt cannot route a
  task somewhere the workflow never intended by returning an outcome nothing
  in the catalog permits.
