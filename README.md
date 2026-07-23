# harness

Orchestration harness for multiple agents. The unit of work is a **task**; it moves
between queues according to a **workflow**, which is a small state machine with
explicit edges for each outcome.

Each step's work is done by a real agent (`claude -p`, or `--agent dummy` for
testing the pipeline itself), running inside a git worktree the harness manages
per task. The last step, landing, pushes the task's branch and opens a pull
request — the harness proposes, a human decides the merge. Tasks arrive either
by hand (`harness submit`) or ingested from GitHub issues; an operator board
shows every task's state, its artifacts, its live stage output while a step is
running, and a restart control for anything that failed.

## Installation

The harness installs as a [uv](https://docs.astral.sh/uv/) tool — no clone, no
virtualenv to manage:

```sh
uv tool install git+https://github.com/onpaj/harness_v2.git
```

That puts a `harness` command on your `PATH` (uv's shim in `~/.local/bin`).
Verify it:

```sh
harness --version
```

If you don't have uv yet:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Prerequisites

- Python 3.11+ (uv will fetch one if you have none)
- `git` >= 2.38, for worktree support
- the [`claude` CLI](https://claude.ai/code), installed and **authenticated** —
  every agent step shells out to it, so an expired login fails every task
- a GitHub token with `repo` scope, if you want issues ingested; `gh auth login`
  is enough (see [Running it as a service](#running-it-as-a-service))

### First run

```sh
harness init --root ~/harness-root
```

That writes the workflow, the default agent personas, and an empty `repos.json`.
Map each repo name to its path on this machine before running anything real:

```jsonc
// ~/harness-root/repos.json
{
  "my-app": "/Users/you/code/my-app"
}
```

The name is what `harness submit --repo <name>` takes; the harness derives the
per-task worktree path itself. Note that a task branches from whatever the
registered clone currently has checked out, so point it at a clone that stays
on your default branch.

### Updating

```sh
harness update
```

Runs `uv tool upgrade harness` and reports the version it installed. A running
service keeps the old code until you restart it. To upgrade **and** restart in
one step:

```sh
harness update --restart                 # restart now (may interrupt a stage)
harness update --restart --only-if-idle  # restart only when no stage is running
```

To keep the box current on its own, schedule the idle-gated form a few times a
day (macOS launchd):

```sh
harness service autoupdate               # runs at 02:00, 08:00, 14:00, 20:00
harness service autoupdate --hours 3,15  # custom times
harness service autoupdate --remove      # stop auto-updating
```

Each firing upgrades, then restarts the service **only if no stage is mid-run** —
a firing that lands while a task is being worked skips the restart and leaves it
for the next slot, so an update never kills a running agent. Output goes to
`<root>/logs/autoupdate.log`.

Versions are cut automatically: every push to `main` runs the test suite, and
[python-semantic-release](https://python-semantic-release.readthedocs.io/)
derives the next version from the [conventional
commits](https://www.conventionalcommits.org/) since the last tag (`feat:` →
minor, `fix:`/`perf:` → patch, `BREAKING CHANGE:` → major), tags it and cuts a
GitHub release. `harness --version` reports both the version and the exact
commit it was built from.

To pin instead of tracking `main`:

```sh
uv tool install git+https://github.com/onpaj/harness_v2.git@v0.2.0
```

### Contributing

Commit messages must follow conventional commits — the release workflow reads
them to decide the next version. `feat:` and `fix:` are what move it; `docs:`,
`chore:`, `test:`, `refactor:` and `ci:` appear in the notes without cutting a
release on their own.

## Running it as a service

`harness run` in a terminal dies with the terminal. To keep the loop supervised
and bring it back at login (macOS launchd):

```sh
harness service install --root ~/harness-root
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

The LaunchAgent points at uv's shim rather than at a virtualenv, so
`harness update` does not invalidate it; restart the service to pick the new
version up.

### The claude token (required for the service)

Every agent step shells out to `claude`, and **`claude` cannot read the macOS
login keychain when it runs under launchd** — an interactive `claude` login is
invisible to the background service, so every task fails with "Not logged in".
The service therefore needs a token in its environment instead:

```sh
claude setup-token                 # interactive, once — creates a long-lived token
```

Put the value in `<root>/secrets.env` (created 0600 by `harness service
install`):

```sh
# ~/harness-root/secrets.env
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

then restart the service:

```sh
launchctl kickstart -k gui/$(id -u)/com.harness
```

`CLAUDE_CODE_OAUTH_TOKEN` makes `claude` skip the keychain entirely, which is
what a background agent needs. Running `harness run` yourself in a terminal does
*not* need this — there the keychain is reachable and your normal login works.

Logs land in `<root>/logs/harness.log` and `<root>/logs/harness.error.log`.

### Autoupdating the service

`harness update` above is manual. To have it run on a schedule — and, when a
real update landed, restart the run-loop service so the new code is actually
live — install a second, independent LaunchAgent:

```sh
harness service autoupdate install --root ~/harness-root --every 15m
```

`--every` accepts whole minutes, hours or days (`15m`, `2h`, `1d`) — there is
no hourly floor, `1m` is a valid schedule. Each firing runs `harness update`
and, only when the reported version actually changed, kickstarts the main
service (`com.harness` by default; pass `--service-label` if you installed the
run-loop service under a different `--label`). A no-op upgrade never restarts
anything. Installing also runs the update once immediately (the LaunchAgent's
`RunAtLoad`), so don't be surprised by an entry in the log the moment
`install` returns.

```sh
harness service autoupdate status      # loaded? configured interval?
harness service autoupdate uninstall   # stop it and remove the agent
```

`autoupdate uninstall` only touches the autoupdate LaunchAgent — the run-loop
service it restarts is untouched. Logs land in
`<root>/logs/harness-autoupdate.log` and `<root>/logs/harness-autoupdate.error.log`,
separate from the run-loop's own log files. This needs no `GITHUB_TOKEN` — it
only shells out to `uv` and, on a real change, `launchctl kickstart`.

## Developing

Working on the harness itself needs a clone and an editable install:

```sh
git clone https://github.com/onpaj/harness_v2.git
cd harness_v2
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

## Quick start

```sh
harness init --root /tmp/harness-demo
harness submit --root /tmp/harness-demo --repo app-backend \
    --data '{"request": "add rate limiting"}'
harness run --root /tmp/harness-demo --delay 0.5 --request-changes-at review
```

### Running without an agent

Every step shells out to `claude`, so an unavailable or unauthenticated CLI
fails every task. To exercise the pipeline itself — worktree, commits, push,
pull request — without it:

```sh
harness run --root ~/harness-root --agent dummy
```

The step behaviors become stubs that write an artifact and commit; everything
around them stays real, including the PR that `land` opens. Pair it with
`--forge fake` if you want no GitHub calls at all.

## Documentation

The full architecture — an animated ports-and-adapters explorer you can drill
into, backed by the ADRs under `docs/adr/` — is published at
[onpaj.github.io/harness_v2](https://onpaj.github.io/harness_v2/). Rebuild it
locally with `python scripts/build_docs.py --out site` and open `site/index.html`.

## Board

Alongside the orchestration loop, `harness run` serves a board at
`http://127.0.0.1:8420/`. The columns are the workflow steps plus `done` and
`failed` (and `healed` when [self-healing](#self-healing-the-failed-queue) is
enabled), the cards are tasks, and a click shows metadata, history, the
artifacts each step wrote, and — while a step is actively running — a live tail
of the agent's output, streamed over SSE. A task in `failed/` gets a **Restart**
control, which resets it and re-inboxes it for the dispatcher to route again.
The board itself updates over SSE too.

`--api-port 0` turns the board off.

The board reads exclusively through the `BoardView`/`ArtifactView`/
`StageOutputView` ports and writes only through `TaskControl`. That the tasks
are JSON files and the queues directories, it does not know — and must not.

## GitHub issue ingestion

`harness run` watches every repository registered in `repos.json` whose git
`origin` resolves to a GitHub slug, and pulls in issues labeled for pickup
(default `harness:todo`, override with `--github-label`) as new tasks. A repo
with no GitHub origin is skipped with a warning — there is no per-repo opt-out
flag, ingestion is automatic for anything registered with a GitHub remote.

Each ingested issue moves through a managed label lifecycle as its task
progresses: `harness:todo` (selected) → `harness:queued` (claimed) → a
per-step label from the workflow (e.g. `harness:in-progress` while in
`development`, `harness:in-review` while in `review`) → `harness:pr-open` on
success or `harness:failed` on failure. Foreign labels on the same issue (`bug`,
`priority`, ...) are left untouched — only labels in this managed set are ever
added or removed.

`--github-workflow` picks which workflow a newly ingested issue starts on
(default `default`); `--source-poll` sets how often GitHub is polled (default
30s, deliberately coarser than `--poll` to respect rate limits). Without a
`GITHUB_TOKEN` (see [Running it as a service](#running-it-as-a-service)), GitHub
ingestion is simply inactive — `harness submit` keeps working regardless.

## Autoresolving merge conflicts

Earlier versions of `harness run` watched every registered repo's open PRs and
queued a resolver task for each conflicted ("dirty") one by default
(`--watch-mergeability`). That flag is gone — autoresolution is now opt-in,
authored the same way any other scheduled process is: drop a file under
`processes/`.

```json
// ~/harness-root/processes/autoresolver.json
{
  "trigger": {"interval": "60s"},
  "action": {"check": "github-conflicts", "params": {"head_prefix": "harness/"}},
  "target": {"workflow": "resolver"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

This is not written by `harness init` — a `github-conflicts` process without a
`GITHUB_TOKEN` fails the run fast (`ProcessValidationError`), so seeding it
unconditionally would break every token-less `harness run`. Copy the block
above once a token is configured (see
[Running it as a service](#running-it-as-a-service)); `harness run` then
auto-updates a `behind` PR server-side and queues exactly one resolve→land task
per `dirty` PR, deduped per conflicted head commit.

## Self-healing the failed queue

By default a task that fails comes to rest in `failed/` and stays there — an
operator has to notice, read its history, and decide whether the harness itself
was at fault. `--heal-repo <owner/repo>` turns `failed/` into a queue that
drains, by assigning a **healer** agent to it:

```sh
harness run --root ~/harness-root --agent claude --heal-repo onpaj/harness_v2
```

The healer is **not a workflow** — not a step, and not a workflow of its own. It
is a loop assigned to the `failed/` queue, the same way each workflow step has an
agent behind it (so there is no `healer` workflow file to find). It claims one
failed task at a time, reads a **failure report** built from that task's reason
and history — no worktree, no git — and decides whether the failure points at a
fixable bug in the harness itself (a driver contract, a wiring gap, a missing
workflow edge) as opposed to an external or expected failure (a flaky network, a
task whose request was simply wrong). When it judges it a harness bug, the healer
opens a diagnostic **issue** on the repo you named, with a diagnosis and a
concrete proposed change. Either way the task then settles onto a new terminal
`healed/` queue and leaves `failed/`.

The healer only ever opens an *issue* — never a PR, never a new task — so nothing
it does can re-enter `failed/` and loop; a failed heal (agent error, or the issue
can't be opened) still settles to `healed/`, with the reason in the task's
history. The issue is idempotent per failed task (a hidden marker in its body),
so a restart mid-heal never files a second one.

`--heal-repo` needs `--agent claude` — the healer is a claude agent. Its persona
lives in `agents/healer.json`, written by `harness init` alongside the step
personas (data, not code). With a `GITHUB_TOKEN` present the issue is opened on
GitHub; offline it falls back to an in-memory tracker so the loop still runs
harmlessly. Without `--heal-repo`, `failed/` stays a dead-end terminal exactly as
before.

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

See `docs/adr/` for the *why* behind each of these — one Architecture Decision
Record per load-bearing rule.
