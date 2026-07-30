# harness

Orchestration harness for multiple agents. The unit of work is a **task**; it moves
between queues according to a **workflow**, which is a small state machine with
explicit edges for each outcome.

Each step's work is done by a real agent (`claude -p`, or `--agent dummy` for
testing the pipeline itself), running inside a git worktree the harness manages
per task. The last step, landing, pushes the task's branch and opens a pull
request — the harness proposes, a human decides the merge, unless you hand that
decision over too (see [Automatic merging](#automatic-merging)). Tasks arrive either
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

### Settings that live in `secrets.env`

`secrets.env` is not only for secrets: the generated wrapper sources it under
`set -a`, so every line in it becomes an environment variable for the service.
That is the only reliable way to configure the service, because launchd hands it
almost no environment — an `export` in your own shell has no effect on it, and
editing the generated `harness-run.sh` is undone by the next `service install`
(the autoupdate path runs one).

| Variable | What it does |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | the claude token above — required for agent steps |
| `GITHUB_TOKEN` | issue ingestion, PRs, merges; falls back to `gh auth token` |
| `SLACK_WEBHOOK_URL` | enables a Process's `slack` sink |
| `HARNESS_RETENTION_DAYS` | how long a settled task stays on the board (default `2`) |

**`HARNESS_RETENTION_DAYS`** is the retention window for terminal tasks: a
`done` or `healed` task is moved to `archived/` — off every board column, still
`GET`-able by id — once it has been settled longer than this many days, so the
board stops growing without bound. Failures are exempt: a task in `failed/` stays
put however old it is, because nothing is coming to fix it and it must remain
visible and restartable. There is no "off" value — `0` is the *most* aggressive
setting (archive everything settled on the next sweep), so to effectively disable
the sweep use a very large window, e.g. `36500`. An unparseable or negative value
warns to the error log and falls back to `2` rather than failing the run.

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

This one is not written by `harness init` — copy the block above yourself, once
a token is configured (see
[Running it as a service](#running-it-as-a-service)). `harness run` then
auto-updates a `behind` PR server-side and queues exactly one resolve→land task
per `dirty` PR, deduped per conflicted head commit. Without a token the process
is skipped with a warning rather than failing the run.

## Automatic merging

The harness normally stops at the merge button: it proposes a PR and a human
decides. The **automerge Process** (ADR-0023) is where you can hand that last
step over — an agent reviews the PR's real diff and, if it is confident enough,
the harness merges it.

`harness init` seeds all three pieces, so it is already running on a fresh root:

```json
// ~/harness-root/processes/automerge.json
{
  "trigger": {"interval": "5m"},
  "action": {"check": "github-mergeable", "params": {"head_prefix": "harness/"}},
  "target": {"workflow": "automerge"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

**Running is not merging.** The seeded `workflows/automerge.json` binds its
`merge` step with `dry_run: true`, so out of the box the harness reviews every
candidate PR and records on the board exactly what it *would* have merged, with
the confidence. Watch a few of those decisions on your own PRs, then flip one
field to arm it:

```jsonc
// ~/harness-root/workflows/automerge.json
"finishers": {
  "merge": {
    "kind": "merge-pr",
    "from_step": "merge-review",
    "min_confidence": 0.8,   // the bar; raise it to be stricter
    "method": "squash",      // merge | squash | rebase
    "dry_run": false         // ← this is the switch
  }
}
```

One Process covers **every** repository: the check iterates `repos.json`, so
adding a repo puts it under review automatically and a non-GitHub repo is
skipped. Without a `GITHUB_TOKEN` the process is skipped with a warning, never
fatally.

### What gets reviewed, and what merges

A PR is a candidate only when **all four** hold:

| Gate | Meaning |
|---|---|
| GitHub state `clean` | GitHub's own verdict: merges without conflict, every *required* check green, every *required* review present |
| not a draft | draft PRs are never candidates |
| no `harness:no-automerge` label | the per-PR veto — anyone can apply it, no config change, no restart |
| head matches `head_prefix` | default `harness/`, so the harness only proposes to merge its own work |

Each candidate becomes one task keyed `slug:pr:head_sha`, so a re-pushed PR is
re-reviewed from scratch and an unchanged one is not reviewed twice. The
`merge-review` persona runs in a worktree checked out on the PR's *own* branch
— it reads the real diff and the surrounding source, not an API summary — and
returns `approve`/`reject` plus a confidence.

Only `approve` reaches the `merge` step, and the agent does not decide there:
the `merge-pr` finisher compares the confidence against `min_confidence` **from
the workflow file**, and the merge is pinned to the exact `head_sha` the
reviewer read. A persona that learns to write `"confidence": 1.0` on everything
still cannot lower the bar, and code no agent reviewed cannot be merged. Every
other path refuses — a moved head, a below-bar confidence, a red check, a
protection rule — and refusing is cheap: the next scan reviews the new head.

> **The gate is only as strong as your branch protection.** `clean` means
> "required checks green, required reviews present" — so on a repo with *no*
> protection rules it means only "no conflict and nothing failing", and the
> persona's confidence becomes the entire decision. Protect the branch first,
> then set `dry_run: false`.

## Self-healing the failed queue

By default a task that fails comes to rest in `failed/` and stays there — an
operator has to notice, read its history, and decide whether the harness itself
was at fault. `harness init` seeds `processes/autoheal.json`, an **autoheal
Process** (ADR-0018) that drains `failed/` — but with an empty
`action.params.repository`, so it fires heal tasks with nowhere to file an
issue until you point it at one:

```json
{
  "trigger": {"interval": "30s"},
  "action": {"check": "failed-tasks", "params": {"repository": "harness_v2"}},
  "target": {"workflow": "heal"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

Edit `processes/autoheal.json` directly (or through the dashboard's process
editor) and set `action.params.repository` to a repo *name* — a key in
`repos.json` (e.g. `"harness_v2"`), not an `owner/repo` slug like
`"onpaj/harness_v2"` — then `harness run --agent claude`
(the heal step is a claude agent) picks it up on the next tick. This is
config, not a CLI flag: the launchd service self-heals with no run flag
needed, once the file names a repo.

Self-healing is an ordinary Process, not bespoke machinery. The
`failed-tasks` action drains `failed/`: on each tick it claims one failed
task, settles the original to a new terminal `healed/` queue, and fires a
fresh task through the three-step `heal` workflow (`workflows/heal.json`:
`heal` → `dedup` → `file-issue`). The `heal` step reads a **failure report**
built from that task's reason and history, inside a worktree — a scratch one
until `action.params.repository` names a repo, then that repo's own, the same
way any ordinary agent step gets one — and decides whether the failure points
at a fixable bug in the harness itself (a driver
contract, a wiring gap, a missing workflow edge) as opposed to an external or
expected failure (a flaky network, a task whose request was simply wrong).
When it judges it a harness bug, it drafts a diagnosis and a concrete proposed
change and returns `file`, routing to `dedup`; otherwise it returns `skip` and
nothing more happens. `dedup` reads the target repo's open issues and returns
`unique` (routes to `file-issue`) or `duplicate` (settles silently — no
issue). Only on the `unique` path does the `file-issue` step's **`open-issue`
finisher** open the diagnostic **issue** on the repo named in
`action.params.repository`.

The heal step's persona only ever drafts an *issue* — never a PR, never a new
task. Recursion is guarded by a marker: the check stamps `data.heal` on the heal
task it produces, and a heal task that itself fails is board-visible in `failed/`
once before the check retires it to `healed/` without re-observing it, so nothing
loops. The issue is idempotent per `(repo, scope_label, marker)` — a hidden
marker in its body, scoped to the *heal* task and the draft's title
(`marker_for(task.id, draft.title)`), not the original failed task (whose id
survives as `data.heal.of` and in the heal task's `dedup_key`). That protects
a re-run of the same heal
task from filing the same draft twice; it says nothing about the failure that
triggered it.

The `heal`/`dedup` personas live in `agents/heal.json`/`agents/dedup.json`,
and `workflows/heal.json` is written by `harness init` alongside the step
personas (data, not code) — self-healing is seeded live on every root
(`processes/autoheal.json` runs on a 30s interval from the first `harness
run`) and files nothing until `action.params.repository` names a registered
repo. With a `GITHUB_TOKEN` present the
issue is opened on GitHub; offline it falls back to an in-memory tracker so
the finisher runs harmlessly. Until `action.params.repository` is set,
`failed/` still drains into `healed/` on each tick and `heal`/`dedup` still
run (a repo-less task gets a scratch worktree, not a failure) — only on the
`unique` path does `file-issue`'s `open-issue` finisher hit a repo-less task
and fail it, with a message saying exactly that ("set the process's
params.repository"); that failed heal task is retired to `healed/` on the
next tick without re-observing it (invariant 25), so it doesn't loop.

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
  "name": "development",
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
