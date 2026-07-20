# Fáze 4 — konektor zdroje tasků (GitHub): Implementation Plan

> **For agentic workers:** implementuj task po tasku. Každý task: napiš padající
> test → spusť (červená) → implementuj → spusť (zelená) → commit. Kroky mají
> checkbox (`- [ ]`).

**Goal:** Task přitéká z GitHub Issues za portem `TaskSource` (`poll` /
`report_progress` / `finish`), jeho stav se promítá zpět změnou labelů. GitHub je
jeden driver; filesystem/Jira jsou sourozenci. Vše za porty, které jdou vyměnit.

**Spec:** `docs/superpowers/specs/2026-07-20-orchestration-phase4-design.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. **Žádná nová produkční
závislost** — reálný GitHub client jede na stdlib `urllib.request`.

## Global Constraints

- **Rozhodovací role z fáze 1–2 platí.** Consumer nevětví na outcome. Status mění
  dispatcher. Router je čistá funkce a `data.source` **nečte**.
- **`TaskSource` neimportuje `dispatcher`/`consumer`.** Sahá na něj jen
  `SourcePoller` (jádro, jen porty) a `SourceReflectorSink` (driver). Wiring v
  `app.py`. Hlídá `test_architecture.py`.
- **Projekce ven je idempotentní a izolovaná.** `report_progress` dvakrát = no-op;
  selhání GitHubu nesmí zastavit smyčku.
- **Původ tasku žije v `task.data.source`**, ne ve vedlejším stavu.
- **Testy nesahají na skutečný čas ani síť.** In-memory + `FakeClock` +
  `FakeGithubClient`. Reálný `HttpGithubClient` se v unit sadě netestuje (jen se
  dodá; volitelný guarded integrační test smí být `skip` bez tokenu).
- Čas je ISO 8601 UTC se sufixem `Z`.
- Vývoj na branchi `claude/harness-github-connector-p76esm` (instrukce sezení,
  ne konvence z `CLAUDE.md`).

---

### Task 1: Port `TaskSource` + `MemoryTaskSource`

**Files:** `src/harness/ports/source.py`, `src/harness/drivers/memory.py`,
`tests/test_source_memory.py`.

**Interfaces:**
- `Progress(step: str, summary: str = "")` — frozen.
- `FinishResult(ok: bool, pr_url: str | None = None, summary: str = "")` — frozen.
- `TaskSource(ABC)`: atribut `kind: str`; `poll() -> list[Task]`;
  `report_progress(task, progress) -> None`; `finish(task, result) -> None`.
- `MemoryTaskSource(TaskSource)`: `kind = "memory"`.
  - konstruktor bere `clock`, `workflow="default"`, volitelně `repository`,
    `worktree_root="/memory/worktrees"`.
  - `submit(title, body="") -> str` (test helper) přidá „issue" do interní fronty
    a vrátí jeho id.
  - `poll()`: vezme dosud nezkonzumované issue, každé označí za claimed a poskládá
    `Task(id=new_task_id(), workflow_template=workflow, created=clock.now(),
    repository=repository, worktree=f"{worktree_root}/{id}",
    data={"title","body","source":{"kind":"memory","issue":<issue-id>}})`.
  - `report_progress`/`finish`: zapíší do `self.states: dict[str, list]`
    (issue-id → seznam projekcí) — pro aserce. `_mine(task)` guard podle `kind`.

- [ ] **Step 1:** Testy — dva `submit`+`poll` dají dva tasky s `data.source.kind
  == "memory"`; druhý `poll` bez nového submitu vrátí `[]` (claim drží);
  `report_progress`/`finish` zapíší projekci pod správné issue-id; task cizího
  `kind` (ručně složený) je v `report_progress` ignorován (guard).
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: port TaskSource + in-memory driver`.

---

### Task 2: `SourcePoller` (jádro)

**Files:** `src/harness/source_poller.py`, `tests/test_source_poller.py`.

**Interfaces:**
- `SourcePoller(*, source: TaskSource, inbox: TaskQueue, events: EventSink)`.
- `tick() -> bool`: `tasks = source.poll()`; každý `inbox.put(task)` a
  `events.emit("ingested", task_id=…, queue="tasks", task=task.to_dict())`;
  vrátí `bool(tasks)`. **Výjimku z `poll()` chytit** → `events.emit("source_error",
  source=source.kind, error=str(e))`, vrátit `False` (smyčka pak spí a zkusí zas).
