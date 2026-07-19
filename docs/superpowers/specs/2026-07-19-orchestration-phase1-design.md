# Fáze 1 — orchestrační smyčka

Status: schváleno
Datum: 2026-07-19

## Cíl

Funkční program, který polluje zdrojovou složku s tasky a rozřazuje je podle
workflow do jednotlivých output queues, kde je přebírají consumeři a vracejí
zpět. Task proteče celým workflow od `start` do `end`.

Toto je POC. Většina pohyblivých částí se v dalších fázích vymění — JSON soubory
za úložiště, stdout za OTel, adresáře za storage queues, dummy behavior za
skutečného agenta. **Architektonický požadavek: vyměnit se smí vždy jen driver,
nikdy jeho okolí.**

### Co je mimo rozsah fáze 1

- Git. Harness nic neverzuje, nepoužívá worktrees. `repository` a `worktree` jsou
  neprůhledná metadata, která se pouze vozí.
- Skutečné volání agentů. `ConsumerBehavior` je dummy.
- Perzistentní úložiště, HTTP API, dashboard, scheduler, retry politiky,
  rate limiting.
- Více procesů. Fáze 1 běží v jednom procesu.

## Základní teze (ARD1)

Jednotkou práce je **task**. Task nese svá metadata a putuje mezi frontami.
Modularita znamená, že každá pohyblivá část leží za portem a je nahraditelná
záměnou driveru.

## Datové modely

### Task

```json
{
  "id": "tsk_2026071910000000",
  "repository": "app-backend",
  "worktree": null,
  "workflowTemplate": "default",
  "status": null,
  "lastOutcome": null,
  "lockId": null,
  "created": "2026-07-19T10:00:00Z",
  "history": [],
  "data": {}
}
```

| Pole | Význam |
|---|---|
| `id` | Identita tasku. Stabilní po celý život. |
| `repository` | Neprůhledné. Harness nečte. |
| `worktree` | Neprůhledné. Harness nečte. |
| `workflowTemplate` | Jméno workflow. `WorkflowRepository` podle něj načte definici. |
| `status` | Aktuální krok. `null` u nového tasku. Mění **výhradně dispatcher**. |
| `lastOutcome` | Výsledek posledního běhu behavioru. Zapisuje **výhradně consumer**. |
| `lockId` | Identita držitele leasu, `null` když task nikdo nedrží. |
| `created` | ISO 8601 UTC. |
| `history` | Audit log, viz níže. |
| `data` | Neprůhledný payload. Harness nečte. |

Pole `workflow` (inline definice workflow v tasku) je pro MVP vynecháno.
Používá se pouze `workflowTemplate`.

### HistoryEntry

`history` je audit log, nikoli seznam navštívených fází. Položka se připisuje při
**každé** změně stavu, včetně chybových.

```json
{
  "at": "2026-07-19T10:00:05Z",
  "actor": "dispatcher",
  "from": "design",
  "to": "architecture",
  "outcome": "done"
}
```

- `actor` — `dispatcher` nebo `consumer:<krok>`.
- `from` / `to` — kroky; `null` na začátku, `"end"` / `"failed"` na konci.
- `outcome` — outcome, na jehož základě se rozhodovalo; u consumera outcome,
  který behavior vrátil. U chyby nese `to` hodnotu `"failed"` a přibývá pole
  `reason` s textem důvodu.

Cena je vyšší objem záznamů. Přínos je, že z tasku je vidět celý jeho příběh
včetně toho, proč spadl.

### Workflow

```json
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan",         "on": "done",            "to": "design"},
    {"from": "design",       "on": "done",            "to": "architecture"},
    {"from": "architecture", "on": "done",            "to": "development"},
    {"from": "development",  "on": "done",            "to": "review"},
    {"from": "review",       "on": "done",            "to": "end"},
    {"from": "review",       "on": "request_changes", "to": "development"}
  ]
}
```

Workflow je malý state machine. Každý přechod je trojice `(from, on, to)`, takže
zpětné hrany jsou explicitní a **nemusí být symetrické** — `review` se vrací na
`development`, ale `architecture` se může vracet třeba rovnou na `plan`.

- `start` — krok, do kterého jde task se `status == null`.
- `end` — vyhrazené jméno terminálního uzlu. `to: "end"` znamená přesun do `done/`.
  Uzel `end` nemá odchozí hrany a nemá vlastní frontu.
- Retry téhož kroku se vyjádří jako `to == from`; žádná zvláštní konstrukce
  není potřeba.

`end` je vyhrazený uzel **záměrně**, místo pravidla „stav bez odchozích hran je
konec". Překlep ve jméně kroku by při implicitním pravidle tiše vypadal jako
úspěch; s vyhrazeným `end` spadne task do `failed/`, kde ho uvidíš.

### Outcome

