# Fáze 3 — skutečný agent přes `claude -p`

Status: návrh
Datum: 2026-07-20

## Cíl

Vyměnit `DummyBehavior` za driver, který skutečnou práci kroku svěří **agentovi
spuštěnému přes `claude` CLI** (`claude -p`, headless), **ne přes API**. Každá
fronta má svého agenta (`architect`, `planner`, `reviewer`, …) — jiná persona,
jiný model, jiná sada nástrojů. Volání CLI je jeden sdílený wrapper; to, co se
liší frontu od fronty, je **konfigurace agenta jako data**.

Fáze 3 mění výhradně to, *jak vzniká `BehaviorResult`*. Smyčka, dispatcher,
router, fronty, projekce ani porty z fází 1–2 se nemění — platí invariant 1:
**vyměňuje se driver, nikdy jeho okolí.** Skutečný agent je driver za portem.

## Co je ve fázi 3 nově

- **`AgentRunner` (port).** Sdílený wrapper kolem `claude -p`. Dostane invokaci
  `(prompt, agent_spec, cwd, timeout)`, spustí subprocess, vrátí strukturovaný
  výsledek. Reálný driver skládá CLI flagy; fake driver vrací připravený výstup
  pro testy — **žádný subprocess, žádná síť, žádné peníze v test sadě.**
- **`AgentCatalog` (port) + `AgentSpec` (data).** Pojmenované definice agentů.
  `AgentSpec` nese personu, model, nástroje a povolené outcomes. Vazba
  fronta→agent je defaultně identitou jména.
- **`ClaudeCliBehavior`.** Generický behavior konstruovaný `(agent_spec, runner,
  workspace)`. Nahrazuje `DummyBehavior`: připojí worktree, spustí agenta,
  namapuje jeho verdikt na `BehaviorResult`, worker commitne.
- **`RepositoryRegistry` (port).** Mapa jméno repa → cesta na disku, **specifická
  pro stroj**. Task nese jen jméno repa; cestu k worktree odvodí harness sám.
- **Artefakty se stěhují do worktree.** Fáze žádá agenta, ať píše artefakty
  (plán, ADR, review) do `.artifacts/<task-id>/` uvnitř worktree. Jsou
  **verzované** — worker je commitne spolu s kódem. Předchozí kroky je tím vidí
  jako obyčejné soubory ve svém cwd.

## Co je pořád mimo rozsah

- **Skutečný GitHub.** Landing pořád jde přes `Forge`; ostrý běh má fake / lokální
  driver. GitHub driver je čistý follow-up — záměna forge driveru.
- **Více procesů, TTL leasu, distribuovaný běh.** Jeden proces, recovery při
  startu, jako dřív.
- **Retry politiky nad rámec `fallback_model`.** Tranzientní chyby (rate-limit,
  timeout) padají do `failed/`; sofistikovanější retry je vědomě odložený
  (viz Otevřené otázky).

## Nosná teze (ARD3): agent je driver, persona jsou data

Rozhodování „co se stalo" (`Outcome`) i „co se udělalo" (`summary`) vzniká pořád
na jediném místě — v behavioru. Fáze 1 to řešila `sleep`em, fáze 2 dummym, fáze 3
skutečným agentem. Z pohledu consumeru, dispatcheru a routeru se **nic nemění**;
pořád dostávají `BehaviorResult` a routují podle `(status, lastOutcome)`.

Dvě věci, které z toho plynou a které fáze 3 chrání:

1. **Agent je za `AgentRunner`.** `ClaudeCliBehavior` nezná subprocess ani flagy;
   zná jen port. Test ho pohání `FakeAgentRunner`em — stejně jako fáze 1 pohání
   čas `FakeClock`em. Bez tohoto řezu je behavior netestovatelný.
2. **Persona je konfigurace, ne kód.** V `ClaudeCliBehavior` není větev podle
   jména agenta. Rozdíl mezi `architect` a `reviewer` je obsah `AgentSpec`u,
   který dostal při konstrukci. Přidání agenta = nový soubor v katalogu, ne
   nová třída.

## Repository registry — kde repa na tomhle stroji leží

Fáze 2 bere `task.repository` i `task.worktree` jako **holé filesystémové cesty**
(`GitWorkspace.attach`: `repo = Path(task.repository)`). Tím prosakuje layout
konkrétního stroje do tasku a task přestává být přenositelný.

Fáze 3 to rozděluje:

- **`task.repository` je logické jméno** (`"harness_v2"`), ne cesta.
- **`RepositoryRegistry.resolve(name) -> Path`** — mapa jméno → kořen repa na
  disku. Machine-specific config (`~/.harness/repos.json` nebo env), **mimo task,
  necommitnutá.**
- **Worktree cestu odvodí harness**, ne submitter: `<worktrees_root>/<task_id>`.
  `task.worktree` přestává být povinný vstup — je odvozený.

