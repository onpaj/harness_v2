# Board UI — přehled nad harness

Status: schváleno
Datum: 2026-07-19
Navazuje na: `2026-07-19-orchestration-phase1-design.md`

## Cíl

Webová aplikace, která ukazuje, co se v harness právě děje. Sloupce jsou kroky
workflow, karty jsou tasky ve svém aktuálním stavu, klik na kartu ukáže metadata
a historii tasku. Board se aktualizuje sám.

Vzniká paralelně s implementací fáze 1 a je **součástí harness balíčku** — ne
samostatný projekt.

### Architektonický požadavek

Stejný jako u fáze 1 a je pro tento návrh určující: **UI nesmí vědět nic o
driverech, na kterých harness běží.** Nesmí vědět, že tasky jsou JSON soubory,
že fronty jsou adresáře ani že jednou budou v databázi. Vidí výhradně port.

### Co je mimo rozsah

- Jakákoli akce. Board je read-only pozorovatelna, ne ovladač. Žádný retry,
  requeue, ruční přesun karty ani zakládání tasků.
- Autentizace, více uživatelů, perzistence pohledu.
- Více workflow. Fáze 1 má jen `default`; board ho zobrazí napevno. Přepínač
  přibude, až přibudou workflow.

## Základní teze

Zdrojem pravdy pro UI je **proud eventů**, ne úložiště. Board je projekce nad
tímto proudem, drží se v paměti a API ji čte přes port `BoardView`.

Důsledek: UI se k datům nedostává jinou cestou než tou, kterou už harness
publikuje. Nemá vlastní čtení ze storage, tedy ani žádnou cestu, kterou by
znalost driverů mohla prosáknout.

## Topologie

```
dispatcher/consumer ──emit──> EventSink (CompositeEventSink)
                                 ├──> StdoutEvents
                                 └──> ProjectionSink ──> BoardProjection
                                                            ▲        │
                              TaskQueue.list() ─hydratace───┘        │
                                                                     ▼
                                                      BoardView (port) <── FastAPI
```

Vše běží v **jednom procesu** a jednom asyncio loopu — dispatcher, consumeři
i uvicorn. Projekce je tím pádem prostý objekt v paměti; žádný transport, žádná
serializace navíc.

Cena: UI spadne s harnessem a škáluje s ním. Pro POC je to správný obchod;
oddělení do vlastní služby je záměna driveru `BoardView` a přidání transportu
pod `ProjectionSink`, ne přepis API.

## Přírůstky k fázi 1

| Přírůstek | Druh | Odpovědnost |
|---|---|---|
| `BoardView` | **port** | `snapshot()`, `get(id)`, `subscribe()` |
| `BoardProjection` | read model | drží stav boardu, aplikuje eventy |
| `CompositeEventSink` | driver `EventSink` | rozešle event více sinkům |
| `ProjectionSink` | driver `EventSink` | doručí event do `BoardProjection` |
| `api/` | aplikace | FastAPI + šablony |

Dispatcher ani consumer se **nemění**. Nevědí, že posluchačů eventů přibylo.

## Změna kontraktu eventů

Event musí nést **celý snapshot tasku**, ne pouze `task_id`.

Bez toho projekce neumí zobrazit task, který vznikl až po startu procesu:
hydratace o něm neví a event o něm neříká nic než identitu. S plným snapshotem
je projekce soběstačná a nikdy nesahá zpátky do fronty.

```json
{
  "name": "task.routed",
  "at": "2026-07-19T10:00:05Z",
  "actor": "dispatcher",
  "queue": "design",
  "task": { "id": "tsk_…", "status": "design", "…": "…" }
}
```

`queue` je cílová fronta — jméno kroku, `done` nebo `failed`. Viz Zařazení do
sloupce.

Cena je objemnější řádek na stdout. Formátování zůstává věcí driveru —
`StdoutEvents` si smí snapshot zkrátit, `ProjectionSink` ho potřebuje celý.

### Viditelnost nových tasků

Task čerstvě vhozený do `tasks/` je na boardu vidět až ve chvíli, kdy ho
dispatcher poprvé odbaví, tedy se zpožděním nejvýše jednoho poll intervalu.

Proto board **nemá sloupec Inbox** — byl by prakticky vždy prázdný a jeho obsah
by byl nahodilý podle toho, kdy se stránka načetla.

