"""The operator's surface.

Every command fails with a readable message and a non-zero exit, never a
traceback -- an operator debugging a stuck queue at 2am should not have to read
a Python stack.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from agentharness.config import Config, load_config
from agentharness.dispatch.dispatcher import Dispatcher
from agentharness.git import merge as gitmerge
from agentharness.queue.filesystem import FilesystemQueue
from agentharness.registry.agents import AgentRegistry, AgentValidationError
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.executor import LocalExecutor
from agentharness.runner.runner import Runner
from agentharness.store.runs import RunStore

app = typer.Typer(help="Stateless multi-agent orchestration over claude -p", no_args_is_help=True)
agents_app = typer.Typer(help="Inspect and validate agent definitions", no_args_is_help=True)
repos_app = typer.Typer(help="Manage the repositories agents operate on", no_args_is_help=True)
queue_app = typer.Typer(help="Inspect queues and dead letters", no_args_is_help=True)
runs_app = typer.Typer(help="Inspect runs", no_args_is_help=True)
trace_app = typer.Typer(help="Inspect traces", no_args_is_help=True)
schedule_app = typer.Typer(help="Manage schedules", no_args_is_help=True)

app.add_typer(agents_app, name="agents")
app.add_typer(repos_app, name="repos")
app.add_typer(queue_app, name="queue")
app.add_typer(runs_app, name="runs")
app.add_typer(trace_app, name="trace")
app.add_typer(schedule_app, name="schedule")


def fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def ctx() -> tuple[Config, AgentRegistry, RepoRegistry, RunStore, FilesystemQueue]:
    cfg = load_config()
    if not cfg.home.exists():
        fail(f"harness home {cfg.home} does not exist -- run 'agentharness init' first")
    repos = RepoRegistry(cfg)
    try:
        known = {r.repo_id for r in repos.list()}
        agents = AgentRegistry.load(cfg.agents_dir, known_repos=known or None)
    except AgentValidationError as exc:
        fail(str(exc))
    except FileNotFoundError:
        fail(f"no agents directory at {cfg.agents_dir}")
    return cfg, agents, repos, RunStore(cfg.db_path), FilesystemQueue(cfg.queues_dir)


def build_dispatcher(cfg, agents, repos, store, queue) -> Dispatcher:
    runner = Runner(cfg, agents, repos, store, LocalExecutor(cfg.claude_binary))
    return Dispatcher(cfg, agents, repos, queue, store, runner)


CONFIG_TEMPLATE = """\
# agentharness configuration. Every key is optional; defaults are shown.