Uzavřený výčet: `done` | `request_changes`. Jiná hodnota z behavioru je chyba
a posílá task do `failed/`.

## Topologie — všechno je fronta

```
<root>/
  workflows/
    default.json
  tasks/                 # inbox dispatchera
    .processing/
  queues/
    plan/
      .processing/
    design/
      .processing/
    architecture/
      .processing/
    development/
      .processing/
    review/
      .processing/
  done/
  failed/
```

`tasks/`, `queues/*`, `done/` i `failed/` jsou instance **téhož** portu
`TaskQueue`. „Hotovo" a „selhalo" jsou prostě fronty, které nikdo nekonzumuje —
pro terminální stavy neexistuje zvláštní kód.

Fronty pod `queues/` se zakládají podle workflow: jedna na každý krok, který se
vyskytne jako `from` nebo `to` v přechodech, kromě `end`.

## Tok

```
tasks/ ──dispatcher──> queues/<krok>/ ──consumer──> tasks/ ──dispatcher──> …
                                                                    │
                                                              done/ nebo failed/
```

1. **Dispatcher** vybere přes `EnqueueStrategy` jeden task z `tasks/` (nebo žádný).
2. Zabere ho (`claim`).
3. Načte workflow podle `workflowTemplate`.
4. Zavolá čistou funkci `route(task, workflow)`.
5. Podle rozhodnutí přepíše `status`, připíše `history` a přesune task do cílové
   fronty / `done/` / `failed/`.
6. **Consumer** nad frontou `<krok>` task zabere, předá ho `ConsumerBehavior`,
   dostane outcome, zapíše `lastOutcome` a `history`, vrátí task do `tasks/`.

### Rozdělení rozhodování

Každý článek zná jen svůj kousek a nesmí sahat vedle:

```
ConsumerBehavior  →  co se stalo     (done | request_changes)
Consumer          →  jen to doručí   (žádné rozhodnutí)
Dispatcher        →  kam to jde dál  (lookup ve workflow)
```

- **`ConsumerBehavior` je jediné místo, kde outcome vzniká.** Jak k němu dojde,
  je jeho vnitřní věc — dnes sleep, zítra agent čtoucí diff.
- **Consumer je tenká obálka.** Nemá **žádnou** větev kódu závislou na hodnotě
  outcome; jen ho zapíše. Objeví-li se v consumeru `if outcome == ...`, prosákla
  odpovědnost přes hranici.
- **Consumer nikdy nemění `status`.** To umí výhradně dispatcher.
- **Dispatcher nikdy nezkoumá `data`.** Rozhoduje jen podle `(status, lastOutcome)`.

## Router

```python
def route(task: Task, workflow: Workflow) -> Decision
```

Čistá funkce. Žádné I/O, žádný filesystem, žádný čas. `Decision` je jedno z:

| Decision | Kdy | Důsledek |
|---|---|---|
| `MoveTo(step)` | našla se hrana | task jde do `queues/<step>/` |
| `Finished` | cíl hrany je `end` | task jde do `done/` |
| `Failed(reason)` | žádná hrana nesedí | task jde do `failed/` |

Pravidla:

- `status is None` → `MoveTo(workflow.start)`.
- Jinak lookup `(status, lastOutcome)`. Nenajde-li se, `Failed`.
- `lastOutcome is None` u tasku, který už status má, je nekonzistence →
  `Failed`.

Celá routovací logika včetně zpětných hran a chybových cest je tím testovatelná
bez jediného souboru na disku.

## Porty a drivery

| Port | Odpovědnost | Driver ve fázi 1 | Vymění se za |
|---|---|---|---|
| `TaskQueue` | `list()`, `claim(task)`, `put(task)`, `recover()` | adresář s JSON soubory | storage queue |
| `EnqueueStrategy` | `select(tasks) -> Task \| None` | FIFO podle `created` | priority, fair-share |
| `WorkflowRepository` | `get(name) -> Workflow` | `workflows/<name>.json` | DB, API |
| `ConsumerBehavior` | `run(task) -> Outcome` | sleep 5 s → `done` | skutečný agent |
| `EventSink` | `emit(event)` | řádky na stdout | OTel |
| `Clock` | `now()`, `sleep(s)` | reálný čas | — (existuje kvůli testům) |

Ke každému portu vzniká i **in-memory driver pro testy**. Celá smyčka pak jede
bez disku a bez pětisekundových čekání; testy nesmí sahat na skutečný čas.

### Poznámky k portům

- `EnqueueStrategy` dostává už načtený seznam tasků a vrací vybraný. Výběr je tím
  oddělen od toho, jak se fronta čte.
- `EventSink` dostává strukturovaný event (jméno, task id, pole), ne
  naformátovaný řetězec. Formátování je věc driveru — jinak by OTel driver
  parsoval text.