- Importuje **jen porty** (`ports.source`, `ports.queue`, `ports.events`).

- [ ] **Step 1:** Testy s `MemoryTaskSource` + `MemoryTaskQueue` +
  `MemoryEventSink` — po `submit`+`tick` je task v inboxu a padl event `ingested`
  s `queue="tasks"` a `task`; prázdný poll → `tick()` False; `poll` co vyhodí →
  `tick()` False a event `source_error` (fake source s `raises=True`).
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: SourcePoller plní inbox ze zdroje`.

---

### Task 3: `SourceReflectorSink` (projekce ven)

**Files:** `src/harness/drivers/source_reflector.py`,
`tests/test_source_reflector.py`.

**Interfaces:**
- `SourceReflectorSink(EventSink)`: `__init__(self, sources: list[TaskSource])`.
- `emit(name, **fields)`:
  - potřebuje `fields["task"]` (dict) → `Task.from_dict`; jinak return.
  - `name == "dispatched"`: `progress = Progress(step=fields.get("to") or
    fields.get("queue",""), summary="")`; pro každý source `source.report_progress(task, progress)`.
  - `name == "finished"`: `finish(task, FinishResult(ok=True))`.
  - `name == "failed"`: `finish(task, FinishResult(ok=False, summary=fields.get("reason","")))`.
  - jiné názvy: ignoruj.
  - routing: volej **všechny** sourcey; guard `_mine` je v adapteru (cizí `kind`
    → no-op). Reflector sám `kind` neřeší.
- Robustnost: `emit` nesmí propadnout výjimku ven jinak, než co odchytí
  `CompositeEventSink` — ale sink sám drží kontrakt „nezlom se o data" (chybějící
  pole → tichý return).

- [ ] **Step 1:** Testy s `MemoryTaskSource` — event `dispatched` s taskem
  (`data.source.kind=="memory"`) zavolá `report_progress` s `step` z `to`;
  `finished` → `finish(ok=True)`; `failed` → `finish(ok=False)` s reason; event
  bez `task` → nic; task cizího kind → source ho ignoruje (přes guard).
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: SourceReflectorSink promítá stav do zdroje`.

---

### Task 4: Wiring + e2e na in-memory driverech

**Files:** `src/harness/app.py`, `tests/test_phase4_e2e.py`,
`tests/test_app.py` (rozšíření).

**Interfaces:**
- `build(...)` získává `sources: list[TaskSource] | None = None`.
  - default `[]` (zpětně kompatibilní).
  - postaví `pollers = [SourcePoller(source=s, inbox=inbox, events=events) for s in sources]`.
  - `events` composite dostane navíc `SourceReflectorSink(sources)` — přidat do
    `CompositeEventSink(...)` **až za** `ProjectionSink`, aby projekce ven nebyla
    před projekcí do boardu (na pořadí nezáleží funkčně, ale drž to čitelné).
  - `Harness` dostane `pollers`; `run()` je gatheruje vedle dispatcheru/consumerů
    (`_source_loop(poller, poll_interval, stop)` — tik/spánek jako ostatní smyčky).
- `Harness.__init__` bere `pollers: list[SourcePoller]` (default `[]`).

- [ ] **Step 1:** E2E — `MemoryTaskSource.submit("Fix bug")`; `build` s tímto
  source, in-memory workspace/artifacts/forge, `FakeClock`, `ScriptedBehavior`
  (nebo Dummy). Pusť smyčku (vzor z `test_phase2_e2e.py` — omezený počet tiků /
  `stop`). Ověř:
  - task doputoval do `done`;
  - `source.states[issue]` obsahuje aspoň jednu `report_progress` a jeden
    `finish(ok=True)`;
  - task nesl `data.source.kind == "memory"` celou cestu (projekce ven proběhla).
  - Kontrola zpětné kompatibility: task vložený přímo do inboxu (bez source)
    doteče taky a `source.states` o něm nic nemá.
- [ ] **Step 2:** Červená → **Step 3:** wiring → **Step 4:** zelená (celá sada).
- [ ] **Step 5:** Commit `feat: wiring fáze 4 — poller a reflector v běhu`.

---

### Task 5: GitHub client — `GithubClient`, `FakeGithubClient`, `HttpGithubClient`

