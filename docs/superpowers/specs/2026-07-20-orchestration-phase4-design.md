# Fáze 4 — konektor zdroje tasků (GitHub)

Status: návrh
Datum: 2026-07-20

## Cíl

Task přestává vznikat jen ručně (`harness submit` píše JSON do inboxu). Ve fázi 4
task **přitéká z reálného světa řízení práce** — z GitHub Issues — a jeho stav se
**promítá zpět** do toho světa. Celý ten vnější svět leží za **jedním portem**
`TaskSource` se třemi slovesy:

- **`poll()`** — přines nové, ještě nezkonzumované tasky.
- **`report_progress(task, progress)`** — promítni průběžný stav ven.
- **`finish(task, result)`** — promítni terminální stav (úspěch / selhání).

GitHub je **jedna implementace** toho portu. Souborový „drop-folder" je druhá.
Jira, Linear apod. jsou další — **záměna driveru, nikdy jeho okolí**. Jak se stav
renderuje ven, je věcí driveru: GitHub adapter to dělá **změnou labelů** na issue.

Fáze 4 je pořád POC. Skutečný agent zůstává dummy; reálné volání GitHubu je
tenký HTTP driver za `GithubClient` — testy běží na in-memory fake.

### Co je ve fázi 4 nově

- Port `TaskSource` a smyčka `SourcePoller`, která z něj plní inbox — druhý
  producent téže fronty vedle `harness submit`.
