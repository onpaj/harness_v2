# Agent Harness (harness_v2) — Design

Date: 2026-07-19
Status: Approved for implementation

## 1. What this is

A stateless, CLI-driven multi-agent orchestration platform. Agents are declared as
configuration; each has a durable input queue; each invocation is one `claude -p`
subprocess that reads a task, does work in a git worktree, writes artifacts, and
emits follow-up tasks to other agents. An actor model over one-shot CLI processes.

Three non-negotiable constraints:

1. **CLI-only.** Claude is reached exclusively by spawning `claude -p`. No Anthropic
   API, no SDK path that talks to the API.
2. **Agents are stateless.** No memory between invocations. All state arrives with
   the task, preferably as filesystem artifacts inherited through git.
3. **Reactive and proactive.** Agents drain queues; a scheduler injects tasks on
   cron. Everything is observable end to end.

## 2. Decisions

These were settled during brainstorming and are binding for the MVP.

| Decision | Choice | Rationale |
|---|---|---|
| Relationship to v1 (`onpaj/harness`) | **Coexist** | v1 keeps running its pipeline untouched. No migration, no shared code. |
| Language | **Python 3.11** | Matches the ecosystem needs (APScheduler, FastAPI, OTel). `python3.11` at `~/.local/bin/python3.11`; plain `venv` + `pip` (no `uv` binary on this machine). |
| Scope | **Full PRD MVP** | Queues, dispatcher, runner, routing, retries/DLQ, scheduler, run store, read-only dashboard. Delivered in phases so it runs early. |
| Harness artifacts | **In-repo `.harness/`** | `task.json`, `result.json`, logs committed into the target repo alongside work products, so the record travels with the code. |
| Merge policy | **Merge to integration branch, gate `main`** | Traces auto-merge into a per-repo integration branch. Promotion to `main` is a human PR. The harness never auto-writes `main`. |
| Repo-less agents | **Internal scratch repo** | The harness keeps its own git repo; repo-less tasks get worktrees there. One code path — every run always has a worktree and an `output_ref`. |
| Fan-in | **Not in MVP** | Linear + fan-out only. `join` is added later without changing the task contract. |
| Isolation | **Tool allow-list + workspace confinement** | Runs as the OS user, `cwd` = worktree, `--add-dir` limited to it. Containerization is a later executor, not a refactor. |
| Rate limits | **Pause dispatch, resume automatically** | Detect throttling, stop leasing globally, surface `paused: rate limited`, retry with backoff. In-flight runs finish. |
| Process model | **Long-running daemon + CLI** | `agentharness serve` runs dispatcher + scheduler + dashboard in one asyncio process, installable as a launchd service. |
| Global concurrency | **3, configurable** | Coexists with interactive Claude Code usage on the same subscription. |
| Branch retention | **Keep, GC after 30 days** | `agentharness gc` prunes merged `run/*` branches past the window. |
| Human-in-the-loop | **Not in MVP** | The `main` promotion gate is already human. `needs_input` parks a task as `blocked` for inspection. |
| Proof workload | **Port v1's dev pipeline** | `planner → implementer → reviewer` over a real repo, as the acceptance test. |

### Consequence worth stating

Because `.harness/` artifacts are committed but merges stop at the integration
branch, the run noise never reaches `main` unattended. The human PR that promotes
integration → `main` is where you choose to keep or strip `.harness/`.

## 3. Layout on disk

### Harness home

`~/.agentharness/`, overridable via `AGENTHARNESS_HOME`. The control plane's own
state. Never lives inside a target repo.

```
config.yaml                     # global concurrency, rate-limit policy, paths
agents/<name>.yaml
agents/<name>.system.md
repos/<repo_id>.git             # bare mirror per managed repo
scratch.git                     # internal repo backing repo-less agents
queues/<agent>/pending/         # FIFO-ish mailbox
queues/<agent>/processing/      # leased, with visibility deadline
queues/<agent>/dead/            # dead-letter
worktrees/<trace_id>/<task_id>/ # transient; removed after commit
runs.db                         # SQLite: tasks, runs, handoffs, schedules, events
logs/
```