**Files:** `src/harness/drivers/github_client.py`,
`tests/test_github_client.py`.

**Interfaces:**
- `Issue(number, title, body, url, labels: tuple[str,...])` — frozen; `labels`
  je tuple.
- `GithubClient(ABC)`: `list_issues(repo, *, label) -> list[Issue]`;
  `add_label(repo, number, label)`; `remove_label(repo, number, label)`
  (idempotentní — chybějící label je no-op).
- `FakeGithubClient`: issue drží v `dict[int, Issue]` (seedni v konstruktoru nebo
  `add_issue`); `list_issues` vrátí ty se `label` v `labels`; add/remove přeskládají
  `labels` (frozen → nová instance přes `replace`). `remove_label` chybějícího
  ignoruje.
- `HttpGithubClient(token, *, api="https://api.github.com", opener=None)`:
  - `urllib.request` (stdlib). Header `Authorization: Bearer <token>`,
    `Accept: application/vnd.github+json`.
  - `list_issues`: `GET {api}/repos/{repo}/issues?state=open&labels={label}` →
    mapuj JSON na `Issue` (pozor: PR jsou taky „issues" — odfiltruj ty s klíčem
    `pull_request`).
  - `add_label`: `POST …/issues/{n}/labels` s `{"labels":[label]}`.
  - `remove_label`: `DELETE …/issues/{n}/labels/{label}`; 404 spolkni.
  - `opener` injektovatelný, ať jde otestovat bez sítě (fake opener vrátí
    připravené odpovědi). **Reálné HTTP se v CI nevolá.**

- [ ] **Step 1:** Testy — `FakeGithubClient`: `list_issues` filtruje podle labelu;
  add/remove mění `labels`; remove neexistujícího je no-op. `HttpGithubClient`
  s fake `opener`: `list_issues` odfiltruje PR a namapuje pole; `add`/`remove`
  sestaví správnou metodu+URL+tělo (asertuj na zachycený request z fake openeru).
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: GithubClient + fake a stdlib http driver`.

---

### Task 6: `GithubTaskSource`

**Files:** `src/harness/drivers/github_source.py`,
`tests/test_github_source.py`.

**Interfaces:**
- `GithubTaskSource(TaskSource)`, `kind = "github"`.
- `__init__(*, client, clock, repo, workflow="default", repository, worktree_root,
  select_label="harness:todo", claimed_label="harness:queued",
  pr_label="harness:pr-open", failed_label="harness:failed",
  step_labels: dict[str,str] | None = None)`.
- `_managed`: `{claimed_label, pr_label, failed_label, *step_labels.values()}`.
- `poll()`: `for issue in client.list_issues(repo, label=select_label):`
  `client.remove_label(repo, issue.number, select_label)`;
  `client.add_label(repo, issue.number, claimed_label)`; poskládej `Task` s
  `data={"title":issue.title,"body":issue.body,
  "source":{"kind":"github","repo":repo,"issue":issue.number,"url":issue.url}}`.
- `report_progress(task, progress)`: `if not _mine: return`;
  `label = step_labels.get(progress.step)`; `if label: _set_state(number, label)`.
- `finish(task, result)`: `if not _mine: return`;
  `_set_state(number, pr_label if result.ok else failed_label)`.
- `_set_state(number, target)`: `for l in _managed - {target}:
  client.remove_label(repo, number, l)`; `client.add_label(repo, number, target)`.
- `_mine(task)`: `task.data.get("source",{}).get("kind") == "github"`;
  `_issue(task)`: `task.data["source"]["issue"]`.

- [ ] **Step 1:** Testy s `FakeGithubClient` (seed issue #1 s `harness:todo`):
  - `poll()` → issue #1 má `harness:queued` a ne `todo`; task má `data.source.issue==1`.
  - druhý `poll()` (žádné nové `todo`) → `[]`.
  - `report_progress(task, Progress("development"))` se `step_labels={"development":
    "harness:coding"}` → issue má `harness:coding`, ne `queued`.
  - neznámý krok (není v `step_labels`) → labely beze změny (coarse default).
  - `finish(ok=True)` → `harness:pr-open`; `finish(ok=False)` → `harness:failed`;
    vždy právě jeden managed label.
  - task bez `data.source` → `report_progress`/`finish` no-op (guard).
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: GithubTaskSource — issue → task, stav → label`.

