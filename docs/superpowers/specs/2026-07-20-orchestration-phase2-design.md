# Fáze 2 — artefakty, worktree a landing

Status: návrh
Datum: 2026-07-20

## Cíl

Task přestává být jen token, který putuje mezi frontami. Ve fázi 2 každá fáze
**pracuje ve worktree** pojmenovaném v tasku, **produkuje artefakty** (plán,
design, review) do harnessem vlastněné složky a **commituje svou práci** s
vlastní zprávou. Na konci workflow **landing** krok přiklopí artefakty do
worktree a otevře **pull request**. Harness se nikdy nedotkne `main` — jen
navrhuje.

Fáze 2 je pořád POC. Skutečný agent, skutečný GitHub a produkční úložiště se
vymění v dalších fázích — **záměnou driveru, nikdy jeho okolí**.

### Co je ve fázi 2 nově

- `repository` a `worktree` přestávají být neprůhledný náklad. Behavior je čte,
  aby se připojil k pracovní ploše. **Router ani dispatcher je pořád nečtou.**
- Fáze produkují artefakty. Behavior je píše *živě* do harnessové složky; UI je
  vidí, jak přibývají.
- Behavior vrací `(outcome, summary)` místo holého `Outcome`. `summary` je
  krátký popis „co jsem udělal".
- Práce se commituje po fázích na task branch. Zprávu commitu tvoří `summary`.
- Landing krok přiklopí artefakty do worktree a otevře PR.

### Co je pořád mimo rozsah

- **Skutečný agent.** Behavior je dummy — píše připravené artefakty a vrací
  determinstický outcome + summary.
- **Skutečný GitHub.** Landing jde přes port `Forge`; ve fázi 2 má testovací
  driver (zaznamená PR / pushne do lokálního bare remote). GitHub driver je
  čistý follow-up — záměna driveru.
- **Více procesů, retry politiky, rate limiting, TTL leasu.**

## Základní teze (ARD2): task je transakce

Během tasku žije všechna práce v izolované pracovní ploše, kterou skutečná
historie projektu nevidí. Na konci se přiklopí jako celek — nebo vůbec ne.

Tři vlastnosti, které z toho plynou a které fáze 2 chrání:

1. **Atomicita.** Task, který selže nebo je opuštěn, nechá projekt netknutý.
2. **Čistá historie.** Projekt nevidí pět revizí plánu a smyčku request_changes.
   Vidí jeden návrh (PR).
3. **Izolace.** Dva tasky běžící souběžně se nepotkají v historii projektu,
   protože ani jeden v ní není, dokud nelanduje.

Cena: harness musí držet potenciálně velký pracovní stav (worktree + složka
artefaktů) per task, **durabilně** po celý život tasku. „Neverzované během
tasku" ≠ „neperzistentní" — recovery po pádu platí i tady.

## Dvě pracovní plochy per task

Task má za běhu **dvě** oddělené plochy. Jsou oddělené záměrně.

| Plocha | Co drží | Verzování | Kdo čte |
|---|---|---|---|
| **Worktree** (`repository`/`worktree`) | kód, který fáze upravují | git branch tasku, commit po fázi | behavior |
| **Složka artefaktů** (harness) | plán, design, review | neverzovaná do landingu | behavior + UI |

Proč ne jedna plocha (artefakty přímo ve worktree):

- **`git status`/`diff` zůstane čistý.** Diff kódu je jen kód, ne scaffolding.
- **UI čte bez gitu.** Jedna sdílená harness složka, ne N worktree přes N repo.
- **Odolnost vůči git operacím.** `git clean -fdx`, přepnutí branch ani reset
  ve worktree nemůžou smazat artefakty — leží mimo doménu gitu.
- **„Neverzované během tasku" je zadarmo.** Ve worktree by neverzovaný artefakt
  musel být *untracked* → přesně stav, který `git clean` maže. Ve složce mimo
  git jsou „neverzované" i „odolné" současně; ve worktree se vylučují.

Cena je landing krok, který artefakty přiklopí do worktree — a to je vlastně
výhoda: rozhodne se *kam* v repu artefakty přistanou.

### Adresace artefaktů — attempt-indexed

Artefakty žijí pod `<artifacts_root>/<task-id>/<step>/<attempt>/<name>`.

`attempt` je pořadové číslo běhu daného kroku daným taskem. Zpětná hrana
(`review --request_changes--> development`) znamená, že `development` i `review`
běží víckrát. Kdyby druhý běh přepsal `review.md`, audit trail by o smyčce
přišel — proto **každý pokus dostane vlastní podadresář**. Store alokuje další
`attempt` slot při `begin(task_id, step)`.

## Kontrakt behavioru