`Workspace.attach(task)` pak: `base = registry.resolve(task.repository)` →
`git worktree add <worktrees_root>/<task_id> -b harness/<task_id>` z `base`.

Driver fáze 3: `FilesystemRepositoryRegistry` (čte JSON). In-memory driver pro
testy. Dispatcher ani consumer registry nezná — sahá na ni jen `Workspace` přes
wiring.

## Artefakty ve worktree — verzované, ploché, attempt-suffixované

Fáze 2 psala artefakty do harnessem vlastněné složky *mimo* worktree. Reálný
subprocess agent ale vidí **jen svoje cwd** — plán architekta v externí složce
by developer nepřečetl. Proto se artefakty stěhují **do worktree**, kde je každý
další krok vidí jako obyčejné soubory.

### Layout

```
.artifacts/<task-id>/
  plan.md
  architecture-decisions.md
  development-01.md
  review-01.md
  development-02.md
  review-02.md
```

- **Kořen `.artifacts/`** — dot-prefix signalizuje „harnessová metadata, ne
  zdroják"; většina nástrojů (pytest, lintery, coverage) dot-adresáře přeskakuje,
  takže artefakty neznečistí tooling cílového repa.
- **Ploché soubory, žádná hierarchie.** Pokus je v suffixu jména, ne v podadresáři
  — listing se lexikálně řadí po kroku a pak po pokusu, smyčka je čitelná na první
  pohled. Kdyby krok potřeboval víc souborů na pokus, prefix `development-02`
  je stejně seskupí; hierarchie by zbytečně zamykala tvar.
- **Task-level = holé jméno** (`plan.md`), **step-attempt = `<step>-NN`**
  (dvouciferný zero-pad, per-step counter). Kroky, do kterých se workflow vrací
  smyčkou, dostávají číslo; run-once kroky holé jméno.

### Kdo počítá `NN`

Behavior driver před spuštěním agenta oskenuje `.artifacts/<task-id>/`, spočítá
existující `<step>-*.md` a alokuje další číslo. Je to zbytek `ArtifactStore.begin()`
z fáze 2 scvrknutý na malý helper nad worktree filesystémem — samostatný store
zápisově zaniká.

### Verzování a commit

Agent artefakty **zapíše**; `git add`/`commit` nespouští (invariant 9 platí dál).
Worker po doběhu agenta commitne vše — kód i `.artifacts/**` — se `summary` jako
zprávou. `GitWorkspace.commit` už dnes dělá `git add -A`, takže artefakty
posbírá bez úprav; reálná změna je jen *kam agent píše*, ne *jak se commituje*.

Důsledek: artefakty jedou v git historii a přistanou v PR jako dokumentace
návrhu. Přežijí zbourání worktree (jsou v branchi), takže board i audit je vidí
i po dokončení tasku. Landing tím ztrácí kopírovací krok — artefakty už ve
worktree jsou; landing jen otevře PR.

### Recovery — gapless číslování zadarmo

Rozdělaný pokus (`development-02.md`, který agent píše) je do workerova commitu
**necommitnutý**. Když agent v půlce spadne, recovery udělá `reset --hard HEAD`
(viz níže) → necommitnutý `development-02.md` zmizí → re-run spočítá commitnuté
`development-*` = `01` → znovu alokuje `02`. Stejné číslo, žádná díra, žádný
půlnapsaný artefakt. Tři rozhodnutí (indexování + commit + reset) do sebe
zapadají.

## Agent — katalog, spec, vazba na frontu

### `AgentSpec` (data)

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str                      # = jméno fronty (default vazba)
    prompt: str                    # persona
    model: str | None = None       # None → harness-level default
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[Outcome, ...] = (Outcome.DONE,)
```

- `allowed_outcomes` je **náš** koncept, ne CLI flag. `architect`/`planner`
  smí jen `DONE`; `reviewer` `DONE`/`REQUEST_CHANGES`. Verdikt mimo množinu →
  výjimka → `failed/`. Kontrakt tím sedí u agenta, ne rozházený po workflow.

### `AgentCatalog` (port)

`get(name) -> AgentSpec`. Driver fáze 3 `FilesystemAgentCatalog` čte
`agents/<name>.json` (**náš** formát, ať je katalog jediný zdroj pravdy).
In-memory driver pro testy. Neplatné/chybějící jméno → `AgentNotFound`, symetricky
k `WorkflowNotFound`.

### Vazba fronta → agent

Defaultně **identita**: jméno kroku == jméno agenta (`architect` fronta →
`architect` spec). Indirekci (dvě fronty sdílí agenta) řeší volitelná mapa
`step → agent` — buď pole `"agent"` u kroku ve workflow JSONu, nebo zvlášť.
Wiring z fáze 2 už má hák `behavior_for(step)`; ve fázi 3 z něj bude
`ClaudeCliBehavior(spec=catalog.get(agent_of(step)), runner=shared, …)`.

## `AgentRunner` — wrapper kolem `claude -p`

Port:

```python
class AgentRunner(ABC):
    async def run(self, *, prompt: str, spec: AgentSpec, cwd: Path,
                  timeout: float) -> AgentRun: ...