### Inside a target repo

Work products at their natural paths, plus harness artifacts under a reserved path:

```
.harness/runs/<trace_id>/<task_id>/task.json
.harness/runs/<trace_id>/<task_id>/result.json
.harness/runs/<trace_id>/<task_id>/logs/{stdout.log,stderr.log,cli.json}
```

Committed on branch `run/<task_id>`, merged into the repo's configured
`integration_branch`.

## 4. Module boundaries

Each unit has one job, a typed interface, and is testable alone.

```
src/agentharness/
  config.py                 # global config load/validate
  ids.py                    # ULID minting for task/trace/run ids
  models.py                 # pydantic types shared by every other module
  registry/agents.py        # agent YAML load + validation, tool & routing allow-lists
  registry/repos.py         # managed repo registration, mirror maintenance
  git/mirror.py             # clone --mirror, fetch, ref inspection
  git/worktree.py           # worktree add/commit/push/remove
  git/merge.py              # leaf run/* branches -> integration branch
  git/lock.py               # per-repo lock serialising ref-mutating ops only
  queue/base.py             # Queue ABC: enqueue/lease/ack/nack/dead_letter
  queue/filesystem.py       # atomic rename() + visibility timeout
  store/db.py               # SQLite schema + migrations
  store/runs.py             # task/run/handoff/event persistence, trace queries
  runner/executor.py        # Executor ABC -> LocalExecutor, FakeExecutor
  runner/prompt.py          # protocol preamble + prompt composition
  runner/result.py          # result.json parsing, validation, degraded fallback
  runner/runner.py          # the run lifecycle
  dispatch/dispatcher.py    # lease loop, per-agent + global semaphores
  dispatch/routing.py       # handoff validation against can_handoff_to
  dispatch/retry.py         # backoff policy, DLQ
  dispatch/limits.py        # global concurrency + rate-limit pause gate
  scheduler/scheduler.py    # APScheduler, job store in runs.db
  obs/logging.py            # structlog JSON lifecycle events
  obs/metrics.py            # cost/latency/queue-depth aggregation
  web/app.py                # read-only FastAPI + HTMX dashboard
  cli.py                    # typer entrypoint
```

Dependency direction is strictly downward: `models` depends on nothing;
`git`/`queue`/`store` depend only on `models`; `runner` depends on those;
`dispatch` depends on `runner`; `cli`/`web`/`scheduler` sit on top. No cycles.

## 5. Data model

### Agent definition

```yaml
name: researcher
description: Gathers sources on a topic and produces a research brief.
model: claude-sonnet-4-5
permission_mode: acceptEdits         # default | plan | acceptEdits | bypassPermissions
allowed_tools: [Read, Write, WebSearch, WebFetch, Bash]
mcp_config: mcp/research.json        # optional
system_prompt_file: agents/researcher.system.md
max_turns: 25
timeout_seconds: 900
concurrency: 3
retries:
  max_attempts: 3
  backoff: exponential
repos: [app-backend]                 # managed repo_ids this agent may operate on
can_handoff_to: [writer]             # the ONLY agents it may hand work to
```

Validation is strict: unknown keys reject, `can_handoff_to` must name registered
agents, `repos` must name registered repos, `permission_mode` must be one of the
four literals.

### Task envelope (immutable — the agent's entire input)

```json
{
  "task_id": "t_01J...",
  "trace_id": "tr_01J...",
  "parent_task_id": null,
  "agent": "writer",
  "repo": "app-backend",
  "intent": "draft_article",
  "payload": {"topic": "...", "tone": "neutral"},
  "artifacts": {"base_ref": "9f3a1c...", "inputs": ["brief.md"]},
  "idempotency_key": "writer:draft:tr_01J...",
  "priority": 5,
  "attempt": 1,
  "created_at": "2026-07-19T07:00:00Z",
  "schedule_id": "daily-news"
}
```

`repo: null` resolves to the internal scratch repo. `payload` is reserved for small
scalars and config; anything sizeable is a committed artifact.

