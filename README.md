# harness

Orchestrační harness pro více agentů. Jednotkou práce je **task**; ten putuje
mezi frontami podle **workflow**, což je malý state machine s explicitními
hranami pro každý outcome.

Fáze 1 je POC celé smyčky: task proteče workflow od `start` do `end`, ale práci
zatím zastupuje dummy behavior. Skutečné agenty, perzistentní úložiště a git
přijdou v dalších fázích.

## Instalace

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Rychlý start

```sh
harness init --root /tmp/harness-demo
harness submit --root /tmp/harness-demo --repo app-backend \
    --data '{"request": "add rate limiting"}'
harness run --root /tmp/harness-demo --delay 0.5 --request-changes-at review
```

## Board

`harness run` vedle orchestrační smyčky servíruje read-only board na
`http://127.0.0.1:8420/`. Sloupce jsou kroky workflow plus `done` a `failed`,
karty jsou tasky, klik ukáže metadata a historii. Board se aktualizuje sám
přes SSE.

`--api-port 0` board vypne.

Board čte výhradně přes port `BoardView`. O tom, že tasky jsou JSON soubory
a fronty adresáře, neví — a vědět nesmí.

## Jak práce teče

```
tasks/ ──dispatcher──> queues/<krok>/ ──consumer──> tasks/ ──dispatcher──> …
                                                                    │
                                                              done/ nebo failed/
```

1. Dispatcher vezme task z `tasks/`, načte workflow podle `workflowTemplate`
   a podle dvojice `(status, lastOutcome)` najde cílový krok.
2. Přepíše `status`, připíše řádek do `history` a přesune task do `queues/<krok>/`.
3. Consumer nad tou frontou předá task `ConsumerBehavior`, dostane zpět outcome
   (`done` nebo `request_changes`), zapíše ho a vrátí task do `tasks/`.
4. Až hrana ukáže na `end`, task končí v `done/`. Cokoli nesměrovatelného končí
   v `failed/` s důvodem v historii.

## Workflow

```json
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan", "on": "done", "to": "design"},
    {"from": "review", "on": "done", "to": "end"},
    {"from": "review", "on": "request_changes", "to": "development"}
  ]
}
```

Zpětné hrany jsou explicitní a nemusí být symetrické. Retry téhož kroku se
vyjádří jako `to == from`.

## Architektura

Každá pohyblivá část leží za portem a vymění se záměnou driveru:

| Port | Fáze 1 | Později |
|---|---|---|
| `TaskQueue` | adresář s JSON soubory | storage queue |
| `EnqueueStrategy` | FIFO podle `created` | priority, fair-share |
| `WorkflowRepository` | `workflows/<name>.json` | DB, API |
| `ConsumerBehavior` | sleep → `done` | skutečný agent |
| `EventSink` | řádky na stdout | OTel |

Rozhodování je rozděleno na tři role, které se nepřekrývají: `ConsumerBehavior`
říká *co se stalo*, dispatcher *kam to jde dál*, consumer jen doručuje.