@dataclass(frozen=True)
class AgentRun:
    outcome: Outcome
    summary: str
    raw: str          # syrový výstup pro audit / event stream
```

### Driver `ClaudeCliRunner` — mapování na flagy

Ověřeno proti `claude 2.1.211`:

| `AgentSpec` / kontext | Flag |
|---|---|
| `prompt` (persona) | `--append-system-prompt` *nebo* `--agents '<json>' --agent <name>` |
| `model` | `--model` (alias i plné ID) |
| `fallback_model` | `--fallback-model` |
| `allowed_tools` | `--allowedTools` |
| pracovní prompt (task + krok) | pozicní `-p "<prompt>"` |
| cwd | worktree cesta z `RepositoryRegistry` |
| — | `--output-format json` (strojově čitelný výsledek) |
| — | `--permission-mode bypassPermissions` (headless, bez člověka) |
| — | `--setting-sources project` (determinismus, viz níže) |

Verdikt: agent v personě dostane instrukci skončit strojově čitelným
`{outcome, summary}`. Runner ho vytáhne z JSON obálky. Chybějící/nečitelný/mimo
`allowed_outcomes` → výjimka → `failed/`. `BehaviorResult(outcome, summary)` z
fáze 2 je pro tenhle verdikt skoro 1:1 — model se kvůli fázi 3 nemění.

### Timeout

`claude -p` běží minuty, ne milisekundy. Runner vlastní timeout → kill
subprocessu → výjimka → `failed/`. Žádný port fáze 1–2 timeout nezná; přidává se
tady, uvnitř runneru.

## `ClaudeCliBehavior` — tok

```
attach worktree (Workspace, cwd z RepositoryRegistry)
  → alokuj attempt číslo v .artifacts/<id>/
  → prompt = compose(task, step, ukazatele na .artifacts/ předchozích kroků)
  → run = await runner.run(prompt, spec, cwd, timeout)     # agent píše kód + artefakty
  → worker: handle.commit(run.summary)                     # commit dělá driver, ne agent
  → BehaviorResult(run.outcome, run.summary)
