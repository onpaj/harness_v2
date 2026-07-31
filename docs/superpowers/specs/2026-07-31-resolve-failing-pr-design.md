# Resolve failing PR — design

**Date:** 2026-07-31
**Status:** approved, not yet implemented

## Problem

Harness has half a PR-hygiene story. `processes/resolve-conflicts.json` runs the
`github-conflicts` check every 60s and handles two of GitHub's `mergeable_state`
values: it calls the Update-branch API on a `behind` PR (server-side, no task,
no tokens) and fires the `resolver` workflow at a `dirty` one. Everything else
is explicitly out of scope:

```python
if pr.mergeable_state != "dirty":
    continue  # clean/blocked/unstable/unknown → leave alone (v1 scope)
```

`unstable` means "mergeable, but a check run failed"; `blocked` means a
*required* check failed or a required review is missing. Both are dead ends
today — the PR sits there until a human notices. `GithubClient` has no
check-run API at all, so there is not even the raw material to act on.

Separately, every existing process is gated to the `harness/` branch prefix, so
human-authored PRs get none of this.

## Goal

One process that takes any unhealthy open pull request and gets it unblocked:
merge conflicts, failing CI, or both, fixed by a single agent in a single run.

## Decisions

These were settled during brainstorming and are not open questions:

| Decision | Choice |
|---|---|
| Shape | One agent that fixes whatever is wrong, not a triage-and-dispatch pair |
| CI signal | GitHub check-run logs, fetched by the check |
| Triage | Deterministic, in the check — zero tokens, pure function over PR state |
| Loop guard | Attempt budget (default 3), then a `harness:needs-human` label and stop |
| PR scope | All open PRs; `harness:no-autofix` opts out |
| Stale branches | Keep the server-side *merge* (Update-branch API). No rebase, no force-push |
| Replaces | `resolve-conflicts.json`, `resolver.json`, `resolve.json`. `automerge.json` is untouched |

Rejected alternatives, with reasons:

- **Thin check + agent triage step.** Maximally flexible, but spends a full
  agent run per unhealthy PR just to classify, and makes the interesting logic
  untestable without a model in the loop.
- **Extend the existing resolver in place.** Smallest diff, but the agent gets
  no CI log and would have to discover why CI is red by itself.
- **Rebase instead of merge.** Force-pushing onto a branch a human has checked
  out gives them a diverged local copy. Not worth linear history.
- **Persisting attempt counts in harness's own store.** Cannot work: the dedup
  ledger keys on `head_sha`, and every fix push mints a new one.

## Architecture

```
resolve-failing-pr.json  (process, 60s)
        │
        ▼
github-unhealthy-prs  (check — deterministic triage, no tokens)
        │
        ├─ behind ────────────► update_branch() server-side, no task
        ├─ clean ─────────────► ignore (automerge.json's business)
        ├─ no-autofix label ──► ignore
        ├─ needs-human label ─► ignore (already given up)
        ├─ over attempt budget► add harness:needs-human, no task
        └─ dirty | unstable | blocked-with-red-checks
                   │
                   ▼  Observation carrying the full brief
             workflow: unblock-pr
                   │
              ┌────┴─────┐
           unblock ──done──► land ──► end
              │
              └──stuck───► end
```

Every yes/no decision happens in the check. By the time a task exists, what is
wrong with the PR is already in `task.data`, and the agent's only job is fixing
it. This mirrors how `GithubConflictsCheck` already partitions PRs
deterministically before any agent is involved.

## Components

### 1. `drivers/github_unhealthy_prs_check.py` (new, replaces `github_conflicts_check.py`)

Stamps `SOURCE_KIND = "pull-request-health"`.

**Decision order** — first match wins, cheap checks before network calls:

| # | Condition | Action |
|---|---|---|
| 1 | `skip_label` present | skip |
| 2 | `give_up_label` present | skip |
| 3 | `draft` | skip |
| 4 | `mergeable_state == "behind"` | `update_branch()`, no task |
| 5 | `mergeable_state == "clean"` | skip — automerge's |
| 6 | `dirty` | conflicted; continue to 8 |
| 7 | `unstable` / `blocked` / `unknown` | fetch check-runs; **no failures → skip**; else continue |
| 8 | attempt ≥ `max_attempts` | add `give_up_label`, no task |
| 9 | — | bump attempt label, fetch logs, emit |

Rows 6 and 7 are exhaustive over the remaining states, so nothing reaches row 8
without a known reason to act on it.

Row 7 is what keeps a `blocked` PR that is merely awaiting human review out of
scope — no agent can supply a required review. It is also what makes `unknown`
safe: GitHub reports `unknown` while it is still computing mergeability, and a
PR with no failing check-runs is skipped rather than guessed at. Row 4 stays
ahead of everything so a stale branch keeps costing one idempotent API call and
no tokens.

**Failing** means `conclusion in {"failure", "timed_out"}`. `cancelled` and
`skipped` are not something to fix.

**Attempt counting** lives in a single rolling label on the PR,
`harness:autofix-<n>`. The check reads it off `pr.labels`, which it already has
from the detail call, so counting is free; it removes the old label and adds the
new one at emit time (two API calls per *attempt*, none per tick). A PR with no
such label is at attempt 0, and the first emit stamps `harness:autofix-1`; row 8
therefore lets exactly `max_attempts` emits through. Counting here
rather than in the behavior makes it a pure function of PR state: it survives
restarts, cannot drift from what is on the PR, and an operator resets the budget
by deleting a label.

**Per-PR isolation:** each PR is processed in its own `try/except`, as
`GithubConflictsCheck` does today. One PR that 500s must not sink the tick. A
log fetch that fails degrades to `log_tail: null` and still emits.

**The observation:**

```json
{
  "branch": "harness/foo",
  "title": "unblock PR #42",
  "body": "<the brief below, rendered as markdown>",
  "source": {"kind": "pull-request-health", "repo": "onpaj/x",
             "pr": 42, "base": "main", "url": "..."},
  "problem": {
    "conflicted": true,
    "attempt": 2,
    "failing_checks": [
      {"name": "pytest (3.12)", "url": "...", "log_tail": "…last ~200 lines…"}
    ]
  }
}
```

`state_key` is `slug:number:head_sha`, matching both existing PR checks — one
emit per push, not one per tick.

`data.problem` is the structured form: what tests assert against and what a
human reads in the task JSON. `data.body` is its markdown rendering, and
`compose_prompt` already picks up `data.title` and `data.body`
(`behaviors/agent.py`), so the brief reaches the agent with **no change to the
prompt machinery**.

### 2. `PR_BORN_SOURCE_KINDS` (edit, `drivers/failed_tasks_check.py`)

The new `SOURCE_KIND` must be imported into the frozenset alongside
`MERGEABILITY_SOURCE_KIND` and `PULL_REQUEST_SOURCE_KIND`. Without it the
healer's one-hop recursion guard (invariant 25) silently stops covering these
tasks. That module's docstring warns about exactly this failure mode; the import
is what makes a rename break loudly instead of silently disarming the guard.

### 3. `GithubClient` (extend)

```python
@dataclass(frozen=True)
class CheckRun:
    id: int
    name: str
    conclusion: str   # "success" | "failure" | "timed_out" | "cancelled" | "skipped" | ""
    url: str

def list_check_runs(self, repo: str, sha: str) -> list[CheckRun]: ...
def check_run_log(self, repo: str, check_run_id: int) -> str: ...
```

- `list_check_runs` → `GET /repos/{repo}/commits/{sha}/check-runs`
- `check_run_log` → `GET /repos/{repo}/actions/jobs/{id}/logs`, which 302s to a
  plain-text log. GitHub Actions check-run ids *are* job ids.

Checks not backed by Actions have no log at that endpoint. Those come through
with `log_tail: null`, and the agent is told the log was unavailable rather than
being handed a fabrication.

Logs are tailed to `log_tail_lines` (default 200) **in the check, not the
agent** — a 40 MB log must never reach a prompt.

