# harness_v2 — orientace pro Claude

Orchestrační harness pro více agentů. Jednotkou práce je **task**, který putuje
mezi frontami podle **workflow** — malého state machine s explicitními hranami.

Spec fáze 1: `docs/superpowers/specs/2026-07-19-orchestration-phase1-design.md`
Plán fáze 1: `docs/superpowers/plans/2026-07-19-orchestration-phase1.md`
Spec fáze 2: `docs/superpowers/specs/2026-07-20-orchestration-phase2-design.md`
Plán fáze 2: `docs/superpowers/plans/2026-07-20-orchestration-phase2.md`
Spec fáze 3: `docs/superpowers/specs/2026-07-20-orchestration-phase3-design.md`
Plán fáze 3: `docs/superpowers/plans/2026-07-20-orchestration-phase3.md`

Projekt se staví **po fázích**. Fáze 1 je POC orchestrační smyčky. Fáze 2 přidává
**worktree, artefakty a landing**: každá fáze pracuje ve worktree, píše artefakty,
commituje po fázích a na konci otevře PR. Fáze 3 vyměňuje `DummyBehavior` za
**skutečného agenta přes `claude -p`**: personu kroku (`AgentSpec`) spustí sdílený
`AgentRunner`, `task.repository` je jméno (cestu řeší `RepositoryRegistry`) a
artefakty se stěhují **do worktree** pod `.artifacts/<id>/`, verzované. Skutečný
GitHub je pořád jen driver (forge), který se vymění dál.

## Invarianty — nerozbíjet

1. **Vyměnit se smí driver, nikdy jeho okolí.** Každá pohyblivá část leží za
   portem v `ports/`. `dispatcher.py` ani `consumer.py` nesmí importovat nic
   z `drivers/` — wiring patří výhradně do `app.py`. Hlídá to
   `tests/test_architecture.py`.
2. **Rozhodování má tři oddělené role.** `ConsumerBehavior` říká *co se stalo*,
   dispatcher *kam to jde dál*, consumer nerozhoduje nic. V `consumer.py` nesmí
   být větev závislá na hodnotě outcome; test to kontroluje čtením zdrojáku.
3. **Status mění dispatcher — s jednou výjimkou.** Rozhodnutí *kam task jde dál*
   (krok, `end`) patří výhradně dispatcheru. Jediná výjimka: když consumer sám
   nedokáže task doručit (behavior vyhodí výjimku, nebo vrátí neplatný
   outcome), zapíše mu terminální status `failed` sám — symetricky k tomu, jak
   `Dispatcher._fail` dělá totéž, když selže routing. `lastOutcome` zapisuje
   výhradně consumer.
4. **Router je čistá funkce.** `route()` nesmí sáhnout na I/O, čas ani stav.
5. **`api/` ani `projection.py` neimportují `drivers/`.** UI nesmí vědět, na čem harness běží.
6. **V `Harness.run()` jde `recover()` před `hydrate()`.** Obráceně se ztratí tasky z `.processing/`.
7. **Event o pohybu tasku nese `task` i `queue`.** Bez toho projekce neuvidí tasky vzniklé po startu.
8. **`repository`/`worktree` čte jen behavior.** Router a dispatcher pořád rozhodují výhradně podle `(status, lastOutcome)`.
9. **Commit dělá behavior driver, ne consumer a ne LLM.** Consumer nezná git.
10. **Artefakty jsou attempt-indexed** (`<task>/<step>/<attempt>/`). Re-run kroku nikdy nepřepíše předchozí pokus — jinak by smyčka `request_changes` z audit trailu zmizela.
11. **`Workspace`/`Forge`/`ArtifactStore` nezná dispatcher ani consumer.** Sahá na ně jen behavior; wiring v `app.py`. `api/` sahá jen na `ArtifactView`. Hlídá `test_architecture.py`.
12. **Landing je krok, ne magie.** Přiklopí artefakty do worktree a otevře PR; může selhat do `failed/`. `end` zůstává čistý terminál. Fáze 3: artefakty už ve worktree jsou, landing je nekopíruje — jen otevře PR.
13. **Agent je za `AgentRunner`.** `ClaudeCliBehavior` nezná subprocess ani CLI flagy; test ho pohání `FakeAgentRunner`em, jako fáze 1 čas `FakeClock`em.
14. **Persona je data, ne kód.** V `behaviors/agent.py` není větev podle jména agenta — rozdíl mezi personami je obsah `AgentSpec`u dodaného při konstrukci.
15. **`task.repository` je jméno, ne cesta.** Cesty řeší `RepositoryRegistry` — machine-specific config (`repos.json`), mimo task. Worktree cestu odvodí harness (`<worktrees_root>/<task_id>`).
16. **Artefakty žijí ve worktree pod `.artifacts/<id>/`, verzované.** Píše je agent, commituje worker. Číslování pokusů (`<step>-NN`) je gapless přes reset-on-reattach.
17. **`AgentRunner`/`AgentCatalog`/`RepositoryRegistry` nezná dispatcher ani consumer.** Sahá na ně jen behavior / wiring. Hlídá `test_architecture.py`.

