# agentharness

A stateless, CLI-driven multi-agent orchestration platform.

You declare agents in YAML. Each gets a durable input queue. Each invocation is
one `claude -p` subprocess that reads a task, does work inside its own git
worktree, commits, and hands work to the next agent. It is an actor model where
every actor step is a fresh CLI process.

Three constraints shape everything:

- **CLI-only** — Claude is reached exclusively by spawning `claude -p`. No API,
  no SDK.
- **Stateless agents** — no memory between invocations. State arrives with the
  task, as artifacts inherited through git.
- **Reactive and proactive** — agents drain queues; a cron scheduler injects
  work. Every task, run, cost, and handoff is recorded.

## Install

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/agentharness init
```

`init` creates `~/.agentharness/` (override with `AGENTHARNESS_HOME`) containing
the agent definitions, queues, bare repo mirrors, the SQLite run store, and an
internal scratch repo used by agents that have no target repo.

## Quick start

```sh
# Register the repo your agents will work on.
agentharness repos add app-backend git@github.com:you/app-backend.git

# Install the example planner -> implementer -> reviewer pipeline.
cp examples/agents/* ~/.agentharness/agents/
agentharness agents validate

# Submit work, then run the harness.
agentharness submit planner plan_feature --repo app-backend \
    --payload '{"request": "add rate limiting to the upload endpoint"}'
agentharness serve
```

`serve` runs the dispatcher, the scheduler, and a read-only dashboard on
<http://127.0.0.1:8787> in one process.

## How work flows

1. A producer — the CLI, a schedule, or another agent's handoff — enqueues a task.
2. The dispatcher leases it, respecting per-agent and global concurrency caps.
3. The runner adds a git worktree off the repo's bare mirror at the task's
   `base_ref`, writes `task.json`, and spawns one `claude -p` process in it.
4. The agent works and writes `result.json` declaring outputs and handoffs.
5. The runner commits the worktree. That commit SHA becomes the child task's
   `base_ref` — which is how artifacts are inherited rather than copied.
6. Handoffs are accepted only if the target is on the emitting agent's
   `can_handoff_to` allow-list. Agents cannot invent fan-out.
7. When nothing of a trace is still in flight, its leaf `run/*` branches merge
   into the repo's integration branch.

**The harness never writes to `main`.** Promotion from the integration branch is
a human PR.

## Defining an agent

```yaml
name: reviewer
description: Reviews an implementation and writes a verdict.
permission_mode: default
allowed_tools: [Read, Glob, Grep, Bash]
system_prompt_file: reviewer.system.md
max_turns: 30
timeout_seconds: 1200
concurrency: 1
retries:
  max_attempts: 2
  backoff: exponential
can_handoff_to: []          # terminal step
```

Validation is strict: unknown keys, unknown handoff targets, unknown repos, and
missing prompt files all fail at load rather than at run time.

## Operating

```sh
agentharness queue list                 # depth and dead letters per agent
agentharness queue dead reviewer        # inspect poison tasks
agentharness queue replay reviewer t_…  # requeue one after fixing its input
agentharness runs list                  # recent runs with duration and cost
agentharness trace show tr_…            # every run in one workflow
agentharness gc --days 30               # prune merged run/* branches
```

Failure handling is policy, not exception: timeouts kill the process, failures
retry with backoff, poison tasks land in the agent's dead-letter queue with full
context. A detected rate limit pauses dispatch globally and resumes on its own
after a backoff — an overnight run survives hitting the subscription ceiling.

## Running as a service

```sh
./deploy/install.sh --print   # render the launchd plist, change nothing
./deploy/install.sh           # install and start
launchctl kickstart -k gui/$(id -u)/com.agentharness   # restart
```

## Tests

```sh
.venv/bin/pytest -q          # 368 tests, zero subscription usage
.venv/bin/pytest -m live -v  # opt-in: spawns a real claude -p and costs usage
```

The default suite uses `FakeExecutor`, a scripted stand-in that never spawns a
process, so the entire queue → dispatch → git → merge path is exercised for free.
The `live` test exists to catch the one thing fakes cannot: the real CLI's flags
or JSON envelope changing. It skips cleanly if your `claude` login has expired.

## Not in this version

No fan-in/join, no human-in-the-loop approval gates, no containerised executor,
no Azure or git queue backends, no Postgres, no OpenTelemetry. Each has a defined
seam — the queue is an ABC, the executor is an ABC — so adding one does not
change the task or result contracts.