Both fakes in `github_client.py` grow matching stubs.

### 4. `behaviors/unblock_pr.py` (rename + extend `resolve_conflict.py`)

`ResolveConflictBehavior` already does the right dance: `workspace.attach(task)`
checks out the PR branch, `handle.merge(base)` produces real conflict markers,
the agent runs, the worker commits. Renamed `UnblockPrBehavior`, with two
changes:

1. The "merged cleanly, no conflicts" early return fires **only when there is
   also nothing red** (`problem.failing_checks` empty). Otherwise it falls
   through to the agent.
2. Before committing, `.artifacts/` is appended to `.git/info/exclude` so the
   agent's artifact is not committed onto the PR branch (see *Artifacts*, below).

`app.py`'s `RESOLVE_STEP` constant becomes `UNBLOCK_STEP`.

### 5. `workflows/unblock-pr.json` (new)

```json
{
  "name": "unblock-pr",
  "start": "unblock",
  "transitions": [
    {"from": "unblock", "on": "done",  "to": "land",
     "hint": "the conflict is resolved and/or the failing checks should now pass"},
    {"from": "unblock", "on": "stuck", "to": "end",
     "hint": "you could not fix this from what you were given — push nothing"},
    {"from": "land",    "on": "done",  "to": "end"}
  ],
  "descriptions": {
    "unblock": "fix whatever is blocking this pull request — merge conflicts, failing checks, or both",
    "land": "commit the fix and push it to the pull request's branch"
  },
  "max_parallel": {"unblock": 2}
}
```

`max_parallel` matters more here than in existing workflows: with
`head_prefix: ""`, one bad merge into main can turn every open PR red at once,
and without a cap the harness would spawn an agent per PR simultaneously.

`land` is the existing `LandingBehavior`, unchanged. The PR already exists, so
find-or-create finds it and the step just pushes — exactly what the resolver
does today.

`stuck` is a new outcome: the agent read the log, could not fix it, and says so
rather than pushing a guess. It routes to `end`; the check's budget label is
what escalates.

### 6. `agents/unblock.json` (new, replaces `resolve.json`)

`allowed_tools: [Read, Write, Edit, Bash, Grep, Glob]`,
`allowed_outcomes: [done, stuck]`, `model: sonnet`.

Bash is load-bearing in a way it is not for the current `resolve` agent: without
it the agent cannot run the tests the prompt tells it to run.

```
You are a senior developer whose only job right now is to get one pull
request unblocked. The working directory is a checkout of the PR's own
branch, and the base branch has already been merged in — so if there was a
conflict, the files with <<<<<<< ======= >>>>>>> markers are in front of you
now.

Your brief above says what is wrong: a conflict, one or more failing checks
with the tail of their logs, or both. Fix all of it.

For a conflict: read each conflicted file, understand both sides from the
surrounding code and tests, and produce a resolution that preserves the
combined intent. Remove every marker.

For a failing check: the log tail tells you what failed, not always why.
Read the code the failure points at before you change it. Then run the
relevant tests yourself and confirm they pass — a fix you have not run is a
guess, and pushing a guess costs another round of CI.

A log tail may be absent for checks whose logs this harness cannot fetch.
Say so and work from the check's name and the diff rather than inventing
what it said.

Do not widen the scope. You are fixing what is broken, not improving what
happens to be nearby — an unrelated change here lands on someone else's PR.

If you cannot fix it from what you have — the failure is environmental, the
log is uninformative, or the right fix is a judgement call that is not yours
to make — choose "stuck" and explain why in your artifact. Stuck is a
perfectly good answer and costs a human one glance. A speculative push costs
a full CI run and burns one of three attempts.

Do not commit, push, create a branch, or open a worktree — the harness does
all of that.
```

### 7. `processes/resolve-failing-pr.json` (new, in `~/harness-root`)