# max_concurrency: 3               # simultaneous claude -p processes
# lease_timeout_seconds: 1800      # a lease older than this is reclaimed
# poll_interval_seconds: 1.0
# branch_retention_days: 30        # gc window for merged run/* branches
# claude_binary: claude
# default_integration_branch: harness/integration
# default_base_branch: main
# retry_base_seconds: 30.0
# rate_limit_initial_backoff_seconds: 60
# rate_limit_max_backoff_seconds: 3600
"""


@app.command()
def init() -> None:
    """Create the harness home, the scratch repo, and a starter config."""
    cfg = load_config()
    cfg.ensure_dirs()
    config_file = cfg.home / "config.yaml"
    if not config_file.exists():
        config_file.write_text(CONFIG_TEMPLATE)
    RunStore(cfg.db_path)
    RepoRegistry(cfg).ensure_scratch()
    typer.echo(f"initialised harness home at {cfg.home}")


@app.command()
def serve(
    web: bool = typer.Option(True, "--web/--no-web", help="Serve the dashboard"),
    port: int = typer.Option(8787, help="Dashboard port"),
) -> None:
    """Run the dispatcher, scheduler, and dashboard until interrupted."""
    cfg, agents, repos, store, queue = ctx()
    dispatcher = build_dispatcher(cfg, agents, repos, store, queue)

    async def main() -> None:
        tasks = [asyncio.create_task(dispatcher.run_forever())]

        try:
            from agentharness.scheduler.scheduler import Scheduler

            tasks.append(asyncio.create_task(Scheduler(store, dispatcher).run_forever()))
        except ImportError:
            typer.echo("scheduler unavailable; running dispatcher only", err=True)

        if web:
            try:
                import uvicorn

                from agentharness.web.app import create_app

                api = create_app(cfg, queue, store, dispatcher.limiter, dispatcher.gate)
                server = uvicorn.Server(
                    uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning")
                )
                tasks.append(asyncio.create_task(server.serve()))
                typer.echo(f"dashboard on http://127.0.0.1:{port}")
            except ImportError:
                typer.echo("dashboard unavailable; running headless", err=True)

        typer.echo(f"dispatching with max_concurrency={cfg.max_concurrency}")
        await asyncio.gather(*tasks)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        dispatcher.stop()
        typer.echo("stopped")


@app.command()
def submit(
    agent: str,
    intent: str,
    repo: str = typer.Option(None, help="Managed repo_id; omit for the scratch repo"),
    payload: str = typer.Option(None, help="Inline JSON payload"),
    priority: int = typer.Option(5, help="Lower runs first"),
) -> None:
    """Enqueue a root task."""
    cfg, agents, repos, store, queue = ctx()

    parsed = {}
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            fail(f"--payload is not valid JSON: {exc}")

    if repo is not None:
        try:
            repos.get(repo)
        except KeyError:
            fail(f"no managed repo {repo!r} -- add it with 'agentharness repos add'")

    dispatcher = build_dispatcher(cfg, agents, repos, store, queue)
    try:
        task = dispatcher.submit(agent, intent, repo=repo, payload=parsed, priority=priority)
    except KeyError:
        fail(f"no such agent {agent!r} -- see 'agentharness agents list'")

    typer.echo(f"task_id  {task.task_id}")
    typer.echo(f"trace_id {task.trace_id}")


@agents_app.command("list")
def agents_list() -> None:
    """List every registered agent and where it may route work."""
    _, agents, _, _, _ = ctx()
    if not agents.names():
        typer.echo("no agents defined")
        return
    for name in agents.names():
        a = agents.get(name)
        targets = ", ".join(a.can_handoff_to) or "-- terminal --"
        typer.echo(f"{name:20} -> {targets}")


@agents_app.command("show")
def agents_show(name: str) -> None:
    """Print one agent's full definition."""
    _, agents, _, _, _ = ctx()
    try:
        typer.echo(agents.get(name).model_dump_json(indent=2))
    except KeyError:
        fail(f"no such agent {name!r}")


@agents_app.command("validate")
def agents_validate() -> None:
    """Validate every agent definition, including routing and repo references."""
    _, agents, _, _, _ = ctx()
    typer.echo(f"ok: {len(agents.names())} agent(s) valid")


@repos_app.command("add")
def repos_add(
    repo_id: str,
    url: str,
    integration_branch: str = typer.Option(None),
    base_branch: str = typer.Option(None),
) -> None:
    """Register a repo and create its bare mirror."""
    cfg = load_config()
    cfg.ensure_dirs()
    try:
        repo = RepoRegistry(cfg).add(repo_id, url, integration_branch, base_branch)
    except Exception as exc:  # noqa: BLE001 -- surface git failures readably
        fail(f"could not register {repo_id!r}: {exc}")
    typer.echo(f"registered {repo.repo_id} (integration branch {repo.integration_branch})")


@repos_app.command("list")
def repos_list() -> None:
    """List managed repos."""
    _, _, repos, _, _ = ctx()
    entries = repos.list()
    if not entries:
        typer.echo("no repos registered")
        return
    for r in entries:
        typer.echo(f"{r.repo_id:20} {r.base_branch} -> {r.integration_branch}  {r.url}")


@repos_app.command("sync")
def repos_sync(repo_id: str) -> None:
    """Fetch the latest refs into a repo's mirror."""
    _, _, repos, _, _ = ctx()
    try:
        repos.sync(repo_id)
    except KeyError:
        fail(f"no managed repo {repo_id!r}")
    typer.echo(f"synced {repo_id}")


@queue_app.command("list")
def queue_list() -> None:
    """Show queue depth and dead-letter count per agent."""
    _, agents, _, _, queue = ctx()
    typer.echo(f"{'AGENT':20} {'PENDING':>8} {'DEAD':>6}")
    for name in agents.names():
        typer.echo(f"{name:20} {queue.depth(name):>8} {len(queue.list_dead(name)):>6}")


@queue_app.command("peek")
def queue_peek(agent: str) -> None:
    """Show the tasks waiting on one agent's queue."""
    _, _, _, _, queue = ctx()
    typer.echo(f"{queue.depth(agent)} pending on {agent}")


@queue_app.command("dead")
def queue_dead(agent: str) -> None:
    """List dead-lettered tasks for an agent."""
    _, _, _, _, queue = ctx()
    dead = queue.list_dead(agent)
    if not dead:
        typer.echo(f"no dead letters for {agent}")
        return
    for t in dead:
        typer.echo(f"{t.task_id}  {t.intent}  attempt={t.attempt}  trace={t.trace_id}")


