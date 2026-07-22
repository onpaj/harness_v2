# Design — mergeability watcher (auto-rebase "behind" PRs, queue conflicts to a resolver step)

No user interface is introduced by this feature. The board renders resolver
tasks through the existing `BoardView`/`ArtifactView` with no new endpoint or
screen (FR-7); the UX/UI section is omitted.

## Component design

Six components change or are added. Each is a driver or a small piece of
wiring — no new port is introduced except one method on the existing
`WorkspaceHandle` port (`merge`), because the resolver step's core job (attempt
a real merge, decide whether an agent is needed) is exactly the kind of git
operation that port already exists to encapsulate (it already has
`write`/`commit`/`push`).

```
                         ┌─────────────────────────────┐
                         │   GithubMergeabilityWatcher  │  (TaskSource, kind="mergeability")
                         │   drivers/mergeability_      │
                         │   watcher.py                 │
                         └───────────────┬─────────────-┘
                                         │ poll() → [] | [resolver Task]
                                         │ side effect: update_branch() on "behind"
                                         ▼
                         ┌─────────────────────────────┐
                         │        SourcePoller          │  (unchanged — core)
                         └───────────────┬─────────────-┘
                                         ▼
                                       inbox
                                         │
                       dispatcher routes by task.workflow_template = "resolver"
                                         ▼
                         ┌─────────────────────────────┐
                         │   ResolveConflictBehavior    │  step "resolve"
                         │   behaviors/resolve_conflict │
                         │   .py                        │
                         └───────────────┬─────────────-┘
                            handle.merge(base) │ handle.commit() / AgentRunner.run()
                                         ▼
                         ┌─────────────────────────────┐
                         │      LandingBehavior         │  step "land" (unchanged, reused)
                         └───────────────┬─────────────-┘
                                         ▼
                                        end
```

`GithubClient`, `GitWorkspace`/`GitWorkspaceHandle`, `build()`/`app.py` and
`BoardProjection` are extended, not replaced.

### 1. `GithubClient` — two new verbs

`drivers/github_client.py` gains a data type and two abstract methods,
implemented on both `FakeGithubClient` and `HttpGithubClient` (no new
production dependency — stdlib `urllib` throughout, matching the file's
existing constraint).

```python
@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    url: str
    head_branch: str
    head_sha: str
    base_branch: str
    mergeable_state: str  # "behind" | "dirty" | "clean" | "blocked" | "unstable" | "unknown"


class GithubClient(ABC):
    ...
    @abstractmethod
    def list_pull_requests(
        self, repo: str, *, head_prefix: str | None = None
    ) -> list[PullRequestInfo]:
        """Open PRs, optionally restricted to a head-branch prefix."""

    @abstractmethod
    def update_branch(self, repo: str, number: int) -> None:
        """Merge base into head server-side. A 422 (already up to date / not
        behind) is swallowed — idempotent, safe to call every tick."""
```

**`HttpGithubClient` real-API detail that matters:** GitHub's list endpoint
(`GET /repos/{repo}/pulls`) does **not** return `mergeable_state` — that field
is only computed and returned by the single-PR endpoint
(`GET /repos/{repo}/pulls/{number}`), and even there it can be `null` while
GitHub is still computing it. `list_pull_requests` is therefore two-tier:

1. `GET .../pulls?state=open` (one call, cheap) → filter by `head.ref`
   startswith `head_prefix`.
2. For each *matching* PR only, `GET .../pulls/{number}` (one call per
   harness-owned open PR — bounded and small) to read `mergeable_state`;
   `None`/missing → `"unknown"`.

`update_branch`:

```python
def update_branch(self, repo: str, number: int) -> None:
    url = f"{self._api}/repos/{repo}/pulls/{number}/update-branch"
    request = urllib.request.Request(url, data=b"{}", headers=self._json_headers(), method="PUT")
    try:
        self._opener.open(request)
    except urllib.error.HTTPError as error:
        if error.code == 422:  # not behind / already up to date
            return
        raise
```

