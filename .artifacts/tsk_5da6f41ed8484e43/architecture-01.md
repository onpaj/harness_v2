# Architecture — reviewer syncs with base branch, `request_changes` on conflict

## Alignment with existing patterns and integration points

Verified directly against the current tree (not just the plan/design prose):

- `_REVIEW_PERSONA` lives at `src/harness/cli.py:213-239`, one entry in the
  `AGENT_PERSONAS` map (`cli.py:243-252`) alongside `_PLAN_PERSONA`,
  `_DESIGN_PERSONA`, `_ARCHITECTURE_PERSONA`, `_DEVELOPMENT_PERSONA`. Every
  persona is a plain string; `compose_prompt` (`behaviors/agent.py:83-112`)
  appends the artifact path and verdict-block boilerplate identically for
  every step. There is no per-persona branch anywhere in `ClaudeCliBehavior`
  or `compose_prompt` — confirms invariant 14 (persona is data) is already
  the shape of the code, and this task fits it without adding a branch.
- `review`'s tool list is already `["Read", "Grep", "Glob", "Bash"]`
  (`cli.py:251`) — `Bash` is present today, so the sync instructions need no
  catalog change.
- `_allowed_outcomes_for` (`cli.py:275-281`) derives `allowed_outcomes` from
  the workflow's transitions, not from persona text. `review → request_changes
  → development` is already an edge in `DEFAULT_DEFINITION` (`cli.py:69`), so
  `allowed_outcomes` for `review` is `["done", "request_changes"]` before and
  after this change — no `agents/review.json` field changes shape, only the
  `prompt` value.
- `GitWorkspaceHandle.commit` (`drivers/git_workspace.py:78-87`) already
  no-ops on an empty `git status --porcelain` — this is the exact mechanism
  that makes the conflict path safe: if the agent runs `git merge --abort`
  and leaves the tree clean, the worker's own commit step naturally produces
  no empty commit, with zero new logic.
- `GitWorkspace.attach`'s reset-on-reattach (`git_workspace.py:125-143`,
  specifically the `reset --hard HEAD` + `clean -fd` branch at lines
  136-142) is the existing safety net for the back-edge to `development` —
  it already discards any leftover git state unconditionally, so this task
  does not need to invent its own cleanup guarantee; it only needs the
  agent's own `merge --abort` as a courtesy for the *current* attempt's
  `commit()` call, not as a correctness requirement for the next one.
- No helper resolving "the default branch from a local clone" exists in
  production code — the only `default_branch` today is
  `GithubClient.default_branch` (`drivers/github_client.py:52,96,202`),
  which asks the GitHub API and is used solely by `GithubForge`
  (`drivers/github_forge.py:116,132-135`) for the PR's base, in a completely
  different process (the harness process, not the agent's shell). That is
  the right boundary to preserve: `review` runs *inside* the task's own
  worktree via `claude -p`, with no access to a `GithubClient`/token, so it
  must resolve the base branch locally from git itself
  (`git symbolic-ref refs/remotes/origin/HEAD`, falling back to `main`), not
  by reusing `GithubForge`'s API-based lookup. These two "find the base
  branch" mechanisms are deliberately separate — one is a git-porcelain
  fallback chain run by an LLM's shell, the other is an authenticated API
  call run by a driver — and this task must not try to unify them.

Conclusion: the plan and design are already correctly scoped to a single
string constant. There is no integration point this change needs to touch
beyond that string.

## Proposed architecture

**Decision: this is a persona-text change, not a code change.** No new port,
no new driver, no new field on any model, no new branch in
`ClaudeCliBehavior` or `compose_prompt`.

Options considered:

1. **Give the harness itself a pre-review git-sync step** (e.g. a small
   method on `Workspace`/`GitWorkspaceHandle` that merges `origin/<base>`
   before `ClaudeCliBehavior` builds the prompt, with the result folded into
   `BehaviorResult` on conflict). Rejected: it would require
   `ClaudeCliBehavior` (or `Workspace`) to special-case the `review` step —
   directly contradicts invariant 14 ("no branch on the agent's name") and
   invariant 2 (decision-making stays out of the consumer/dispatcher path;
   here it would leak into the behavior). It would also need a new way for
   the harness to *decide* `request_changes` outside of the agent's verdict
   JSON, which doesn't exist and shouldn't be invented for one step.
2. **A new outcome value or edge** (e.g. `merge_conflict` routing somewhere
   special). Rejected per plan-01.md's own non-functional scope: `review`
   already has exactly the edge needed
   (`request_changes → development`), and `development`'s persona already
   generically "reads [the review] in full... and addresses every point it
   raises" (`cli.py:191-211`). A conflict is just one more reason a review
   can request changes; it doesn't need to be distinguishable to the router
   or dispatcher, only to the human/developer reading the summary.
3. **Persona-only change** (chosen): teach `_REVIEW_PERSONA` to run the sync
   as its first shell action, using the same verdict channel every other
   review outcome already uses. This is the only option consistent with
   invariants 2 and 14, requires touching exactly one string, and needs no
   test-architecture exemption.

Rationale: the entire "decision" here — did the merge conflict? — is made by
the agent, reported through the existing verdict JSON
(`{"outcome": ..., "summary": ...}`), and consumed through the existing
`request_changes → development` edge. The harness's job (dispatcher/router)
is unchanged and untouched; it keeps deciding solely on `(status,
lastOutcome)` per invariant 8/3. This is the smallest change that satisfies
the requirement and the one the invariants effectively mandate.

## Implementation guidance

- **Where the new code goes:** exclusively inside the `_REVIEW_PERSONA`
  string literal, `src/harness/cli.py:213-239`. Insert the sync procedure
  from design-01.md as a new first paragraph, before the existing `"Check:"`
  bullet list (line 218) — ordering matters and should be pinned by a test
  (see below), because the whole point of FR-3 is to short-circuit *before*
  any code-correctness judgment is made.
- **Contract the new block must establish**, restated precisely so
  development doesn't have to re-derive it from prose:
  - Step order: `git fetch origin` → resolve base (`git symbolic-ref
    refs/remotes/origin/HEAD`, else `main`) → `git merge origin/<base>`.
  - On conflict: capture `git diff --name-only --diff-filter=U` output →
    `git merge --abort` → skip the rest of the checklist → finish with
    `outcome: "request_changes"` whose `summary` names `origin/<base>` and
    lists the captured paths.
  - On success (fast-forward / merge commit / already up to date): fall
    through to the existing checklist verbatim, unmodified in wording — the
    sync step must never itself be a reason to return anything.
  - No instruction to force-push, force-resolve, create a branch, or switch
    branches — the agent operates on the checked-out task branch only, same
    constraint `_DEVELOPMENT_PERSONA` already states explicitly
    (`cli.py:197-199`); mirror that phrasing for consistency between the two
    personas that both run inside a live worktree.
- **Data flow:** unchanged. `ClaudeCliBehavior.run` (`behaviors/agent.py:43-80`)
  still does exactly: attach → compute attempt → compose prompt → run agent →
  `handle.commit(run.summary)` → return `BehaviorResult(run.outcome,
  run.summary)`. The conflict path only changes *what the agent does inside
  its own shell* before it emits the verdict JSON that this method already
  reads generically. No signature, no new field, touches nothing on this
  call path.
- **Test surface, two layers** (matches this repo's existing split between
  fast in-memory tests and the deliberately-real git smoke):
  1. `tests/test_cli.py` — a pure string/ordering assertion:
     `_REVIEW_PERSONA.index("git fetch origin") < _REVIEW_PERSONA.index("Check:")`,
     plus substring checks for `"git merge origin"`, `"git merge --abort"`,
     and that the conflict paragraph mentions `request_changes`. Also assert
     `_allowed_outcomes_for(workflow, "review")` is untouched
     (`["done", "request_changes"]`) — this is a regression guard, not new
     behavior, since nothing about the workflow definition changes.
  2. `tests/test_smoke_git.py` — extend `EchoRunner` (`test_smoke_git.py:55-85`)
     with an opt-in conflict-performing path for the `review` step only,
     as design-01.md specifies: when driving `review`, actually shell out in
     `cwd` and execute the persona's literal git sequence, rather than the
     canned-verdict shortcut every other step uses. Add a base-branch commit
     to the bare `origin` remote that collides with the task branch's own
     change, so the merge genuinely conflicts; assert the resulting
     `BehaviorResult` is `request_changes`, the summary/artifact name the
     conflicting path, and `git status --porcelain` in the worktree is empty
     immediately after. Keep the existing happy-path test
     (`test_task_lands_as_pull_request_on_real_git`) as-is — it already
     exercises the "already up to date" branch implicitly, since nothing
     touches `origin` between worktree creation and `review` in that test.
- **Do not** touch `_DEVELOPMENT_PERSONA`, `DEFAULT_DEFINITION`, `router.py`,
  `dispatcher.py`, or any port. If a development attempt touches any of
  those files, that is a scope violation of this design, not a
  necessary consequence of it.

## Risks and mitigations

- **Risk: agent doesn't follow the numbered procedure literally** (e.g.
  attempts to resolve the conflict instead of aborting, or judges code
  correctness anyway before checking for conflicts). Mitigation: the
  persona already phrases this repo's other multi-step personas as strict
  numbered procedures for the same reason (`_DEVELOPMENT_PERSONA`'s "DO NOT"
  bullets); phrase the new block the same way, with an explicit "do not
  resolve, do not judge correctness, run `merge --abort`" instruction, not
  softer prose. This is a prompt-quality risk, not an architectural one —
  there is no code-level backstop for it (correctly so: inventing one would
  violate invariant 14).
- **Risk: existing installs never see the new wording.** `_write_default_agents`
  only writes `agents/<step>.json` when it doesn't already exist
  (`cli.py:284-301`, the `if path.exists(): continue` at line 290). Any repo
  that already ran `harness init` keeps its old `review.json` prompt
  forever. Mitigation: none needed at the architecture level — this is
  consistent with how every other persona-wording change in this project
  ships (plan-01.md's own "Open questions" already accepts this); it is not
  a regression this task introduces, just a pre-existing migration gap that
  is out of scope here.
- **Risk: base-branch name resolution fails in an unusual remote setup**
  (e.g. no `origin/HEAD` ref cached locally, detached-HEAD-like origin).
  Mitigation: the persona instructs a fallback to the literal `main`,
  matching this project's own convention of assuming `main`-shaped repos
  elsewhere (`DEFAULT_STEP_LABELS`, `DEFAULT_DEFINITION`). If the assumption
  is wrong for a given repo, the merge step will simply fail loudly (`git
  merge origin/main: unknown revision`), which surfaces as an agent/runner
  error today already goes through the existing `AgentError` → consumer
  `_fail` → `failed/` path (`behaviors/agent.py`'s docstring, "Runner
  exceptions... are left to bubble up") — no new failure mode is created.
- **Risk: the new `EchoRunner` conflict path silently biases the smoke
  test into always taking the conflict branch**, masking the happy path.
  Mitigation: gate it behind an explicit per-step flag (a constructor
  parameter naming which step actually executes git, per design-01.md),
  defaulting to the current canned behavior for every step including
  `review` in the untouched happy-path test — this keeps
  `test_task_lands_as_pull_request_on_real_git` exercising exactly what it
  exercises today, unmodified.

## Prerequisites before implementation begins

None outside this repo's existing state. `Bash` tool access on `review`,
the `request_changes → development` edge, and `GitWorkspace`'s
reset-on-reattach are all already in place — verified above, not merely
assumed from the plan/design. Development can proceed directly to editing
`_REVIEW_PERSONA` and the two test files; no scaffolding, port, or driver
work needs to land first.