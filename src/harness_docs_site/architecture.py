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
                "Every task enters through a single port. The harness core never "
                "knows it is talking to GitHub — it asks TaskSource for work and "
                "gets domain tasks back, so a new origin is a new adapter, not a "
                "change to the loop."
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
                "through."
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
            id="queues",
            name="Queues",
            kind="core",
            tagline="Directories; claimed atomically by rename.",
            description=(
                "Each workflow step is a directory. A worker claims a task by "
                "renaming its file into a private processing dir — an atomic, "
                "lock-free operation the filesystem guarantees, so two workers can "
                "never claim the same task."
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
            id="router",
            name="Router",
            kind="core",
            tagline="A pure function: (task, outcome) → next step.",
            description=(
                "The router is the workflow state machine with no side effects. Given "
                "a task and the outcome of a step, it returns the next queue — and "
                "nothing else. It imports only the domain models, which is what makes "
                "the whole workflow trivially testable."
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
                "The consumer/dispatcher that runs a step: it invokes the step's "
                "agent behavior, keeps the LLM's decision separate from the act "
                "that applies it and the persist that records it, and reports the "
                "outcome back to the router."
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
            name="Persona catalog",
            kind="store",
            tagline="Each agent is data, not a subclass.",
            description=(
                "An agent's persona — its prompt, model, and tools — is a JSON record "
                "the catalog serves, not code. Adding or editing an agent never "
                "touches the runner; the behavior is fixed, the persona varies."
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
                "checkout is never disturbed."
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
            tagline="Per-stage outputs, kept out of the worktree.",
            description=(
                "Each stage writes its artifacts (plan, review, logs) to a folder "
                "kept separate from the code worktree, so generated notes never end "
                "up committed to the branch under review."
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
            name="Landing",
            kind="core",
            tagline="Proposes a PR; never touches main.",
            description=(
                "When the work is done, landing pushes the branch and opens a pull "
                "request. It proposes — it never merges or writes to main — so a "
                "human (or a downstream check) always makes the final call."
            ),
            adrs=("0009-landing-proposes-never-touches-main",),
            sources=("src/harness/behaviors/landing.py",),
            x=52.0,
            y=12.0,
        ),
        Part(
            id="board",
            name="Board",
            kind="ui",
            tagline="Read-only web view; writes go through ports.",
            description=(
                "The web board renders task state live. It never imports a driver: it "
                "reads through BoardView and StageOutputView and writes through "
                "TaskControl, so the UI is decoupled from how state is stored."
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
        Stage("task-source", "A labelled GitHub issue becomes a task"),
        Stage("queues", "Claimed atomically by rename into a step dir"),
        Stage("router", "The pure router picks the next step"),
        Stage("agent-runner", "The step's agent does the work"),
        Stage("worktree", "…in an isolated git worktree"),
        Stage("landing", "Landing opens a PR — never touches main"),
        Stage("github-source", "The pull request goes back out to GitHub"),
    ),
    edges=(
        Edge("github-source", "task-source"),
        Edge("repo-registry", "github-source"),
        Edge("task-source", "queues"),
        Edge("queues", "router"),
        Edge("router", "agent-runner"),
        Edge("agent-runner", "persona-catalog"),
        Edge("agent-runner", "worktree"),
        Edge("agent-runner", "artifact-folder"),
        Edge("agent-runner", "landing"),
        Edge("worktree", "landing"),
        Edge("landing", "github-source"),
        Edge("queues", "board"),
    ),
)