---

### Task 7: CLI, architektura, smoke, dokumentace

**Files:** `src/harness/cli.py`, `tests/test_architecture.py`,
`tests/test_smoke_github.py` (nový), `CLAUDE.md`.

**Interfaces:**
- `cli.py run`: přidej `--github-repo`, `--github-label` (default `harness:todo`),
  `--github-workflow` (default `default`), `--worktree-root`. Když je
  `--github-repo` a `GITHUB_TOKEN` v env, sestav
  `GithubTaskSource(client=HttpGithubClient(token), clock=SystemClock(), repo=…,
  repository=<repo lokální cesta>, worktree_root=…, step_labels=<default map pro
  DEFAULT_DEFINITION kroky>)` a předej `build(..., sources=[source])`. Bez toho
  `sources=[]` (beze změny chování).
- Default `step_labels` pro výchozí workflow: rozumná coarse mapa, např.
  `{"development":"harness:in-progress","review":"harness:in-review","land":"harness:landing"}`
  (ostatní kroky bez labelu → míň šumu; je to jen default, ne zákon).

- [ ] **Step 1 (architektura):** rozšiř `test_architecture.py`:
  - `dispatcher.py`/`consumer.py` neimportují `harness.ports.source`
    (přidej `ports.source` do kontroly, nebo nový test analogický `WORK_PORTS`).
  - `source_poller.py` importuje jen `harness.ports.*` + `harness.models`
    (žádný `harness.drivers`).
  - `test_only_app_and_cli_wire_drivers` musí projít i s novými moduly (source_poller
    je top-level a drivery neimportuje).
- [ ] **Step 2 (smoke):** `tests/test_smoke_github.py` — plně in-memory až na
  záměr fáze: `FakeGithubClient` se seedovaným issue `harness:todo`,
  `GithubTaskSource`, in-memory workspace/artifacts/forge, `FakeClock`,
  `ScriptedBehavior`. Pusť smyčku (krátce, jako phase2 e2e — **bez reálného
  spánku**, přes omezený počet tiků / `stop`). Ověř konečný stav: issue má
  `harness:pr-open`, task v `done/`. (Tenhle smoke **nesahá na síť ani disk** —
  je to e2e konektoru, ne reálného GitHubu.)
- [ ] **Step 3 (docs):** `CLAUDE.md` — mapa modulů o `ports/source`,
  `source_poller`, `source_reflector`, `github_*`; invarianty 13–16; sekce
  „Co je za co zodpovědné" o `TaskSource` a projekci ven.
- [ ] **Step 4:** `.venv/bin/pytest -q` — celá sada zelená.
- [ ] **Step 5:** Commit `docs+test: architektura, smoke a CLAUDE.md pro fázi 4`.

---

## Pořadí a závislosti

```
T1 (TaskSource + Memory) ─┬─> T2 (SourcePoller) ─┐
                          └─> T3 (Reflector) ────┼─> T4 (wiring + e2e) ─┐
T5 (GithubClient) ─────────> T6 (GithubTaskSource) ────────────────────┴─> T7 (CLI+arch+smoke+docs)
```

T1 je základ (port + memory driver). T2/T3 stojí na T1 a jdou paralelně, ale oba
sahají na testy s `MemoryTaskSource` — psát sériově kvůli sdílenému `memory.py`.
T4 spojuje smyčku. T5/T6 jsou GitHub větev nezávislá na T2–T4 až do T7. T7 uzavírá
(CLI vdrátuje reálný client, architektura + smoke + docs).

## Poznámky pro implementaci

- **`memory.py` needituj paralelně s jiným taskem** — T1 do něj přidává
  `MemoryTaskSource`; ostatní se ho nedotýkají.
- **Reflector čte `to` i `queue`.** `dispatched` nese `from`/`to` (viz
  `dispatcher._move`); `queue` je taky přítomné. Ber `to` a `queue` jako fallback.
- **`CompositeEventSink` izoluje selhání** — projekce ven se nemusí bát, že shodí
  smyčku, ale i tak drž `report_progress` idempotentní (reconcile zadarmo).
- **Žádná nová produkční závislost.** `HttpGithubClient` = `urllib.request` +
  `json`. Kdyby to svádělo k `requests`/`httpx`, nedělej to.
