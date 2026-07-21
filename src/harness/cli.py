"""Harness CLI."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata as metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn

from harness.api.app import create_app
from harness.app import LANDING_STEP, HarnessLayout, build
from harness.drivers.claude_cli import ClaudeCliRunner
from harness.drivers.fake_forge import FakeForge
from harness.drivers.fs_agents import FilesystemAgentCatalog
from harness.drivers.fs_repos import FilesystemRepositoryRegistry
from harness.drivers.fs_workflows import invalid_workflow_name
from harness.drivers.git_remote import github_slug
from harness.drivers.git_workspace import GitWorkspace
from harness.drivers.github_client import GithubClient, HttpGithubClient
from harness.drivers.github_forge import GithubForge
from harness.drivers.github_merge_checker import GithubMergeChecker
from harness.drivers.github_source import GithubTaskSource
from harness.drivers.launchd import (
    DEFAULT_LABEL,
    ServiceError,
    load,
    plist_bytes,
    plist_path,
    status,
    unload,
    wrapper_script,
)
from harness.drivers.system_clock import SystemClock
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.merge import MergeChecker
from harness.ports.repos import RepositoryRegistry
from harness.ports.source import TaskSource
from harness.ports.workflows import WorkflowNotFound

PACKAGE_NAME = "harness"

DEFAULT_WORKFLOW = "default"

# A sensible coarse mapping of default-workflow steps to labels. Other steps
# get no label → less noise. It's just a default, not a law.
DEFAULT_STEP_LABELS = {
    "development": "harness:in-progress",
    "review": "harness:in-review",
    "land": "harness:landing",
}

DEFAULT_DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "architecture"},
        {"from": "architecture", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}


def _root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    return Path(os.environ.get("HARNESS_HOME", "~/.harness")).expanduser()


def _init(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)

    if invalid_workflow_name(args.workflow):
        print(f"error: invalid workflow name: {args.workflow!r}", file=sys.stderr)
        return 2

    layout.workflows.mkdir(parents=True, exist_ok=True)

    definition_path = layout.workflows / f"{args.workflow}.json"
    if not definition_path.exists():
        definition_path.write_text(
            json.dumps(DEFAULT_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    try:
        harness = build(root, args.workflow)
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    _write_default_agents(layout, harness.workflow)
    _write_default_repos(layout)

    print(f"harness ready at {root}")
    print(f"steps: {', '.join(harness.workflow.steps())}")
    return 0


# Default step personas, carried over from harness v1 (repo onpaj/harness,
# agentharness/data/agents/) and adapted to phase 3 conventions: the prompt is
# only the **persona** (role, inputs, what to deliver) — how to read the
# artifacts of previous steps, where to write output, and how to close with a
# verdict block is supplied at runtime by `compose_prompt`, so we don't repeat
# it here. The persona is data (invariant 14): a step → (prompt, tools) map, not
# a branch in code. We leave the model at `null` — it's per queue (invariant),
# and the operator tunes the default in `agents/<step>.json`.
#
#   plan          ← v1 analyst + planner (first step: brief → spec + rough plan)
#   design        ← v1 designer
#   architecture  ← v1 architect
#   development    ← v1 developer (no commit — the worker does that, invariant 9)
#   review        ← v1 reviewer + code-reviewer (PASS/REVISION → done/request_changes)

_PLAN_PERSONA = (
    "You are a senior product manager and technical lead — the first step of "
    "the pipeline. From the task's request you produce a structured "
    "specification and a rough plan that the later steps (design, architecture, "
    "development) build on.\n\n"
    "The output has this structure:\n"
    "- Summary — 2–3 sentences on what this is about.\n"
    "- Context — why it's needed.\n"
    "- Functional requirements — numbered (FR-1, FR-2, …), each with testable "
    "acceptance criteria.\n"
    "- Non-functional requirements — performance, security, where it makes "
    "sense.\n"
    "- Data model — the key entities and how they relate.\n"
    "- Interfaces — endpoints, events, or UI flows at a high level.\n"
    "- Dependencies and scope — what it rests on and what is explicitly out of "
    "scope.\n"
    "- Rough plan — the implementation steps at a high level.\n"
    "- Open questions — what's unclear; where the request is ambiguous, pick a "
    "sensible default and note it here.\n\n"
    "Be specific and complete. Vague requirements lead to bad implementation."
)

_DESIGN_PERSONA = (
    "You are a senior software designer. From the specification and the "
    "architectural assessment of the previous steps you produce a concrete "
    "design.\n\n"
    "First, from the inputs, work out whether the feature has a user "
    "interface. If it has no UI, omit the UX/UI section entirely — don't write "
    "placeholders.\n\n"
    "The design covers:\n"
    "- UX/UI — only when there is a user interface: wireframes (ASCII), the "
    "component hierarchy, the key interactions.\n"
    "- Component design — the boundaries, responsibilities, and interfaces of "
    "the individual components or modules.\n"
    "- Data schemas — DB schemas, request and response shapes, event "
    "payloads.\n\n"
    "Don't define developer tasks — that's the development step's job."
)

_ARCHITECTURE_PERSONA = (
    "You are a senior software architect. From the brief and the specification "
    "you produce an architectural assessment that steers the implementation. "
    "You don't write code — you define the structure the developers will "
    "follow.\n\n"
    "Before you start writing, actively explore the project so the design "
    "rests on reality:\n"
    "1. Documentation first — architecture docs, ADRs, README, descriptions of "
    "patterns.\n"
    "2. When the docs are missing or insufficient, read the code — use "
    "Grep/Glob/Bash to find similar existing implementations and confirm the "
    "design fits the conventions.\n"
    "3. Never guess — when unsure, read the relevant source before proposing "
    "something that may conflict with it.\n\n"
    "The assessment contains:\n"
    "- Alignment with existing patterns and the integration points.\n"
    "- The proposed architecture — an overview of the components and the key "
    "decisions (options considered, the chosen approach, the rationale).\n"
    "- Implementation guidance — where new code belongs, the key interfaces "
    "and contracts, the data flow.\n"
    "- Risks and their mitigations, prerequisites before implementation "
    "begins.\n\n"
    "Have an opinion. Developers need a clear direction, not a list of "
    "options. When unsure, state your assumption and why."
)

_DEVELOPMENT_PERSONA = (
    "You are a senior developer. Following the specification, architecture, and "
    "design from the previous steps, you implement the request. You run "
    "non-interactively in an automated pipeline.\n\n"
    "The working directory is already a checkout of your branch — make all "
    "changes right here:\n"
    "1. DO NOT create a git worktree, and DO NOT create or switch branches. "
    "Code outside this directory will never be seen by the pipeline and "
    "silently disappears.\n"
    "2. DO NOT commit or push yourself, and don't open a PR — the harness "
    "handles committing your work and opening the PR. You just write the "
    "changes into the working directory.\n"
    "3. Write tests for what you implement.\n"
    "4. Never wait for interactive input — where a skill or tool would prompt "
    "you to choose, take the non-interactive path and carry on.\n\n"
    "When you're in a revision round (there's a review of the previous attempt "
    "among the artifacts), read it in full along with your previous "
    "implementation and address every point it raises.\n\n"
    "In your output artifact, summarize what was implemented, which files were "
    "created or changed, and how to verify it."
)

_REVIEW_PERSONA = (
    "You are a senior code reviewer. You check the implementation against the "
    "specification and architecture from the previous steps. Be fair but "
    "rigorous — this is about correctness and conformance to the request, not "
    "stylistic preferences.\n\n"
    "Check:\n"
    "- Conformance to the spec — does the implementation meet the functional "
    "requirements?\n"
    "- Adherence to the architecture — does it follow the proposed patterns "
    "and structure?\n"
    "- Completeness — are the acceptance criteria met and the required tests "
    "written?\n"
    "- Correctness — obvious logic errors, missing error handling, security or "
    "concurrency problems.\n\n"
    "Return the verdict `request_changes` only when:\n"
    "- a functional requirement from the spec is not met,\n"
    "- the implementation conflicts with the architecture,\n"
    "- tests that were explicitly required are missing,\n"
    "- there is a clear correctness bug.\n"
    "In that case, write in the summary — specifically and actionably — what's "
    "wrong and what to fix; the development step will go into another round "
    "based on it.\n\n"
    "Don't return `request_changes` over stylistic nitpicks, subjective "
    "preferences, out-of-scope improvements, or missing documentation. When "
    "the implementation is sound, return `done` (optionally with non-binding "
    "cleanup suggestions)."
)

# Step → (persona, default tools). The tools are names of Claude Code tools,
# which `claude_cli` passes through via `--allowedTools`.
AGENT_PERSONAS: dict[str, tuple[str, list[str]]] = {
    "plan": (_PLAN_PERSONA, ["Read", "Grep", "Glob"]),
    "design": (_DESIGN_PERSONA, ["Read", "Grep", "Glob"]),
    "architecture": (_ARCHITECTURE_PERSONA, ["Read", "Grep", "Glob", "Bash"]),
    "development": (
        _DEVELOPMENT_PERSONA,
        ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"],
    ),
    "review": (_REVIEW_PERSONA, ["Read", "Grep", "Glob", "Bash"]),
}


def _agent_persona(step: str) -> str:
    """Step persona. Known steps have a persona carried over from v1; an unknown
    step gets a generic instruction (the rest of the boilerplate is supplied by
    `compose_prompt`)."""
    known = AGENT_PERSONAS.get(step)
    if known is not None:
        return known[0]
    return (
        f"You are the agent for the '{step}' step. Read the artifacts of the "
        f"previous steps in your working directory, do the step's work, and "
        f"write the output where the task prompt directs you."
    )


def _agent_tools(step: str) -> list[str]:
    """Default tools for the step; an unknown step gets none."""
    known = AGENT_PERSONAS.get(step)
    return list(known[1]) if known is not None else []


def _allowed_outcomes_for(workflow, step: str) -> list[str]:
    """Unique outcomes of edges leaving the step (in definition order)."""
    seen: list[str] = []
    for transition in workflow.transitions:
        if transition.from_step == step and transition.on not in seen:
            seen.append(transition.on)
    return seen


def _write_default_agents(layout: HarnessLayout, workflow) -> None:
    layout.agents.mkdir(parents=True, exist_ok=True)
    for step in workflow.steps():
        if step == LANDING_STEP:
            continue
        path = layout.agents / f"{step}.json"
        if path.exists():
            continue
        definition = {
            "prompt": _agent_persona(step),
            "model": None,
            "fallback_model": None,
            "allowed_tools": _agent_tools(step),
            "allowed_outcomes": _allowed_outcomes_for(workflow, step),
        }
        path.write_text(
            json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _write_default_repos(layout: HarnessLayout) -> None:
    if not layout.repos.exists():
        layout.repos.write_text(
            json.dumps({}, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _submit(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"error: {root} is not initialized, run `harness init`", file=sys.stderr)
        return 2

    try:
        data = json.loads(args.data) if args.data else {}
    except json.JSONDecodeError as error:
        print(f"error: --data is not valid JSON: {error}", file=sys.stderr)
        return 2

    task = Task(
        id=new_task_id(),
        workflow_template=args.workflow,
        created=SystemClock().now(),
        repository=args.repo,
        worktree=args.worktree,
        data=data,
    )
    (layout.tasks / f"{task.id}.json").write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(task.id)
    return 0


def _github_sources(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    slug_of=github_slug,
    client: GithubClient | None = None,
) -> list[TaskSource]:
    """One `GithubTaskSource` per repo in `repos.json` that has a GitHub origin.

    The slug is derived from each clone's git origin (`slug_of`); a repo with no
    GitHub origin is skipped with a warning. Without `GITHUB_TOKEN` (and no
    injected client) there are no sources and the harness runs on `harness
    submit` alone."""
    if client is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return []
        client = HttpGithubClient(token)

    worktree_root = args.worktree_root or str(root / "worktrees")
    sources: list[TaskSource] = []
    for name in registry.names():
        slug = slug_of(registry.resolve(name))
        if slug is None:
            print(f"warning: {name} has no GitHub origin, not scanned", file=sys.stderr)
            continue
        sources.append(
            GithubTaskSource(
                client=client,
                clock=SystemClock(),
                repo=slug,
                workflow=args.github_workflow,
                repository=name,
                worktree_root=worktree_root,
                select_label=args.github_label,
                step_labels=DEFAULT_STEP_LABELS,
            )
        )
    return sources


def service_path_entries(harness: Path) -> list[str]:
    """`PATH` for the service: the venv's bin first, then the usual locations.

    launchd starts a process with a minimal `PATH`, so `git`, `gh` and `claude`
    would all be missing. `~/.npm-global/bin` and `~/.local/bin` are here
    because that is where a user-installed `claude` and `python3.11` land.
    """
    home = Path.home()
    return [
        str(harness.parent),
        str(home / ".npm-global" / "bin"),
        str(home / ".local" / "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]


def installed_commit() -> str | None:
    """The git commit a `uv tool install git+...` came from, or None.

    `pyproject.toml` carries a single static version, so two different installs
    both report `0.1.0` and `--version` alone cannot tell you whether an update
    landed. pip/uv record the source in `direct_url.json` (PEP 610); the commit
    from there is the only honest answer.
    """
    try:
        raw = metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        commit = json.loads(raw).get("vcs_info", {}).get("commit_id")
    except json.JSONDecodeError:
        return None
    return commit[:7] if isinstance(commit, str) and commit else None


def version_string() -> str:
    """The installed version, with the source commit when there is one."""
    try:
        version = metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:  # running from source without an install
        return "unknown (not installed)"
    commit = installed_commit()
    return f"{version} (git {commit})" if commit else version


def uv_shim() -> Path:
    """Where `uv tool install` puts the stable `harness` shim."""
    return Path.home() / ".local" / "bin" / "harness"


def service_entry_point() -> Path:
    """Absolute path to the `harness` the service should exec.

    Prefers uv's shim: `uv tool upgrade` rebuilds the tool environment, but the
    shim path is the contract uv keeps stable, so an upgrade never invalidates
    an installed LaunchAgent. Falls back to this environment's own script for a
    from-source venv.

    `sys.prefix` is the venv root. `sys.executable` is not usable here because
    resolving it follows the venv's python symlink out to the base interpreter
    (with uv-managed CPython that lands in `~/.local/share/uv/python/...`, where
    no `harness` script exists). `sys.argv[0]` is no good either — it is
    whatever the caller typed, or `pytest`.
    """
    shim = uv_shim()
    if shim.exists():
        return shim
    return Path(sys.prefix) / "bin" / "harness"


def uv_executable() -> Path | None:
    """The `uv` binary, or None when it is not installed.

    Checked explicitly rather than relying on `PATH`: `harness update` may be
    invoked from the service context, whose `PATH` we build ourselves.
    """
    found = shutil.which("uv")
    if found:
        return Path(found)
    candidate = Path.home() / ".local" / "bin" / "uv"
    return candidate if candidate.exists() else None


def installed_version_report() -> str:
    """Ask the installed `harness` script what it is now.

    Called right after an upgrade, from the process the upgrade replaced — so
    it must shell out rather than read its own already-stale metadata.
    """
    entry = service_entry_point()
    if not entry.is_file():
        return "harness (installed; run `harness --version` to confirm)"
    result = subprocess.run(
        [str(entry), "--version"], capture_output=True, text=True, check=False
    )
    reported = result.stdout.strip()
    if result.returncode != 0 or not reported:
        return "harness (installed; run `harness --version` to confirm)"
    return reported


def _update(args: argparse.Namespace) -> int:
    """Upgrade the installed harness in place via `uv tool upgrade`."""
    uv = uv_executable()
    if uv is None:
        print(
            "error: uv is not installed — install it with\n"
            "  curl -LsSf https://astral.sh/uv/install.sh | sh",
            file=sys.stderr,
        )
        return 2

    result = subprocess.run(
        [str(uv), "tool", "upgrade", PACKAGE_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print(f"error: uv tool upgrade failed (exit {result.returncode})", file=sys.stderr)
        return 1

    # This process is still the *old* code, so version_string() here would
    # report the version we just replaced. Ask the freshly installed script.
    print(f"\nnow: {installed_version_report()}")
    print(
        "the running service still has the previous version — restart it with\n"
        "  launchctl kickstart -k gui/$(id -u)/com.harness"
    )
    return 0


def _require_macos() -> str | None:
    """The error message for a non-macOS host, or None when launchd is available."""
    if sys.platform != "darwin":
        return (
            f"`harness service` needs macOS launchd; this is {sys.platform}. "
            "Run `harness run` under your own supervisor (systemd, supervisord)."
        )
    return None


def _service_install(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"error: {root} is not initialized, run `harness init`", file=sys.stderr)
        return 2

    harness = service_entry_point()
    if not harness.is_file():
        print(
            f"error: cannot locate the harness entry point at {harness} — "
            "install the package into this environment first",
            file=sys.stderr,
        )
        return 2

    home = Path.home()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    wrapper = root / "harness-run.sh"
    wrapper.write_text(
        wrapper_script(
            harness=harness,
            root=root,
            api_port=args.api_port,
            path_entries=service_path_entries(harness),
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    target = plist_path(home, args.label)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        plist_bytes(
            label=args.label,
            wrapper=wrapper,
            working_dir=root,
            log_dir=log_dir,
            home=home,
        )
    )

    try:
        load(os.getuid(), target, args.label)
    except ServiceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"service {args.label} installed and started")
    print(f"  wrapper: {wrapper}")
    print(f"  plist:   {target}")
    print(f"  logs:    {log_dir}/harness.log, {log_dir}/harness.error.log")
    print(f"  board:   http://127.0.0.1:{args.api_port}/")
    return 0


def _service_uninstall(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    was_loaded = unload(os.getuid(), args.label)
    target = plist_path(Path.home(), args.label)
    existed = target.exists()
    target.unlink(missing_ok=True)

    if not was_loaded and not existed:
        print(f"service {args.label} was not installed")
        return 0
    print(f"service {args.label} removed")
    return 0


def _service_status(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    target = plist_path(Path.home(), args.label)
    report = status(os.getuid(), args.label)
    print(f"label:  {args.label}")
    print(f"plist:  {target} ({'present' if target.exists() else 'missing'})")
    if report is None:
        print("state:  not loaded")
        return 1
    for line in report.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "pid =", "last exit code =")):
            print(f"        {stripped}")
    print("state:  loaded")
    return 0


def _build_forge(kind: str, root: Path, registry: RepositoryRegistry | None = None):
    """The forge for a real run. `fake` writes into `<root>/forge/prs.json`.

    `github` without a `GITHUB_TOKEN` yields a forge that fails at `land` rather
    than one that refuses to start: the harness stays usable for `harness
    submit`, and the operator sees exactly which task needs the token.
    """
    if kind == "fake":
        return FakeForge(root / "forge")
    token = os.environ.get("GITHUB_TOKEN")
    return GithubForge(
        HttpGithubClient(token) if token else None, registry=registry
    )


def _build_merge_checker(args: argparse.Namespace) -> MergeChecker | None:
    """A live `MergeChecker`, gated on `GITHUB_TOKEN` — same condition as
    `GithubForge`, independent of `--forge`. Reconciliation only ever exists
    for tasks a real forge landed: `--forge fake` synthesizes non-GitHub
    `repo` placeholders (`local/<branch>`) that a real merge check can't
    resolve, so a fake-forge run must never get a live checker.
    """
    token = os.environ.get("GITHUB_TOKEN")
    return GithubMergeChecker(HttpGithubClient(token)) if token else None


def _run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    # The real run: agent behind `claude -p`, git worktree under a shared root,
    # repo name→path from `repos.json`, personas from `agents/`, artifacts
    # versioned in the worktree, and a real GitHub forge (`--forge fake` swaps
    # in prs.json for offline runs and tests).
    registry = FilesystemRepositoryRegistry(layout.repos)
    # `--agent dummy` leaves catalog/runner unset, which makes `build()` fall
    # back to DummyBehavior for the step queues while everything around it stays
    # real: real worktree, real commits, real push, real PR. That exercises the
    # whole pipeline on a machine where `claude` is unavailable or unauthenticated.
    use_agent = args.agent == "claude"
    catalog = FilesystemAgentCatalog(layout.agents) if use_agent else None
    runner = ClaudeCliRunner() if use_agent else None
    workspace = GitWorkspace(registry, layout.worktrees)
    artifact_view = WorktreeArtifactView(layout.worktrees)
    forge = _build_forge(args.forge, root, registry)
    sources = _github_sources(args, root, registry)
    merge_checker = _build_merge_checker(args)
    try:
        harness = build(
            root,
            args.workflow,
            workspace=workspace,
            forge=forge,
            runner=runner,
            catalog=catalog,
            artifact_view=artifact_view,
            agent_timeout=args.agent_timeout,
            sources=sources or None,
            merge_checker=merge_checker,
            delay=args.delay,
            request_changes_once_at=args.request_changes_at,
        )
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        asyncio.run(
            serve(
                harness,
                args.api_port,
                args.poll,
                args.source_poll,
                args.reconcile_poll,
            )
        )
    except KeyboardInterrupt:
        return 0
    return 0


async def serve(
    harness,
    port: int,
    poll_interval: float,
    source_interval: float = 30.0,
    reconcile_interval: float = 300.0,
) -> None:
    """The loop and the board in a single event loop."""
    stop = asyncio.Event()
    loop = asyncio.create_task(
        harness.run(
            poll_interval=poll_interval,
            source_interval=source_interval,
            reconcile_interval=reconcile_interval,
            stop=stop,
        )
    )

    if port == 0:
        await loop
        return

    app = create_app(
        view=harness.projection,
        artifacts=harness.artifacts,
        output=harness.stage_output,
        control=harness.control,
        clock=SystemClock(),
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = asyncio.create_task(uvicorn.Server(config).serve())
    try:
        done, _ = await asyncio.wait({loop, server}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # propagate the exception if either task crashed
    finally:
        stop.set()
        server.cancel()
        await asyncio.gather(loop, server, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness")
    parser.add_argument(
        "--version",
        action="version",
        version=f"harness {version_string()}",
    )
    # --root and --workflow are declared only on the subcommands (see below). A
    # declaration on the top-level parser would be dead: argparse's
    # _SubParsersAction overwrites the parent's namespace with the subcommand's
    # values, so a --root given before the subcommand would be silently dropped
    # and the harness would reach for the wrong (default) root. The subcommand
    # is required=True, so this collision always occurs.
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create the directory tree")
    init.add_argument("--root", default=None)
    init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    init.set_defaults(handler=_init)

    submit = subparsers.add_parser("submit", help="submit a new task")
    submit.add_argument("--root", default=None)
    submit.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    submit.add_argument("--repo", default=None)
    submit.add_argument("--worktree", default=None, help="path to the task's worktree")
    submit.add_argument("--data", default=None, help="JSON payload")
    submit.set_defaults(handler=_submit)

    run = subparsers.add_parser("run", help="start the orchestration loop")
    run.add_argument("--root", default=None)
    run.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    run.add_argument("--delay", type=float, default=5.0)
    run.add_argument("--poll", type=float, default=0.2)
    run.add_argument(
        "--source-poll",
        type=float,
        default=30.0,
        dest="source_poll",
        help="interval (s) for polling the task source (e.g. GitHub); kept "
        "well above --poll to respect remote API rate limits",
    )
    run.add_argument(
        "--reconcile-poll",
        type=float,
        default=300.0,
        dest="reconcile_poll",
        help="interval (s) for checking done tasks' PR merge status and "
        "archiving them once merged; deliberately long to respect GitHub "
        "rate limits",
    )
    run.add_argument("--agent-timeout", type=float, default=600.0, dest="agent_timeout")
    run.add_argument("--request-changes-at", default=None, dest="request_changes_at")
    run.add_argument(
        "--github-label",
        default="harness:todo",
        help="label that selects issues to ingest",
    )
    run.add_argument("--github-workflow", default=DEFAULT_WORKFLOW)
    run.add_argument("--worktree-root", default=None, help="root of the task worktrees")
    run.add_argument(
        "--api-port",
        type=int,
        default=8420,
        help="board port; 0 disables the board",
    )
    run.add_argument(
        "--agent",
        choices=("claude", "dummy"),
        default="claude",
        help="who does the work in each step (dummy: no claude, for testing the pipeline)",
    )
    run.add_argument(
        "--forge",
        choices=("github", "fake"),
        default="github",
        help="where landing proposes the change (default: real GitHub)",
    )
    run.set_defaults(handler=_run)

    service = subparsers.add_parser(
        "service", help="run the harness as a background service (macOS launchd)"
    )
    service_actions = service.add_subparsers(dest="action", required=True)

    service_install = service_actions.add_parser(
        "install", help="write the LaunchAgent and start it"
    )
    service_install.add_argument("--root", default=None)
    service_install.add_argument("--label", default=DEFAULT_LABEL)
    service_install.add_argument(
        "--api-port", type=int, default=8420, dest="api_port"
    )
    service_install.set_defaults(handler=_service_install)

    service_uninstall = service_actions.add_parser(
        "uninstall", help="stop the service and remove its LaunchAgent"
    )
    service_uninstall.add_argument("--label", default=DEFAULT_LABEL)
    service_uninstall.set_defaults(handler=_service_uninstall)

    service_status = service_actions.add_parser(
        "status", help="report whether the service is loaded"
    )
    service_status.add_argument("--root", default=None)
    service_status.add_argument("--label", default=DEFAULT_LABEL)
    service_status.set_defaults(handler=_service_status)

    update = subparsers.add_parser(
        "update", help="upgrade the installed harness via uv"
    )
    update.set_defaults(handler=_update)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
