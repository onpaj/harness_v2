# ADR-0006: Worktree vs. artifact-folder split (and its phase-3 evolution)

Status: Accepted

## Context

A task produces two different kinds of output: the code changes a step makes
(which need to be versioned, diffable, and eventually proposed as a PR) and the
harness's own record of what each step did (a plan, a design, a review — useful
to a human or the next step, but not itself "the change"). Phase 2 kept these
in two separate places: a git worktree for the code, and a harness-owned
artifact store outside it. That kept the worktree clean and let the UI read
artifacts without touching git — but it also meant the worker had to copy
artifacts into the worktree at landing time, and a `git clean` on the worktree
would never touch them.

Phase 3 changed where artifacts live, without changing the underlying idea that
there are two concerns. Once the agent (not the harness) is the one producing
artifact content, having the agent write directly into the worktree (under
`.artifacts/<task_id>/`) is simpler than routing it through a separate store —
and it means the artifacts ride along in the same commit as the code they
document, in the same PR.

## Decision

`ports/workspace.py`'s `Workspace.attach(task) -> WorkspaceHandle` is the
task's connection to its worktree, versioned by the task's git branch; the
worktree path is derived from `<worktrees_root>/<task_id>` and reattaching a
dirty worktree resets it to HEAD (reset-on-reattach). `artifacts_layout.py`
(package-free, imported by nothing else in `harness`) is the single source of
truth for where artifacts sit inside that worktree:
`.artifacts/<task_id>/<step>-<NN>.md`, attempt-indexed and gapless. The agent
writes the file; **the worker commits it** together with any code changes
(invariant #9) — never the consumer, never the LLM directly. `drivers/
worktree_artifacts.py`'s `WorktreeArtifactView` is the read side the UI uses,
reading the same convention `artifacts_layout.py` defines for the write side,
so the two can never drift apart on where a file lives.

## Consequences

- A step re-run never overwrites the previous attempt (invariant #10) — the
  `request_changes` loop stays visible in the artifact history because
  `next_attempt()` always allocates the next number, never reuses one, except
  when reset-on-reattach discards an unfinished attempt and the retry
  legitimately gets the same number back.
- Landing (phase 3) no longer copies artifacts into the worktree — they are
  already there, versioned — so `LandingBehavior` only pushes the branch and
  opens the PR (see ADR-0009); the phase-2 copy path (`copy_artifacts=True`)
  still exists for backward compatibility with the separate-store shape but is
  not exercised by the phase-3 wiring.
- The UI reads artifacts without git (`ArtifactView`/`WorktreeArtifactView`),
  so a task's plan/design/review are browsable even while the worktree itself
  is mid-commit or the process is between runs.
