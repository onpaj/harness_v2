"""The curated architecture model the Explorer draws — the single source of
truth for the diagram, its animated flow, and every part's drill-down.

Hand-authored (not derived from the live module graph): a small, legible graph
of the harness's parts, each grounded in the ADR(s) that decide it. `validate`
keeps this model honest against the real `docs/adr/` files and `src/` tree, and
runs in the test suite so a rename that breaks the mapping fails CI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

_KINDS = {"port", "driver", "core", "ui", "store"}


@dataclass(frozen=True)
class Part:
    id: str
    name: str
    kind: str  # one of _KINDS
    tagline: str
    description: str
    adrs: tuple[str, ...]  # ADR slugs: docs/adr/<slug>.md
    sources: tuple[str, ...]  # repo-relative source paths
    x: float  # 0..100 diagram coordinate
    y: float  # 0..100 diagram coordinate
    related_docs: tuple[str, ...] = ()  # optional extra doc slugs (specs/plans)


@dataclass(frozen=True)
class Stage:
    part_id: str
    caption: str


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str


@dataclass(frozen=True)
class ArchitectureModel:
    parts: tuple[Part, ...]
    flow: tuple[Stage, ...]
    edges: tuple[Edge, ...]


def validate(model: ArchitectureModel, repo_root: Path) -> None:
    """Raise ValueError if the model is internally inconsistent or has drifted
    from the real docs/source tree. Silence means coherent."""
    repo_root = Path(repo_root)
    ids = [p.id for p in model.parts]
    id_set = set(ids)

    if len(ids) != len(id_set):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate part id(s): {dupes}")

    for part in model.parts:
        if part.kind not in _KINDS:
            raise ValueError(f"part {part.id!r} has unknown kind {part.kind!r}")
        for slug in part.adrs:
            if not (repo_root / "docs" / "adr" / f"{slug}.md").is_file():
                raise ValueError(
                    f"part {part.id!r} cites missing ADR {slug!r}"
                )
        for src in part.sources:
            if not (repo_root / src).exists():
                raise ValueError(
                    f"part {part.id!r} cites missing source path {src!r}"
                )

    for stage in model.flow:
        if stage.part_id not in id_set:
            raise ValueError(f"flow stage references unknown part {stage.part_id!r}")

    referenced: set[str] = set()
    for edge in model.edges:
        for endpoint in (edge.src, edge.dst):
            if endpoint not in id_set:
                raise ValueError(f"edge references unknown part {endpoint!r}")
            referenced.add(endpoint)
    referenced.update(stage.part_id for stage in model.flow)

    orphans = sorted(id_set - referenced)
    if orphans:
        raise ValueError(f"orphan part(s) on no edge and not in flow: {orphans}")


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
    parts=(
        Part(
            id="task-source",
            name="TaskSource",
            kind="port",
            tagline="The one seam to the outside world.",
            description=(
                "Every task enters through a single port. Whether the origin is a "
                "GitHub issue, a scheduled trigger, or an action gathering items, "
                "the core asks TaskSource for work and gets domain tasks back — so "
                "a new origin is a new adapter, not a change to the loop. The same "
                "port carries state back outward, which is how a sink reflects "
                "progress."
            ),
            adrs=("0010-tasksource-single-external-port",),
            sources=("src/harness/ports/source.py",),
            x=8.0,
            y=30.0,
        ),
        Part(
            id="github-source",
            name="GitHub adapter",
            kind="driver",
            tagline="Turns GitHub issues into tasks; opens PRs back.",
            description=(
                "The adapter behind TaskSource. It polls repositories named in the "
                "registry for labelled issues and hands them to the core, and it is "
                "the same GitHub boundary a finished task's pull request goes out "
                "through. As a task moves it relabels the issue — the GitHub form of "
                "a sink."
            ),
            adrs=(
                "0010-tasksource-single-external-port",
                "0008-repository-registry-name-to-path",
            ),
            sources=(
                "src/harness/drivers/github_source.py",
                "src/harness/drivers/github_forge.py",
            ),
            x=8.0,
            y=12.0,
        ),
        Part(
            id="repo-registry",
            name="RepositoryRegistry",
            kind="store",
            tagline="Maps a repo name to a path on this machine.",
            description=(
                "A task carries a repo name, never a filesystem path. The registry "
                "resolves the name to a clone on disk, so the same task definition is "
                "portable across machines and the core stays free of absolute paths."
            ),
            adrs=("0008-repository-registry-name-to-path",),
            sources=(
                "src/harness/ports/repos.py",
                "src/harness/drivers/fs_repos.py",
            ),
            x=8.0,
            y=52.0,
        ),
        Part(
            id="process",
            name="Process",
            kind="driver",
            tagline="Ties the whole automation together, as data.",
            description=(
                "A Process is the operator's authoring aggregate — it names four "
                "interchangeable roles: a trigger (cadence), an action (a named "
                "check plus params), a target (a workflow or a single step) and a "
                "sink (where to report). It is a compile-time concept: each "
                "processes/*.json compiles into a scheduled TaskSource that feeds "
                "the inbox, and the board editor writes the same files through the "
                "same validator. Nothing in the core ever learns the word 'process'."
            ),
            adrs=("0015-process-authoring-aggregate",),
            sources=(
                "src/harness/drivers/fs_processes.py",
                "src/harness/drivers/scheduled_trigger.py",
            ),
            x=8.0,
            y=72.0,
        ),
        Part(
            id="trigger",
            name="Trigger",
            kind="driver",
            tagline="Fires work on a clock — a gate, not a loop.",
            description=(
                "A trigger is a TaskSource that produces work instead of ingesting "
                "it. A scheduled trigger gates on the interval bucket "
                "floor(now / interval): it returns nothing between fires and, when "
                "the bucket rolls over, runs its action and hands the resulting "
                "tasks to the inbox. It reflects nothing outward and never places a "
                "task on a queue itself — the dispatcher still decides where each "
                "one goes. Dedup is bucket- or state-keyed, so a fire survives a "
                "restart at most once per interval."
            ),
            adrs=("0014-triggers-produce-tasks-not-placements",),
            sources=(
                "src/harness/ports/source.py",
                "src/harness/drivers/scheduled_trigger.py",
                "src/harness/ports/triggers.py",
            ),
            x=30.0,
            y=90.0,
        ),
        Part(
            id="action",
            name="Action (Check)",
            kind="port",
            tagline="Gathers the work items — one observation each.",
            description=(
                "An action is a Check: evaluate() looks at the world — open GitHub "
                "issues, files matching a glob, the lines a command prints — and "
                "returns a list of observations. Each observation becomes one task "
                "in the inbox. The schedule is data and the condition is code, so a "
                "new kind of action is a small Check class plus one registry entry, "
                "named from a process by string. 'command' is the escape hatch: an "
                "operator wires a simple action with no Python at all."
            ),
            adrs=(
                "0015-process-authoring-aggregate",
                "0014-triggers-produce-tasks-not-placements",
            ),
            sources=(
                "src/harness/ports/triggers.py",
                "src/harness/drivers/checks.py",
                "src/harness/drivers/github_issues_check.py",
            ),
            x=30.0,
            y=72.0,
        ),
        Part(
            id="queues",
            name="Queues",
            kind="core",
            tagline="Directories; claimed atomically by rename.",
            description=(
                "Each workflow step is a directory. A worker claims a task by "
                "renaming its file into a private processing dir — an atomic, "
                "lock-free operation the filesystem guarantees, so two workers can "
                "never claim the same task. Terminal states (done, failed, healed, "
                "archived) are just queues nobody consumes."
            ),
            adrs=("0003-atomic-queue-claim-by-rename",),
            sources=(
                "src/harness/ports/queue.py",
                "src/harness/drivers/fs_queue.py",
            ),
            x=30.0,
            y=30.0,
        ),
        Part(
            id="workflow",
            name="Workflow",
            kind="store",
            tagline="The state machine, written as data.",
            description=(
                "A workflow is a small explicit state machine: a start step and a "
                "set of (from, on-outcome, to) transitions ending at the reserved "
                "END node. It also carries per-step concurrency (maxParallel) and "
                "the finisher binding — which step finishes, and by which kind. The "
                "router interprets it and nothing about it is code, so an operator "
                "reshapes how work flows by editing JSON, never by touching the loop."
            ),
            adrs=("0004-pure-router", "0016-finisher-as-data"),
            sources=(
                "src/harness/models.py",
                "src/harness/ports/workflows.py",
                "src/harness/drivers/fs_workflows.py",
            ),
            x=41.0,
            y=44.0,
        ),
        Part(
            id="router",
            name="Router",
            kind="core",
            tagline="A pure function: (state, outcome) → next step.",
            description=(
                "The router walks the workflow's state machine with no side effects. "
                "Given only a task's status and the last outcome, it returns the next "
                "queue — and nothing else. It never reads the repository, the step, "
                "or the task data, and imports only the domain models, which is what "
                "makes the whole workflow trivially testable."
            ),
            adrs=("0004-pure-router",),
            sources=("src/harness/router.py",),
            x=52.0,
            y=30.0,
        ),
        Part(
            id="agent-runner",
            name="Agent runner",
            kind="core",
            tagline="Runs a step's agent; splits decide / act / persist.",
            description=(
                "The consumer and dispatcher that run a step: the behavior invokes "
                "the step's agent, the dispatcher routes the outcome it reports, and "
                "the worker — not the agent — commits the result. The three roles "
                "stay separate: the behavior says what happened, the dispatcher says "
                "where it goes next, the consumer decides nothing."
            ),
            adrs=(
                "0002-three-way-decision-split",
                "0007-agent-persona-as-data",
            ),
            sources=(
                "src/harness/behaviors/agent.py",
                "src/harness/dispatcher.py",
                "src/harness/consumer.py",
            ),
            x=74.0,
            y=30.0,
        ),
        Part(
            id="persona-catalog",
            name="Agent (persona)",
            kind="store",
            tagline="Each agent is data, not a subclass.",
            description=(
                "An agent's persona — its prompt, model, tools and allowed outcomes "
                "— is a JSON record the catalog serves per step, not code. The "
                "runner is fixed and the persona varies, so swapping the agent behind "
                "a queue is a file edit; there is no branch on the agent's name "
                "anywhere in the runner."
            ),
            adrs=("0007-agent-persona-as-data",),
            sources=(
                "src/harness/ports/agent.py",
                "src/harness/drivers/fs_agents.py",
            ),
            x=74.0,
            y=12.0,
        ),
        Part(
            id="worktree",
            name="Worktree",
            kind="driver",
            tagline="Each task gets an isolated git worktree.",
            description=(
                "A task's agent works in its own git worktree branched from the "
                "registered clone, so parallel tasks never collide and the main "
                "checkout is never disturbed. The worker commits the code and the "
                "artifacts together into the task's branch."
            ),
            adrs=(
                "0006-worktree-vs-artifact-folder-split",
                "0009-landing-proposes-never-touches-main",
            ),
            sources=(
                "src/harness/ports/workspace.py",
                "src/harness/drivers/git_workspace.py",
            ),
            x=74.0,
            y=52.0,
        ),
        Part(
            id="artifact-folder",
            name="Artifact folder",
            kind="store",
            tagline="Per-stage outputs, versioned in the worktree.",
            description=(
                "Each stage writes its artifacts (plan, design, review) under "
                ".artifacts/<id>/ in the worktree, attempt-indexed so a re-run never "
                "overwrites the previous attempt. The board reads them without git; "
                "they ride into the branch and the pull request alongside the code."
            ),
            adrs=("0006-worktree-vs-artifact-folder-split",),
            sources=(
                "src/harness/ports/artifacts.py",
                "src/harness/drivers/worktree_artifacts.py",
                "src/harness/artifacts_layout.py",
            ),
            x=52.0,
            y=52.0,
        ),
        Part(
            id="landing",
            name="Finisher",
            kind="core",
            tagline="Finishes the task — the kind is data.",
            description=(
                "The finishing step is an ordinary workflow node that can fail into "
                "failed/ like any other. What it does is chosen from a registry by "
                "kind, not by the step's name: the default open-pr kind pushes the "
                "branch and opens a pull request through the Forge, proposing only — "
                "it never merges or writes to main. A create-file or call-api "
                "finisher would be a sibling kind, no new branch in the runner."
            ),
            adrs=(
                "0009-landing-proposes-never-touches-main",
                "0016-finisher-as-data",
            ),
            sources=(
                "src/harness/behaviors/landing.py",
                "src/harness/app.py",
            ),
            x=52.0,
            y=12.0,
        ),
        Part(
            id="sink",
            name="Sink",
            kind="driver",
            tagline="Reflects task state back outward.",
            description=(
                "A sink is the outbound half of reflection. As a task moves, the "
                "event stream is fanned by SourceReflectorSink to each registered "
                "destination: the GitHub adapter relabels the issue, a Slack sink "
                "posts a short line. A process names its sink by kind (none or "
                "slack), and the destination is chosen independently of where the "
                "task came from — so work can enter from GitHub and be reported to "
                "Slack."
            ),
            adrs=(
                "0015-process-authoring-aggregate",
                "0010-tasksource-single-external-port",
            ),
            sources=(
                "src/harness/drivers/source_reflector.py",
                "src/harness/drivers/slack_sink.py",
                "src/harness/drivers/github_source.py",
            ),
            x=8.0,
            y=90.0,
        ),
        Part(
            id="board",
            name="Board",
            kind="ui",
            tagline="Read-only web view; writes go through ports.",
            description=(
                "The web board renders task state live and hosts the agent, workflow "
                "and process editors. It never imports a driver: it reads through "
                "BoardView, ArtifactView and StageOutputView and writes through "
                "TaskControl and the admin ports, so the UI is decoupled from how "
                "state is stored."
            ),
            adrs=(
                "0005-ui-never-imports-a-driver",
                "0011-taskcontrol-write-side-of-boardview",
                "0012-stageoutputview-third-ui-surface",
            ),
            sources=(
                "src/harness/api/app.py",
                "src/harness/ports/board.py",
                "src/harness/ports/control.py",
            ),
            x=30.0,
            y=52.0,
        ),
    ),
    flow=(
        Stage("task-source", "A trigger, an action, or a GitHub issue becomes a task"),
        Stage("queues", "Claimed atomically by a rename into a step directory"),
        Stage("workflow", "The workflow — a state machine as data — holds the next step"),
        Stage("router", "The pure router computes it from (state, last outcome) alone"),
        Stage("agent-runner", "The step's agent runs; decide, act and persist stay apart"),
        Stage("worktree", "…in an isolated git worktree"),
        Stage("landing", "The finisher proposes a PR — never touches main"),
        Stage("github-source", "The pull request goes back out through the GitHub adapter"),
    ),
    edges=(
        Edge("repo-registry", "github-source"),
        Edge("github-source", "task-source"),
        Edge("task-source", "queues"),
        Edge("queues", "workflow"),
        Edge("workflow", "router"),
        Edge("workflow", "landing"),
        Edge("router", "agent-runner"),
        Edge("agent-runner", "persona-catalog"),
        Edge("agent-runner", "worktree"),
        Edge("agent-runner", "artifact-folder"),
        Edge("worktree", "landing"),
        Edge("landing", "github-source"),
        Edge("queues", "board"),
        Edge("process", "trigger"),
        Edge("process", "action"),
        Edge("process", "sink"),
        Edge("trigger", "action"),
        Edge("trigger", "task-source"),
        Edge("sink", "queues"),
    ),
)