## BoardProjection

### Hydratace při startu

In-memory projekce je po restartu prázdná, ale tasky ležící ve frontách žádný
event nevygenerují, dokud se nepohnou. Board by tedy lhal o stavu systému.

Při startu proto projekce jednorázově přečte **všechny fronty přes
`TaskQueue.list()`** — `queues/*`, `done/` i `failed/` — a postaví si z nich
výchozí stav. Teprve pak začne aplikovat eventy.

Čte přes port, nikoli přes filesystem; požadavek na neznalost driverů drží.

Cena: projekce má dva zdroje, snapshot a proud. Je to vědomé — alternativou byl
perzistentní event log s replayem, což znamená formát, rotaci a replay time už
ve fázi 1.

Tasky v `<queue>/.processing/` se do hydratace zahrnou také; jinak by po
restartu zmizely právě ty tasky, na kterých se pracovalo.

### Stav a revize

Projekce drží mapu `task_id -> Task` a monotónně rostoucí `revision`, které se
zvýší při každé aplikované změně. `revision` jde ven v SSE události a slouží
klientovi k přeskočení zbytečného překreslení.

### Zařazení do sloupce

Sloupec **není** odvozen ze `status` — `done/` a `failed/` nejsou kroky
workflow a task v nich si nese poslední `status`, který měl. Projekce proto
zařazuje podle zdroje:

- **Při hydrataci** podle fronty, ze které byl task načten (`queues/<krok>`,
  `done/`, `failed/`).
- **Za běhu** podle rozhodnutí, které event nese: `MoveTo(step)` → sloupec
  `step`, `Finished` → `Done`, `Failed` → `Failed`.

Event tedy vedle snapshotu tasku nese i cílovou frontu. Bez toho by projekce
musela dohadovat terminální stav ze `status`, což je přesně ta nejednoznačnost,
kvůli které má fáze 1 vyhrazený uzel `end`.

`lockId != null` znamená, že se na tasku právě pracuje — badge na kartě.

## Port BoardView

```python
class BoardView(Protocol):
    def snapshot(self) -> Board: ...
    def get(self, task_id: str) -> Task | None: ...
    def subscribe(self) -> AsyncIterator[int]: ...   # yielduje revision
```

Toto je jediné, co API vidí. `BoardProjection` je dnešní driver. Až bude read
model ve fázi 2 v databázi, vymění se driver; API se nedotkne.

`subscribe()` je součást portu záměrně — kdyby ho API řešilo samo, muselo by
znát vnitřek projekce.

Implementace `subscribe()`: `asyncio.Queue` na připojeného klienta, **bounded**,
při zaplnění se přebytek zahazuje. U události, která nenese data a jen říká
„podívej se znovu", je zahození bezpečné — další notifikace přijde a fragment je
vždy celá pravda.

## API

Všechny endpointy jsou read-only.

| Endpoint | Vrací |
|---|---|
| `GET /` | HTML stránka boardu |
| `GET /fragment/board` | HTML fragment se sloupci — cíl htmx swapu |
| `GET /fragment/task/{id}` | HTML fragment detailu do modalu |
| `GET /api/events` | SSE stream |
| `GET /api/board` | JSON snapshot boardu |
| `GET /api/tasks/{id}` | JSON detail tasku |

JSON varianty htmx nepoužívá. Existují jako testovací povrch a případný druhý
klient. Obě větve čtou z téhož `BoardView.snapshot()`, takže se nemohou rozejít.

Neznámé `task_id` vrací 404 v obou větvích.

## Živý refresh

Server posílá přes SSE **holé oznámení**, ne data ani diff:

```
event: board
data: {"revision": 42}
```

Prohlížeč na něj reaguje `hx-get="/fragment/board"` a překreslí sloupce.

Tím padá celá kategorie problémů: reconnect nepotřebuje dopočítávat zmeškané
události (další notifikace stejně přijde a fragment je vždy celá pravda), pomalý
klient nenafoukne buffer a nezáleží na pořadí zpráv.

Dvě opatření:

- **Coalescing** — nejvýše jedna notifikace za 250 ms. Pět consumerů by jinak
  dokázalo tepat překreslováním.
