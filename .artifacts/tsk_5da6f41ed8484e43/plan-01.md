# Reviewer merges `main` into the working branch; conflicts → `request_changes`

## Summary

Today the `review` step's persona (`_REVIEW_PERSONA` in `src/harness/cli.py`)
only judges the implementation against the spec/architecture; it never checks
whether the task branch is still mergeable into the repository's default
branch. We add an explicit instruction, up front in the persona, to sync the
task branch with the base branch before reviewing, and to short-circuit to
`request_changes` when that sync produces a merge conflict — before spending
any effort on the rest of the review.

## Context

A task's worktree is created once, from the base branch's HEAD at that time
(`GitWorkspace.attach`, `src/harness/drivers/git_workspace.py:125`), and is
never rebased or merged against upstream again afterward. Since a task can sit
in `development`/`review` for a long-running multi-round loop, the base branch
can move on in the meantime (other tasks landing, direct pushes). Today the
harness never notices until `land` opens a PR and GitHub reports a conflict
out of band — by then the task has already gone through a `done` review and
landed in a state a human has to untangle by hand, outside the pipeline.
Catching the conflict at `review` keeps it inside the loop: `review` already
routes `request_changes` back to `development`
(`src/harness/cli.py:69`, `DEFAULT_DEFINITION`), which is exactly the
existing mechanism to send a task back to fix something, so no workflow or
routing change is needed — this is a persona-content change plus (if the
merge needs it) a small runtime addition, not new architecture. Per invariant
14, the difference between personas is data (the `AgentSpec.prompt`), not a
branch in behavior code, so this belongs in `_REVIEW_PERSONA`, not in
`ClaudeCliBehavior`.

## Functional requirements

**FR-1 — Sync step before review.**
The review persona instructs the agent, as its first action, to bring the
task branch up to date with the repository's base branch before evaluating
the implementation:
1. `git fetch origin`.
2. Determine the base branch (typically `main`; if unsure, resolve it, e.g.
   via `git symbolic-ref refs/remotes/origin/HEAD` or `git remote show
   origin`, rather than assuming the literal name `main`).
3. `git merge origin/<base>` into the current branch (the worktree is
   already checked out on the task branch — invariant: the agent must not
   create or switch branches, same rule as `development`).

*Acceptance:* `_REVIEW_PERSONA` contains these steps, phrased as the agent's
first action, before the existing "check conformance/architecture/tests"
instructions.

**FR-2 — Clean sync → review proceeds unchanged.**
When the merge completes with no conflicts (whether it fast-forwards, creates
a merge commit, or is already up to date), the agent continues with today's
review exactly as before, and returns `done`/`request_changes` per the
existing correctness/conformance criteria — the sync step must not change the
review's verdict on its own.

*Acceptance:* given a base branch with unrelated new commits that merge
cleanly into the task branch, the review's outcome depends only on the
implementation's correctness, as today; the artifact/summary does not mention
a merge conflict.

**FR-3 — Conflicting sync → `request_changes`, reported specifically.**
When the merge produces conflicts, the agent:
1. Aborts the merge (`git merge --abort`) — it must not attempt to resolve
   the conflict itself and must leave the working tree clean (this also
   makes the step's own commit, done by the driver via
   `handle.commit()` in `ClaudeCliBehavior.run()`, a no-op — nothing new is
   staged).
2. Skips the rest of the review (no need to judge code correctness when
   `development` will have to redo work against a moved base regardless).
3. Writes the step's artifact and returns outcome `request_changes`, with a
   summary that says explicitly that merging `origin/<base>` produced
   conflicts, and lists the conflicting file paths (from `git status` /
   `git diff --name-only --diff-filter=U` before the abort).

*Acceptance:* given a base branch with a commit that conflicts with the task
branch, the review step's verdict is `request_changes`; the summary/artifact
names the conflicting paths; a `git status --porcelain` in the worktree after
the step is clean.

**FR-4 — Round-trips correctly through `development`.**
Because `review --request_changes--> development` is an existing edge
(`src/harness/cli.py:69`), no workflow-definition change is required. On the
next `development` attempt, `GitWorkspace.attach` resets the worktree to the
task branch's own HEAD (`reset --hard` + `clean -fd`,
`src/harness/drivers/git_workspace.py:136`) — this discards any leftover
merge state regardless of whether the reviewer's own `merge --abort` ran, so
the loop is safe even if the agent's cleanup step is skipped by a crash.
`development`'s persona already tells it to read the prior review among the
artifacts and address every point it raises (`_DEVELOPMENT_PERSONA`) — a
conflict-shaped review summary from FR-3 is addressed the same way (no
persona change needed there): the developer merges/rebases against the
now-current base and resolves the conflict as part of its normal round.

*Acceptance:* a `request_changes` verdict caused by a merge conflict routes
to `development` exactly like any other `request_changes` verdict; no new
outcome value, no new edge, no dispatcher/router change.