```

Behavior nevětví na hodnotě outcome (invariant 2 platí dál). Commit dělá driver,
ne agent (invariant 9). `attach`/`commit`/`failed` cesty jsou beze změny z fáze 2.

## Determinismus prostředí

`claude -p` z cwd **automaticky natáhne `CLAUDE.md` cílového repa, jeho skilly,
pluginy, MCP**. To může být žádoucí (agent ctí konvence repa), ale i zdroj
nedeterminismu a průsaku operátorovy globální konfigurace. Fáze 3 volí
`--setting-sources project` — natáhne projektovou konfiguraci repa, ne
uživatelskou. Per-agent override (`setting_sources` ve specu) je otevřená otázka.

## Recovery

`Workspace.attach` fáze 2 worktree jen znovupoužije. Reálný agent, který spadl v
půlce, ale nechá **špinavý worktree**; re-run by se na něj navrstvil. Fáze 3
proto při re-attach dělá `git reset --hard HEAD` + `git clean -fd` (bez `-x`,
ať přežijí případné ignorované soubory) — vrátí worktree na poslední per-krok
commit a rozdělanou práci zahodí. To zároveň drží gapless číslování artefaktů
(viz výše). Commitnuté artefakty i kód přežijí; jen rozdělaný běh se přehraje.

## Chybové stavy (přibývá k fázím 1–2)

| Situace | Detekce | Kam |
|---|---|---|
| `RepositoryRegistry.resolve` selže (repo neznámé) | výjimka z behavioru | `failed/` |
| `claude` skončí nenulově / spadne | runner vyhodí | `failed/` |
| timeout agenta | runner kill → výjimka | `failed/` |
| verdikt chybí / nečitelný JSON | runner vyhodí | `failed/` |
| verdikt mimo `allowed_outcomes` | behavior validuje | `failed/` |
| `AgentCatalog.get` selže | výjimka při wiring/behavioru | `failed/` |

Vše přes stávající `_fail` cestu — jeden vadný task nezastaví smyčku.

## Nové porty a drivery

| Port | Odpovědnost | Driver fáze 3 | Vymění se za |
|---|---|---|---|
| `AgentRunner` | `run(prompt, spec, cwd, timeout) -> AgentRun` | `ClaudeCliRunner` (`claude -p`) | jiný agent CLI / API |
| `AgentCatalog` | `get(name) -> AgentSpec` | `FilesystemAgentCatalog` | DB, remote |
| `RepositoryRegistry` | `resolve(name) -> Path` | `FilesystemRepositoryRegistry` | — |

Ke každému in-memory driver pro testy. Orchestrace (dispatcher, consumer) žádný z
nich nezná — sahá na ně jen behavior / wiring. `api/` beze změny.

## Co se z fáze 2 mění

- **`ArtifactStore` (zápisová strana) retiruje.** Artefakty píše agent do
  worktree; `begin/put` nahrazuje path-konvence + attempt helper. `ArtifactView`
  (čtení pro board) **zůstává**, jen jeho driver čte `.artifacts/` ve worktree
  místo oddělené složky (invariant 11 — `api/` sahá jen na `ArtifactView`).
- **Landing** ztrácí kopírovací krok (artefakty už ve worktree jsou); jen otevře
  PR.
- **`GitWorkspace.attach`** resolvuje jméno repa přes `RepositoryRegistry` a
  resetuje worktree na re-attach.

## Testovací story

- **`FakeAgentRunner`** vrací připravený `AgentRun` — `ClaudeCliBehavior` jde
  testovat bez subprocessu, bez sítě, bez `claude`. Unit i integrační testy
  běží in-memory a na `FakeClock`, jako celá sada.
- **In-memory `AgentCatalog` / `RepositoryRegistry`** pro testy.
- **Žádný reálný `claude` v test sadě** — nedeterministický, drahý, vyžaduje
  auth. Volitelný smoke s reálným `claude` je za env flagem, mimo `pytest -q`
  (viz Otevřené otázky).
- `tests/test_smoke_git.py` z fáze 2 (reálný git) zůstává; artefakty se v něm
  přesunou do worktree.

## Invarianty — nové/upřesněné

Rozšiřují seznam z `CLAUDE.md` (1–12), neruší ho.

13. **Agent je za `AgentRunner`.** `ClaudeCliBehavior` nezná subprocess ani CLI
    flagy; test ho pohání fake runnerem.
14. **Persona je data, ne kód.** V behavioru není větev podle jména agenta.
15. **`task.repository` je jméno, ne cesta.** Cesty řeší `RepositoryRegistry`,
    machine-specific, mimo task.
16. **Artefakty žijí ve worktree pod `.artifacts/<id>/`, verzované.** Píše je
    agent, commituje worker. Číslování pokusů je gapless přes reset-on-reattach.
17. **`AgentRunner`/`AgentCatalog`/`RepositoryRegistry` nezná dispatcher ani
    consumer.** Sahá na ně jen behavior / wiring.

## Otevřené otázky

- **Retry tranzientních chyb.** Default fáze 3: žádný, vše nešťastné → `failed/`,
  `fallback_model` jako částečná pojistka proti přetížení modelu. Zavést
  rozlišení transient/permanent a backoff, nebo počkat na fázi víceprocesů?
- **Reálný smoke.** Chceme opt-in test s živým `claude` (za env flagem), nebo
  stačí fake runner + ruční ověření ostrého běhu?
- **Per-agent `permission_mode` / `setting_sources`.** Globálně
  (`bypassPermissions`, `project`), nebo pole ve `AgentSpec`?
- **Persona přes `--append-system-prompt` vs `--agents`+`--agent`.** Obojí
  deterministické; první jednodušší, druhé nese i model/tools v jedné definici.

## Ověření hotovosti

Fáze 3 je hotová, když:

1. Task se jménem repa proteče workflow, kde každý krok obsluhuje **skutečný
   `claude -p` agent** dané persony a modelu (ověřeno opt-in smokem nebo ručně).
2. `RepositoryRegistry` přeloží jméno repa na cestu; worktree vznikne na
   odvozené cestě, task nenese absolutní cesty.
3. Každá fáze zapsala artefakt do `.artifacts/<id>/` ve worktree; předchozí
   kroky ho čtou jako soubor v cwd; worker ho commitnul se `summary`.
4. Zpětná hrana (`request_changes`) vytvoří `development-02` / `review-02` vedle
   `-01`; všechny pokusy jsou ve worktree a v git historii.
5. `reviewer` umí vrátit `REQUEST_CHANGES`; `architect`/`planner` jen `DONE`;
   verdikt mimo `allowed_outcomes` skončí v `failed/`.
6. Pád agenta / timeout / nečitelný verdikt → `failed/`, smyčka běží dál.
7. Zabití procesu uprostřed a restart vede k dokončení tasku (reset-on-reattach,
   gapless attempt).
8. `ClaudeCliBehavior` je zelený s `FakeAgentRunner`em, bez skutečného `claude`.
9. Architektonické testy: dispatcher/consumer neimportují nové porty ani drivery;
   `api/` sahá jen na `ArtifactView`; behavior nevětví na outcome.