- `DummyBehavior` vrací `done` **deterministicky**, s konfigurovatelnou výjimkou:
  pro jeden zvolený krok vrátí při prvním průchodu `request_changes` a napodruhé
  `done`. Bez toho by se zpětná hrana nikdy neproklepla; bez determinismu by se
  smyčka mohla točit donekonečna.

## Souběh, lease a pády

Jeden proces, asyncio: jeden dispatcher task a jeden consumer task na každý krok
workflow, všechny ve stejném event loopu. Polling s konfigurovatelným intervalem.

**`claim()` je atomický `rename` do `<queue>/.processing/`.** Jedna operace řeší
tři věci najednou:

- **lease** — soubor zmizí z fronty, nikdo jiný ho nevybere; `lockId` se zapíše
  do tasku,
- **idempotenci** — přesun buď proběhl, nebo ne; mezistav neexistuje,
- **původ** — protože má `.processing/` každá fronta vlastní, je po pádu vidět,
  odkud task pochází, aniž by se to kamkoli ukládalo.

Prohraje-li proces závod o soubor (`rename` selže, protože už tam není), není to
chyba — jen si vezme další.

**Recovery při startu:** každá fronta vrátí obsah svého `.processing/` zpátky
k sobě a vynuluje `lockId`. Protože je práce ve fázi 1 idempotentní, stačí to.

TTL leasu se ve fázi 1 neimplementuje — jeden proces, který spadl, se zotaví při
startu. `lockId` v modelu ale je, protože ho fáze 2 bude potřebovat.

## Chybové stavy

Vše níže posílá task do `failed/`, připíše `history` s `reason` a vyemituje
event. **Jeden vadný task nesmí zastavit smyčku.**

| Situace | Detekce |
|---|---|
| Neznámý `workflowTemplate` | `WorkflowRepository.get` nenajde |
| `status` bez odpovídající hrany | router vrátí `Failed` |
| Neplatný outcome z behavioru | validace proti výčtu |
| Rozbitý nebo nečitelný JSON | při načtení z fronty |
| Výjimka z `ConsumerBehavior` | zachycena v consumeru |

Rozbitý JSON je zvláštní případ: task se nedá deserializovat, takže mu nelze
připsat historii. Soubor se přesune do `failed/` tak, jak je, a důvod jde jen do
eventu.

## Struktura kódu

```
src/harness/
  models.py            # Task, HistoryEntry, Workflow, Transition, Outcome, Decision
  router.py            # čistá route()
  dispatcher.py
  consumer.py
  ports/
    queue.py
    workflows.py
    strategy.py
    behavior.py
    events.py
    clock.py
  drivers/
    fs_queue.py
    fs_workflows.py
    fifo_strategy.py
    dummy_behavior.py
    stdout_events.py
    memory.py          # in-memory drivery pro testy
  app.py               # wiring + asyncio runtime
  cli.py
```

Závislosti tečou striktně dolů. `models.py` neimportuje nic z balíku.
`ports/` neimportuje `drivers/`. `dispatcher.py` a `consumer.py` znají jen porty,
nikdy konkrétní driver — veškeré wiring je v `app.py`.

Balík se jmenuje `harness`.

## Testovací strategie

| Vrstva | Jak |
|---|---|
| `router.py` | tabulkové unit testy — dopředné hrany, zpětné, `start`, `end`, chybějící hrana, nekonzistentní stav |
| Dispatcher, consumer | in-memory drivery, fake clock, žádný disk |
| Filesystem drivery | tmp adresář; zvlášť atomicita `claim` a recovery z `.processing/` |
| End-to-end | celý průtok `start → end` na in-memory driverech, včetně jednoho `request_changes` |
| Smoke | jeden běh na skutečném filesystemu se zkráceným intervalem |

Test, který ověří, že `dispatcher.py` ani `consumer.py` neimportují nic
z `drivers/`, hlídá hlavní architektonický invariant.

## Ověření hotovosti

Fáze 1 je hotová, když:

1. `harness init` založí strom adresářů podle workflow.
2. Vložení jednoho task JSONu do `tasks/` vede k tomu, že task doputuje do
   `done/`, přičemž prošel všemi pěti kroky a jednou zpětnou hranou.
3. Na stdout je z eventů čitelné, co se s taskem v každém kroku stalo.
4. `history` doputovaného tasku ten průběh věrně popisuje.
5. Task s neznámým `workflowTemplate` skončí v `failed/` a smyčka běží dál.
6. Zabití procesu uprostřed běhu a restart vede k dokončení tasku.

## Stack

Python 3.11 (`/Users/rem/.local/bin/python3.11`), `venv` + `pip install -e ".[dev]"`.
Na stroji není `uv`. Testy `pytest`.

`CLAUDE.md` v repu popisuje mrtvou architekturu předchozího pokusu a bude
přepsán jako součást fáze 1.
