# harness_v2 — orientace pro Claude

Orchestrační harness pro více agentů. Jednotkou práce je **task**, který putuje
mezi frontami podle **workflow** — malého state machine s explicitními hranami.

Spec fáze 1: `docs/superpowers/specs/2026-07-19-orchestration-phase1-design.md`
Plán fáze 1: `docs/superpowers/plans/2026-07-19-orchestration-phase1.md`

Projekt se staví **po fázích**. Fáze 1 je POC orchestrační smyčky; skutečné
agenty, perzistentní úložiště ani git v ní nejsou.

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

## Práce tady

```sh
.venv/bin/pytest -q
```

Python je **3.11** (`/Users/rem/.local/bin/python3.11`), na stroji **není `uv`** —
plain `venv` + `pip install -e ".[dev]"`. Runtime nemá žádné produkční závislosti.

Unit a integrační testy běží na in-memory driverech a `FakeClock` — bez disku
a bez skutečného čekání. Nikdy do nich nepiš test, který spí v reálném čase.

Jedinou záměrnou výjimkou je `tests/test_smoke.py` — běží na skutečném
filesystemu a poluje reálným `asyncio.sleep(0.01)`, protože je to jediné
místo, které ověřuje filesystémový driver naživo, end-to-end. Nejde o
nedopatření a neuklízej ho do in-memory podoby — tím by zmizelo jediné
pokrytí reálného FS v celé sadě.

## Git konvence

**Commituj přímo do `main`.** V této fázi je to zamýšlený postup — nezakládej
branch, neotevírej PR a neptej se. Platí to pro repo harnessu samotného.

## Mapa modulů

Závislosti tečou striktně dolů, cykly nejsou.

| Vrstva | Moduly |
|---|---|
| Základ | `models` (neimportuje nic z balíku), `ids` |
| Logika | `router` (zná jen `models`) |
| Porty | `ports/{queue,workflows,strategy,behavior,events,clock}` |
| Orchestrace | `dispatcher`, `consumer` — znají jen porty |
| Drivery | `drivers/{fs_queue,fs_workflows,fifo_strategy,dummy_behavior,stdout_events,system_clock,memory}` |
| Okraje | `app` (wiring), `cli` |

- `projection.py` — in-memory read model boardu; hydratace z front + proud eventů
- `ports/board.py` — port `BoardView`, kterým se dívá UI
- `api/` — FastAPI board; vidí jen `BoardView`, nikdy driver

## Co je za co zodpovědné

- **`TaskQueue`** — inbox, fronty kroků, `done/` i `failed/` jsou instance téhož
  portu. Terminální stavy jsou prostě fronty, které nikdo nekonzumuje.
- **`claim()`** je atomický `rename` do `<queue>/.processing/`. Jedna operace řeší
  lease, idempotenci i původ po pádu.
- **`END = "end"`** je vyhrazený uzel. Není to „stav bez odchozích hran" —
  překlep by tak tiše vypadal jako úspěch.

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

## Operátor

Ondrej Pajgrt — „Ondrej" / „Rem". GitHub `onpaj`. Europe/Prague. Kontext stroje
(NanoClaw, podman) je v `~/CLAUDE.md`.

Předchozí pokus o tuto myšlenku leží v historii tohoto repa na commitu `7bc0e6e`;
`main` byl vyprázdněn commitem `b7cab63`, aby se stavělo po fázích od začátku.