## Práce tady

```sh
.venv/bin/pytest -q
```

Python je **3.11** (`/Users/rem/.local/bin/python3.11`), na stroji **není `uv`** —
plain `venv` + `pip install -e ".[dev]"`. Runtime nemá žádné produkční závislosti.

Unit a integrační testy běží na in-memory driverech a `FakeClock` — bez disku
a bez skutečného čekání. Nikdy do nich nepiš test, který spí v reálném čase.

Záměrnou výjimkou jsou `tests/test_smoke.py` (reálný FS) a
`tests/test_smoke_git.py` (fáze 3: reálný git worktree + artefakty ve worktree +
fake forge, práci kroku pohání `ClaudeCliBehavior` s lokálním test-runnerem, ne
reálný `claude`). Oba poluí reálným `asyncio.sleep(0.01)` — je to jediné pokrytí
reálných FS/git driverů naživo. Neuklízej je do in-memory podoby; tím by to
pokrytí zmizelo.

`tests/test_smoke_claude.py` je **opt-in** smoke se skutečným `claude -p` — běží
jen s `HARNESS_SMOKE_CLAUDE=1`, jinak se přeskočí a `pytest -q` ho nespustí.
Kryje tenkou subprocess slupku `ClaudeCliRunner`u, kterou fake runnery obcházejí.

## Git konvence

**Commituj přímo do `main`.** V této fázi je to zamýšlený postup — nezakládej
branch, neotevírej PR a neptej se. Platí to pro repo harnessu samotného.

## Mapa modulů

Závislosti tečou striktně dolů, cykly nejsou.

| Vrstva | Moduly |
|---|---|
| Základ | `models` (neimportuje nic z balíku), `ids` |
| Logika | `router` (zná jen `models`) |
| Základ (bez balíku) | `models`, `ids`, `artifacts_layout` (konvence `.artifacts/<id>/<step>-NN`) |
| Porty | `ports/{queue,workflows,strategy,behavior,events,clock,workspace,artifacts,forge,board,agent,repos}` |
| Orchestrace | `dispatcher`, `consumer` — znají jen porty (a ne `workspace`/`forge`/`artifacts`/`agent`/`repos`) |
| Behaviory | `behaviors/{landing,agent}` — sahají na porty, ne na drivery |
| Drivery | `drivers/{fs_queue,fs_workflows,fifo_strategy,dummy_behavior,stdout_events,system_clock,memory,fs_artifacts,git_workspace,fake_forge,claude_cli,fs_agents,fs_repos,worktree_artifacts}` |
| Okraje | `app` (wiring), `cli` |

- `projection.py` — in-memory read model boardu; hydratace z front + proud eventů
- `artifacts_layout.py` — jediné místo pravdy o rozmístění artefaktů ve worktree (`next_attempt`, `artifacts_dir`); čte z něj behavior i `WorktreeArtifactView`
- `ports/board.py` — port `BoardView`, kterým se dívá UI
- `ports/artifacts.py` — `ArtifactStore` (zápis, fáze 2) a `ArtifactView` (čtení pro UI); fáze 3 čte přes `WorktreeArtifactView`
- `ports/workspace.py` — `Workspace.attach(task) -> WorkspaceHandle` (worktree + commit)
- `ports/forge.py` — `Forge.open_pull_request(...)` (landing navrhne PR)
- `ports/agent.py` — `AgentRunner.run(...)`, `AgentCatalog.get(name)`, `AgentSpec` (persona jako data)
- `ports/repos.py` — `RepositoryRegistry.resolve(name) -> Path` (jméno repa → cesta)
- `behaviors/agent.py` — `ClaudeCliBehavior`: attach worktree → alokuj attempt → spusť agenta → worker commitne
- `api/` — FastAPI board; vidí jen `BoardView` a `ArtifactView`, nikdy driver ani `ArtifactStore`

## Co je za co zodpovědné

- **`TaskQueue`** — inbox, fronty kroků, `done/` i `failed/` jsou instance téhož
  portu. Terminální stavy jsou prostě fronty, které nikdo nekonzumuje.