### Result contract

The agent writes `result.json` into its worktree:

```json
{
  "status": "ok",
  "summary": "Drafted 1200-word article.",
  "outputs": ["draft.md"],
  "handoffs": [
    {
      "agent": "reviewer",
      "intent": "review_article",
      "payload": {"checklist": "editorial"},
      "artifacts": {"inputs": ["draft.md"]}
    }
  ],
  "metrics": {"words": 1200}
}
```

`status` is `ok | failed | needs_input`. The orchestrator sets each accepted
handoff's `base_ref` to this run's `output_ref`; input artifacts are inherited
through git, never copied.

### Run record

```
run_id, task_id, trace_id, agent, attempt,
status, exit_code, is_error,
started_at, ended_at, duration_ms,
claude_session_id, num_turns, total_cost_usd,
workspace_path, output_ref, stdout_log, stderr_log
```

### SQLite tables

`tasks`, `runs`, `handoffs`, `schedules`, `events`. `events` is the append-only
lifecycle log (enqueue, lease, run start/end, handoff, retry, dead-letter), keyed by
`task_id` / `trace_id` / `run_id`, and is the source for the dashboard and metrics.

## 6. Execution model — the `claude -p` contract

A run performs exactly these steps:

1. **Prepare worktree.** Under the repo lock:
   `git worktree add <home>/worktrees/<trace_id>/<task_id> -b run/<task_id> <base_ref>`
   off the bare mirror. Write `.harness/runs/<trace_id>/<task_id>/task.json`. Input
   artifacts are already present because they live in `base_ref`.

2. **Compose the prompt.** Deliberately tiny; state lives in files:

   ```
   You are operating as the "{agent}" agent.
   Your task is in .harness/runs/{trace_id}/{task_id}/task.json. Read it first.
   Input artifacts are in this working directory.
   Do the work. Write outputs where the task asks.
   When finished, write .harness/runs/{trace_id}/{task_id}/result.json following the
   result schema (status, summary, outputs, handoffs, metrics).
   Emit handoffs ONLY to: {allowed}.
   ```

3. **Spawn `claude -p`** with `cwd` = the worktree:

   ```
   claude -p "$PROMPT" \
     --append-system-prompt "$(cat <agent>.system.md)" \
     --allowedTools "$ALLOWED_TOOLS" \
     --permission-mode "$PERMISSION_MODE" \
     --model "$MODEL" --max-turns "$MAX_TURNS" \
     --add-dir "$WORKSPACE" --output-format json
   ```

   `--mcp-config` is added only when the agent declares one.

4. **Interpret.** Prefer `result.json`. If missing, fall back to the CLI JSON's
   `result` text and mark the run **degraded**. Non-zero exit or `is_error: true`
   takes the failure path.

5. **Commit, route, record.** Commit the worktree; the SHA is `output_ref`. Push
   `run/<task_id>` to the mirror. Persist the run record. Validate handoffs against
   `can_handoff_to` and enqueue accepted ones with `base_ref = output_ref`. Remove
   the worktree; keep the branch. On failure, retry with backoff or dead-letter.

### Statelessness is enforced, not documented

`LocalExecutor` never passes `--resume` or `--continue`. A test asserts the
constructed argv contains neither. This is the one constraint that silently rots
otherwise.

## 7. Merge and trace completion

A trace is complete when it has no pending or processing tasks and no in-flight
runs. On completion the harness merges only the trace's **leaf** `run/*` branches
into `integration_branch` — linear chains already contain their ancestors, so
merging leaves is sufficient and avoids redundant merges.

Fan-out with two or more leaves merges sequentially with the `ort` strategy. The
first conflict fails the **trace** to the DLQ with every branch left intact for
manual resolution. No integrator agent in the MVP.

## 8. Concurrency, failure handling, rate limits

- **Global semaphore** caps simultaneous `claude -p` processes at `max_concurrency`
  (default 3). Per-agent `concurrency` caps sit underneath it.
