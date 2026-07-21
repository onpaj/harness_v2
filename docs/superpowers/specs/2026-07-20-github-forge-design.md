# GitHub forge — landing opens a real pull request

## Summary

`land` claims to open a pull request, but the real run wires `FakeForge`, which
only appends a record to `<root>/forge/prs.json`. A task therefore finishes with
the summary `opened PR file:///…/prs.json#1` and reaches `end` while nothing
exists on GitHub and the task branch was never pushed. This spec replaces the
stub with a `GithubForge` driver, gives `WorkspaceHandle` the `push` it is
missing, and makes a failure to open a PR loud instead of silent.

## Context

Landing was built in phase 2 behind the `Forge` port with the real driver
deliberately deferred — `cli.py` says so in a comment: *"fake forge (PR into
prs.json). The GitHub driver is a clean follow-up — a swap of the forge
driver."* Phase 4 then landed the ingestion half of the GitHub integration
(`GithubTaskSource`, `HttpGithubClient`, `git_remote.github_slug`), so issues
flow **in** over the real API while results still flow **out** into a JSON file.
This closes that asymmetry.

Observed on task `tsk_f2db8a73491e49d0` (issue #14): every stage ran to `done`,
`land` reported success, and the work sat unpushed in a local worktree with no
PR anywhere.

## Functional requirements

**FR-1 — The task branch reaches `origin`.**
`WorkspaceHandle` gains `push()`. `GitWorkspaceHandle` implements it as
`git push --force-with-lease -u origin <branch>`; `--force-with-lease` because
reset-on-reattach rewrites the branch on a re-run, and the lease refuses to
clobber a ref someone else moved. Pushing an already-current branch is a no-op.
`MemoryWorkspaceHandle` records the call so landing stays testable in-memory.

**FR-2 — `land` pushes before it proposes.**
`LandingBehavior.run` calls `handle.push()` immediately before
`forge.open_pull_request(...)`. No other structural change; landing remains an
ordinary step that may fail into `failed/` (invariant #12).

**FR-3 — `GithubForge` opens the PR.**
A new driver `drivers/github_forge.py`. `open_pull_request` resolves the GitHub
slug **per task**, from `task.worktree`'s git origin via the existing
`git_remote.github_slug` — the forge is constructed once but serves every repo
in `repos.json`, mirroring how `_github_sources` derives a slug per repo. It
then opens a PR from `harness/<task-id>` into the repo's default branch.

**FR-4 — Idempotent by branch.**
Before creating, `GithubForge` looks for an open PR with `head=<owner>:<branch>`
and returns it if found. The `Forge` port already mandates this; it is what
keeps a crash re-run from opening a second PR for the same task.

**FR-5 — Base branch is the repo's real default.**
`GET /repos/{slug}` yields `default_branch`, cached per slug for the process
lifetime. No hardcoded `main`, no new config: a repo on `master` or `trunk`
works untouched.

**FR-6 — Failure is loud.**
Missing `GITHUB_TOKEN`, a repo whose origin is not GitHub, a rejected push, or
any API error raises `ForgeError`. The consumer catches it and writes the task
into `failed/` with the reason on the event (invariant #3 — this is the
established way a behavior fails). The point of the change: an "opened PR"
summary must from now on always mean a PR that exists.

**FR-7 — `FakeForge` stays, but you must ask for it.**
`harness run` gains `--forge {github,fake}`, defaulting to **github**. The fake
remains the driver for tests, smoke and offline demos, where the absence of a
token is expected and fine. `build()`'s in-memory default is unchanged, so the
existing suite is unaffected.

**FR-8 — The PR closes its issue.**
When `task.data.source` is a GitHub issue on the same slug, the body gets a
trailing `Closes #<n>`, so merging the PR closes the issue that spawned the
task. A task from `harness submit`, or one whose source is a different repo,
gets no such line.

## Components

### `ports/workspace.py`
One new abstract method on `WorkspaceHandle`:

```python
@abstractmethod
def push(self) -> None:
    """Publish the task branch to `origin`. Idempotent."""
```

Both drivers (`git_workspace`, `memory`) implement it. This is the only port
change in the spec.

### `drivers/github_client.py`
The `GithubClient` ABC grows three methods, alongside the existing issue verbs:

| method | HTTP |
|---|---|
| `default_branch(repo) -> str` | `GET /repos/{repo}` |
| `find_pull_request(repo, *, head) -> PullRequestRef \| None` | `GET /repos/{repo}/pulls?head=…&state=open` |
| `create_pull_request(repo, *, head, base, title, body) -> PullRequestRef` | `POST /repos/{repo}/pulls` |

`HttpGithubClient` implements them on the existing `urllib` + `_headers()`
pattern — no new production dependency, per the module's own standing note.
`FakeGithubClient` gets in-memory equivalents (a PR list keyed by head, a
settable default branch) so the forge's tests and the e2e run without a network.

`PullRequestRef` is the client-level `(number, url, head)` record; the port's
`PullRequest` stays the harness-level type, built by the forge.

### `drivers/github_forge.py`
```python
class GithubForge(Forge):
    def __init__(self, client: GithubClient, *, slug_of=github_slug) -> None: ...
    def open_pull_request(self, task, *, branch, title, body) -> PullRequest: ...
```
`slug_of` is injected so tests can drive it without a real clone. Flow:
resolve slug → cached `default_branch` → `find_pull_request` → return or
`create_pull_request`. `ForgeError` on every failure path.

### `cli.py`
`_run` builds the forge from `--forge`. For `github`: read `GITHUB_TOKEN`,
construct `HttpGithubClient` and `GithubForge`. A missing token is **not** a
startup error — it fails at `land`, on the task, where the operator can see
which task it broke and restart it after exporting the token.

(A `harness doctor` that reports `GITHUB_TOKEN` presence is being added by the
unmerged work for issue #14. Once that lands, its warning becomes materially
more important than it reads today and its text should say so — but this spec
does not depend on it.)

## Data flow

```
land step
  └─ LandingBehavior.run(task)
       ├─ workspace.attach(task)          → handle (worktree, branch)
       ├─ handle.push()                   → git push --force-with-lease -u origin
       └─ forge.open_pull_request(task, branch, title, body)
            ├─ github_slug(task.worktree) → "onpaj/harness_v2"
            ├─ client.default_branch(slug)      [cached]
            ├─ client.find_pull_request(slug, head="onpaj:harness/tsk_…")
            │    └─ hit → return it (idempotent re-run)
            └─ client.create_pull_request(slug, head, base, title, body + Closes #n)
```

## Error handling

| condition | behaviour |
|---|---|
| `GITHUB_TOKEN` unset, `--forge github` | `ForgeError` at `land` → `failed/` |
| repo origin is not github.com | `ForgeError` naming the repo → `failed/` |
| `git push` rejected (lease stale, no perms) | `GitError` from the workspace → `failed/` |
| GitHub 4xx/5xx | `ForgeError` carrying status and response body → `failed/` |
| PR already open for the branch | returned unchanged, no second PR |

Everything lands in `failed/`, which the board already renders and which
`restart` can re-drive once the cause is fixed. Nothing is retried in-process.

## Testing

- `tests/test_github_forge.py` — unit, on `FakeGithubClient`: creates a PR;
  returns the existing one on a second call; uses the repo's default branch;
  raises on a non-GitHub origin; appends `Closes #n` only for a matching source.
- `tests/test_github_client.py` — extended with the three new verbs against the
  existing injected-opener fake, including the 404-free `find` miss.
- `tests/test_workspace_memory.py` / `tests/test_git_workspace.py` — `push()`
  on both drivers; the git one against a real local bare remote (this file
  already drives real git).
- `tests/test_landing_behavior.py` — landing pushes before opening, and does not
  open a PR if the push raised.
- `tests/test_architecture.py` — unchanged and must stay green: `behaviors/`
  still imports only ports, `dispatcher`/`consumer` still see no forge.
- `tests/test_cli.py` — `--forge` selects the driver; default is `github`.

The smoke tests keep using `FakeForge` (`--forge fake`), by FR-7. No test talks
to real GitHub; `test_smoke_github.py`'s existing opt-in shape is the precedent
if a live check is ever wanted.

## Out of scope

- Merging, reviewing or closing PRs — the harness proposes, a human merges.
- Draft PRs, reviewers, labels or milestones on the PR.
- Non-GitHub forges (GitLab, Gitea). The port already permits them; nobody
  needs one today.
- Reworking `repos.json`'s shape. The slug is derived from the clone, as it
  already is for ingestion.
