# Development — the `healer` workflow

Implemented per `plan-01.md`/`design-01.md`/`architecture-01.md`, following the
architecture step's 10-step build order exactly. All 518 tests pass
(`.venv/bin/pytest -q`), including the full pre-existing suite (no
regressions) plus 47 new tests. No open questions were left unresolved.

## What was implemented

### 1. `Outcome` extension (`src/harness/models.py`)
Added `BUG_CONFIRMED = "bug_confirmed"` and `NOT_A_BUG = "not_a_bug"` to the
`Outcome` enum. Purely additive — confirmed no code exhaustively switches on
`Outcome`, and `test_consumer_has_no_branch_on_outcome_value` only forbids a
branch in `consumer.py`, not enum size.

### 2. `Forge.open_issue` (`src/harness/ports/forge.py`)
New `FiledIssue` dataclass (`number`, `url`, `title`) and
`Forge.open_issue(task, *, title, body) -> FiledIssue`, symmetric with
`open_pull_request`/`PullRequest`. Implemented in all three concrete forges:

- **`MemoryForge.open_issue`** (`drivers/memory.py`) — idempotent by
  `task.id` directly (in-process, no text search needed).
- **`FakeForge.open_issue`** (`drivers/fake_forge.py`) — records into
  `<root>/issues.json` (sibling to `prs.json`), idempotent by a `task_id`
  field on the record.