`FakeGithubClient` gets a parallel, simpler store — a list of
`PullRequestInfo` (distinct from the `pulls: list[PullRequestRef]` already
used by `find_pull_request`/`create_pull_request`, which the forge owns and
which has no `mergeable_state`), plus an `add_pull_request(info)` helper and
an `updated_branches: list[tuple[str, int]]` audit list so tests can assert
`update_branch` was called. To make e2e tests realistic without touching real
git, `update_branch` on the fake also flips the matching entry's
`mergeable_state` to `"clean"` — simulating a successful server-side update.

### 2. `GitWorkspace.attach` — branch override

`task.data["branch"]`, when present, makes `attach` check out that *existing*
branch instead of creating `harness/<task.id>` from HEAD (invariant 15 still
holds — `task.repository` stays a name, only the *branch* is overridden).

```python
def attach(self, task: Task) -> GitWorkspaceHandle:
    override = task.data.get("branch")
    branch = override or f"harness/{task.id}"
    base = self._registry.resolve(task.repository)
    worktree = self._worktrees_root / task.id

    if not worktree.exists():
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if override:
            _git(["-C", str(base), "fetch", "origin", branch])
            _git(["-C", str(base), "worktree", "add", str(worktree), "-B", branch, f"origin/{branch}"])
        else:
            _git(["-C", str(base), "worktree", "add", str(worktree), "-b", branch])
    else:
        # Reset-on-reattach is unchanged — same two commands, now against
        # whichever branch this worktree already tracks.
        _git(["-C", str(worktree), "reset", "--hard", "HEAD"])
        _git(["-C", str(worktree), "clean", "-fd"])
    return GitWorkspaceHandle(worktree, branch)
```