- Původ tasku putuje **s taskem** v `data.source` (`{kind, repo, issue, url}`),
  durabilně. Jakákoli pozdější projekce ven (label, „Closes #42") ho čte odtud.
- `SourceReflectorSink` — `EventSink`, který překládá proud harness eventů na
  volání `report_progress`/`finish`. Zrcadlí `ProjectionSink` (board), jen míří
  ven místo do UI.
- GitHub drivery: `GithubTaskSource` + `GithubClient` (ABC) s `FakeGithubClient`
  (testy) a `HttpGithubClient` (reálný, stdlib `urllib`, žádná nová závislost).

### Co je pořád mimo rozsah

- **Skutečný agent.** Behavior je dummy jako ve fázi 2/3.
- **Reálný GitHub v testech.** `HttpGithubClient` se dodává, ale unit/e2e sada
  běží na `FakeGithubClient`. Ostrý běh je záměna clientu.
- **Více procesů, rate-limit backoff, TTL leasu, webhooky.** Poll je prostý tik.

## Základní teze (ARD4): vnější svět je jeden port

Harness nezná GitHub. Zná `TaskSource` se třemi slovesy. Všechno GitHubí —
hledání issue, jména labelů, HTTP — leží v driveru. Tři důsledky, které fáze 4
chrání:

1. **Jeden abstraktní tvar, N implementací.** GitHub, filesystem, Jira jsou
   sourozenci za týmž portem. Přidat zdroj = přidat driver, ne sáhnout do jádra.
2. **Původ cestuje s taskem.** Korelace „task ↔ issue" žije v `task.data.source`,
   ne ve vedlejší tabulce. Je durabilní (task se perzistuje) a projekce ven ji
   čte bez sdíleného stavu.
3. **Projekce ven je jen projekce.** `report_progress`/`finish` nic v harnessu
   nemění a nerozhodují — mají vedlejší efekt jen ven. Selhání GitHubu nesmí
   zastavit smyčku (izoluje `CompositeEventSink`).

## Port `TaskSource`

```python
@dataclass(frozen=True)
class Progress:
    step: str            # krok, do kterého task právě vstoupil
    summary: str = ""    # co se stalo (volitelné, z historie)

@dataclass(frozen=True)
class FinishResult:
    ok: bool
    pr_url: str | None = None
    summary: str = ""

class TaskSource(ABC):
    kind: str            # "github" / "memory" / "fs" — klíč pro routing projekce

    def poll(self) -> list[Task]: ...
    def report_progress(self, task: Task, progress: Progress) -> None: ...
    def finish(self, task: Task, result: FinishResult) -> None: ...
```

Interní slovník harnessu `(status, last_outcome, queue)` **neprosakuje** portem.
Reflector ho zmapuje na `Progress`/`FinishResult`; adapter mapuje ten na label.
Dvě mapovací vrstvy, každá ve své vrstvě:

- **harness event → `Progress`/`FinishResult`** — v reflectoru, bez znalosti GitHubu.
- **`Progress`/`FinishResult` → label** — v GitHub adapteru, jediné místo se znalostí GitHubu.

## Dvě sloveso-adaptérové tabulky

| Sloveso | GitHub adapter | Filesystem adapter |
|---|---|---|
| `poll()` | `list_issues(repo, label=select)`, pro každý issue **přehodí label** `todo→queued` (claim) a poskládá `Task` s `data.source` | přečti nové specy v drop-diru, atomicky přesuň do `.claimed/`, poskládej `Task` |
| `report_progress` | odeber aktuální managed label, přidej `step_labels[step]` (neznámý krok → bez labelu, coarse) | zapiš `step`/`summary` do JSON |
| `finish` | úspěch → `pr-open`; selhání → `failed` | přesuň JSON do `done/` / `failed/` |

**Label-jako-claim.** Přehození labelu v `poll()` je GitHubí dvojče atomického
`rename` ve `fs_queue.claim()` — další poll issue s claim labelem nevrátí. To dává
ingesci „nanejvýš jednou" bez vedlejšího ledgeru.

**Dual-write.** `poll()` přehodí label *dřív*, než task odejde do inboxu. Pád
mezi tím → ztracený task, ale **viditelně** (issue visí na `queued` bez PR).
Volíme to před duplicitou (dva PR). Reconcile smyčka (níže) ztracené dorovná.

## Kde se ta tři slovesa volají

- `poll()` → **`SourcePoller`** — nová smyčka v `Harness.run()` vedle
  dispatcheru/consumerů. `for t in source.poll(): inbox.put(t)`. Jádro zůstává
  GitHub-slepé; `SourcePoller` zná jen porty (`TaskSource`, `TaskQueue`, `EventSink`).
- `report_progress`/`finish` → **`SourceReflectorSink(EventSink)`**, přidaný do
  `CompositeEventSink`. Mapuje:
  - `dispatched` (task vstoupil do kroku) → `report_progress(task, Progress(step=queue, …))`
  - `finished` (task došel na `end`) → `finish(task, FinishResult(ok=True))`
  - `failed` → `finish(task, FinishResult(ok=False, summary=reason))`

Reflector drží **seznam** sourceů a routuje podle `task.data.source.kind`; adapter
cizí task (jiný `kind`, nebo bez `source`) tiše ignoruje. Tasky z `harness submit`
tak projdou bez projekce ven.

Net: `TaskSource` sahá jen `SourcePoller` (jádro) a `SourceReflectorSink` (driver),
obojí drátované v `app.py`. `dispatcher.py`/`consumer.py` port neimportují —
`test_architecture.py` to hlídá.

## `report_progress` je idempotentní → reconcile je zadarmo

Nastavit label, který už je nastavený, je no-op. Proto může jednoduchá
**reconcile smyčka** při restartu znovu odvodit cílový label z *aktuálního* stavu
tasku a zavolat `report_progress` — zmeškané eventy se samy dorovnají, přesně jak
se board re-hydratuje. Fáze 4 reconcile smyčku **specifikuje jako volitelný krok
plánu**; jádro projekce je čistě event-driven, reconcile je pojistka.

## Vztah k portu `Forge`

`Forge` (fáze 2) **vytváří** PR. `TaskSource.finish` **promítá** terminální stav
na *issue*. Zůstávají to **dva porty**, i když je GitHub implementace může sdílet
jeden HTTP client:

- Zdroj bez integrovaného VCS (filesystem, Jira-bez-GitHubu) `finish` implementuje
  smysluplně (přesun JSON) — bez `Forge`.
- Odkaz issue↔PR („Closes #42") dělá **GitHub `Forge` driver** v těle PR, protože
  má `task.data.source.issue`. `finish` proto label nepotřebuje URL — stačí mu
  coarse stav (`pr-open`). `landing.py:50` už vrací `otevřen PR {url}` do summary,
  kdyby reflector chtěl URL do komentáře; pro label ho nepotřebuje.

Zda fáze 4 dodá i ostrý GitHub `Forge`, je rozhodnutí plánu (Task „reálný GitHub").
Default: konektor (zdroj + projekce) je jádro fáze; ostrý `Forge` je čistý
follow-up ve stejném duchu, jako to fáze 2 předjímala.

## GitHub drivery

```python
@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...]

class GithubClient(ABC):
    def list_issues(self, repo: str, *, label: str) -> list[Issue]: ...
    def add_label(self, repo: str, number: int, label: str) -> None: ...
    def remove_label(self, repo: str, number: int, label: str) -> None: ...  # idempotentní (404 → nic)
```

- **`FakeGithubClient`** — issue v dictu, `list_issues` filtruje podle labelu,
  add/remove mutují. Pro unit/e2e i smoke.
- **`HttpGithubClient`** — reálný, stdlib `urllib.request` proti `api.github.com`,
  token z `GITHUB_TOKEN`. `GET /repos/{repo}/issues?labels=&state=open`,
  `POST /repos/{repo}/issues/{n}/labels`, `DELETE …/labels/{label}`. Žádná nová
  produkční závislost.

`GithubTaskSource(TaskSource)`:
- `kind = "github"`.
- Konfigurace: `client, clock, repo, workflow, repository, worktree_root,
  select_label="harness:todo", claimed_label="harness:queued",
  pr_label="harness:pr-open", failed_label="harness:failed", step_labels: dict`.
- `poll()`: pro každý issue se `select_label` → remove `select`, add `claimed`,
  `Task(id=new_task_id(), workflow_template=workflow, created=clock.now(),
  repository=repository, worktree=f"{worktree_root}/{id}",
  data={"title","body","source":{kind,repo,issue,url}})`.
- `_set_state(number, target)`: z **známé managed množiny**
  `{claimed, pr, failed, *step_labels.values()}` odeber vše krom `target`, přidej
  `target`. Managed množina je známá → přesně jeden stavový label naráz, idempotentně.

## Model, wiring, CLI

- **`Task` se nemění.** `data.source` je jen konvence v už existujícím `data`.
- **`app.py`**: `build(...)` bere `sources: list[TaskSource] | None`; postaví
  `SourcePoller` per source a `SourceReflectorSink(sources)` přidá do composite.
  Default `sources=[]` (zpětně kompatibilní — `submit` funguje dál).
- **`cli.py`**: `run` při `--github-repo`/`GITHUB_TOKEN` vdrátuje
  `GithubTaskSource(HttpGithubClient(...))`. Bez toho běží jako dřív.
- **`Harness.run()`**: `asyncio.gather` dostane i smyčky sourceů (tik = `poll` +
  `inbox.put`, jinak spí `poll_interval`).

## Chybové stavy (přibývá k fázi 2)

| Situace | Detekce | Kam |
|---|---|---|
| `poll()` vyhodí (GitHub down) | výjimka v `SourcePoller.tick` | zaloguj event, tik vrátí False, smyčka spí a zkusí zas |
| `report_progress`/`finish` vyhodí | `CompositeEventSink` izoluje | traceback na stderr, smyčka jede dál |
| task bez `data.source` v reflectoru | adapter `_mine()` → False | tiše ignorován |

## UI

Board se **nemění**. `data.source` je v detailu tasku vidět jako součást `data`.
(Odkaz na issue v kartě je volitelný follow-up, ne součást fáze 4.)

## Struktura kódu (přírůstky)

```
src/harness/
  ports/
    source.py            # TaskSource, Progress, FinishResult
  source_poller.py       # SourcePoller (jádro, zná jen porty)
  drivers/
    memory.py            # + MemoryTaskSource
    source_reflector.py  # SourceReflectorSink(EventSink)
    github_client.py     # GithubClient, Issue, FakeGithubClient, HttpGithubClient
    github_source.py     # GithubTaskSource
  app.py                 # wiring sources + reflector
  cli.py                 # --github-repo/--github-label; HttpGithubClient
```

## Invarianty — nové/upřesněné

Rozšiřují seznam z `CLAUDE.md`, neruší ho.

13. **Vnější svět tasků je jeden port `TaskSource` (`poll`/`report_progress`/
    `finish`).** GitHub je driver; jak se stav renderuje (label), zná jen driver.
14. **Původ tasku žije v `task.data.source`.** Projekce ven ho čte odtud, ne z
    vedlejšího stavu. Router/dispatcher `data.source` nečtou.
15. **`TaskSource` sahá jen `SourcePoller` a `SourceReflectorSink`**, drátované v
    `app.py`. `dispatcher.py`/`consumer.py` ho neimportují.
16. **Projekce ven je idempotentní a neblokuje rozhodování.** `report_progress`
    dvakrát je no-op; selhání izoluje `CompositeEventSink`.

## Ověření hotovosti

Fáze 4 je hotová, když:

1. `FakeGithubClient` má issue s labelem `harness:todo`; po `poll()` má issue
   `harness:queued` (ne `todo`) a vznikl `Task` s `data.source.issue`.
2. Druhý `poll()` týž issue **nevrátí** (claim drží).
3. Task proteče smyčkou; na `dispatched` do kroků se label mění podle `step_labels`.
4. Po dojití na `end` má issue `harness:pr-open`; po pádu do `failed/` má `harness:failed`.
5. Task z `harness submit` (bez `data.source`) projde bez jediného volání GitHubu.
6. E2E na in-memory driverech (Memory queue/workspace/artifacts/forge, FakeClock,
   `MemoryTaskSource`): issue-ekvivalent → task → PR → terminální projekce.
7. Architektonické testy: `dispatcher`/`consumer` neimportují `ports/source`;
   `SourcePoller` importuje jen porty; drivery mimo `app.py`/`cli.py` nikdo jiný.
8. `HttpGithubClient` existuje a nepřidává produkční závislost (jen stdlib).