@queue_app.command("replay")
def queue_replay(agent: str, task_id: str) -> None:
    """Move a dead-lettered task back onto its queue."""
    _, _, _, _, queue = ctx()
    if not queue.replay_dead(agent, task_id):
        fail(f"no dead-lettered task {task_id!r} for agent {agent!r}")
    typer.echo(f"replayed {task_id}")


@runs_app.command("list")
def runs_list(limit: int = typer.Option(20)) -> None:
    """List recent runs."""
    _, _, _, store, _ = ctx()
    typer.echo(f"{'RUN':28} {'AGENT':16} {'STATUS':8} {'MS':>7} {'COST':>7}")
    for r in store.recent_runs(limit):
        cost = f"{r.total_cost_usd:.4f}" if r.total_cost_usd is not None else "-"
        typer.echo(f"{r.run_id:28} {r.agent:16} {r.status:8} {r.duration_ms or 0:>7} {cost:>7}")


@runs_app.command("show")
def runs_show(run_id: str) -> None:
    """Print one run record."""
    _, _, _, store, _ = ctx()
    run = store.get_run(run_id)
    if run is None:
        fail(f"no run {run_id!r}")
    typer.echo(run.model_dump_json(indent=2))


@trace_app.command("show")
def trace_show(trace_id: str) -> None:
    """Show every run in a trace, in order."""
    _, _, _, store, _ = ctx()
    runs = store.trace_runs(trace_id)
    if not runs:
        fail(f"no runs for trace {trace_id!r}")
    total = 0.0
    for r in runs:
        total += r.total_cost_usd or 0.0
        typer.echo(f"{r.started_at:%H:%M:%S} {r.agent:16} {r.status:8} {r.duration_ms or 0:>7}ms")
    typer.echo(f"{len(runs)} run(s), total cost ${total:.4f}")


@schedule_app.command("list")
def schedule_list() -> None:
    """List durable schedules."""
    _, _, _, store, _ = ctx()
    schedules = store.list_schedules()
    if not schedules:
        typer.echo("no schedules defined")
        return
    for s in schedules:
        state = "enabled" if s.enabled else "disabled"
        typer.echo(f"{s.schedule_id:20} {s.cron:16} {s.agent:16} {s.intent:20} {state}")


@schedule_app.command("add")
def schedule_add(
    schedule_id: str,
    cron: str,
    agent: str,
    intent: str,
    repo: str = typer.Option(None),
    payload: str = typer.Option(None, help="Inline JSON payload"),
) -> None:
    """Add or replace a schedule."""
    from agentharness.models import ScheduleDef

    cfg, agents, repos, store, queue = ctx()
    if agent not in agents.names():
        fail(f"no such agent {agent!r}")

    parsed = {}
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            fail(f"--payload is not valid JSON: {exc}")

    from agentharness.scheduler.scheduler import Scheduler

    scheduler = Scheduler(store, build_dispatcher(cfg, agents, repos, store, queue))
    try:
        scheduler.add(
            ScheduleDef(
                schedule_id=schedule_id, cron=cron, agent=agent, intent=intent,
                repo=repo, payload=parsed,
            )
        )
    except ValueError as exc:
        fail(str(exc))
    typer.echo(f"scheduled {schedule_id}: {cron} -> {agent}/{intent}")


@schedule_app.command("remove")
def schedule_remove(schedule_id: str) -> None:
    """Delete a schedule."""
    _, _, _, store, _ = ctx()
    store.delete_schedule(schedule_id)
    typer.echo(f"removed {schedule_id}")


@app.command()
def gc(
    days: int = typer.Option(None, help="Retention window; defaults to config"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List candidates without deleting"),
) -> None:
    """Delete merged run/* branches older than the retention window."""
    cfg, _, repos, _, _ = ctx()
    window = days if days is not None else cfg.branch_retention_days

    for repo in repos.list():
        mirror = repos.mirror_path(repo.repo_id)
        if not mirror.exists():
            continue
        if dry_run:
            from agentharness.git.mirror import list_branches

            candidates = list_branches(mirror, "run/*")
            typer.echo(f"{repo.repo_id}: {len(candidates)} run branch(es) present")
            continue
        deleted = gitmerge.gc_run_branches(mirror, window, cfg)
        typer.echo(f"{repo.repo_id}: deleted {len(deleted)} branch(es)")


if __name__ == "__main__":
    app()