The absent-key path (every non-resolver task, today's whole test suite) is
byte-for-byte unchanged: `override` is `None`, the `if override` branch is
skipped.

### 3. `WorkspaceHandle.merge` — the new port method

`ports/workspace.py` gains one abstract method, sitting next to `commit`:

```python
@abstractmethod
def merge(self, base: str) -> bool:
    """Merge origin/<base> into the current branch, without committing.

    Returns True when the merge left conflict markers in the working tree
    (dirty — nothing was staged for a clean commit); False when the merge
    applied cleanly (already up to date, fast-forward, or an automatic
    merge) — the result is staged and ready for `commit()`.
    """
```

`GitWorkspaceHandle.merge`:

```python
def merge(self, base: str) -> bool:
    _git(["-C", str(self._path), "fetch", "origin", base])
    result = subprocess.run(
        ["git", "-C", str(self._path), "merge", "--no-commit", "--no-ff", f"origin/{base}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return False
    if "CONFLICT" in result.stdout or "Automatic merge failed" in result.stdout:
        return True  # conflict markers are now in the working tree
    raise GitError(f"git merge origin/{base} failed: {result.stderr.strip()}")
```

`MemoryWorkspaceHandle` (drivers/memory.py) gets a settable `conflicted: bool
= False` attribute and records calls in `merges: list[str]`; `merge()` simply
returns the preset value — the seam a unit test uses to drive both branches of
`ResolveConflictBehavior` without a real git repo.

### 4. `ResolveConflictBehavior` — new behavior, `behaviors/resolve_conflict.py`

A small, dedicated behavior (per the plan's default) rather than a flag on
`ClaudeCliBehavior` — it has one extra step (`merge`) before the
agent-or-not fork, and giving `ClaudeCliBehavior` a git-merge branch would mix
concerns invariant 14 exists to keep apart (persona-as-data, not
control-flow-as-code). It reuses `compose_prompt` from `behaviors/agent.py`
unchanged, so the artifact/attempt/verdict machinery is identical to every
other agent step.

```python
class ResolveConflictBehavior(ConsumerBehavior):
    def __init__(self, *, clock, workspace, runner, spec, events, timeout=600.0): ...

    async def run(self, task: Task) -> BehaviorResult:
        handle = self._workspace.attach(task)
        base = task.data["source"]["base"]

        if not handle.merge(base):
            # FR-4: conflict already gone (e.g. the PR was updated in the
            # meantime) — commit the clean merge, no agent call spent.
            handle.commit(f"[resolve] merge {base} — no conflicts")
            return BehaviorResult(Outcome.DONE, f"merged {base} cleanly, no conflicts")

        attempt, relpath = next_attempt(handle.path, task.id, "resolve")
        prompt = compose_prompt(task, step="resolve", artifact_relpath=relpath, spec=self._spec)

        def on_output(line: str) -> None:
            self._events.emit("stage_output", task_id=task.id, step="resolve", attempt=attempt, line=line)

        run = await self._runner.run(
            prompt=prompt, spec=self._spec, cwd=handle.path,
            timeout=self._timeout, on_output=on_output,
        )
        handle.commit(run.summary)  # invariant 9: the worker commits, not the agent
        return BehaviorResult(run.outcome, run.summary)
```

`task.data["source"]["base"]` is required for a resolver task (stamped by
`GithubMergeabilityWatcher.poll()`, see below) — there is no fallback,
because a resolver task with no known base is a construction bug in the
watcher, not a runtime condition to handle gracefully.

### 5. `GithubMergeabilityWatcher` — new `TaskSource`, `drivers/mergeability_watcher.py`

```python
class GithubMergeabilityWatcher(TaskSource):
    kind = "mergeability"

    def __init__(
        self, *, client: GithubClient, clock: Clock, repo: str,
        repository: str, worktree_root: str,
        resolver_workflow: str = "resolver",
        head_prefix: str = "harness/",
        resolving_label: str = "harness:resolving",
    ) -> None: ...

    def poll(self) -> list[Task]:
        tasks: list[Task] = []
        for pr in self._client.list_pull_requests(self._repo, head_prefix=self._head_prefix):
            if pr.mergeable_state == "behind":
                try:
                    self._client.update_branch(self._repo, pr.number)
                except Exception:
                    continue  # one bad PR must not block the rest of this tick (FR-6)
                continue
            if pr.mergeable_state != "dirty":
                continue  # clean/blocked/unstable/unknown → leave alone (v1 scope)
            task_id = new_task_id()
            tasks.append(Task(
                id=task_id,
                workflow_template=self._resolver_workflow,
                created=self._clock.now(),
                repository=self._repository,
                worktree=f"{self._worktree_root}/{task_id}",
                dedup_key=dedup_key(self.kind, self._repo, pr.number, pr.head_sha),
                data={
                    "branch": pr.head_branch,
                    "title": f"resolve merge conflict on PR #{pr.number}",
                    "source": {
                        "kind": self.kind, "repo": self._repo, "pr": pr.number,
                        "url": pr.url, "base": pr.base_branch,
                    },
                },
            ))
        return tasks

    def report_progress(self, task: Task, progress: Progress) -> None:
        if not self._mine(task):
            return
        if progress.step in ("resolve", "land"):
            self._client.add_label(self._repo, self._pr(task), self._resolving_label)

    def finish(self, task: Task, result: FinishResult) -> None:
        if not self._mine(task):
            return
        self._client.remove_label(self._repo, self._pr(task), self._resolving_label)

    def _mine(self, task: Task) -> bool:
        src = task.data.get("source", {})
        return src.get("kind") == self.kind and src.get("repo") == self._repo

    def _pr(self, task: Task) -> int:
        return task.data["source"]["pr"]
```

Per-PR isolation is handled *inside* `poll()` (the `try/except` around
`update_branch`) rather than only at `SourcePoller.tick()`'s level: a single
persistently-misbehaving PR (e.g. one whose update call always 5xxs) must not
starve every PR that sorts after it in the same list response, tick after
tick. A failure in `list_pull_requests` itself (auth expired, GitHub down)
still propagates out of `poll()` uncaught, exactly like `GithubTaskSource`
today — caught once by `SourcePoller.tick`, logged as `source_error`, retried
next tick (FR-6, mirrors existing isolation).

There is no `_claimed`-style in-process ledger like `GithubTaskSource`'s: the
dedup key here already embeds `head_sha`, so a re-poll of the same
still-conflicted PR yields the *same* key every time (harmlessly caught by
`SourcePoller._seen`) — there is no read-after-write lag to guard against
because nothing is claimed via a label swap before the dedup check runs.

Wired exactly like `GithubTaskSource`: one instance per repo with a GitHub
origin, appended to the same `sources: list[TaskSource]`, gets a
`SourcePoller` "for free" (no new loop in `Harness.run()`).

### 6. Wiring — `build()`/`app.py`, `BoardProjection`, `cli.py`

**Board columns must see the second workflow.** `BoardProjection._store`
silently drops a task whose column name isn't in the order computed at
construction (`column_order(workflow)`), which is derived from a *single*
`Workflow`. Left alone, a resolver task sitting in the new `resolve` queue
would never render on the board — violating FR-7. `column_order` and
`BoardProjection.__init__` are extended to take extra workflows whose steps
also need a column:

```python
def column_order(workflow: Workflow, *extra: Workflow) -> tuple[str, ...]:
    order: list[str] = []
    for wf in (workflow, *extra):
        pending = [wf.start]
        while pending:
            step = pending.pop(0)
            if step == END or step in order:
                continue
            order.append(step)
            for t in wf.transitions:
                if t.from_step == step and t.to_step not in order:
                    pending.append(t.to_step)
    for wf in (workflow, *extra):
        for step in wf.steps():
            if step not in order:
                order.append(step)
    return (TODO_COLUMN,) + tuple(order) + (DONE_COLUMN, FAILED_COLUMN)

class BoardProjection(BoardView):
    def __init__(self, workflow: Workflow, *, extra_workflows: tuple[Workflow, ...] = ()) -> None:
        self._order = column_order(workflow, *extra_workflows)
        ...
```

Since `land` is shared by name between `default` and `resolver`, this adds
exactly one new column (`resolve`) in the common case.

**`build()`** gains one new parameter, `extra_workflow_names: tuple[str, ...]
= ()`. Step queues, consumers and board columns are unioned across the
primary workflow and every extra one:

```python
def build(root, workflow_name, *, ..., extra_workflow_names: tuple[str, ...] = (), ...):
    ...
    workflows = FilesystemWorkflowRepository(layout.workflows)
    workflow = workflows.get(workflow_name)
    extra = tuple(workflows.get(name) for name in extra_workflow_names)

    projection = BoardProjection(workflow, extra_workflows=extra)

    all_steps: list[str] = list(workflow.steps())
    for wf in extra:
        all_steps += [s for s in wf.steps() if s not in all_steps]
    step_queues = {step: FilesystemTaskQueue(name=step, root=layout.queues / step, events=events, quarantine=failed) for step in all_steps}
```

`RESOLVE_STEP = "resolve"` is added alongside `LANDING_STEP = "land"` at
module level, and `behavior_for` grows one branch before the generic agent
case — the same literal-step-name pattern `app.py` already uses for landing,
not a new kind of special-casing:

```python
def behavior_for(step: str) -> ConsumerBehavior:
    if step == landing_step:
        return landing
    if step == RESOLVE_STEP and catalog is not None:
        return ResolveConflictBehavior(
            clock=clock, workspace=workspace, runner=runner,
            spec=catalog.get(step), events=events, timeout=agent_timeout,
        )
    if catalog is not None:
        return ClaudeCliBehavior(...)
    return work
```

(Dispatcher routing needs no change at all — it already resolves
`task.workflow_template` per task via `WorkflowRepository.get()`, so a task
carrying `"resolver"` already routes correctly; this was true before this
feature and is the reason multi-workflow support is cheap here.)

**`cli.py`**:

- `_mergeability_sources(args, root, registry, *, client=None)` — mirrors
  `_github_sources` verbatim in structure: one `GithubMergeabilityWatcher` per
  registry entry with a GitHub origin, empty list without a token (or when
  `--watch-mergeability` is off).
- New `run` flags: `--watch-mergeability` / `--no-watch-mergeability`
  (`argparse.BooleanOptionalAction`, default `True` — the token's absence
  already yields no sources, mirroring how `--github-repo` ingestion today
  defaults to "on but silent" without a token) and `--resolver-workflow`
  (default `"resolver"`).
- `_run()`:
  ```python
  mergeability = _mergeability_sources(args, root, registry) if args.watch_mergeability else []
  sources = _github_sources(args, root, registry) + mergeability
  extra_workflows = (args.resolver_workflow,) if mergeability else ()
  harness = build(root, args.workflow, ..., sources=sources or None, extra_workflow_names=extra_workflows, ...)
  ```
- `_init()` additionally writes `workflows/resolver.json` (new constant
  `RESOLVER_DEFINITION`, written the same guarded way as
  `DEFAULT_DEFINITION`) and then calls the existing, already-generic
  `_write_default_agents(layout, resolver_workflow)` a second time — it
  writes `agents/resolve.json` (a new `AGENT_PERSONAS["resolve"]` entry) and
  no-ops on `land` (already skipped by name, and the file would already exist
  regardless).

## Data schemas

### `PullRequestInfo` (new, `drivers/github_client.py`)

| field | type | notes |
|---|---|---|
| `number` | `int` | PR number |
| `url` | `str` | `html_url` |
| `head_branch` | `str` | `head.ref` — the bare branch name, not `owner:branch` |
| `head_sha` | `str` | `head.sha` — feeds the dedup key |
| `base_branch` | `str` | `base.ref` |
| `mergeable_state` | `str` | GitHub's vocabulary: `behind`, `dirty`, `clean`, `blocked`, `unstable`, `unknown` |

### `Task.data` (resolver task, stamped by `GithubMergeabilityWatcher.poll()`)

```jsonc
{
  "branch": "harness/tsk_abc123",          // existing PR branch — GitWorkspace.attach checks it out as-is
  "title": "resolve merge conflict on PR #42",
  "source": {
    "kind": "mergeability",
    "repo": "onpaj/some-repo",
    "pr": 42,
    "url": "https://github.com/onpaj/some-repo/pull/42",
    "base": "main"
  }
}
```

### `Task.dedup_key`

```
mergeability:<repo>:<pr-number>:<head-sha>
```

New head commits on the same PR change the key, so a fixed-then-reconflicted
PR produces a fresh task (FR-3 AC2); the same conflicted head never queues
twice, restart or not (FR-3 AC1), via the existing `SourcePoller._seen`
mechanism — no new persistence.

### `workflows/resolver.json` (new, written by `harness init`)

```json
{
  "name": "resolver",
  "start": "resolve",
  "transitions": [
    { "from": "resolve", "on": "done", "to": "land" },
    { "from": "land", "on": "done", "to": "end" }
  ]
}
```

### `agents/resolve.json` (new default persona, written by `harness init`)

```json
{
  "prompt": "You are a senior developer whose only job right now is to resolve a git merge conflict. The working directory already contains a real conflict from merging the base branch into this PR's branch — files with <<<<<<<, =======, >>>>>>> markers. Read each conflicted file, understand both sides using the surrounding code and tests, and produce a correct resolution: remove every marker, preserve the combined intent of both changes, and leave a tree that would pass the project's existing tests. Do not commit, create a branch, or open a worktree — the harness handles all of that.",
  "model": null,
  "fallback_model": null,
  "allowed_tools": ["Read", "Edit", "Bash", "Grep", "Glob"],
  "allowed_outcomes": ["done"]
}
```

### CLI flags (new, on `harness run`)

| flag | default | meaning |
|---|---|---|
| `--watch-mergeability` / `--no-watch-mergeability` | on | enable/disable the mergeability watcher across every registered repo with a GitHub origin |
| `--resolver-workflow` | `resolver` | workflow template used for tasks the watcher queues |

### Event payloads

No new event names. `update_branch` and the dirty→task path ride the
existing `ingested`/`source_error` events emitted by `SourcePoller`
(`source=self._source.kind` will read `"mergeability"` for this driver,
distinguishing it from `"github"` in logs/board debugging) — consistent with
invariant 7 (a task-movement event carries both `task` and `queue`) and
invariant 21 (outward projection doesn't block decision-making).
