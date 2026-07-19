"""CLI harnessu."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from harness.app import HarnessLayout, build
from harness.drivers.system_clock import SystemClock
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.workflows import WorkflowNotFound

DEFAULT_WORKFLOW = "default"

DEFAULT_DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "architecture"},
        {"from": "architecture", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}


def _root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    return Path(os.environ.get("HARNESS_HOME", "~/.harness")).expanduser()


def _invalid_workflow_name(name: str) -> bool:
    """Stejné pravidlo jako FilesystemWorkflowRepository.get (drivers/fs_workflows.py) —
    jméno nesmí obsahovat cestový oddělovač a nesmí to být "", "." nebo "..".
    Duplikováno zde, protože potřebujeme ověřit jméno ještě před zápisem
    definičního souboru, tedy dřív, než se vůbec dostaneme k repository."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


def _init(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)

    if _invalid_workflow_name(args.workflow):
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

    print(f"harness připraven v {root}")
    print(f"kroky: {', '.join(harness.workflow.steps())}")
    return 0


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
        data=data,
    )
    (layout.tasks / f"{task.id}.json").write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(task.id)
    return 0


def _run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    try:
        harness = build(
            root,
            args.workflow,
            delay=args.delay,
            request_changes_once_at=args.request_changes_at,
        )
    except WorkflowNotFound as error:
        print(f"chyba: {error}", file=sys.stderr)
        return 2

    try:
        asyncio.run(harness.run(poll_interval=args.poll))
    except KeyboardInterrupt:
        return 0
    return 0


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
    submit.add_argument("--data", default=None, help="JSON payload")
    submit.set_defaults(handler=_submit)

    run = subparsers.add_parser("run", help="spusť orchestrační smyčku")
    run.add_argument("--root", default=None)
    run.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    run.add_argument("--delay", type=float, default=5.0)
    run.add_argument("--poll", type=float, default=0.2)
    run.add_argument("--request-changes-at", default=None, dest="request_changes_at")
    run.set_defaults(handler=_run)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