- **Repo lock** serialises only ref-mutating git ops (worktree add/remove, branch
  create, merge, push). The `claude -p` work itself stays fully parallel.
- **Visibility timeout** on leases. A worker that dies lets the lease expire; the
  task reappears and re-execution is safe because the retry builds a fresh worktree
  from the same immutable `base_ref`.
- **Idempotency.** `idempotency_key` deduplicates re-enqueues across the
  "handoff written but not yet enqueued" crash window.
- **Timeouts.** Per-agent `timeout_seconds`; the process is killed, the run marked
  failed, the task retried or dead-lettered.
- **Retries.** Exponential backoff to `max_attempts`, then the agent's DLQ with full
  context for manual replay.
- **Rate limits.** Throttle indicators in the CLI JSON trip a global pause gate. The
  dispatcher stops leasing, logs `paused: rate limited`, and probes with exponential
  backoff until it clears. In-flight runs finish normally.
- **Orphaned worktrees.** A periodic `git worktree prune` plus reconciliation
  against active leases cleans up after dead workers.

## 9. Observability

- **Structured JSON logs** (structlog) for every lifecycle event, keyed by
  `task_id` / `trace_id` / `run_id`.
- **Metrics**: queue depth per agent, run latency, success/failure rate, retry
  count, DLQ size, active concurrency, cost per agent and per trace summed from
  `total_cost_usd`.
- **Cost tracking doubles as rate-limit budgeting**, since subscription usage is the
  binding constraint.
- **Dashboard**: read-only FastAPI + HTMX — queues, recent runs, a trace tree view,
  per-run logs and workspace links. OTel and Prometheus are explicitly later.

## 10. Testing strategy

`FakeExecutor` is load-bearing, not a convenience. It returns a scripted
`result.json` without spawning anything, which lets the entire
queue → dispatch → git → merge → store path be exercised with **zero subscription
usage**. Real `claude -p` appears only in a handful of opt-in
`@pytest.mark.live` smoke tests.

Test layers:

- **Unit** — models, routing allow-list, retry backoff, result parsing, prompt
  composition, argv construction.
- **Integration** — FS queue against a real temp directory; git modules against real
  throwaway repos; dispatcher end-to-end with `FakeExecutor`.
- **Live** (opt-in) — one real `claude -p` run proving the contract holds against
  the actual CLI.

## 11. CLI surface

```
agentharness serve                     # dispatcher + scheduler + dashboard
agentharness submit <agent> <intent> [--repo R] [--payload JSON] [--input PATH]
agentharness agents list|show|validate
agentharness repos add|list|sync
agentharness queue list|peek|drain|replay
agentharness runs list|show <run_id>
agentharness trace show <trace_id>
agentharness schedule list|add|remove
agentharness gc [--days 30]
```

## 12. Delivery phases

Each phase ends with something runnable and tested.

1. **Foundations** — config, ids, models, agent registry, SQLite store. No execution.
2. **Git plane** — mirror, worktree, lock, merge, scratch repo. Tested against real
   throwaway repos.
3. **Queue** — FS backend with atomic rename, visibility timeout, DLQ.
4. **Runner** — executor ABC, `FakeExecutor`, `LocalExecutor`, prompt composition,
   result parsing, full run lifecycle.
5. **Dispatcher** — lease loop, semaphores, routing, retries, rate-limit gate, trace
   completion and merge. **First end-to-end run happens here.**
6. **Scheduler + CLI** — APScheduler, the full command surface, launchd plist.
7. **Observability + dashboard** — structlog events, metrics, FastAPI board.
8. **Proof workload** — `planner → implementer → reviewer` agent set exercised
   against a real repo.

## 13. Non-goals

- Not a general LLM-app framework. `claude -p` *is* the agent runtime; there is no
  model abstraction layer.
- Not a parallel-coding IDE tool. Headless backend platform only.
- Not an API gateway. Subscription CLI only.
- No fan-in/join, no human-in-the-loop approval, no containerised executor, no
  Azure/git queue backends, no Postgres, no OTel in this MVP. Each has a defined
  seam so it can be added without changing the task or result contracts.