- **`claim()`** je atomický `rename` do `<queue>/.processing/`. Jedna operace řeší
  lease, idempotenci i původ po pádu.
- **`END = "end"`** je vyhrazený uzel. Není to „stav bez odchozích hran" —
  překlep by tak tiše vypadal jako úspěch.
- **Task má dvě pracovní plochy** (fáze 2). **Worktree** (`repository`/`worktree`)
  drží kód, verzuje ho git branch tasku. **Složka artefaktů** (harnessová,
  neverzovaná do landingu, čitelná pro UI) drží plán/design/review. Oddělené
  záměrně — worktree zůstane čistý, UI čte bez gitu, `git clean` artefakty
  nesmaže. Detaily viz spec fáze 2.
- **`BehaviorResult(outcome, summary)`** je návrat behavioru. `outcome` routuje
  dispatcher; `summary` je zpráva commitu, řádek historie, tělo PR i board.
- **Task je transakce.** Práce žije v izolovaném worktree/složce; na konci
  **landing** přiklopí artefakty a otevře PR. Harness se nedotkne `main` — jen
  navrhuje. Merge strategii řeší člověk.
- **Agent** (fáze 3) je persona jako data: `AgentSpec` nese prompt, model,
  nástroje a `allowed_outcomes`. `AgentCatalog` mapuje jméno kroku na spec
  (default identita jméno==krok), sdílený `AgentRunner` ho spustí. Model je per
  fronta, ne per třída — přidání agenta = nový soubor v katalogu, ne nová třída.
  `reviewer` smí `done`+`request_changes`, ostatní jen `done`.
- **`RepositoryRegistry`** překládá jméno repa na cestu na tomhle stroji
  (`repos.json`, machine-specific, necommitnuté). Task nese jen jméno; worktree
  cestu (`<worktrees_root>/<task_id>`) odvodí harness. Reattach špinavý worktree
  resetuje na HEAD (reset-on-reattach).
- **Artefakty ve worktree** (fáze 3): agent je píše do `.artifacts/<id>/`,
  **commituje je worker** spolu s kódem (jedou v git historii a v PR). Číslování
  pokusů (`<step>-NN`) je gapless — rozdělaný pokus zahodí reset-on-reattach a
  re-run alokuje stejné číslo znovu.
- **`harness init`** zapíše default agenty (`agents/<step>.json`, persona
  instruuje verdikt blok + psaní artefaktu) a prázdný `repos.json`. `harness run`
  injektuje `ClaudeCliRunner`, `Filesystem{AgentCatalog,RepositoryRegistry}`,
  `GitWorkspace` a `WorktreeArtifactView`.

## Gotchas

- **`.processing/` má každá fronta vlastní.** Proto se po pádu nemusí nikam
  ukládat, odkud task pochází — recovery ho vrátí do fronty, pod kterou leží.
- **Prohraný závod o `claim()` není chyba.** `os.replace` vyhodí
  `FileNotFoundError`, driver vrátí `None` a smyčka si vezme další task.
- **Rozbitý JSON nemá komu připsat historii.** Soubor se přesune do `failed/`
  tak, jak je, a důvod nese jen event.
- **`DummyBehavior` musí vracet `done` deterministicky.** `request_changes_once_at`
  vrátí `REQUEST_CHANGES` jen při prvním průchodu daného tasku daným krokem;
  jinak by se smyčka točila donekonečna.
- **`build()` má default pracovní drivery in-memory.** Substrát dummy behavioru,
  stejně jako dummy sám je fake. `build` s `catalog` přepne agentní kroky na
  `ClaudeCliBehavior`; skutečný běh (`cli._run`) vstříkne `GitWorkspace`,
  `ClaudeCliRunner`, `WorktreeArtifactView`, `Filesystem{AgentCatalog,Repository‑
  Registry}` a `FakeForge` — záměna driveru, ne okolí.
- **Landing je idempotentní.** Forge při existujícím PR pro branch vrátí ten
  stávající. Re-run po pádu tak neotevře druhý PR.
- **`ArtifactStore.begin(task, step)` alokuje další attempt.** Zápis do jednoho
  slotu patří jednomu běhu; druhý průchod (smyčka) dostane nový podadresář.

## Operátor

Ondrej Pajgrt — „Ondrej" / „Rem". GitHub `onpaj`. Europe/Prague. Kontext stroje
(NanoClaw, podman) je v `~/CLAUDE.md`.

Předchozí pokus o tuto myšlenku leží v historii tohoto repa na commitu `7bc0e6e`;
`main` byl vyprázdněn commitem `b7cab63`, aby se stavělo po fázích od začátku.
