"""CLI harnessu."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
from harness.drivers.git_workspace import GitWorkspace
from harness.drivers.git_workspace import git_remote_url
from harness.drivers.github_client import HttpGithubClient
from harness.drivers.github_source import GithubTaskSource, slug_from_source
from harness.drivers.system_clock import SystemClock
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.repos import RepositoryNotFound, RepositoryRegistry
from harness.ports.source import TaskSource
from harness.ports.workflows import WorkflowNotFound

DEFAULT_WORKFLOW = "default"

# Rozumná coarse mapa kroků výchozího workflow na labely. Ostatní kroky bez
# labelu → míň šumu. Je to jen default, ne zákon.
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
        print(f"chyba: neplatné jméno workflow: {args.workflow!r}", file=sys.stderr)
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
        print(f"chyba: {error}", file=sys.stderr)
        return 2

    _write_default_agents(layout, harness.workflow)
    _write_default_repos(layout)

    print(f"harness připraven v {root}")
    print(f"kroky: {', '.join(harness.workflow.steps())}")
    return 0


def _agent_persona(step: str) -> str:
    return (
        f"Jsi agent kroku '{step}'. Nejdřív si přečti existující artefakty "
        f"předchozích kroků v adresáři .artifacts/<task_id>/ ve svém pracovním "
        f"adresáři. Svůj výstup zapiš do souboru, na který tě nasměruje prompt "
        f"úkolu. Až budeš hotov, skonči přesně strojově čitelným verdikt blokem "
        f'```json {{"outcome": "...", "summary": "..."}}``` a ničím za ním.'
    )


def _allowed_outcomes_for(workflow, step: str) -> list[str]:
    """Unikátní outcomes hran vycházejících z kroku (v pořadí definice)."""
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
            "allowed_tools": [],
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
        print(f"chyba: {root} není inicializovaný, spusť `harness init`", file=sys.stderr)
        return 2

    try:
        data = json.loads(args.data) if args.data else {}
    except json.JSONDecodeError as error:
        print(f"chyba: --data není platný JSON: {error}", file=sys.stderr)
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


def _github_slug_and_repository(
    args: argparse.Namespace, root: Path, registry: RepositoryRegistry
) -> tuple[str, str] | None:
    """Odvoď (GitHub slug `owner/name`, jméno repa pro task) z argumentů.

    Preferuje `--repo <jméno>`: repo se dohledá v `repos.json` (lokální složka)
    a jeho zdroj se **přečte z git remotu té složky** — URL už v checkoutu je,
    nedrží se zvlášť. `task.repository` je pak logické jméno, takže `GitWorkspace`
    z registru vytáhne tutéž složku. `--github-repo` zůstává explicitním override
    slugu (fork, jiný remote) i cestou pro repo mimo registr."""
    if args.repo:
        try:
            path = registry.resolve(args.repo)
        except RepositoryNotFound as error:
            print(f"varování: {error}, GitHub zdroj vypnut", file=sys.stderr)
            return None
        slug = args.github_repo
        if not slug:
            url = git_remote_url(path)
            if not url:
                print(
                    f"varování: repo {args.repo!r} ({path}) nemá git remote "
                    "'origin', GitHub zdroj vypnut",
                    file=sys.stderr,
                )
                return None
            try:
                slug = slug_from_source(url)
            except ValueError as error:
                print(f"varování: {error}, GitHub zdroj vypnut", file=sys.stderr)
                return None
        return slug, args.repo

    if args.github_repo:
        # Legacy: repo mimo registr — slug je přímo argument, task.repository
        # ukazuje na klasickou složku `<root>/repo`.
        return args.github_repo, str(root / "repo")

    return None


def _github_source(
    args: argparse.Namespace, root: Path, registry: RepositoryRegistry
) -> TaskSource | None:
    """Zdroj z GitHub Issues, když je zadané repo (`--repo`/`--github-repo`) a
    `GITHUB_TOKEN`. Jinak None — harness běží jako dřív (jen `harness submit`)."""
    resolved = _github_slug_and_repository(args, root, registry)
    if resolved is None:
        return None
    slug, repository = resolved

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "varování: GitHub zdroj bez GITHUB_TOKEN, zdroj vypnut",
            file=sys.stderr,
        )
        return None
    worktree_root = args.worktree_root or str(root / "worktrees")
    return GithubTaskSource(
        client=HttpGithubClient(token),
        clock=SystemClock(),
        repo=slug,
        workflow=args.github_workflow,
        repository=repository,
        worktree_root=worktree_root,
        select_label=args.github_label,
        step_labels=DEFAULT_STEP_LABELS,
    )


def _run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    # Skutečný běh fáze 3: agent za `claude -p`, git worktree pod společným
    # kořenem, repo jméno→cesta z `repos.json`, persony z `agents/`, artefakty
    # versované ve worktree, fake forge (PR do prs.json). GitHub driver je čistý
    # follow-up — záměna forge driveru.
    registry = FilesystemRepositoryRegistry(layout.repos)
    catalog = FilesystemAgentCatalog(layout.agents)
    runner = ClaudeCliRunner()
    workspace = GitWorkspace(registry, layout.worktrees)
    artifact_view = WorktreeArtifactView(layout.worktrees)
    forge = FakeForge(root / "forge")
    source = _github_source(args, root, registry)
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
            sources=[source] if source else None,
            delay=args.delay,
            request_changes_once_at=args.request_changes_at,
        )
    except WorkflowNotFound as error:
        print(f"chyba: {error}", file=sys.stderr)
        return 2

    try:
        asyncio.run(serve(harness, args.api_port, args.poll))
    except KeyboardInterrupt:
        return 0
    return 0


async def serve(harness, port: int, poll_interval: float) -> None:
    """Smyčka a board v jednom event loopu."""
    stop = asyncio.Event()
    loop = asyncio.create_task(harness.run(poll_interval=poll_interval, stop=stop))

    if port == 0:
        await loop
        return

    app = create_app(
        view=harness.projection, artifacts=harness.artifacts, clock=SystemClock()
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = asyncio.create_task(uvicorn.Server(config).serve())
    try:
        done, _ = await asyncio.wait({loop, server}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # propaguj výjimku, pokud některá úloha spadla
    finally:
        stop.set()
        server.cancel()
        await asyncio.gather(loop, server, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness")
    # --root a --workflow se deklarují jen na podpříkazech (viz níže). Deklarace
    # na top-level parseru by byla mrtvá: argparse's _SubParsersAction přepíše
    # jmenný prostor rodiče hodnotami z podpříkazu, takže by --root zadané
    # před podpříkazem bylo tiše zahozeno a harness by sáhl na chybný (výchozí)
    # kořen. Podpříkaz je required=True, takže tahle kolize nastane vždy.
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="založ strom adresářů")
    init.add_argument("--root", default=None)
    init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    init.set_defaults(handler=_init)

    submit = subparsers.add_parser("submit", help="vlož nový task")
    submit.add_argument("--root", default=None)
    submit.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    submit.add_argument("--repo", default=None)
    submit.add_argument("--worktree", default=None, help="cesta k worktree tasku")
    submit.add_argument("--data", default=None, help="JSON payload")
    submit.set_defaults(handler=_submit)

    run = subparsers.add_parser("run", help="spusť orchestrační smyčku")
    run.add_argument("--root", default=None)
    run.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    run.add_argument("--delay", type=float, default=5.0)
    run.add_argument("--poll", type=float, default=0.2)
    run.add_argument("--agent-timeout", type=float, default=600.0, dest="agent_timeout")
    run.add_argument("--request-changes-at", default=None, dest="request_changes_at")
    run.add_argument(
        "--repo",
        default=None,
        help="jméno repa z repos.json; GitHub zdroj se odvodí z jeho git remotu",
    )
    run.add_argument(
        "--github-repo",
        default=None,
        help="explicitní slug owner/name (override 'source' z repos.json)",
    )
    run.add_argument(
        "--github-label",
        default="harness:todo",
        help="label, kterým se vybírají issue k ingesci",
    )
    run.add_argument("--github-workflow", default=DEFAULT_WORKFLOW)
    run.add_argument("--worktree-root", default=None, help="kořen worktree tasků")
    run.add_argument(
        "--api-port",
        type=int,
        default=8420,
        help="port boardu; 0 board vypne",
    )
    run.set_defaults(handler=_run)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
