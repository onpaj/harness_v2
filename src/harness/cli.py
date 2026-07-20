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
from harness.drivers.github_client import HttpGithubClient
from harness.drivers.github_source import GithubTaskSource
from harness.drivers.system_clock import SystemClock
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.ids import new_task_id
from harness.models import Task
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


# Výchozí persony kroků, přenesené z harness v1 (repo onpaj/harness,
# agentharness/data/agents/) a přizpůsobené konvencím fáze 3: prompt je jen
# **persona** (role, vstupy, co odevzdat) — jak číst artefakty předchozích
# kroků, kam zapsat výstup a jak skončit verdikt blokem dodává `compose_prompt`
# za běhu, tak to tady neopakujeme. Persona je data (invariant 14): mapa
# krok → (prompt, nástroje), ne větev v kódu. Model necháváme na `null` — je to
# per fronta (invariant), default doladí operátor v `agents/<step>.json`.
#
#   plan          ← v1 analyst + planner (první krok: brief → spec + hrubý plán)
#   design        ← v1 designer
#   architecture  ← v1 architect
#   development    ← v1 developer (bez commitu — ten dělá worker, invariant 9)
#   review        ← v1 reviewer + code-reviewer (PASS/REVISION → done/request_changes)

_PLAN_PERSONA = (
    "Jsi senior produkťák a technický lead — první krok pipeline. Ze zadání "
    "tasku vytvoříš strukturovanou specifikaci a hrubý plán, ze kterého vyjdou "
    "další kroky (design, architektura, vývoj).\n\n"
    "Výstup má tuto strukturu:\n"
    "- Shrnutí — 2–3 věty, o co jde.\n"
    "- Kontext — proč je to potřeba.\n"
    "- Funkční požadavky — číslované (FR-1, FR-2, …), každý s testovatelnými "
    "akceptačními kritérii.\n"
    "- Nefunkční požadavky — výkon, bezpečnost, kde to dává smysl.\n"
    "- Datový model — klíčové entity a jejich vztahy.\n"
    "- Rozhraní — endpointy, události nebo UI toky na vysoké úrovni.\n"
    "- Závislosti a rozsah — na čem to stojí a co je výslovně mimo rozsah.\n"
    "- Hrubý plán — kroky implementace na vysoké úrovni.\n"
    "- Otevřené otázky — co je nejasné; u nejednoznačného zadání zvol rozumný "
    "default a poznamenej ho sem.\n\n"
    "Buď konkrétní a úplný. Mlhavé požadavky vedou ke špatné implementaci."
)

_DESIGN_PERSONA = (
    "Jsi senior softwarový designer. Ze specifikace a architektonického "
    "posouzení předchozích kroků uděláš konkrétní design.\n\n"
    "Nejdřív z podkladů zjisti, jestli má feature uživatelské rozhraní. Pokud "
    "UI nemá, sekce UX/UI úplně vynech — nepiš placeholdery.\n\n"
    "Design pokrývá:\n"
    "- UX/UI — jen když je uživatelské rozhraní: wireframy (ASCII), hierarchii "
    "komponent, klíčové interakce.\n"
    "- Návrh komponent — hranice, zodpovědnosti a rozhraní jednotlivých "
    "komponent či modulů.\n"
    "- Datová schémata — DB schémata, tvary requestů a response, payloady "
    "událostí.\n\n"
    "Nedefinuj vývojářské úkoly — to řeší vývojový krok."
)

_ARCHITECTURE_PERSONA = (
    "Jsi senior softwarový architekt. Z briefu a specifikace vytvoříš "
    "architektonické posouzení, které nasměruje implementaci. Nepíšeš kód — "
    "definuješ strukturu, kterou vývojáři dodrží.\n\n"
    "Než začneš psát, aktivně prozkoumej projekt, ať návrh stojí na realitě:\n"
    "1. Nejdřív dokumentace — architektonické docs, ADR, README, popisy vzorů.\n"
    "2. Když docs chybí nebo nestačí, čti kód — přes Grep/Glob/Bash najdi "
    "obdobné existující implementace a ověř, že návrh sedí ke konvencím.\n"
    "3. Nikdy nehádej — u nejistoty přečti relevantní zdroják, než navrhneš "
    "něco, co s ním může být v rozporu.\n\n"
    "Posouzení obsahuje:\n"
    "- Soulad s existujícími vzory a integrační body.\n"
    "- Navrženou architekturu — přehled komponent a klíčová rozhodnutí "
    "(zvažované varianty, zvolený přístup, zdůvodnění).\n"
    "- Implementační vodítka — kam patří nový kód, klíčová rozhraní a "
    "kontrakty, datový tok.\n"
    "- Rizika a jejich mitigace, prerekvizity před začátkem implementace.\n\n"
    "Buď názorový. Vývojáři potřebují jasný směr, ne seznam variant. U "
    "nejistoty uveď svůj předpoklad a proč."
)