```python
@dataclass(frozen=True)
class BehaviorResult:
    outcome: Outcome
    summary: str = ""

class ConsumerBehavior(ABC):
    async def run(self, task: Task) -> BehaviorResult: ...
```

`run` vrací `BehaviorResult` místo `Outcome`. Důvody, proč to neporušuje
rozdělení rolí z fáze 1:

- **`outcome` musel být návratová hodnota už dřív** — je to řídicí signál, na
  který routuje dispatcher. Přibalit `summary` je symetrické: „tohle se stalo a
  tohle jsem udělal".
- **`summary` je terminální výrok o běhu** — jedna věta na konci práce. To je
  něco jiného než plán/review, které se *streamují* do složky artefaktů, jak
  agent pracuje. Oba žijí vedle sebe: velké dokumenty do složky živě, summary
  na návratu.

Jeden `summary` obsluhuje **čtyři** konzumenty:

1. **Zpráva commitu** — behavior driver commituje s ním (`[development] přidán…`).
2. **Řádek historie** — consumer ho zapíše do audit logu.
3. **Board UI** — „co který krok udělal".
4. **Tělo PR** — landing agreguje summary z historie do popisu PR.

### Kde se commituje — behavior driver, ne consumer

„Harness commituje, ne agent" znamená přesně: commit dělá **behavior driver**
(harnessový kód obalující agenta), ne LLM. Agent edituje soubory a *řekne*, co
udělal; nikdy nespouští `git commit`. Nezávisíme na tom, že si vzpomene, že to
udělá správně, nebo že stageuje správné cesty.

