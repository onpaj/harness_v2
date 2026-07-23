# Design — the `healer` workflow

No UX/UI section: this feature has no user-facing surface of its own. The
existing board (`api/`) renders whatever `BoardView.snapshot()` returns; once
FR-7's column-set fix lands, healer tasks show up as ordinary cards in
ordinary columns, with no template change. (One cosmetic gap, not a feature:
`board.html`'s per-outcome badge CSS (`.badge.request_changes { … }`) has no
rule for `bug_confirmed`/`not_a_bug`, so those badges render in the default,
unstyled colour. Harmless, and explicitly out of scope per the plan — noted
here only so it isn't mistaken for a bug later.)

## Component design

Numbered to match the plan's FRs. Each item names the exact file, the exact
signature being added or changed, and how it composes with what already
exists — no new port is introduced beyond `Forge.open_issue`; everything else
is a new driver/behavior plugged into existing ports.

### 1. `Outcome` extension (`models.py`)

```python
class Outcome(str, Enum):
    DONE = "done"
    REQUEST_CHANGES = "request_changes"
    BUG_CONFIRMED = "bug_confirmed"
    NOT_A_BUG = "not_a_bug"
```

Confirmed safe by inspection: no code exhaustively switches on `Outcome`
members today (`grep Outcome\.` finds only `Outcome.DONE`/`Outcome.
REQUEST_CHANGES` as *defaults*, never an `if/elif` chain over the enum), and
`test_consumer_has_no_branch_on_outcome_value` only forbids `consumer.py` from
comparing on outcome — it says nothing about the enum's size. Genuinely
additive.

### 2. `healer` workflow definition + personas (`cli.py`)

New module-level constant, parallel to `DEFAULT_DEFINITION`:

```python
HEALER_WORKFLOW = "healer"
FILE_ISSUE_STEP = "file_issue"

HEALER_DEFINITION = {
    "name": "healer",
    "start": "diagnose",
    "transitions": [
        {"from": "diagnose", "on": "bug_confirmed", "to": "file_issue"},
        {"from": "diagnose", "on": "not_a_bug", "to": "end"},
        {"from": "file_issue", "on": "done", "to": "end"},
    ],
}
```

`_init()` writes `workflows/healer.json` unconditionally (if missing),
independent of `--workflow` — so `--heal` can be turned on later without
re-running `init` with `--workflow healer`. It then also runs
`_write_default_agents` against the healer workflow. `_write_default_agents`
gains a `skip` parameter (was hardcoded to `LANDING_STEP`):

```python
def _write_default_agents(
    layout: HarnessLayout, workflow, skip: frozenset[str] = frozenset({LANDING_STEP})
) -> None:
    ...
    for step in workflow.steps():
        if step in skip:
            continue
        ...

# in _init():
_write_default_agents(layout, harness.workflow)          # unchanged call
healer = FilesystemWorkflowRepository(layout.workflows).get(HEALER_WORKFLOW)
_write_default_agents(layout, healer, skip=frozenset({FILE_ISSUE_STEP}))
```

New persona, added to `AGENT_PERSONAS["diagnose"] = (_DIAGNOSE_PERSONA,
["Read", "Grep", "Glob", "Bash"])` — read-only tools, matching `architecture`
(it inspects the harness's own source but changes nothing). Content contract
for `_DIAGNOSE_PERSONA` (exact prose is a development-step detail, but the
*contract* is load-bearing because `file_issue` depends on it):

1. Read the diagnostic report in the task prompt (original task id, workflow,
   step, failure reason — see §8) and the harness's own source in `cwd` (the
   healer task's worktree is the `healer_repo`, per §8/FR-6).
2. Decide: is this a defect in *this* codebase, as opposed to the target
   repo, a flaky agent run, a transient GitHub/API error, or an operator
   error (e.g. a bad `--healer-repo`)? Default to `not_a_bug` when unsure — a
   missed bug just leaves the task in `done/` quietly; a false positive spams
   the issue tracker.
3. Write the full write-up (what broke, the evidence, a suggested fix) to the
   step's artifact file, as every agent step already does.
4. Additionally put an issue-ready title and explanation **in the verdict's
   `summary` field** — not just a one-liner. This overrides `compose_prompt`'s
   generic "short summary" framing on purpose, the same way `_REVIEW_PERSONA`
   already tells the reviewer to write "specifically and actionably" into
   `summary` instead of a short note. It matters here because `file_issue`
   (§7) builds the GitHub issue body purely from consumer-history summaries,
   mirroring `LandingBehavior._body` — there is no other channel from
   `diagnose` to `file_issue` and none is being added (FR-5 explicitly rules
   out touching `compose_prompt`).
5. Finish with `bug_confirmed` or `not_a_bug` — the only two outcomes
   `diagnose`'s `AgentSpec.allowed_outcomes` permits, enforced the same way
   `review`'s `done`/`request_changes` pair is today (`ClaudeCliRunner.
   verdict_from_final` → `VerdictError` on anything else).

### 3. `Forge.open_issue` (`ports/forge.py`)

```python
@dataclass(frozen=True)
class FiledIssue:
    number: int
    url: str
    title: str

class Forge(ABC):
    @abstractmethod
    def open_pull_request(...): ...          # unchanged

    @abstractmethod
    def open_issue(self, task: Task, *, title: str, body: str) -> FiledIssue:
        """File an issue against the task's own repository. Idempotent — a
        retry for the same task returns the previously filed issue instead of
        creating a second one."""
```

Symmetric with `PullRequest`/`open_pull_request` on purpose: same argument
shape (`task`, keyword `title`/`body`), same idempotency contract, same
"every implementation must grow this" obligation.

### 4. `GithubClient.create_issue` / `find_issue` (`drivers/github_client.py`)

```python
@dataclass(frozen=True)
class IssueRef:
    number: int
    url: str
    title: str

class GithubClient(ABC):
    ...
    @abstractmethod
    def create_issue(self, repo: str, *, title: str, body: str) -> IssueRef: ...

    @abstractmethod
    def find_issue(self, repo: str, *, marker: str) -> IssueRef | None:
        """An issue (open or closed) whose body contains `marker`, or None."""
```

`find_issue` is the idempotency check FR-4 requires, and it deliberately does
**not** use GitHub's Search API. `HttpGithubClient.find_issue` instead calls
the same kind of endpoint `list_issues` already uses — `GET
/repos/{repo}/issues?state=all&per_page=100` — and scans bodies client-side
for `marker`. Reasoning: Search has documented, unbounded indexing lag; a
fast retry of `file_issue` (crash between `create_issue` and the consumer
recording `done`) could run again before the just-created issue is
searchable and would file a duplicate. The direct list endpoint is exactly
the mechanism `find_pull_request`/`create_pull_request` already trust for
the equivalent PR-idempotency check — reusing it here keeps the risk profile
identical to code already in production, rather than introducing a new,
weaker one. Trade-off, called out rather than hidden: `per_page=100` without
pagination means a `healer_repo` with more than 100 issues (open + closed)
could miss an older marker; the harness's own repo is not expected to
approach that in v1, but a future fix (if needed) is pagination in this one
method, not a mechanism change.

`FakeGithubClient` grows `self.issues: list[IssueRef]` and
`self._issue_bodies: dict[int, str]`; `create_issue` appends/records,
`find_issue` scans `_issue_bodies` for the substring — same shape as its
existing `pulls`/`created` bookkeeping.

### 5. `GithubForge.open_issue` (`drivers/github_forge.py`)

```python
_HEALER_MARKER = "<!-- harness-healer:{task_id} -->"

def open_issue(self, task: Task, *, title: str, body: str) -> FiledIssue:
    client = self._client
    if client is None:
        raise ForgeError(
            "GITHUB_TOKEN is not set — cannot file an issue. "
            "Export it, or run with --forge fake."
        )
    repo_path = self._repo_path(task)
    if repo_path is None:
        raise ForgeError(
            f"task {task.id}: cannot locate repository {task.repository!r} — "
            "not in repos.json and the task carries no worktree"
        )
    slug = self._slug_of(repo_path)
    if slug is None:
        raise ForgeError(f"{task.repository} has no GitHub origin — cannot file an issue")

    marker = _HEALER_MARKER.format(task_id=task.id)
    try:
        existing = client.find_issue(slug, marker=marker)
        if existing is not None:
            return FiledIssue(existing.number, existing.url, existing.title)
        created = client.create_issue(slug, title=title, body=f"{body.rstrip()}\n\n{marker}\n")
    except ForgeError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ForgeError(f"GitHub refused to file an issue on {slug}: {_explain(error)}") from error
    return FiledIssue(created.number, created.url, title)
```

Reuses `_repo_path`/`_slug_of`/`_explain` verbatim — no change to those
helpers. `_repo_path` already prefers the registry over `task.worktree`
(invariant 15), which is exactly right here: the healer task's `repository`
is the configured `healer_repo` name (§8/FR-6), resolved through the same
`RepositoryRegistry` every other task uses.

### 6. `MemoryForge`/`FakeForge.open_issue` (`drivers/memory.py`, `drivers/fake_forge.py`)

`MemoryForge` (tests): idempotent by `task.id` directly (no marker needed —
it's in-process, no text search):

```python
def open_issue(self, task: Task, *, title: str, body: str) -> FiledIssue:
    existing = self._issues_by_task.get(task.id)
    if existing is not None:
        return existing
    issue = FiledIssue(len(self.issues) + 1, f"https://forge.local/issues/{len(self.issues)+1}", title)
    self.issues.append(issue)
    self._issues_by_task[task.id] = issue
    return issue
```

`FakeForge` (offline `harness run --forge fake`, e2e/smoke): records into a
new `<root>/forge/issues.json`, mirroring `prs.json`'s shape/idempotency
exactly (`_load_issues`/`_store_issues` parallel `_load`/`_store`), keyed by a
`task_id` field on the record instead of a body-marker scan:

```python
{"number": 1, "url": "file://.../issues.json#1", "title": "...", "body": "...", "task_id": "tsk_..."}
```

### 7. `FileIssueBehavior` (new file `behaviors/file_issue.py`)

```python
class FileIssueBehavior(ConsumerBehavior):
    def __init__(self, *, forge: Forge) -> None:
        self._forge = forge

    async def run(self, task: Task) -> BehaviorResult:
        issue = self._forge.open_issue(task, title=self._title(task), body=self._body(task))
        return BehaviorResult(Outcome.DONE, f"filed issue {issue.url}")

    @staticmethod
    def _title(task: Task) -> str:
        failed = task.data.get("failed_task") or {}
        if not failed:
            return f"harness bug found while healing task {task.id}"
        return f"harness bug: task {failed.get('id')} failed at step {failed.get('step')!r}"

    @staticmethod
    def _body(task: Task) -> str:
        lines = ["## Diagnosis", ""]
        for entry in task.history:
            if entry.actor.startswith("consumer:") and entry.summary:
                lines.append(f"- **{entry.from_step}** — {entry.summary}")
        failed = task.data.get("failed_task") or {}
        if failed:
            lines += [
                "", "## Original failure", "",
                f"- task: {failed.get('id')}",
                f"- workflow: {failed.get('workflow')}",
                f"- step: {failed.get('step')}",
                f"- reason: {failed.get('reason')}",
                f"- repository: {failed.get('repository')}",
            ]
        return "\n".join(lines) + "\n"
```

No `Workspace`, no `ArtifactView` — the issue body is assembled purely from
`task.history` entry summaries, the exact same pattern
`LandingBehavior._body` already uses for the PR body. This is what makes
§2 point 4 (the diagnose persona putting the full write-up into `summary`,
not just a short note) load-bearing rather than cosmetic: it is the only
channel `file_issue` has into what `diagnose` found.

Only ports touched: `Forge`, `models`. Passes the architecture guard
(`test_behaviors_import_only_ports_not_drivers`) trivially.

### 8. `FailedQueueTaskSource` (new file `drivers/failed_queue_source.py`)

```python
class FailedQueueTaskSource(TaskSource):
    kind = "failed-queue"

    def __init__(
        self, *, failed: TaskQueue, clock: Clock,
        target_workflow: str = "default",
        healer_workflow: str = "healer",
        healer_repo: str,
    ) -> None: ...

    def poll(self) -> list[Task]:
        return [
            self._build(failure)
            for failure in self._failed.list()
            if failure.workflow_template == self._target_workflow
        ]

    def _build(self, failure: Task) -> Task:
        step, reason = self._failure_point(failure)
        return Task(
            id=new_task_id(),
            workflow_template=self._healer_workflow,
            created=self._clock.now(),
            repository=self._healer_repo,
            dedup_key=dedup_key(self.kind, failure.id, len(failure.history)),
            data={
                "request": self._diagnostic_report(failure, step, reason),
                "failed_task": {
                    "id": failure.id, "workflow": failure.workflow_template,
                    "step": step, "reason": reason, "repository": failure.repository,
                },
                "source": {"kind": self.kind, "task": failure.id},
            },
        )

    @staticmethod
    def _failure_point(failure: Task) -> tuple[str | None, str | None]:
        """`(from_step, reason)` of the FAILED-labelled history entry — the one
        `Dispatcher._fail`/`Consumer._fail` wrote. `Task` has no separate
        failure-reason field; that entry is the only place it lives."""
        for entry in reversed(failure.history):
            if entry.to_step == FAILED:
                return entry.from_step, entry.reason
        return None, None

    def report_progress(self, task: Task, progress: Progress) -> None: ...  # no-op
    def finish(self, task: Task, result: FinishResult) -> None: ...          # no-op
```

Design notes:

- **Reads `failed.list()` only** — never `claim`/`transfer`. The original
  task is never touched; it stays in `failed/` for `TaskControl.restart` to
  act on, exactly as the plan's NFR requires.
- **Field naming deviates slightly from the plan on purpose.** The plan's
  FR-5 lists the structured field as `status`; here it's `step`, because what
  it actually holds is `HistoryEntry.from_step` of the FAILED entry — *which
  step the task was in when it failed* — not `Task.status` (which, on a
  failed task, is always just the literal `"failed"` and would be a
  redundant, confusing thing to also call `status` inside `failed_task`).
- **Dedup is delegated entirely to `SourcePoller`/`Task.dedup_key`** — this
  driver carries no ledger (unlike `GithubTaskSource._claimed`, which exists
  only to cover *read-after-write lag on a label swap*; there is no label
  swap here, so there is nothing to lag). `dedup_key("failed-queue",
  failure.id, len(failure.history))` is exactly the plan's FR-2 key.
- **Known, accepted cost, not a bug:** because the original task is never
  removed from `failed/`, every poll tick reconstructs a full `Task` (with a
  fresh `new_task_id()`) for every task already sitting in `failed/`, only
  for `SourcePoller.tick` to discard it as a duplicate. `GithubTaskSource`
  avoids the equivalent cost via its label swap (a claimed issue drops out of
  `list_issues(label=select_label)`); there is no analogous mechanism here
  without either mutating the original failed task (forbidden by the NFR) or
  duplicating the poller's private `_seen` set inside this driver (which
  would be a second, competing dedup mechanism — exactly what FR-2 says not
  to build). The cost is pure object construction, no I/O — accepted as-is.
- `report_progress`/`finish` are unconditional no-ops (the open question in
  the plan, resolved for v1): there is no external system to project a
  healer task's progress into, so there is nothing to write, on either the
  healer task or (necessarily) the original failed task.

### 9. Multi-workflow wiring (FR-7)

**`projection.py`** — `column_order` and `BoardProjection.__init__` become
variadic. Both are backward compatible: a single positional `Workflow`
argument is exactly what every existing call site already passes, and
`*workflows` accepts it unchanged.

```python
def column_order(*workflows: Workflow) -> tuple[str, ...]:
    order: list[str] = []
    for workflow in workflows:
        pending: list[str] = [workflow.start]
        while pending:
            step = pending.pop(0)
            if step == END or step in order:
                continue
            order.append(step)
            for transition in workflow.transitions:
                if transition.from_step == step and transition.to_step not in order:
                    pending.append(transition.to_step)
        for step in workflow.steps():
            if step not in order:
                order.append(step)
    return (TODO_COLUMN,) + tuple(order) + (DONE_COLUMN, FAILED_COLUMN)

class BoardProjection(BoardView):
    def __init__(self, *workflows: Workflow) -> None:
        self._order = column_order(*workflows)
        ...  # unchanged below
```

With healing off, `BoardProjection(workflow)` produces byte-identical output
to today (one workflow in, one tuple out, same algorithm per workflow). With
healing on, `BoardProjection(default_workflow, healer_workflow)` appends
`diagnose`/`file_issue` after the default workflow's columns and before
`done`/`failed`.

Nothing else changes: `hydrate`, `apply`, `snapshot`, `_store`, `_bump` all
already operate on the flat `self._order` tuple and a `step → column` dict —
they don't know or care how many workflows contributed to `_order`.

**`app.py`** — `build()` gains three parameters and a small pre-flight
check:

```python
DEFAULT_HEALER_WORKFLOW = "healer"
FILE_ISSUE_STEP = "file_issue"

class StepCollisionError(ValueError):
    """Two concurrently-active workflows declare the same step name."""

def build(
    root, workflow_name, *,
    ...,                                   # unchanged params
    heal: bool = False,
    healer_repo: str | None = None,
    healer_workflow_name: str = DEFAULT_HEALER_WORKFLOW,
) -> Harness:
    ...
    if heal and not healer_repo:
        raise ValueError("--heal requires --healer-repo")

    workflows_repo = FilesystemWorkflowRepository(layout.workflows)
    active_names = [workflow_name, *( [healer_workflow_name] if heal else [] )]
    active_workflows = [workflows_repo.get(name) for name in active_names]
    workflow = active_workflows[0]          # primary — Harness.workflow, unchanged meaning
    _assert_no_step_collision(active_workflows)

    projection = BoardProjection(*active_workflows)
    ...
    step_queues = {
        step: FilesystemTaskQueue(name=step, root=layout.queues / step, events=events, quarantine=failed)
        for wf in active_workflows
        for step in wf.steps()
    }
```

```python
def _assert_no_step_collision(workflows: list[Workflow]) -> None:
    owner: dict[str, str] = {}
    for wf in workflows:
        for step in wf.steps():
            if step in owner and owner[step] != wf.name:
                raise StepCollisionError(
                    f"step {step!r} is declared by both {owner[step]!r} and "
                    f"{wf.name!r} workflows — active workflow step names must be unique"
                )
            owner[step] = wf.name
```

`default` (`plan/design/architecture/development/review/land`) and `healer`
(`diagnose/file_issue`) don't collide today, so with the shipped workflows
this check never fires — it exists to turn a future silent misconfiguration
(two workflows sharing a step name, which today would just make the second
one's queue clobber/steal the first's tasks) into an immediate, readable
`build()`-time error, per the plan's open question.

`behavior_for(step)` gains one branch, mirrored on the existing
`landing_step` special-case:

```python
file_issue_behavior = FileIssueBehavior(forge=forge)   # built unconditionally, harmless if unused

def behavior_for(step: str) -> ConsumerBehavior:
    if step == landing_step:
        return landing
    if step == FILE_ISSUE_STEP:
        return file_issue_behavior
    if catalog is not None:
        return ClaudeCliBehavior(...)
    return work
```

**`FailedQueueTaskSource` construction — the ordering subtlety.** `build()`
today constructs the composite `events` (which wraps `SourceReflectorSink
(sources)`) *before* the `failed` `TaskQueue` exists, but
`FailedQueueTaskSource` needs that exact `failed` queue object. Rather than
reordering the whole function (queues need `events`; `events` needs
`sources`; `sources` needs `failed` — a real cycle), `build()` keeps the
`sources` list it already builds early, and **mutates it in place** once
`failed` exists:

```python
sources = list(sources or [])            # same object handed to SourceReflectorSink below
...
events = CompositeEventSink(..., SourceReflectorSink(sources))   # holds a live reference, not a copy
...
failed = FilesystemTaskQueue(...)
...
if heal:
    sources.append(FailedQueueTaskSource(
        failed=failed, clock=clock,
        target_workflow=workflow_name,
        healer_workflow=healer_workflow_name,
        healer_repo=healer_repo,
    ))
...
pollers = [SourcePoller(source=source, inbox=inbox, events=events) for source in sources]
```

This works because `SourceReflectorSink.__init__` stores `self._sources =
sources` — the same list object, not a defensive copy — so an append after
construction is still visible when the reflector later iterates it at
emit-time. `pollers` is built even later in the function, so it picks up the
appended source with no special-casing at all. This is the one place the
plan's "`build()` ... constructs+registers the `FailedQueueTaskSource`
itself" note becomes a concrete mechanism rather than a hand-wave — worth a
one-line code comment at the `sources.append(...)` call site so it isn't
mistaken for dead code by a later reader.

`Harness` itself needs no change: `step_queues`, `pollers`,
`_seed_pollers`, `run()`, the dispatcher/consumer loops all already operate
generically over whatever dict/list `build()` hands them.

### 10. CLI wiring (FR-8)

`cli.py`, `run` subparser:

```python
run.add_argument("--heal", action="store_true")
run.add_argument("--healer-repo", default=None, dest="healer_repo")
```

`_run()`:

```python
if args.heal and not args.healer_repo:
    print("error: --heal requires --healer-repo", file=sys.stderr)
    return 2
...
try:
    harness = build(
        root, args.workflow,
        ...,                       # unchanged
        heal=args.heal,
        healer_repo=args.healer_repo,
    )
except (WorkflowNotFound, StepCollisionError, ValueError) as error:
    print(f"error: {error}", file=sys.stderr)
    return 2
```

(The CLI-level check is the fast, friendly path; `build()`'s own
`ValueError` is the backstop for any other caller — tests, a future second
entry point — so the invariant holds even if someone calls `build()`
directly with `heal=True, healer_repo=None`.)

`_init()` changes are covered in §2. No changes to `harness submit` (FR-8
acceptance criterion — submitting a task is orthogonal to healing).

## Data schemas

### `Task.data` shapes (healer tasks only)

```jsonc
{
  // fed into the diagnose agent's prompt verbatim via the existing
  // compose_prompt/_request_of("request") path — no prompt-composition change
  "request": "Task tsk_abc123 (workflow 'default') failed at step 'development'.\nReason: behavior raised an exception: ...\n\nDetermine whether this was caused by a defect in the harness's own code...",

  // structured facts for the board/API and for FileIssueBehavior — no prose parsing needed
  "failed_task": {
    "id": "tsk_abc123",
    "workflow": "default",
    "step": "development",          // HistoryEntry.from_step of the FAILED entry; null if the task never left the inbox
    "reason": "behavior raised an exception: ...",
    "repository": "my-target-repo"  // the ORIGINAL task's repository, not the healer's
  },

  // existing `data.source` convention (invariant 19), routes report_progress/finish to this source's kind
  "source": { "kind": "failed-queue", "task": "tsk_abc123" }
}
```

### `Task` fields (healer task, set by `FailedQueueTaskSource._build`)

| field               | value                                                              |
|---------------------|---------------------------------------------------------------------|
| `id`                | fresh (`new_task_id()`)                                             |
| `workflow_template` | `"healer"` (configurable via `healer_workflow_name`)                 |
| `repository`        | the configured `healer_repo` name (FR-6) — **not** the failed task's own `repository` |
| `dedup_key`         | `dedup_key("failed-queue", <failed_task.id>, <len(failed_task.history)>)` |
| `status`            | `None` (fresh) — router sends it to `workflow.start` = `"diagnose"`  |

### `Forge.open_issue` contract

Request (Python call, not wire — `Forge` is an in-process port):

```python
forge.open_issue(task: Task, *, title: str, body: str) -> FiledIssue
```

Response:

```python
FiledIssue(number: int, url: str, title: str)
```

### GitHub REST calls added to `HttpGithubClient`

**Create** — `POST /repos/{repo}/issues`

```jsonc
// request
{ "title": "harness bug: task tsk_abc123 failed at step 'development'", "body": "## Diagnosis\n\n...\n\n<!-- harness-healer:tsk_xyz789 -->\n" }
// response (subset used)
{ "number": 42, "html_url": "https://github.com/onpaj/harness_v2/issues/42", "title": "..." }
```

**Idempotency check** — `GET /repos/{repo}/issues?state=all&per_page=100`,
filtered client-side for `<!-- harness-healer:{healer_task.id} -->` in
`body` (PRs excluded via the existing `"pull_request" in item` skip, same as
`list_issues`).

### Filesystem records (`FakeForge`, `--forge fake`)

`<root>/forge/issues.json` (new file, sibling to the existing `prs.json`):

```jsonc
[
  {
    "number": 1,
    "url": "file:///.../forge/issues.json#1",
    "title": "harness bug: task tsk_abc123 failed at step 'development'",
    "body": "## Diagnosis\n\n...\n",
    "task_id": "tsk_xyz789"        // the idempotency key for this driver
  }
]
```

### Workflow definition (`workflows/healer.json`, seeded by `harness init`)

```jsonc
{
  "name": "healer",
  "start": "diagnose",
  "transitions": [
    { "from": "diagnose", "on": "bug_confirmed", "to": "file_issue" },
    { "from": "diagnose", "on": "not_a_bug", "to": "end" },
    { "from": "file_issue", "on": "done", "to": "end" }
  ]
}
```

### Agent definition (`agents/diagnose.json`, seeded by `harness init`)

```jsonc
{
  "prompt": "<_DIAGNOSE_PERSONA, see §2>",
  "model": null,
  "fallback_model": null,
  "allowed_tools": ["Read", "Grep", "Glob", "Bash"],
  "allowed_outcomes": ["bug_confirmed", "not_a_bug"]
}
```

(`agents/file_issue.json` is never written — `file_issue` runs
`FileIssueBehavior`, not an agent, the same way `land` never gets a
`land.json`.)

### Event payloads

No new event names. `FailedQueueTaskSource`'s output flows through the
already-existing `"ingested"` event (`SourcePoller.tick`, unchanged) and
`"duplicate_ignored"` for every re-poll of an already-healed failure — both
already carry `task_id`/`queue`/`task` (or just `task_id`/`source` for the
duplicate case), so the board sees healer tasks appear exactly the way any
other sourced task does. `GithubForge`/`FakeForge`/`MemoryForge.open_issue`
raise/return synchronously from within the `file_issue` consumer step, so a
failure surfaces as the existing `"failed"` event (`Consumer._fail`) with no
schema change.

### CLI surface (new flags only, `harness run`)

| flag             | default | meaning                                                        |
|------------------|---------|------------------------------------------------------------------|
| `--heal`         | off     | build and run `FailedQueueTaskSource` + the `healer` workflow's queues |
| `--healer-repo`  | `None`  | name in `repos.json` the healer task attaches to and files issues against; required when `--heal` is set |