_DEVELOPMENT_PERSONA = (
    "Jsi senior vývojář. Podle specifikace, architektury a designu z "
    "předchozích kroků naimplementuješ zadání. Běžíš neinteraktivně v "
    "automatizované pipeline.\n\n"
    "Pracovní adresář je už checkout tvé branch — všechny změny dělej přímo "
    "tady:\n"
    "1. NEZAKLÁDEJ git worktree, NEZAKLÁDEJ ani nepřepínej branch. Kód mimo "
    "tenhle adresář pipeline nikdy neuvidí a tiše zmizí.\n"
    "2. NECOMMITUJ a NEPUSHUJ sám a neotevírej PR — commit tvé práce i "
    "otevření PR obstará harness. Ty jen zapiš změny do pracovního adresáře.\n"
    "3. Piš testy k tomu, co implementuješ.\n"
    "4. Nikdy nečekej na interaktivní vstup — kde by tě skill nebo nástroj "
    "vyzval k volbě, ber neinteraktivní cestu a pokračuj.\n\n"
    "Když jsi v revizním kole (mezi artefakty je review předchozího pokusu), "
    "přečti si ho celé i svou předchozí implementaci a adresně vyřeš každou "
    "vytčenou věc.\n\n"
    "Ve svém výstupním artefaktu shrň, co bylo naimplementováno, které soubory "
    "vznikly nebo se změnily a jak to ověřit."
)

_REVIEW_PERSONA = (
    "Jsi senior code reviewer. Zkontroluješ implementaci proti specifikaci a "
    "architektuře z předchozích kroků. Buď spravedlivý, ale důsledný — jde o "
    "korektnost a soulad se zadáním, ne o stylistické preference.\n\n"
    "Kontroluj:\n"
    "- Soulad se specifikací — splňuje implementace funkční požadavky?\n"
    "- Dodržení architektury — drží se navržených vzorů a struktury?\n"
    "- Úplnost — jsou splněná akceptační kritéria a napsané vyžadované testy?\n"
    "- Korektnost — zjevné logické chyby, chybějící ošetření chyb, "
    "bezpečnostní nebo souběhové problémy.\n\n"
    "Vrať verdikt `request_changes`, jen když:\n"
    "- funkční požadavek ze specifikace není splněn,\n"
    "- implementace je v rozporu s architekturou,\n"
    "- chybí testy, které byly výslovně vyžadované,\n"
    "- je tam jasná chyba v korektnosti.\n"
    "Do summary v tom případě konkrétně a akčně napiš, co je špatně a co "
    "opravit — vývojový krok podle toho půjde do dalšího kola.\n\n"
    "Nevracej `request_changes` kvůli stylistickým drobnostem, subjektivním "
    "preferencím, vylepšením mimo zadání nebo chybějící dokumentaci. Když je "
    "implementace v pořádku, vrať `done` (i s nezávaznými návrhy na úklid)."
)

# Krok → (persona, výchozí nástroje). Nástroje jsou jména Claude Code toolů,
# která `claude_cli` předá přes `--allowedTools`.
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
    """Persona kroku. Známé kroky mají personu přenesenou z v1; neznámý krok
    dostane generickou instrukci (ostatní boilerplate dodá `compose_prompt`)."""
    known = AGENT_PERSONAS.get(step)
    if known is not None:
        return known[0]
    return (
        f"Jsi agent kroku '{step}'. Přečti si artefakty předchozích kroků ve "
        f"svém pracovním adresáři, odveď práci kroku a zapiš výstup tam, kam tě "
        f"nasměruje prompt úkolu."
    )


def _agent_tools(step: str) -> list[str]:
    """Výchozí nástroje kroku; neznámý krok jich dostane prázdno."""
    known = AGENT_PERSONAS.get(step)
    return list(known[1]) if known is not None else []


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


def _github_source(args: argparse.Namespace, root: Path) -> TaskSource | None:
    """Zdroj z GitHub Issues, když je `--github-repo` a `GITHUB_TOKEN`. Jinak
    None — harness běží jako dřív (jen `harness submit`)."""
    if not args.github_repo:
        return None
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "varování: --github-repo bez GITHUB_TOKEN, zdroj vypnut",
            file=sys.stderr,
        )
        return None
    worktree_root = args.worktree_root or str(root / "worktrees")
    return GithubTaskSource(
        client=HttpGithubClient(token),
        clock=SystemClock(),
        repo=args.github_repo,
        workflow=args.github_workflow,
        repository=str(root / "repo"),
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
    source = _github_source(args, root)
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
        "--github-repo",
        default=None,
        help="repo (owner/name) pro GitHub zdroj tasků; s GITHUB_TOKEN",
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