## Non-functional requirements

- **No architecture change.** No new port, no new driver, no branch on
  outcome value inside `ClaudeCliBehavior` (invariants 2, 14). The entire
  change is the text of `_REVIEW_PERSONA` in `src/harness/cli.py`; `review`'s
  `allowed_tools` already includes `Bash` (`AGENT_PERSONAS["review"]`,
  `src/harness/cli.py:251`), so no catalog/tool change is needed either.
- **Idempotency.** Re-running the sync step when the base branch hasn't moved
  since the last attempt is a no-op (`git merge` reports "Already up to
  date"); this must not be treated as a conflict.
- **Safety.** The merge must never be force-pushed or force-resolved by the
  agent; on conflict the only allowed recovery is `git merge --abort`,
  matching the harness's existing no-force-push discipline
  (`GitWorkspaceHandle.push`, `src/harness/drivers/git_workspace.py:89`).
- **No change to landing.** `land`/`GithubForge` are unaffected — they still
  only push and open/reuse a PR; the merge freshness is now guaranteed
  earlier, in `review`, not by `land`.

## Data model

No new or changed entities. `Task`, `BehaviorResult`, `AgentSpec`, `AgentRun`
are unchanged. The only "data" touched is the content of one `AgentSpec.prompt`
string (`_REVIEW_PERSONA`), and (existing-format) the corresponding
`agents/review.json` written by `harness init` picks it up automatically for
new installs — a repo that already ran `harness init` and has a
`review.json` on disk keeps its old prompt until the operator regenerates or
edits it by hand (see Open questions).

## Interfaces

No new CLI flags, endpoints, or events. This is a persona/prompt change
consumed the same way today's review persona is: `compose_prompt`
(`src/harness/behaviors/agent.py:83`) appends the standard artifact/verdict
boilerplate to whatever `_REVIEW_PERSONA` says, unchanged.

## Dependencies and scope

**Rests on:** `review`'s existing `Bash` tool access; the existing
`review --request_changes--> development` edge; `GitWorkspace`'s
reset-on-reattach behavior for cleanup safety net.

**In scope:** the `_REVIEW_PERSONA` text change; a short smoke/unit test (see
rough plan) that pins the new instructions are present and, if feasible, a
git-level smoke test exercising a real conflicting merge/abort sequence.

**Out of scope:**
- Automatic conflict *resolution* by the reviewer (or by any step) — conflicts
  are reported, not fixed, by `review`.
- Changing `land`/`GithubForge`'s own conflict handling (it already fails the
  task via `ForgeError` on a GitHub-side conflict; unaffected).
- Rebase-based sync (`git rebase` instead of `git merge`) — merge is simpler,
  doesn't rewrite the task branch's history, and matches the no-force-push
  invariant on `push()`.
- Changing `DEFAULT_DEFINITION`/routing — no new outcome value or edge.
- Any change to `_DEVELOPMENT_PERSONA` — it already generically instructs
  addressing every point of a prior review.

## Rough plan

1. Extend `_REVIEW_PERSONA` in `src/harness/cli.py` with the sync-first
   instructions from FR-1–FR-3, ordered before the existing
   conformance/architecture checklist.
2. Add/extend a unit test (likely in `tests/test_cli.py`, wherever
   `AGENT_PERSONAS`/`_write_default_agents` are already covered) asserting
   the new instructions are present in the persona text and that
   `allowed_outcomes` for `review` is unaffected (still
   `["done", "request_changes"]`, derived from the workflow edges as before).
3. Extend `tests/test_smoke_git.py` (the real-git smoke, per this repo's
   convention of not faking git/FS coverage away) with a scenario: base
   branch gets a commit that conflicts with the task branch's change, drive
   `review` with a local test-runner double that performs the merge/abort per
   the new persona's contract, assert the step yields `request_changes` and
   the worktree ends clean.
4. Run `.venv/bin/pytest -q`; confirm `tests/test_architecture.py` still
   passes unmodified (no port/layer touched).

## Open questions

- **Existing installs with a hand-edited or already-generated
  `agents/review.json`.** `_write_default_agents` only writes a step's file
  if it doesn't already exist (`src/harness/cli.py:290`), so this change is
  invisible to any repo that already ran `harness init`. Default assumed:
  this is acceptable and consistent with how every other persona wording
  change would ship (the operator regenerates or hand-edits
  `review.json` to pick up new wording) — no auto-migration is introduced.
- **Which base branch name to assume when the agent can't resolve it.**
  Default assumed: prefer `main`, matching the harness's own convention
  (`_default_branch`/`DEFAULT_DEFINITION` elsewhere assume `main`-shaped
  repos); the persona should still tell the agent to double check rather
  than hardcode it blindly, per FR-1 step 2.
- **Merge vs. rebase preference.** Default assumed: merge (see "Out of
  scope"); revisit only if a later phase wants a linear task-branch history.
