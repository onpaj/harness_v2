# harness

Orchestration harness for multiple agents. The unit of work is a **task**; it moves
between queues according to a **workflow**, which is a small state machine with
explicit edges for each outcome.

Phase 1 is a POC of the whole loop: a task flows through the workflow from `start`
to `end`, but the work is stood in for by a dummy behavior for now. Real agents,
persistent storage, and git arrive in later phases.

## Installation

The quickest way from a fresh clone to a runnable harness is the installer. It
checks prerequisites (Python 3.11+, git; it warns if the `claude` CLI is
missing, which real runs need), creates a virtualenv, installs the package with
its dev extras, runs `harness init`, and walks you through populating
`repos.json`:

```sh
./install.sh
```

It is safe to re-run: an existing venv is reused, `harness init` never
overwrites existing files, and the `repos.json` step only adds entries. Useful
flags: `--root DIR` (harness home, mirrors `harness --root`; default
`$HARNESS_HOME` or `~/.harness`), `--workflow NAME`, and `--yes` for a
non-interactive run that skips the `repos.json` wizard. See `./install.sh --help`.

### Running it as a service

`harness run` in a terminal dies with the terminal. To keep the loop supervised
and bring it back at login, add `--service` (macOS launchd):

```sh
./install.sh --service --root ~/harness-root
```

That generates a wrapper and a LaunchAgent, then starts it. Afterwards:

```sh
harness service status      # loaded? pid? last exit code?
harness service uninstall   # stop it and remove the agent
```

The service needs a GitHub token to ingest issues. It does **not** store one:
the generated wrapper takes `GITHUB_TOKEN` if it is already set, and otherwise
asks `gh auth token` for the one in your keyring — so `gh auth login` is the
only setup. Without a token the harness still runs; it just stops pulling
issues, and `harness submit` keeps working.

Logs land in `<root>/logs/harness.log` and `<root>/logs/harness.error.log`.

Prefer to do it by hand? The installer is a thin wrapper over:

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/harness init
```

## Quick start

```sh
harness init --root /tmp/harness-demo
harness submit --root /tmp/harness-demo --repo app-backend \
    --data '{"request": "add rate limiting"}'
harness run --root /tmp/harness-demo --delay 0.5 --request-changes-at review
```

## Board

Alongside the orchestration loop, `harness run` serves a read-only board at
`http://127.0.0.1:8420/`. The columns are the workflow steps plus `done` and
`failed`, the cards are tasks, and a click shows metadata and history. The board
updates itself over SSE.

`--api-port 0` turns the board off.

The board reads exclusively through the `BoardView` port. That the tasks are JSON
files and the queues directories, it does not know — and must not.

## How work flows

```
tasks/ ──dispatcher──> queues/<step>/ ──consumer──> tasks/ ──dispatcher──> …
                                                                    │
                                                              done/ or failed/
```

1. The dispatcher takes a task from `tasks/`, loads the workflow by
   `workflowTemplate`, and finds the target step from the `(status, lastOutcome)`
   pair.
2. It overwrites `status`, appends a line to `history`, and moves the task into
   `queues/<step>/`.
3. The consumer over that queue hands the task to `ConsumerBehavior`, gets back an
   outcome (`done` or `request_changes`), writes it, and returns the task to
   `tasks/`.
4. Once an edge points at `end`, the task ends up in `done/`. Anything unroutable
   ends up in `failed/` with the reason in its history.

## Workflow

```json
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan", "on": "done", "to": "design"},
    {"from": "review", "on": "done", "to": "end"},
    {"from": "review", "on": "request_changes", "to": "development"}
  ]
}
```

Backward edges are explicit and need not be symmetric. Retrying the same step is
expressed as `to == from`.

## Architecture

Every moving part sits behind a port and is swapped by swapping the driver:

| Port | Phase 1 | Later |
|---|---|---|
| `TaskQueue` | directory of JSON files | storage queue |
| `EnqueueStrategy` | FIFO by `created` | priority, fair-share |
| `WorkflowRepository` | `workflows/<name>.json` | DB, API |
| `ConsumerBehavior` | sleep → `done` | real agent |
| `EventSink` | lines on stdout | OTel |

Decision-making is split into three non-overlapping roles: `ConsumerBehavior` says
*what happened*, the dispatcher *where it goes next*, and the consumer just
delivers.