- **Revision** — klient si pamatuje poslední vykreslenou a swap přeskočí, když
  se nezměnila.

## UI

### Sloupce

Kroky workflow `default` v pořadí dosažitelnosti ze `start`; zpětné hrany se při
určování pořadí ignorují, jinak by pořadí nebylo definované. Napravo `Done`
a `Failed`.

Karty ve sloupci řazené podle `created` vzestupně — tedy v pořadí, v jakém je
vezme FIFO `EnqueueStrategy`. Sloupec pak čte jako fronta, kterou skutečně je.

### Karta

`id`, `repository`, čas ve stavu, badge `lastOutcome` (`request_changes`
vizuálně odlišené) a badge **„zpracovává se"** při `lockId != null`.

Ten poslední badge je jediný signál, který odlišuje task čekající ve frontě od
tasku, na kterém právě běží behavior.

### Detail

Klik na kartu otevře modal: všechna metadata tasku a `history` jako časová osa
— `at`, `actor`, `from → to`, `outcome`, u chyby `reason`.

Historie je tady hlavní hodnota. Je z ní vidět, proč task spadl a kolikrát se
vrátil z `review`.

### Stack

Jinja2 šablony ve FastAPI, htmx se SSE rozšířením. Žádný build step, žádné
`node_modules`, celé UI je součástí Python balíčku.

Cena: bohatší interakce později narazí na strop. Pro board s pěti sloupci
a modalem je to daleko.

## Struktura kódu

```
src/harness/
  ports/
    board.py               # BoardView
  projection.py            # BoardProjection
  drivers/
    composite_events.py    # CompositeEventSink
    projection_events.py   # ProjectionSink
  api/
    app.py                 # factory(view: BoardView) -> ASGI app
    routes.py
    templates/
      board.html
      _columns.html
      _task.html
    static/
  app.py                   # wiring: composite sink, hydratace, uvicorn v témže loopu
```

Závislosti:

- `projection.py` importuje `models.py` a `ports/`. Nikdy `drivers/`.
- `api/` importuje `ports/board.py` a `models.py`. Nic víc.
- Factory bere `BoardView` jako parametr. Proto jde API testovat s fake view
  a proto o driverech nemůže vědět ani omylem.
- Veškeré wiring zůstává v `app.py`.

## Chybové stavy

| Situace | Chování |
|---|---|
| Task, ke kterému nedorazila hydratace ani event | na boardu není; objeví se prvním eventem |
| Event s neznámým `task_id` | projekce ho založí jako nový záznam |
| Výjimka v `ProjectionSink` | zachycena v `CompositeEventSink`; **selhání sinku nesmí zastavit smyčku ani ostatní sinky** |
| Odpojený SSE klient | jeho fronta se zahodí; server nic dalšího neřeší |
| Přetečená fronta SSE klienta | přebytek se zahodí, spojení zůstává |

## Testovací strategie

| Vrstva | Jak |
|---|---|
| `BoardProjection` | tabulkově: hydratace, event → snapshot, zpětná hrana, přesun do `failed`, monotonie `revision` |
| `CompositeEventSink` | výjimka z jednoho sinku neshodí ostatní |
| API | `httpx` + fake `BoardView`; JSON i fragmenty; 404 na neznámé id |
| SSE | fake view, ruční tick → coalescing a doručení notifikace |
| End-to-end | celá smyčka na in-memory driverech + HTTP klient: task doteče do `done/` a board to po cestě odráží |
| Invariant | `api/` ani `projection.py` neimportují nic z `drivers/` |

Testy nesmí sahat na skutečný čas — coalescing se testuje přes `Clock`, ne přes
`sleep`.

## Ověření hotovosti

1. Spuštěný harness servíruje board na lokálním portu.
2. Board po startu ukazuje tasky, které už ve frontách ležely.
3. Task procházející workflow se na boardu posouvá mezi sloupci bez zásahu
   uživatele.
4. Task, na kterém běží behavior, má badge „zpracovává se".
5. Task s `request_changes` je vidět, jak se vrátil z `review` do `development`.
6. Detail doputovaného tasku ukazuje celou historii včetně zpětné hrany.
7. Task s neznámým `workflowTemplate` se objeví ve sloupci `Failed` i s důvodem.
8. Restart procesu board obnoví do stavu odpovídajícího frontám.