```json
{
  "name": "resolve-failing-pr",
  "trigger": {"interval": "60s"},
  "action": {"check": "github-unhealthy-prs", "params": {
    "head_prefix": "",
    "skip_label": "harness:no-autofix",
    "give_up_label": "harness:needs-human",
    "max_attempts": 3,
    "log_tail_lines": 200
  }},
  "target": {"workflow": "unblock-pr"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

Every param renders in the process form via `ParamSpec`, as the existing checks
do. `cli.py`'s check registry swaps `github-conflicts` for
`github-unhealthy-prs`; the factory closes over `GithubClient` and the repo
registry exactly as before.

## Artifacts

Agents write artifacts into `.artifacts/` **inside the worktree**, and
`handle.commit()` commits them — which is why `.artifacts/tsk_*/plan-01.md`
files are tracked in this repo today. On harness-authored PRs that is intended.
On a human's PR it means the harness commits `.artifacts/tsk_.../unblock-01.md`
onto their branch, and it rides into main on merge.

**Decision:** `UnblockPrBehavior` appends `.artifacts/` to `.git/info/exclude`
before committing. That file is local to the clone and never tracked, so the
repo's own `.gitignore` is untouched. The artifact still exists on disk for the
operator and in the task's history; it just does not ride along on someone
else's PR.

Rejected: branching on the head-branch name inside the behavior (puts policy
where invariant 14 says it should not go), and accepting the extra commit
(a surprise that lands in main).

## Consequences of widening `head_prefix`

Stated plainly, because it is the one genuinely new risk here. With
`head_prefix: ""`, every open PR across all three repos in `repos.json` is in
scope, and the harness will push commits onto branches humans have checked out.
A colleague's `git pull` will pick up an agent's commit.

It will not force-push, will not touch a PR carrying `harness:no-autofix`, and
gives up after three attempts with a visible label. If that trade turns out
wrong in practice, setting `head_prefix` back to `harness/` restores the old
blast radius with a one-line config change and no code change.

## Error handling

- **Check-level API failure:** per-PR `try/except`; one bad PR does not sink the
  tick. Log-fetch failure degrades to `log_tail: null` rather than skipping.
- **PR merged or branch deleted between emit and run:** `workspace.attach`
  raises, the task lands in `failed/`, and the healer picks it up. Because
  `SOURCE_KIND` is in `PR_BORN_SOURCE_KINDS`, the one-hop limit stops it filing
  an issue about a race that already resolved itself.
- **The re-fire loop:** agent pushes → new `head_sha` → CI reruns → still red →
  check sees attempt 3 → labels `harness:needs-human` → never emits again. The
  per-`head_sha` dedup key means intermediate ticks cost nothing.
- **Agent chooses `stuck`:** routes to `end`. The attempt was still spent, so
  three stuck rounds also reach the give-up label.

## Testing

The decision table is a pure function over a fake `GithubClient`, so all nine
rows get a unit test — including the two that produce no task (`behind`,
budget-exhausted) and the `blocked`-awaiting-review case that must be skipped.

Also:

- attempt-label bump and rollover, including the `max_attempts` boundary
- log tail truncation at the boundary; `log_tail: null` when the fetch fails
- `cancelled` / `skipped` conclusions not counting as failures
- `UnblockPrBehavior`: clean merge + red checks still calls the agent; clean
  merge + nothing red returns early without one
- `.git/info/exclude` written before commit; artifact absent from the commit
- e2e through the fake client: dirty → fix → land, and three failed rounds to
  the give-up label
- `test_architecture.py` already forbids the new driver importing `cli` — free

## Migration

Delete `processes/resolve-conflicts.json`, `workflows/resolver.json`,
`agents/resolve.json` from `~/harness-root`, and add the three new files.

Per the operational note in `~/CLAUDE.md`: migrate `~/harness-root` **before**
pushing to main, since a push triggers a release and the live service
self-upgrades within 30 minutes. A harness-root still holding `resolver.json`
after the upgrade names a workflow whose agent no longer exists.

Any PR already carrying a `harness:autofix-<n>` label from a previous run keeps
its count; there is no state to migrate beyond the config files.
