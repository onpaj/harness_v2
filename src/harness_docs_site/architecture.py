"""The curated architecture model the Explorer renders — the single source of
truth for the port catalogue and every port's drill-down.

The model is port-first: the page shows just the harness's ports, grouped by
area, and each port expands into the drivers available behind it, each with
its own documentation. Hand-authored (not derived from the live module graph)
so it stays legible; `validate` keeps it honest against the real `docs/adr/`
files and `src/` tree, and runs in the test suite so a rename that breaks the
mapping fails CI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Driver:
    id: str
    name: str
    tagline: str
    description: str
    sources: tuple[str, ...]  # repo-relative source paths
    adrs: tuple[str, ...] = ()  # ADR slugs: docs/adr/<slug>.md


@dataclass(frozen=True)
class Port:
    id: str
    name: str
    group: str  # one of ArchitectureModel.groups
    tagline: str
    description: str
    sources: tuple[str, ...]  # repo-relative source paths
    drivers: tuple[Driver, ...]
    adrs: tuple[str, ...] = ()  # ADR slugs: docs/adr/<slug>.md


@dataclass(frozen=True)
class ArchitectureModel:
    groups: tuple[str, ...]
    ports: tuple[Port, ...]


def validate(model: ArchitectureModel, repo_root: Path) -> None:
    """Raise ValueError if the model is internally inconsistent or has drifted
    from the real docs/source tree. Silence means coherent."""
    repo_root = Path(repo_root)

    group_list = list(model.groups)
    if len(group_list) != len(set(group_list)):
        raise ValueError("duplicate group name(s)")

    port_ids = [p.id for p in model.ports]
    if len(port_ids) != len(set(port_ids)):
        dupes = sorted({i for i in port_ids if port_ids.count(i) > 1})
        raise ValueError(f"duplicate port id(s): {dupes}")

    used_groups: set[str] = set()

    def _check_refs(owner: str, adrs: tuple[str, ...], sources: tuple[str, ...]) -> None:
        for slug in adrs:
            if not (repo_root / "docs" / "adr" / f"{slug}.md").is_file():
                raise ValueError(f"{owner} cites missing ADR {slug!r}")
        for src in sources:
            if not (repo_root / src).exists():
                raise ValueError(f"{owner} cites missing source path {src!r}")

    for port in model.ports:
        if port.group not in model.groups:
            raise ValueError(f"port {port.id!r} has unknown group {port.group!r}")
        used_groups.add(port.group)
        if not port.drivers:
            raise ValueError(f"port {port.id!r} lists no drivers")
        _check_refs(f"port {port.id!r}", port.adrs, port.sources)

        driver_ids = [d.id for d in port.drivers]
        if len(driver_ids) != len(set(driver_ids)):
            dupes = sorted({i for i in driver_ids if driver_ids.count(i) > 1})
            raise ValueError(f"port {port.id!r} has duplicate driver id(s): {dupes}")
        for driver in port.drivers:
            _check_refs(
                f"driver {driver.id!r} of port {port.id!r}",
                driver.adrs,
                driver.sources,
            )

    unused = sorted(set(model.groups) - used_groups)
    if unused:
        raise ValueError(f"group(s) with no port: {unused}")


def model_to_dict(model: ArchitectureModel) -> dict:
    """Plain, JSON-serializable dict for embedding in the page."""
    def _convert(obj):
        if isinstance(obj, tuple):
            return [_convert(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        else:
            return obj

    return _convert(asdict(model))


MODEL = ArchitectureModel(
    groups=(
        "Work intake",
        "Orchestration",
        "Workspace & delivery",
        "Reconciliation",
        "Operator surface",
    ),
    ports=(
        # ---- Work intake -------------------------------------------------
        Port(
            id="task-source",
            name="TaskSource",
            group="Work intake",
            tagline="The one seam to the outside world.",
            description=(
                "Every task enters (and every state change leaves) through this "
                "single port: poll() brings new tasks into the inbox, "
                "report_progress()/finish() project state back outward. A new "
                "origin or destination is a new driver, never a change to the "
                "loop; a task's origin travels with it in data.source."
            ),
            adrs=("0010-tasksource-single-external-port",),
            sources=("src/harness/ports/source.py", "src/harness/source_poller.py"),
            drivers=(
                Driver(
                    id="github-source",
                    name="GithubTaskSource",
                    tagline="Labelled GitHub issues become tasks; labels track state.",
                    description=(
                        "Polls the registered repositories for issues carrying the "
                        "harness label and claims each by flipping the label — "
                        "GitHub's twin of the queue's atomic rename, so ingestion "
                        "is at-most-once across restarts. As the task moves, the "
                        "issue is relabelled to mirror its state."
                    ),
                    sources=("src/harness/drivers/github_source.py",),
                    adrs=("0010-tasksource-single-external-port",),
                ),
                Driver(
                    id="github-label-reflector",
                    name="GithubLabelReflector",
                    tagline="The outbound label half alone — no ingestion.",
                    description=(
                        "The standalone reflection half of the GitHub adapter: "
                        "poll() is always empty, only the labels move. Registered "
                        "when ingestion is delegated elsewhere (e.g. to a "
                        "Process's github-issues action) so a task's issue keeps "
                        "tracking its state anyway."
                    ),
                    sources=("src/harness/drivers/github_source.py",),
                ),
                Driver(
                    id="scheduled-trigger",
                    name="ScheduledTrigger",
                    tagline="Fires work on a clock — interval × check × target.",
                    description=(
                        "A TaskSource that produces work instead of ingesting it: "
                        "poll() gates on the interval bucket and, when the bucket "
                        "rolls over, runs its Check and hands the resulting tasks "
                        "to the inbox. Compiled from triggers/*.json and "
                        "processes/*.json alike; dedup is bucket- or state-keyed, "
                        "so a fire survives a restart at most once per interval. "
                        "It never places a task on a queue itself — the "
                        "dispatcher does."
                    ),
                    sources=(
                        "src/harness/drivers/scheduled_trigger.py",
                        "src/harness/drivers/fs_triggers.py",
                        "src/harness/drivers/fs_processes.py",
                    ),
                    adrs=(
                        "0014-triggers-produce-tasks-not-placements",
                        "0015-process-authoring-aggregate",
                    ),
                ),
                Driver(
                    id="mergeability-watcher",
                    name="GithubMergeabilityWatcher",
                    tagline="A dirty harness PR becomes a resolver task.",
                    description=(
                        "Watches harness-owned pull requests: a PR merely behind "
                        "its base is updated server-side (a side effect, no "
                        "task), while a conflicted one is queued as a resolver "
                        "task on the same branch, deduped per head commit."
                    ),
                    sources=("src/harness/drivers/mergeability_watcher.py",),
                ),
                Driver(
                    id="slack-sink",
                    name="SlackWebhookSink",
                    tagline="Outbound only: posts task progress to Slack.",
                    description=(
                        "The first Process sink — matched on the destination "
                        "(data.sink.kind), never the origin, so work can enter "
                        "from GitHub and be reported to Slack. Stateless: each "
                        "report posts a fresh webhook message. Wired only when "
                        "SLACK_WEBHOOK_URL is set; the URL never enters a JSON "
                        "file."
                    ),
                    sources=("src/harness/drivers/slack_sink.py",),
                    adrs=("0015-process-authoring-aggregate",),
                ),
                Driver(
                    id="memory-task-source",
                    name="MemoryTaskSource",
                    tagline="The in-memory double the tests drive.",
                    description=(
                        "Holds tasks in a plain list and records every outward "
                        "report, so poller and reflection tests run with no "
                        "network and no disk."
                    ),
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="check",
            name="Check",
            group="Work intake",
            tagline="A trigger's condition: evaluate() → observations.",
            description=(
                "The condition half of a trigger or Process action — the schedule "
                "is data, the condition is code. evaluate() looks at the world "
                "and returns observations; each becomes one task in the inbox. A "
                "new kind of action is a small Check class plus one registry "
                "entry, named from a trigger or process file by string."
            ),
            adrs=(
                "0014-triggers-produce-tasks-not-placements",
                "0015-process-authoring-aggregate",
            ),
            sources=("src/harness/ports/triggers.py",),
            drivers=(
                Driver(
                    id="always-check",
                    name="AlwaysCheck",
                    tagline="Fires one observation every evaluation.",
                    description=(
                        "The unconditional check behind pure cron-style triggers: "
                        "every interval, one task."
                    ),
                    sources=("src/harness/drivers/checks.py",),
                ),
                Driver(
                    id="disk-threshold-check",
                    name="DiskThresholdCheck",
                    tagline="Observes when free disk drops below a threshold.",
                    description=(
                        "A condition-driven built-in: quiet while disk is fine, "
                        "one observation when the threshold is crossed."
                    ),
                    sources=("src/harness/drivers/checks.py",),
                ),
                Driver(
                    id="file-glob-check",
                    name="FileGlobCheck (fs-files)",
                    tagline="One observation per file matching a glob.",
                    description=(
                        "Scans a glob pattern and emits one observation per "
                        "matching file — a drop-folder as a work source."
                    ),
                    sources=("src/harness/drivers/checks.py",),
                ),
                Driver(
                    id="command-check",
                    name="CommandCheck (command)",
                    tagline="One observation per non-empty stdout line.",
                    description=(
                        "The data-only escape hatch: an operator wires a bespoke "
                        "action as a shell command, no Python at all. Each "
                        "non-empty line the command prints becomes one "
                        "observation."
                    ),
                    sources=("src/harness/drivers/checks.py",),
                ),
                Driver(
                    id="github-issues-check",
                    name="GithubIssuesCheck (github-issues)",
                    tagline="The inbound GitHub-issue scan as a Process action.",
                    description=(
                        "Lists labelled issues across the repository registry, "
                        "claims each via the label swap, and emits one "
                        "provenance-stamped observation per issue — GitHub "
                        "ingestion re-expressed as an action a Process can name."
                    ),
                    sources=("src/harness/drivers/github_issues_check.py",),
                    adrs=("0015-process-authoring-aggregate",),
                ),
                Driver(
                    id="github-conflicts-check",
                    name="GithubConflictsCheck (github-conflicts)",
                    tagline="Conflict detection as a Process action.",
                    description=(
                        "Lists harness-authored open PRs across the registry; a "
                        "PR merely behind its base is updated server-side, a "
                        "conflicted one becomes an observation for the resolver "
                        "workflow, keyed per head commit."
                    ),
                    sources=("src/harness/drivers/github_conflicts_check.py",),
                ),
            ),
        ),
        # ---- Orchestration ----------------------------------------------
        Port(
            id="task-queue",
            name="TaskQueue",
            group="Orchestration",
            tagline="The inbox, every step, and the terminals — one port.",
            description=(
                "Every place a task can rest is an instance of the same port: "
                "the inbox, each workflow step, and the terminal queues (done, "
                "failed, healed, archived) that nobody consumes. claim() is "
                "atomic, so two workers can never take the same task."
            ),
            adrs=("0003-atomic-queue-claim-by-rename",),
            sources=("src/harness/ports/queue.py",),
            drivers=(
                Driver(
                    id="fs-queue",
                    name="FilesystemTaskQueue",
                    tagline="Directories; claimed atomically by rename.",
                    description=(
                        "Each queue is a directory and a task is a JSON file; "
                        "claim() renames it into a private .processing/ dir — an "
                        "atomic, lock-free operation the filesystem guarantees. "
                        "After a crash, recovery returns .processing/ files to "
                        "the queue they sit under."
                    ),
                    sources=("src/harness/drivers/fs_queue.py",),
                    adrs=("0003-atomic-queue-claim-by-rename",),
                ),
                Driver(
                    id="memory-queue",
                    name="MemoryTaskQueue",
                    tagline="The in-memory double the tests drive.",
                    description=(
                        "The same contract over plain dicts, so orchestration "
                        "tests run with no disk and no real waiting."
                    ),
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="workflow-repository",
            name="WorkflowRepository",
            group="Orchestration",
            tagline="Serves the state machines the router walks.",
            description=(
                "A workflow is data: a start step, (from, on-outcome, to) "
                "transitions ending at the reserved END node, per-step "
                "concurrency (maxParallel) and the finisher binding. This port "
                "serves them by name; the pure router interprets them."
            ),
            adrs=("0004-pure-router", "0016-finisher-as-data"),
            sources=("src/harness/ports/workflows.py", "src/harness/router.py"),
            drivers=(
                Driver(
                    id="fs-workflows",
                    name="FilesystemWorkflowRepository",
                    tagline="workflows/*.json, validated fast at load.",
                    description=(
                        "Reads workflow files and rejects a malformed one at "
                        "load time — an unknown step, a bad maxParallel, a "
                        "conflicting finisher binding — never at consume time."
                    ),
                    sources=("src/harness/drivers/fs_workflows.py",),
                ),
                Driver(
                    id="served-workflows",
                    name="ServedWorkflowRepository",
                    tagline="Restricts an inner repository to the served set.",
                    description=(
                        "Wraps another repository and exposes only the workflows "
                        "this run actually serves — the guard that keeps an "
                        "admin-added file from silently joining a running "
                        "harness."
                    ),
                    sources=("src/harness/drivers/fs_workflows.py",),
                ),
                Driver(
                    id="memory-workflows",
                    name="MemoryWorkflowRepository",
                    tagline="The in-memory double the tests drive.",
                    description="The same contract over a plain dict of definitions.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="behavior",
            name="ConsumerBehavior",
            group="Orchestration",
            tagline="Says what happened — never where it goes next.",
            description=(
                "The work a step performs, behind a port. A behavior returns "
                "BehaviorResult(outcome, summary) and nothing else: the "
                "dispatcher routes the outcome, the consumer decides nothing. "
                "That three-way split is the core invariant of the loop."
            ),
            adrs=("0002-three-way-decision-split",),
            sources=("src/harness/ports/behavior.py", "src/harness/consumer.py"),
            drivers=(
                Driver(
                    id="claude-cli-behavior",
                    name="ClaudeCliBehavior",
                    tagline="Runs the step's persona over the task's worktree.",
                    description=(
                        "Attaches the worktree, allocates the next artifact "
                        "attempt, runs the persona through AgentRunner, and lets "
                        "the worker — never the LLM — commit the result."
                    ),
                    sources=("src/harness/behaviors/agent.py",),
                    adrs=("0007-agent-persona-as-data",),
                ),
                Driver(
                    id="landing-behavior",
                    name="LandingBehavior",
                    tagline="The open-pr finisher: syncs base, pushes, proposes.",
                    description=(
                        "The default finishing step. It merges the PR's base "
                        "branch into the task branch first (so the PR is born "
                        "up-to-date), pushes, and opens the pull request through "
                        "the Forge — proposing only, never merging. Which step "
                        "finishes, and by which kind, is workflow data."
                    ),
                    sources=("src/harness/behaviors/landing.py",),
                    adrs=(
                        "0009-landing-proposes-never-touches-main",
                        "0016-finisher-as-data",
                        "0017-landing-syncs-base-before-proposing",
                    ),
                ),
                Driver(
                    id="resolve-conflict-behavior",
                    name="ResolveConflictBehavior",
                    tagline="Merges base into the branch; escalates real conflicts.",
                    description=(
                        "The resolver step's behavior: a clean merge commits "
                        "without spending an agent call; a real conflict runs "
                        "the resolve persona, then the worker commits. Always a "
                        "merge, never a rebase — pushed history is never "
                        "rewritten."
                    ),
                    sources=("src/harness/behaviors/resolve_conflict.py",),
                ),
                Driver(
                    id="dummy-behavior",
                    name="DummyBehavior",
                    tagline="The deterministic phase-1 stand-in.",
                    description=(
                        "Returns done deterministically (optionally one "
                        "request_changes per step, once) so the whole loop runs "
                        "with no agent at all."
                    ),
                    sources=("src/harness/drivers/dummy_behavior.py",),
                ),
                Driver(
                    id="scripted-behavior",
                    name="ScriptedBehavior",
                    tagline="Returns scripted outcomes per step, for tests.",
                    description=(
                        "A test double that plays back a script of outcomes, so "
                        "routing edge cases are driven precisely."
                    ),
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="enqueue-strategy",
            name="EnqueueStrategy",
            group="Orchestration",
            tagline="Chooses which queued task is claimed next.",
            description=(
                "The ordering seam between a queue and its consumer — kept as a "
                "port so a priority scheme is a new driver, not a queue rewrite."
            ),
            sources=("src/harness/ports/strategy.py",),
            drivers=(
                Driver(
                    id="fifo-strategy",
                    name="FifoStrategy",
                    tagline="Oldest first.",
                    description="The shipped ordering: first in, first out.",
                    sources=("src/harness/drivers/fifo_strategy.py",),
                ),
            ),
        ),
        Port(
            id="event-sink",
            name="EventSink",
            group="Orchestration",
            tagline="Every task movement is an event fanned out here.",
            description=(
                "The harness narrates itself through events: every task "
                "movement carries both task and queue. Sinks consume the stream "
                "— the console log, the board projection, the outward "
                "reflection — and a failing sink is isolated so it never blocks "
                "decision-making."
            ),
            sources=("src/harness/ports/events.py",),
            drivers=(
                Driver(
                    id="stdout-events",
                    name="StdoutEventSink",
                    tagline="The console log.",
                    description="Prints each event as a line — the operator's tail.",
                    sources=("src/harness/drivers/stdout_events.py",),
                ),
                Driver(
                    id="composite-events",
                    name="CompositeEventSink",
                    tagline="Fans one stream to many sinks, isolating failures.",
                    description=(
                        "Delivers each event to every registered sink and "
                        "swallows a single sink's failure, so a flaky outward "
                        "reflection can never stall the loop."
                    ),
                    sources=("src/harness/drivers/composite_events.py",),
                ),
                Driver(
                    id="projection-events",
                    name="ProjectionSink",
                    tagline="Feeds the board's read model.",
                    description=(
                        "Applies each event to the in-memory BoardProjection, "
                        "keeping the web board live without polling the queues."
                    ),
                    sources=("src/harness/drivers/projection_events.py",),
                ),
                Driver(
                    id="stage-output",
                    name="StageOutputProjection",
                    tagline="A live, bounded tail of the running stage.",
                    description=(
                        "Both a sink and the StageOutputView the board reads: a "
                        "bounded in-memory tail of what the running stage is "
                        "doing right now, gone once the stage ends."
                    ),
                    sources=("src/harness/drivers/stage_output.py",),
                    adrs=("0012-stageoutputview-third-ui-surface",),
                ),
                Driver(
                    id="source-reflector",
                    name="SourceReflectorSink",
                    tagline="Routes events outward to the matching TaskSource.",
                    description=(
                        "The bridge from the event stream to the outside world: "
                        "each event is offered to every registered source/sink, "
                        "which matches on the task's origin (data.source) or "
                        "destination (data.sink) and ignores foreign tasks."
                    ),
                    sources=("src/harness/drivers/source_reflector.py",),
                    adrs=("0010-tasksource-single-external-port",),
                ),
                Driver(
                    id="memory-events",
                    name="MemoryEventSink",
                    tagline="The in-memory double the tests drive.",
                    description="Records events in a list for assertions.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="clock",
            name="Clock",
            group="Orchestration",
            tagline="Time behind a port, so tests never sleep.",
            description=(
                "Every read of now() and every wait goes through this port. "
                "Scheduled triggers gate on it, timeouts count by it — and the "
                "whole suite runs on a fake, with no real waiting anywhere."
            ),
            sources=("src/harness/ports/clock.py",),
            drivers=(
                Driver(
                    id="system-clock",
                    name="SystemClock",
                    tagline="Real time.",
                    description="Wall-clock time and real asyncio sleeps.",
                    sources=("src/harness/drivers/system_clock.py",),
                ),
                Driver(
                    id="fake-clock",
                    name="FakeClock",
                    tagline="Manually advanced time, for tests.",
                    description=(
                        "Time moves only when a test advances it, so interval "
                        "gates and timeouts are exercised instantly."
                    ),
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        # ---- Workspace & delivery ---------------------------------------
        Port(
            id="workspace",
            name="Workspace",
            group="Workspace & delivery",
            tagline="An isolated worktree per task; the worker commits.",
            description=(
                "attach(task) yields a handle over the task's own worktree on "
                "the branch harness/<task-id>: parallel tasks never collide and "
                "the main checkout is never disturbed. The handle commits, "
                "pushes and merges — the consumer knows no git, and the agent "
                "never commits."
            ),
            adrs=("0006-worktree-vs-artifact-folder-split",),
            sources=("src/harness/ports/workspace.py",),
            drivers=(
                Driver(
                    id="git-workspace",
                    name="GitWorkspace",
                    tagline="Real git worktrees branched from the registered clone.",
                    description=(
                        "Adds a git worktree per task under the worktrees root, "
                        "reset-on-reattach for a dirty tree, plain forward "
                        "pushes only — no force push, no worktree removal, and "
                        "conflict resolution is always a merge, never a rebase."
                    ),
                    sources=("src/harness/drivers/git_workspace.py",),
                ),
                Driver(
                    id="memory-workspace",
                    name="MemoryWorkspace",
                    tagline="The in-memory double the tests drive.",
                    description=(
                        "Records attaches, commits and merges without touching "
                        "git, so behavior tests stay instant."
                    ),
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="artifacts",
            name="ArtifactStore / ArtifactView",
            group="Workspace & delivery",
            tagline="What each step produced — versioned, attempt-indexed.",
            description=(
                "Each step writes its artifacts (plan, design, review) under "
                ".artifacts/<task>/ in the worktree, attempt-indexed so a re-run "
                "never overwrites the previous attempt — the request_changes "
                "loop stays in the audit trail. The store writes, the view "
                "reads for the board without git; the artifacts ride into the "
                "branch and the PR alongside the code."
            ),
            adrs=("0006-worktree-vs-artifact-folder-split",),
            sources=(
                "src/harness/ports/artifacts.py",
                "src/harness/artifacts_layout.py",
            ),
            drivers=(
                Driver(
                    id="worktree-artifacts",
                    name="WorktreeArtifactView",
                    tagline="Reads attempts straight from the worktree.",
                    description=(
                        "Resolves a task's worktree and lists/reads the "
                        "attempt-indexed artifact files there — the board's "
                        "window into a step's work products."
                    ),
                    sources=("src/harness/drivers/worktree_artifacts.py",),
                ),
                Driver(
                    id="memory-artifacts",
                    name="MemoryArtifactStore",
                    tagline="The in-memory double the tests drive.",
                    description="The same attempt-indexed contract over dicts.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="forge",
            name="Forge",
            group="Workspace & delivery",
            tagline="Proposes pull requests; never merges.",
            description=(
                "The delivery seam: open_pull_request() proposes the task's "
                "branch and base_branch() names the branch the PR will target — "
                "which landing syncs in first, so the PR is born up-to-date. "
                "The harness never touches main; the merge is a human's call. "
                "Idempotent: a re-run after a crash returns the existing PR "
                "instead of opening a second one."
            ),
            adrs=(
                "0009-landing-proposes-never-touches-main",
                "0017-landing-syncs-base-before-proposing",
            ),
            sources=("src/harness/ports/forge.py",),
            drivers=(
                Driver(
                    id="github-forge",
                    name="GithubForge",
                    tagline="Opens the real PR on GitHub.",
                    description=(
                        "Derives the slug from the worktree's origin, targets "
                        "the repo's default branch, links Closes #n for an "
                        "issue-born task, and raises ForgeError on every failure "
                        "path — a failed PR fails the task, never a silent "
                        "success."
                    ),
                    sources=("src/harness/drivers/github_forge.py",),
                ),
                Driver(
                    id="fake-forge",
                    name="FakeForge",
                    tagline="Records proposals to a local file — offline runs.",
                    description=(
                        "Writes proposed PRs to prs.json instead of calling "
                        "GitHub; the offline and smoke-test forge."
                    ),
                    sources=("src/harness/drivers/fake_forge.py",),
                ),
                Driver(
                    id="memory-forge",
                    name="MemoryForge",
                    tagline="The in-memory double the tests drive.",
                    description="Records proposals in memory for assertions.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="agent-runner",
            name="AgentRunner",
            group="Workspace & delivery",
            tagline="Runs a persona over a worktree; the persona is data.",
            description=(
                "One shared runner executes every persona: the difference "
                "between a planner and a reviewer is the AgentSpec it is given "
                "(prompt, model, tools, allowed outcomes), never a subclass or "
                "a branch on the agent's name."
            ),
            adrs=("0007-agent-persona-as-data",),
            sources=("src/harness/ports/agent.py",),
            drivers=(
                Driver(
                    id="claude-cli-runner",
                    name="ClaudeCliRunner",
                    tagline="Runs the persona via claude -p.",
                    description=(
                        "The thin subprocess shell around the real claude CLI — "
                        "covered by an opt-in smoke test; everything above it is "
                        "tested through fakes."
                    ),
                    sources=("src/harness/drivers/claude_cli.py",),
                ),
                Driver(
                    id="fake-agent-runner",
                    name="FakeAgentRunner",
                    tagline="The scripted agent the tests drive.",
                    description=(
                        "Returns scripted verdicts and writes scripted "
                        "artifacts, so agent-step behavior is tested without a "
                        "subprocess."
                    ),
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="agent-catalog",
            name="AgentCatalog",
            group="Workspace & delivery",
            tagline="Maps a step's name to its persona.",
            description=(
                "Serves the AgentSpec for a step (default identity: the persona "
                "named like the step). Adding an agent is a new file in the "
                "catalog, not a new class — the persona is data."
            ),
            adrs=("0007-agent-persona-as-data",),
            sources=("src/harness/ports/agent.py",),
            drivers=(
                Driver(
                    id="fs-agents",
                    name="FilesystemAgentCatalog",
                    tagline="agents/*.json — one persona per file.",
                    description=(
                        "Reads each persona from agents/<step>.json, written by "
                        "harness init and editable from the board."
                    ),
                    sources=("src/harness/drivers/fs_agents.py",),
                ),
                Driver(
                    id="memory-agents",
                    name="MemoryAgentCatalog",
                    tagline="The in-memory double the tests drive.",
                    description="The same lookup over a plain dict of specs.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="repository-registry",
            name="RepositoryRegistry",
            group="Workspace & delivery",
            tagline="Repo name → path on this machine.",
            description=(
                "A task carries a repository name, never a filesystem path. The "
                "registry resolves the name to a clone on disk (repos.json — "
                "machine-specific, uncommitted), so the same task definition is "
                "portable and the core stays free of absolute paths."
            ),
            adrs=("0008-repository-registry-name-to-path",),
            sources=("src/harness/ports/repos.py",),
            drivers=(
                Driver(
                    id="fs-repos",
                    name="FilesystemRepositoryRegistry",
                    tagline="repos.json on this machine.",
                    description="Reads the name → path mapping from repos.json.",
                    sources=("src/harness/drivers/fs_repos.py",),
                ),
                Driver(
                    id="memory-repos",
                    name="MemoryRepositoryRegistry",
                    tagline="The in-memory double the tests drive.",
                    description="The same mapping over a plain dict.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        # ---- Reconciliation ---------------------------------------------
        Port(
            id="merge-checker",
            name="MergeChecker",
            group="Reconciliation",
            tagline="Is a done task's PR merged yet?",
            description=(
                "The read the MergeReconciler sweeps with: once a done task's "
                "pull request has merged, the task is retired into archived/ — "
                "off the board but still gettable by id. None means the task "
                "has no PR; a transient failure raises so the caller retries, "
                "never mistaking it for 'not merged'."
            ),
            sources=("src/harness/ports/merge.py", "src/harness/merge_reconciler.py"),
            drivers=(
                Driver(
                    id="github-merge-checker",
                    name="GithubMergeChecker",
                    tagline="Asks GitHub about the PR on the task.",
                    description=(
                        "Reads repo and number straight off task.data['pr'] at "
                        "check time — one checker serves every repo the token "
                        "can reach."
                    ),
                    sources=("src/harness/drivers/github_merge_checker.py",),
                ),
                Driver(
                    id="fake-merge-checker",
                    name="FakeMergeChecker",
                    tagline="The scripted double the tests drive.",
                    description="Answers from a scripted map of task → verdict.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="issue-checker",
            name="IssueChecker",
            group="Reconciliation",
            tagline="Is the task's source issue still open?",
            description=(
                "The read the IssueReconciler sweeps with: a task whose source "
                "issue was closed or deleted out from under it is retired into "
                "archived/. None means this checker doesn't resolve the task's "
                "origin; a transient failure raises so the caller retries, "
                "never mistaking it for 'closed'."
            ),
            adrs=("0013-issue-reconciler-cleanup",),
            sources=(
                "src/harness/ports/issue_state.py",
                "src/harness/issue_reconciler.py",
            ),
            drivers=(
                Driver(
                    id="github-issue-checker",
                    name="GithubIssueChecker",
                    tagline="Asks GitHub about the issue on the task.",
                    description=(
                        "Reads repo and issue straight off task.data['source'] "
                        "at check time; a deleted issue (404) reads as 'not "
                        "open'."
                    ),
                    sources=("src/harness/drivers/github_issue_checker.py",),
                ),
                Driver(
                    id="fake-issue-checker",
                    name="FakeIssueChecker",
                    tagline="The scripted double the tests drive.",
                    description="Answers from a scripted map of task → verdict.",
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        Port(
            id="issue-tracker",
            name="IssueTracker",
            group="Reconciliation",
            tagline="Opens the healer's diagnostic issue, idempotently.",
            description=(
                "The healer's deliverable goes out through this port: when a "
                "failed task looks like a fixable harness bug, the Healer loop "
                "— not the LLM — opens an advisory issue. Idempotent by a "
                "per-task marker, so a crash before the settle never files a "
                "second issue."
            ),
            sources=("src/harness/ports/issues.py", "src/harness/healer.py"),
            drivers=(
                Driver(
                    id="github-issue-tracker",
                    name="GithubIssueTracker",
                    tagline="Opens the issue on GitHub.",
                    description=(
                        "Posts the healer's draft to the harness repo, deduped "
                        "by an embedded harness-heal marker in the body."
                    ),
                    sources=("src/harness/drivers/github_issues.py",),
                ),
                Driver(
                    id="memory-issue-tracker",
                    name="MemoryIssueTracker",
                    tagline="The offline fallback and test double.",
                    description=(
                        "Records opened issues in memory, so the heal loop runs "
                        "harmlessly with no token."
                    ),
                    sources=("src/harness/drivers/memory.py",),
                ),
            ),
        ),
        # ---- Operator surface -------------------------------------------
        Port(
            id="board-view",
            name="BoardView",
            group="Operator surface",
            tagline="The read side the web board renders.",
            description=(
                "The board never imports a driver: it reads task state through "
                "this port alone. The projection behind it is hydrated from the "
                "queues at startup and kept live by the event stream."
            ),
            adrs=("0005-ui-never-imports-a-driver",),
            sources=("src/harness/ports/board.py", "src/harness/api/routes.py"),
            drivers=(
                Driver(
                    id="board-projection",
                    name="BoardProjection",
                    tagline="The in-memory read model of the board.",
                    description=(
                        "Columns from queues plus a todo view of fresh inbox "
                        "tasks; archived tasks drop out of every column but stay "
                        "resolvable by id — declutter the live view, don't "
                        "destroy the record."
                    ),
                    sources=("src/harness/projection.py",),
                ),
            ),
        ),
        Port(
            id="stage-output-view",
            name="StageOutputView",
            group="Operator surface",
            tagline="What the running stage is doing right now.",
            description=(
                "The third read-only UI surface: where BoardView shows where a "
                "task is and ArtifactView shows what it produced, this shows "
                "the live output of the stage that is running — a bounded, "
                "in-memory tail, gone once the stage ends."
            ),
            adrs=("0012-stageoutputview-third-ui-surface",),
            sources=("src/harness/ports/logs.py",),
            drivers=(
                Driver(
                    id="stage-output-projection",
                    name="StageOutputProjection",
                    tagline="The bounded in-memory tail.",
                    description=(
                        "An EventSink and a StageOutputView in one: appends "
                        "stage output as it streams and serves the tail to the "
                        "board."
                    ),
                    sources=("src/harness/drivers/stage_output.py",),
                ),
            ),
        ),
        Port(
            id="task-control",
            name="TaskControl",
            group="Operator surface",
            tagline="The write side: operator actions like restart.",
            description=(
                "BoardView's write-side mirror. restart is a reset, not a "
                "routing decision: it clears status and lastOutcome and "
                "re-inboxes a failed task — then the dispatcher, as always, "
                "decides where it goes next."
            ),
            adrs=("0011-taskcontrol-write-side-of-boardview",),
            sources=("src/harness/ports/control.py",),
            drivers=(
                Driver(
                    id="task-control-service",
                    name="TaskControlService",
                    tagline="The core service behind the board's buttons.",
                    description=(
                        "Implements the operator verbs over the queues, keeping "
                        "every routing decision with the dispatcher."
                    ),
                    sources=("src/harness/task_control.py",),
                ),
            ),
        ),
        Port(
            id="agent-admin",
            name="AgentAdmin",
            group="Operator surface",
            tagline="Edit personas from the board.",
            description=(
                "The write-side counterpart of AgentCatalog for the admin UI: "
                "list, read, write and delete persona files, with validation "
                "errors mapped onto the form."
            ),
            sources=("src/harness/ports/agent_admin.py",),
            drivers=(
                Driver(
                    id="fs-agent-admin",
                    name="FilesystemAgentAdmin",
                    tagline="Writes agents/*.json.",
                    description=(
                        "Edits the same files FilesystemAgentCatalog reads; "
                        "wired only in serve()."
                    ),
                    sources=("src/harness/drivers/fs_agents.py",),
                ),
            ),
        ),
        Port(
            id="workflow-admin",
            name="WorkflowAdmin",
            group="Operator surface",
            tagline="Edit workflow files from the board.",
            description=(
                "The write-side counterpart of WorkflowRepository for the admin "
                "UI: raw-text read and write over the file's exact content, "
                "validated before saving."
            ),
            sources=("src/harness/ports/workflow_admin.py",),
            drivers=(
                Driver(
                    id="fs-workflow-admin",
                    name="FilesystemWorkflowAdmin",
                    tagline="Writes workflows/*.json.",
                    description=(
                        "Edits the same files FilesystemWorkflowRepository "
                        "reads; wired only in serve()."
                    ),
                    sources=("src/harness/drivers/fs_workflows.py",),
                ),
            ),
        ),
        Port(
            id="process-admin",
            name="ProcessAdmin",
            group="Operator surface",
            tagline="Assemble processes from the board, validated on submit.",
            description=(
                "The structured editor for the operator's top-level authoring "
                "aggregate: trigger, action, target and sink assembled in a "
                "form, validated by the same compiler the runtime uses, picked "
                "up on the next run. The form's dropdowns are populated through "
                "the port, so the UI still imports no driver."
            ),
            adrs=("0015-process-authoring-aggregate",),
            sources=("src/harness/ports/process_admin.py",),
            drivers=(
                Driver(
                    id="fs-process-admin",
                    name="FilesystemProcessAdmin",
                    tagline="Writes processes/*.json.",
                    description=(
                        "Edits the same files FilesystemProcessRepository "
                        "compiles at startup, running the shared compile_process "
                        "validator on submit."
                    ),
                    sources=("src/harness/drivers/fs_processes.py",),
                ),
            ),
        ),
        Port(
            id="updater",
            name="Updater",
            group="Operator surface",
            tagline="The board's Update button.",
            description=(
                "The write side of the version string the footer shows: "
                "update() upgrades the installed tool and, on a version change, "
                "restarts the service. A failed restart folds into the result "
                "detail; only a failed upgrade raises."
            ),
            sources=("src/harness/ports/updater.py",),
            drivers=(
                Driver(
                    id="uv-updater",
                    name="UvUpdater",
                    tagline="uv tool upgrade + service kickstart.",
                    description=(
                        "The same flow as harness update, reached from the "
                        "board: discovers uv itself; the entry point and the "
                        "idle gate are injected by serve()."
                    ),
                    sources=("src/harness/drivers/uv_updater.py",),
                ),
            ),
        ),
    ),
)