Commit tím pádem leží v behavioru (má po ruce worktree i „co se změnilo"), **ne
v tenkém consumeru** — ten nikdy nezíská git závislost. Invariant „consumer jen
doručí outcome, nevětví na jeho hodnotě" platí dál: consumer zapíše outcome i
summary, ale nerozhoduje podle nich.

## Landing

Landing je **normální krok workflow**, ne harnessová magie. Poslední krok před
`end`. Jeho behavior:

1. Připojí se k worktree.
2. Přečte složku artefaktů tasku a **přiklopí** ji do worktree (např. pod
   `docs/tasks/<id>/`), commitne „[land] artefakty tasku".
3. Otevře PR přes `Forge` — titul z původního zadání tasku, tělo z agregovaných
   summary v historii.
4. Vrátí `BehaviorResult(DONE, "otevřen PR …")`.

Protože je to krok, může selhat (push odmítnut, API chyba) → `failed/`, stejná
mašinerie jako všude jinde. `end` zůstává čistý terminál bez vedlejších efektů.
Uživatel PR zreviduje a mergne vlastní strategií (squash/rebase/merge) — harness
merge strategii neřeší.

### Idempotence landingu

Landing je vícekrokový (commit → push → otevři PR). Re-run po pádu musí být
idempotentní: „otevři PR, pokud ještě neexistuje". Fáze 2 to řeší nejjednodušeji
— Forge driver při existujícím PR pro branch vrátí ten stávající.

## Nové porty a drivery

| Port | Odpovědnost | Driver fáze 2 | Vymění se za |
|---|---|---|---|
| `Workspace` | `attach(task) -> WorkspaceHandle`; handle má `path`, `branch`, `commit(msg) -> sha \| None` | git worktree | — |
| `ArtifactStore` | `begin(task_id, step) -> ArtifactSlot`; read: `list/read` | složka na disku | S3, DB |
| `ArtifactView` | read-only podmnožina `ArtifactStore` pro UI | tentýž fs driver | — |
| `Forge` | `open_pull_request(task, branch, title, body) -> PullRequest` | fake (zaznamená / lokální bare) | GitHub API |

Ke každému portu vzniká **in-memory driver pro testy**. Orchestrace (dispatcher,
consumer) porty `Workspace`/`Forge`/`ArtifactStore` **nezná** — sahá na ně
výhradně behavior. Wiring je v `app.py`.

### WorkspaceHandle

- `path: Path` — pracovní adresář, kde behavior edituje.
- `branch: str` — task branch (`harness/<task-id>`), na kterém commity leží.
- `commit(message) -> str | None` — stageuje vše a commitne; vrací sha, nebo
  `None` když není co commitovat (fáze bez změny kódu — plán, review).

### GitWorkspace

- `attach(task)`: worktree pod `task.worktree` pro repo `task.repository`.
  Neexistuje-li, `git worktree add <worktree> -b harness/<task_id> <base>`;
  existuje-li, znovupoužije. Vrátí handle.
- Dvě tasky **nesmí** sdílet worktree — jinak si přepíšou práci. Ve fázi 2 to
  garantuje autor tasku; harness invariant jen dokumentuje.

## Změny v modelu workflow

Výchozí workflow dostává krok `land` před `end`:

```json
{"from": "review", "on": "done", "to": "land"},
{"from": "land",   "on": "done", "to": "end"}
```

`land` je běžný krok s vlastní frontou. Wiring mu přiřadí `LandingBehavior`;
ostatním krokům `DummyBehavior`. Který krok je landing, je konfigurace
(`landing_step`, default `"land"`), ne magické jméno v jádře.

## HistoryEntry — nové pole `summary`

`HistoryEntry` dostává volitelné `summary: str | None`. Consumer ho vyplní
hodnotou z `BehaviorResult`. Serializuje se, jen když je přítomné. Audit log
tím nese nejen *co se stalo* (outcome), ale i *co se udělalo* (summary).

## Recovery přestává být zadarmo

Fáze 1 se opírala o „práce je idempotentní, recovery jen znovu spustí". Skutečný
agent, který napůl upravil worktree, ten předpoklad boří. Ve fázi 2 s dummy
behaviorem je re-run pořád bezpečný, ale seam je reálný:

- Per-fázový commit je čistý bod obnovy — poslední commit drží hotovou práci,
  fáze se přehraje z něj.
- Store `begin()` při re-runu alokuje **nový** attempt, takže se half-written
  artefakty nemíchají s novým během.

TTL leasu se ani ve fázi 2 neimplementuje.

## Chybové stavy (přibývá k fázi 1)

| Situace | Detekce | Kam |
|---|---|---|
| Behavior nevrátí `BehaviorResult` | validace v consumeru | `failed/` |
| `attach` selže (repo/worktree chybí) | výjimka z behavioru | `failed/` |
| `commit`/landing selže | výjimka z behavioru | `failed/` |
| Forge odmítne PR | výjimka z behavioru | `failed/` |

Vše přes stávající `_fail` cestu — jeden vadný task nezastaví smyčku.

## UI

Board z fáze 1 dostává druhou věc k vykreslení: **artefakty per task**, živě.
API sahá na `ArtifactView` (read-only port), nikdy na driver. Detail tasku
ukáže seznam artefaktů (step, attempt, name) a jejich obsah.

## Struktura kódu (přírůstky)

```
src/harness/
  models.py            # + BehaviorResult, HistoryEntry.summary
  consumer.py          # run() vrací BehaviorResult; zapíše summary
  ports/
    workspace.py       # Workspace, WorkspaceHandle
    artifacts.py       # ArtifactStore, ArtifactView, ArtifactSlot, ArtifactRef
    forge.py           # Forge, PullRequest
  drivers/
    memory.py          # + MemoryWorkspace, MemoryArtifactStore, MemoryForge
    git_workspace.py   # GitWorkspace
    fs_artifacts.py    # FilesystemArtifactStore
    fake_forge.py      # FakeForge
    dummy_behavior.py  # píše artefakty, commituje, vrací (outcome, summary)
  behaviors/           # (nové) landing.py — LandingBehavior
  app.py               # wiring nových portů, per-step behaviory
```

## Invarianty — nové/upřesněné

Rozšiřují seznam z `CLAUDE.md`, neruší ho.

8. **`repository`/`worktree` čte jen behavior.** Router a dispatcher pořád
   rozhodují výhradně podle `(status, lastOutcome)`.
9. **Commit dělá behavior driver, ne consumer a ne LLM.** Consumer nezná git.
10. **Artefakty jsou attempt-indexed.** Re-run kroku nikdy nepřepíše předchozí
    pokus.
11. **`Workspace`/`Forge`/`ArtifactStore` nezná dispatcher ani consumer.** Sahá
    na ně jen behavior; wiring v `app.py`. `api/` sahá jen na `ArtifactView`.
12. **Landing je krok, ne magie.** `end` zůstává čistý terminál.

## Ověření hotovosti

Fáze 2 je hotová, když:

1. Task s `repository`+`worktree` proteče `plan → … → review → land → end`.
2. Každá fáze zapsala artefakt do `<task>/<step>/<attempt>/` a UI ho vidí.
3. Zpětná hrana (`request_changes`) vytvoří druhý attempt `development` i
   `review` — oba pokusy jsou ve složce vidět.
4. Worktree nese per-fázový commit se smysluplnou zprávou (ze summary), ne
   „development stage".
5. Landing přiklopil artefakty do worktree a otevřel PR (fake Forge zaznamenal
   branch, titul, tělo z agregovaných summary).
6. `history` doputovaného tasku nese `summary` u každého consumer řádku.
7. Zabití procesu uprostřed a restart vede k dokončení tasku (recovery + nový
   attempt).
8. Architektonické testy: dispatcher/consumer neimportují nové porty ani
   drivery; `api/` sahá jen na `ArtifactView`; consumer nevětví na outcome.
```