- **`GithubForge.open_issue`** (`drivers/github_forge.py`) — idempotent via
  a new `GithubClient.find_issue` call before `create_issue`, using a body
  marker `<!-- harness-healer:<task.id> -->` (mirrors the existing
  `find_pull_request`/`create_pull_request` shape for PRs, per the
  architecture's decision 2). Raises `ForgeError` on the same failure
  classes as `open_pull_request` (no token, unresolvable repo, non-GitHub
  origin, API error).

`GithubClient` gained `IssueRef`, `create_issue`, `find_issue` on the ABC,
`FakeGithubClient` (in-memory `filed`/`_filed_bodies`) and `HttpGithubClient`
(stdlib `urllib`, no new dependency). `find_issue` deliberately does **not**
use GitHub's Search API — it calls `GET /repos/{repo}/issues?state=all&per_page=100`
and scans bodies client-side, the same direct-fetch pattern `list_issues`
already uses, to avoid Search's indexing lag causing a duplicate issue on a
fast retry.

### 3. `FileIssueBehavior` (new file `src/harness/behaviors/file_issue.py`)
The `file_issue` step's behavior — parallel in shape to `LandingBehavior` but
simpler: no `Workspace`/`ArtifactView`, just `forge.open_issue(...)`. The
issue title/body are built purely from `task.data["failed_task"]` and
consumer-history summaries (mirrors `LandingBehavior._body`). Touches only
`ports.forge`/`models` — passes `test_behaviors_import_only_ports_not_drivers`.

### 4. `FailedQueueTaskSource` (new file `src/harness/drivers/failed_queue_source.py`)
Polls `failed.list()` for tasks whose `workflow_template` matches the
configured `target_workflow` (default `"default"`) and builds one healer
task per match. Reads only — never claims/moves/mutates the original failed
task, so `TaskControl.restart` keeps working on it independently. Dedup is
delegated entirely to the existing `SourcePoller`/`Task.dedup_key` machinery
via `dedup_key("failed-queue", failure.id, len(failure.history))` — no
driver-local ledger, since `history` strictly grows on every distinct
failure of the same task id (confirmed against `Dispatcher._fail`,
`Consumer._fail`, and `TaskControlService.restart`). `report_progress`/
`finish` are no-ops (there's no external system to project a healer task's
progress into).

### 5. Multi-workflow wiring (`src/harness/projection.py`, `src/harness/app.py`)
- `column_order`/`BoardProjection.__init__` are now variadic
  (`*workflows: Workflow`), unioning columns across every active workflow.
  One workflow in still produces byte-identical output to before.
- `build()` gained `heal: bool = False`, `healer_repo: str | None = None`,
  `healer_workflow_name: str = "healer"`. When `heal=True`: computes
  `active_workflows = [workflow, healer_workflow]`, unions `step_queues`
  across both, passes both to `BoardProjection`, and constructs+appends a
  `FailedQueueTaskSource` to the same `sources` list object
  `SourceReflectorSink` already holds a live reference to (documented at the
  call site — this is the mechanism, not a hand-wave). `behavior_for` gained
  a branch for `FILE_ISSUE_STEP` (mirroring the existing `landing_step`
  special-case); `file_issue_behavior` is constructed unconditionally,
  harmless if unused.
- New `StepCollisionError(ValueError)` + `_assert_no_step_collision()`,
  called at `build()` time whenever healing is on — raises loudly if two
  active workflows declare the same step name, instead of one queue
  silently stealing the other's tasks.
- `heal=False` (the default) is verified byte-identical to pre-change
  behavior by the full pre-existing `test_app.py` suite passing unchanged.

### 6. CLI wiring (`src/harness/cli.py`)
- `HEALER_DEFINITION` workflow JSON constant + `_DIAGNOSE_PERSONA` (read-only
  tools: Read/Grep/Glob/Bash, matching `architecture`'s posture). Registered
  in `AGENT_PERSONAS["diagnose"]`.
- `_write_default_agents` gained a `skip: frozenset[str]` parameter (was
  hardcoded to `{LANDING_STEP}`).
- `_init()` now always seeds `workflows/healer.json` (independent of
  `--workflow`, so `--heal` can be turned on later without re-running init)
  and `agents/diagnose.json` (via a direct
  `FilesystemWorkflowRepository(...).get("healer")` call, skipping
  `file_issue` the same way `land` is skipped — `agents/file_issue.json` is
  never written).
- `harness run` gained `--heal` (off by default, documented rollout-burst
  behavior in its help text) and `--healer-repo` (required when `--heal` is
  set — checked both in `_run()` as a fast/friendly `sys.exit(2)` and as
  `build()`'s own `ValueError` backstop). `StepCollisionError` is caught
  alongside `WorkflowNotFound`.

### 7. Tests
- Unit tests for every new/changed piece, mirroring the existing test file
  for its counterpart: `test_forge_memory.py`, `test_fake_forge.py`,
  `test_github_client.py`, `test_github_forge.py` (all extended in place),
  plus new `test_file_issue_behavior.py`, `test_failed_queue_source.py`.
- `test_projection.py` extended with a two-workflow union test.
- `test_app.py` extended with `heal=True`/`False` build tests, the
  `--healer-repo` requirement, and `StepCollisionError`.
- `test_cli.py` extended with `harness init` seeding assertions and
  `harness run --heal` flag plumbing (using the existing
  monkeypatch-`build`-and-capture-kwargs pattern already used for
  `--agent`).
- New `test_healer_e2e.py`: a default-workflow task whose `development` step
  raises → lands in `failed/` → `FailedQueueTaskSource`/`SourcePoller` pick
  it up → healer task runs `diagnose` (scripted `bug_confirmed`) →
  `file_issue` (in-memory forge) → `done/`, one issue recorded, and the
  original task untouched in `failed/`. A parallel `not_a_bug` test (zero
  issues filed), a re-poll-doesn't-double-heal test, and a test confirming a
  healer task's *own* failure isn't itself healed again (no meta-healing
  loop, since `target_workflow` defaults to `"default"`).

## Files changed

New:
- `src/harness/behaviors/file_issue.py`
- `src/harness/drivers/failed_queue_source.py`
- `tests/test_file_issue_behavior.py`
- `tests/test_failed_queue_source.py`
- `tests/test_healer_e2e.py`

Modified:
- `src/harness/models.py` — `Outcome` extension
- `src/harness/ports/forge.py` — `FiledIssue`, `Forge.open_issue`
- `src/harness/drivers/memory.py` — `MemoryForge.open_issue`
- `src/harness/drivers/fake_forge.py` — `FakeForge.open_issue`
- `src/harness/drivers/github_client.py` — `IssueRef`, `create_issue`,
  `find_issue` (ABC + Fake + Http)
- `src/harness/drivers/github_forge.py` — `GithubForge.open_issue`
- `src/harness/projection.py` — variadic `column_order`/`BoardProjection`
- `src/harness/app.py` — `heal`/`healer_repo`/`healer_workflow_name`,
  `StepCollisionError`, `FailedQueueTaskSource` wiring, `FILE_ISSUE_STEP`
  branch
- `src/harness/cli.py` — healer workflow/persona constants, `_init()`
  seeding, `--heal`/`--healer-repo` flags
- `tests/test_forge_memory.py`, `tests/test_fake_forge.py`,
  `tests/test_github_client.py`, `tests/test_github_forge.py`,
  `tests/test_projection.py`, `tests/test_app.py`, `tests/test_cli.py` —
  extended with new coverage

## How to verify

```sh
.venv/bin/pytest -q                              # 518 passed, 1 skipped
.venv/bin/pytest -q tests/test_healer_e2e.py -v   # the end-to-end healing flow
```

Manual smoke (performed during development, not left in the tree):
```sh
harness init --root /tmp/x
cat /tmp/x/workflows/healer.json      # diagnose -> file_issue -> end
ls /tmp/x/agents                      # includes diagnose.json, not file_issue.json
harness run --root /tmp/x --heal --healer-repo <name> --forge fake --agent dummy --api-port 0
# starts cleanly; "warning: <name> has no GitHub origin, not scanned" only if
# the registered repo isn't a real GitHub clone (expected in a scratch smoke)
```

`harness run` without `--heal` is unaffected — no extra queues, columns, or
sources — verified by the full pre-existing `test_app.py`/`test_cli.py`
suites passing unchanged alongside the new healer-specific tests.
