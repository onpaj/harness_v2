# Design — reviewer syncs with base branch, `request_changes` on conflict

## Summary

No UI is involved (per plan-01.md's non-functional scope: "no new port, no new
driver... the entire change is the text of `_REVIEW_PERSONA`"), so this design
has no UX/UI section. It covers the one production component touched
(`_REVIEW_PERSONA` in `src/harness/cli.py`) and the test doubles/assertions
needed to pin its behavior, in enough concrete detail that `development` can
implement it without re-deriving wording or test shape from the spec alone.

## Component design

### `_REVIEW_PERSONA` (`src/harness/cli.py`)

The only production code this task touches. It stays a single string consumed
unchanged by `compose_prompt` (`src/harness/behaviors/agent.py:83`), which
appends the artifact-path/verdict boilerplate — no signature or call-site
changes anywhere in `ClaudeCliBehavior.run` or `compose_prompt`.

**New block, inserted first**, before the existing "Check:" bullet list. It
reads as a numbered procedure so the agent executes it as literal shell steps,
not as prose to interpret:

```
Before anything else, sync the task branch with the repository's base branch:
1. Run `git fetch origin`.
2. Determine the base branch. Try `git symbolic-ref refs/remotes/origin/HEAD`
   and strip the `refs/remotes/origin/` prefix; if that fails, use `main`.
3. Run `git merge origin/<base>`. You are already checked out on the task
   branch — do not create or switch branches, and do not force-push or
   force-resolve anything.
4. If the merge reports conflicts:
   - Run `git diff --name-only --diff-filter=U` to capture the conflicting
     file paths.
   - Run `git merge --abort` to leave the working tree clean.
   - Skip the rest of this review below — do not judge code correctness.
   - Write your output artifact and finish with outcome `request_changes`.
     The summary and the artifact must both state that merging
     `origin/<base>` produced conflicts and must list every conflicting file
     path from the previous step.
5. If the merge succeeds — fast-forward, a merge commit, or "Already up to
   date" — continue with the review exactly as below. This sync step alone
   must never change your verdict.
```

The existing "Check:" / "Return the verdict `request_changes` only when:" /
"Don't return `request_changes` over..." paragraphs follow, byte-for-byte
unchanged — FR-2 requires the clean-merge path to fall through to today's
logic verbatim.

Everything else about the step is unaffected: `AGENT_PERSONAS["review"]`
keeps its `["Read", "Grep", "Glob", "Bash"]` tool list (`Bash` already there),
`allowed_outcomes` for `review` stays `["done", "request_changes"]`
(`_allowed_outcomes_for`, driven by workflow edges, untouched), and
`ClaudeCliBehavior.run` still does exactly one thing after the agent returns:
`handle.commit(run.summary)`. On the conflict path the agent leaves the
working tree clean (step 4's `merge --abort`), so `commit()`'s own
`git status --porcelain` check (`git_workspace.py:80`) finds nothing to stage
and returns `None` — no empty commit, no branch on outcome inside the
behavior (invariant 2/14 preserved).

### Test double: conflict-capable local runner (`tests/test_smoke_git.py`)

`EchoRunner` (test_smoke_git.py:55) is a fake `AgentRunner` that stands in for
`claude` in the real-git smoke; it doesn't execute the persona's git
instructions today, it just writes the artifact and returns a canned verdict.
To exercise FR-3 on real git, extend it with a conflict-aware branch that
*performs* the persona's contract rather than merely asserting on its text:

- Add an optional constructor flag, e.g. `conflict_step: str | None = None`
  naming a step (`"review"`) whose `run()` should actually drive git in `cwd`
  instead of taking the canned-verdict shortcut.
- When `spec.name == self._conflict_step`: run, via `subprocess` against
  `cwd` (the attached worktree — same directory the real agent would run in),
  the literal sequence from the persona: `git fetch origin`, resolve the base
  branch, `git merge origin/<base>`. On a non-zero exit from the merge
  (conflict), run `git diff --name-only --diff-filter=U`, capture the paths,
  run `git merge --abort`, write the artifact naming those paths, and return
  `AgentRun(Outcome.REQUEST_CHANGES, summary="...conflict in <paths>...")`.
  On a clean merge, fall through to the existing canned logic (write artifact,
  `Outcome.DONE`/first-pass `REQUEST_CHANGES` as today).
- This keeps `EchoRunner` a single class (no second runner type to wire
  through `_catalog()`/`build()`), and keeps the fake's job the same as
  today's: standing in for `claude -p`, just now for one step it actually
  does what the persona says instead of skipping straight to the verdict.

The new smoke scenario built on this double:

1. `_make_repo` as today, but after the task's worktree is created (or, more
   simply, by committing directly to the bare `origin` remote/`repo` before
   `review` runs), introduce a commit on the base branch that touches the
   same file/line the task branch's `development` step changed, so the merge
   in `review` genuinely conflicts.
2. Drive the harness as in the existing test; assert the task's `review`
   attempt yields `request_changes` (visible via the projection/board or by
   inspecting `review-01.md` for the conflict wording), that the loop routes
   back to `development` (existing edge, no new assertion needed beyond
   presence of `review-02` after the retried merge succeeds, mirroring the
   two-attempt shape the existing test already checks for `review`), and that
   `git status --porcelain` in the worktree is empty right after the
   conflicting attempt.
3. Keep the happy-path test (`test_task_lands_as_pull_request_on_real_git`)
   unmodified — it exercises FR-2 implicitly today (no base-branch commit
   between worktree creation and `review`, so `git merge origin/<base>` is
   always "Already up to date").

### Unit test additions (`tests/test_cli.py`)

Wherever `AGENT_PERSONAS`/`_write_default_agents` are already asserted on,
add assertions that `_REVIEW_PERSONA`:
- contains the sync instructions (e.g. substrings `"git fetch origin"`,
  `"git merge origin"`, `"git merge --abort"`, `"request_changes"` in the
  conflict paragraph), and that they appear **before** the existing
  `"Check:"` marker (`_REVIEW_PERSONA.index("git fetch origin") <
  _REVIEW_PERSONA.index("Check:")`) — this pins FR-1's ordering requirement
  directly rather than by convention.
- leaves `_allowed_outcomes_for(workflow, "review")` at
  `["done", "request_changes"]`, unchanged by this task.

## Data schemas

No entity changes: `Task`, `BehaviorResult`, `AgentSpec`, `AgentRun`,
`agents/review.json`'s field set (`prompt`, `model`, `fallback_model`,
`allowed_tools`, `allowed_outcomes`) are all untouched — only the *value* of
`prompt` changes, generated the same way (`_agent_persona("review")` →
`_REVIEW_PERSONA`) and only for installs that don't already have a
`review.json` on disk (`_write_default_agents`'s exists-check, unchanged).

The one shape that matters here is textual, not structural — the contract for
what the conflict-path summary/artifact must contain, since nothing downstream
parses it as structured data (the router still reads only `outcome`;
`development`'s persona reads the artifact as free text like any other
review). That contract, restated precisely so implementation and tests agree
on it:

- **Verdict JSON** (unchanged shape, from `compose_prompt`'s boilerplate):
  `{"outcome": "request_changes", "summary": "<free text>"}`.
- **`summary` on the conflict path** must, as free text, name the base branch
  merged from and enumerate the conflicting paths, e.g.:
  `"Merging origin/main produced conflicts in: src/foo.py, src/bar.py — send back to development."`
  No fixed delimiter/format is mandated beyond "mentions the merge conflict
  against `origin/<base>` and lists the paths" — `development`'s persona
  already generically "reads it in full... and addresses every point it
  raises" (FR-4), so this stays prose, matching how every other
  `request_changes` summary already reads.
- **Output artifact** (`review-NN.md`) content, conflict path: same
  information as the summary, at greater length is fine (e.g. an explicit
  `git diff --name-only --diff-filter=U` transcript) — no new artifact
  schema, it's the same freeform `.md` file `review` always writes.

No new/changed CLI flags, endpoints, or events (unchanged from plan-01.md).
