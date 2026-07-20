# Fáze 2 — artefakty, worktree a landing: Implementation Plan

> **For agentic workers:** implementuj task po tasku. Každý task: napiš padající
> test → spusť (červená) → implementuj → spusť (zelená) → commit. Kroky mají
> checkbox (`- [ ]`).

**Goal:** Task pracuje ve worktree pojmenovaném v tasku, produkuje artefakty do
harnessové složky, commituje po fázích se smysluplnou zprávou a na konci otevře
PR — vše za porty, které jdou vyměnit za driver.

**Spec:** `docs/superpowers/specs/2026-07-20-orchestration-phase2-design.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. Runtime přidává jen to,
co už fáze 1 má (FastAPI/uvicorn/jinja2 pro board). Git driver volá systémový
`git` přes `subprocess` — žádná nová produkční závislost.

## Global Constraints

- **Rozhodovací role z fáze 1 platí.** Consumer nevětví na hodnotě outcome.
  Status mění dispatcher. `lastOutcome` zapisuje consumer.
- **`repository`/`worktree` čte jen behavior.** Router/dispatcher pořád jen
  `(status, lastOutcome)`.
- **Commit dělá behavior driver, ne consumer, ne LLM.**
- **Dispatcher/consumer neimportují `Workspace`/`Forge`/`ArtifactStore`.** Wiring
  je v `app.py`. `api/` sahá jen na `ArtifactView`. Hlídá `test_architecture.py`.
- **Artefakty jsou attempt-indexed** — re-run kroku nepřepíše předchozí pokus.
- **Testy nesahají na skutečný čas.** Real-driver testy (git, fs) smějí použít
  `tmp_path` — stejně jako `test_fs_queue.py` ve fázi 1.
- Čas je ISO 8601 UTC se sufixem `Z`.
- Vývoj na branchi `claude/harness-phase-two-brainstorm-6u384o` (ne přímo main —
  pro tuto fázi platí instrukce sezení, ne konvence z `CLAUDE.md`).

---

### Task 1: `BehaviorResult` a `summary` v historii

Behavior přestává vracet holý `Outcome`. Cross-cutting změna, po které musí být
celá sada zase zelená.

**Files:** `src/harness/models.py`, `src/harness/ports/behavior.py`,
`src/harness/consumer.py`, `src/harness/drivers/memory.py`,
`src/harness/drivers/dummy_behavior.py`, dotčené testy.

**Interfaces:**
- `BehaviorResult(outcome: Outcome, summary: str = "")` — frozen dataclass v `models.py`.
- `HistoryEntry` získává `summary: str | None = None`; `to_dict` ho přidá, jen
  když není `None`; `from_dict` čte `raw.get("summary")`.
- `ConsumerBehavior.run(task) -> BehaviorResult`.
- Consumer: `result = await behavior.run(task)`; validace
  `isinstance(result, BehaviorResult) and isinstance(result.outcome, Outcome)`;
  jinak `_fail(...)`. `_deliver` zapíše `last_outcome=result.outcome.value` a
  `HistoryEntry(..., outcome=result.outcome.value, summary=result.summary or None)`.
  Event `consumed` dostane `summary=result.summary`.
- `ScriptedBehavior` a `DummyBehavior` vracejí `BehaviorResult` (Scripted:
  `BehaviorResult(outcome, summary=f"{step}")` stačí; Dummy zatím
  `BehaviorResult(Outcome.DONE, "hotovo")`).

- [ ] **Step 1:** Testy — `test_models.py`: `BehaviorResult` drží pole;
  `HistoryEntry` se `summary` roundtripuje a bez summary klíč vynechá.
  `test_consumer.py`: po ticku nese `inbox` řádek historie se `summary`;
  neplatný návrat (ne `BehaviorResult`) → `failed/`. Uprav existující consumer
  testy na nový návratový typ.
- [ ] **Step 2:** Červená.
- [ ] **Step 3:** Implementuj napříč soubory. Consumer smí zapsat summary — pořád
  žádná větev na *hodnotě* outcome (invariant test to ověří).
- [ ] **Step 4:** `.venv/bin/pytest -q` — celá sada zelená.
- [ ] **Step 5:** Commit `feat: behavior vrací BehaviorResult (outcome + summary)`.

---

### Task 2: Port `ArtifactStore` / `ArtifactView` + `MemoryArtifactStore`

**Files:** `src/harness/ports/artifacts.py`, `src/harness/drivers/memory.py`,
`tests/test_artifacts_memory.py`.

**Interfaces:**
- `ArtifactRef(step: str, attempt: int, name: str)` — frozen; `to_dict()`.
- `ArtifactView(ABC)`: `list(task_id) -> tuple[ArtifactRef, ...]`,
  `read(task_id, step, attempt, name) -> str | None`.
- `ArtifactSlot(ABC)`: `attempt: int`, `put(name: str, content: str) -> None`.
- `ArtifactStore(ArtifactView)`: `begin(task_id, step) -> ArtifactSlot` — alokuje
  **další** attempt (0,1,2,…) pro dvojici `(task_id, step)`.
- `MemoryArtifactStore` implementuje vše nad dictem.

- [ ] **Step 1:** Testy — `begin` dvakrát pro tentýž `(task, step)` dá attempt 0
  a 1; `put`+`read` roundtrip; `list` vrátí všechny refy napříč kroky/pokusy;
  `read` neexistujícího → `None`.
- [ ] **Step 2:** Červená.
- [ ] **Step 3:** Implementuj port + `MemoryArtifactStore`.
- [ ] **Step 4:** Zelená.
- [ ] **Step 5:** Commit `feat: port ArtifactStore/ArtifactView + in-memory driver`.

---

### Task 3: Port `Workspace` + `MemoryWorkspace`

**Files:** `src/harness/ports/workspace.py`, `src/harness/drivers/memory.py`,
`tests/test_workspace_memory.py`.

**Interfaces:**
- `WorkspaceHandle(ABC)`: `path` (str/Path), `branch: str`,
  `commit(message: str) -> str | None`.
- `Workspace(ABC)`: `attach(task: Task) -> WorkspaceHandle`.
- `MemoryWorkspace`: `attach` vrátí handle s `branch=f"harness/{task.id}"` a
  fiktivní `path`; `commit` zaznamená zprávu do `handle.commits: list[str]` a
  vrátí fiktivní sha `f"sha{len(commits)}"`; „nic k commitu" simulovat nemusí
  (na to je git driver test). `MemoryWorkspace.handles` drží vydané handly pro
  aserce v testech.

- [ ] **Step 1:** Testy — `attach` dá branch odvozený z task.id; opětovný
  `attach` téhož tasku vrátí handle se stejnou branch (znovupoužití); `commit`
  zaznamená zprávu a vrátí sha.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: port Workspace + in-memory driver`.

---

### Task 4: Port `Forge` + `MemoryForge`

**Files:** `src/harness/ports/forge.py`, `src/harness/drivers/memory.py`,
`tests/test_forge_memory.py`.

**Interfaces:**
- `PullRequest(number: int, url: str, branch: str, title: str)` — frozen.
- `Forge(ABC)`: `open_pull_request(task, *, branch, title, body) -> PullRequest`.
- `MemoryForge`: zaznamená PR do `self.opened: list[dict]`; **idempotence** —
  druhé volání pro stejnou `branch` vrátí stávající PR (stejné číslo).

- [ ] **Step 1:** Testy — otevření PR vrátí číslo/url/branch/titul a zaznamená;
  druhé volání stejné branch nevytvoří nový, vrátí týž.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: port Forge + in-memory driver s idempotencí PR`.

---

### Task 5: DummyBehavior fáze 2

**Files:** `src/harness/drivers/dummy_behavior.py`, `tests/test_dummy_behavior.py`.

**Interfaces:**
- `DummyBehavior(*, clock, workspace: Workspace, artifacts: ArtifactStore,
  delay=5.0, request_changes_once_at=None)`.
- `run(task)`:
  1. `handle = workspace.attach(task)`
  2. `slot = artifacts.begin(task.id, task.status)`
  3. `slot.put(f"{task.status}.md", f"# {task.status}\n\n{summary}\n")`
  4. `handle.commit(f"[{task.status}] {summary}")` (dummy může do worktree nic
     needitovat; commit u mem driveru jen zaznamená zprávu — u git driveru je
     „nic k commitu" povolené a vrátí `None`).
  5. `return BehaviorResult(outcome, summary)`.
- `summary` je deterministické, např. `f"{task.status}: hotovo"` a pro
  request_changes `f"{task.status}: vyžádány změny"`.
- request_changes-once logika z fáze 1 zůstává.

- [ ] **Step 1:** Testy s `MemoryWorkspace`+`MemoryArtifactStore` — po `run`
  existuje artefakt `<task>/<step>/0/<step>.md`; workspace zaznamenal commit se
  zprávou začínající `[<step>]`; návrat je `BehaviorResult` se summary;
  request_changes-once vrátí REQUEST_CHANGES jen poprvně.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: DummyBehavior píše artefakt a commituje práci`.

---

### Task 6: LandingBehavior

**Files:** `src/harness/behaviors/__init__.py`,
`src/harness/behaviors/landing.py`, `tests/test_landing_behavior.py`.

**Interfaces:**
- `LandingBehavior(*, clock, workspace, artifacts: ArtifactView, forge: Forge,
  dest="docs/tasks")`.
- `run(task)`:
  1. `handle = workspace.attach(task)`
  2. Pro každý `ArtifactRef` z `artifacts.list(task.id)` zapiš obsah do
     `handle.path / dest / task.id / step / attempt / name` (u mem workspace stačí
     zaznamenat „přiklopené" cesty — viz níže) a `handle.commit("[land] artefakty tasku")`.
  3. `body = _compose_body(task.history)` — agregace `summary` z consumer řádků.
  4. `pr = forge.open_pull_request(task, branch=handle.branch,
     title=_title(task), body=body)`
  5. `return BehaviorResult(Outcome.DONE, f"otevřen PR {pr.url}")`.
- `MemoryWorkspace.WorkspaceHandle` dostane `staged: list[tuple[str,str]]` pro
  aserci přiklopených souborů (cesta, obsah), aby landing šel testovat bez disku.

- [ ] **Step 1:** Testy — po `run` forge eviduje jeden PR pro branch tasku;
  tělo PR obsahuje summary z historie; handle má přiklopené artefakty; návrat
  nese url PR. Idempotence: druhý `run` nevytvoří druhý PR.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: LandingBehavior přiklopí artefakty a otevře PR`.

---

### Task 7: Wiring, `land` krok a e2e

**Files:** `src/harness/app.py`, `src/harness/cli.py`,
`tests/test_app.py` / `tests/test_phase2_e2e.py`.

**Interfaces:**
- `build(...)` staví `ArtifactStore`, `Workspace`, `Forge` (fáze 2 default:
  in-memory pro `build` bez rootu není — pro fs běh git+fs+fake). Přidej
  parametry `workspace`, `artifacts`, `forge`, `landing_step="land"`.
- Per-step behavior: consumer kroku `landing_step` dostane `LandingBehavior`,
  ostatní `DummyBehavior`. `build` sestaví `behaviors: dict[str, ConsumerBehavior]`
  a předá každému consumeru ten jeho.
- Výchozí workflow v `cli.py` (`DEFAULT_DEFINITION`) dostane `land` mezi `review`
  a `end`.
- Consumer konstruktor: přidej `behavior` per instance (už teď ho bere) — jen
  wiring předá různé.

- [ ] **Step 1:** E2E test na in-memory driverech (Memory Workspace/Artifacts/
  Forge, FakeClock, ScriptedBehavior nahradí Dummy tam, kde je potřeba řídit
  smyčku — ale pro artefakty/commity použij Dummy). Task proteče
  `plan→…→review→land→end`; jedna `request_changes` smyčka. Ověř:
  - task skončí v `done`;
  - artefakty mají druhý attempt u `development` i `review`;
  - workspace nese per-fázové commity;
  - forge eviduje právě jeden PR;
  - historie nese `summary` u consumer řádků.
- [ ] **Step 2:** Červená → **Step 3:** wiring → **Step 4:** zelená (celá sada).
- [ ] **Step 5:** Commit `feat: wiring fáze 2, krok land a e2e průtok`.

---

### Task 8: Reálné drivery — `FilesystemArtifactStore` a `GitWorkspace`

**Files:** `src/harness/drivers/fs_artifacts.py`,
`src/harness/drivers/git_workspace.py`, `src/harness/drivers/fake_forge.py`,
`tests/test_fs_artifacts.py`, `tests/test_git_workspace.py`.

**Interfaces:**
- `FilesystemArtifactStore(root: Path)`: attempt = počet existujících podadresářů
  `<root>/<task>/<step>/`; `begin` založí `<root>/<task>/<step>/<attempt>/`;
  `put` zapíše soubor; `list`/`read` čtou z disku.
- `GitWorkspace(repos_root: Path | None = None)`:
  - `attach(task)`: `worktree = Path(task.worktree)`, `repo = task.repository`.
    Neexistuje-li worktree, `git -C <repo> worktree add <worktree> -b
    harness/<task_id>` (base = aktuální HEAD repa); jinak reuse. Handle drží
    `path`, `branch`.
  - `commit(message)`: `git -C <path> add -A`; pokud `git status --porcelain`
    prázdné → `None`; jinak `git -C <path> commit -m message` a vrať sha
    (`git rev-parse HEAD`). Nastav `GIT_AUTHOR_*`/`committer` env, ať test nepadá
    na chybějící identitě.
- `FakeForge`: jako `MemoryForge`, ale pro fs běh (může pushnout branch do
  konfigurovaného bare remote nebo jen zaznamenat do souboru). Ve fázi 2 stačí
  záznam do `<root>/prs.json`.

- [ ] **Step 1:** Testy s `tmp_path`. `fs_artifacts`: attempt roste na disku;
  roundtrip. `git_workspace`: založ tmp git repo s jedním commitem, `attach`
  vytvoří worktree na `harness/<id>` branchi; edituj soubor, `commit` vrátí sha
  a `git log` na branchi ho ukáže; `commit` bez změny vrátí `None`.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: fs artefakty a git worktree driver`.

---

### Task 9: Board — pohled na artefakty

**Files:** `src/harness/ports/board.py` nebo nový `ArtifactView` už z Tasku 2;
`src/harness/api/routes.py`, `src/harness/api/app.py`, template, `tests/test_api_*`.

**Interfaces:**
- `create_app(view=..., artifacts: ArtifactView, clock=...)`.
- Route `GET /tasks/{id}/artifacts` → JSON `[{step, attempt, name}]`.
- Route obsahu `GET /tasks/{id}/artifacts/{step}/{attempt}/{name}` → text.
- Detail tasku v HTML ukáže seznam artefaktů (odkazy na obsah).
- `api/` importuje **jen** `ArtifactView`, ne driver — `test_architecture.py`.

- [ ] **Step 1:** Testy — JSON list vrátí artefakty tasku; obsah vrátí text;
  neexistující → 404. Architektura: `api/` nesahá na driver.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: board ukazuje artefakty tasku`.

---

### Task 10: Architektura, smoke a dokumentace

**Files:** `tests/test_architecture.py`, `tests/test_smoke.py` (nebo
`test_smoke_git.py`), `CLAUDE.md`.

- [ ] **Step 1:** Architektonické testy: `dispatcher.py`/`consumer.py`
  neimportují `ports/workspace`, `ports/forge`, `ports/artifacts` ani drivery;
  `api/` importuje jen `ArtifactView`. Consumer pořád nevětví na outcome.
- [ ] **Step 2:** Smoke na skutečném gitu: init repo v `tmp_path`, submit task s
  `repository`/`worktree`, spusť smyčku se zkráceným intervalem, počkej, ověř —
  task v `done/`, worktree má commity, `prs.json` má PR, artefakty na disku.
  (Poluje reálným krátkým `asyncio.sleep` jako stávající smoke; **jediná** výjimka
  z „nespat v reálném čase".)
- [ ] **Step 3:** Aktualizuj `CLAUDE.md` — mapa modulů o nové porty/drivery,
  invarianty 8–12, sekce „Co je za co zodpovědné" o dvě plochy a landing.
- [ ] **Step 4:** `.venv/bin/pytest -q` — vše zelené.
- [ ] **Step 5:** Commit `docs: CLAUDE.md pro fázi 2; smoke na skutečném gitu`.

---

## Pořadí a závislosti

```
T1 (BehaviorResult) ─┬─> T5 (DummyBehavior) ─┐
T2 (ArtifactStore) ──┤                        ├─> T7 (wiring+e2e) ─> T9 (board) ─> T10 (smoke+docs)
T3 (Workspace) ──────┼─> T6 (Landing) ───────┘         │
T4 (Forge) ──────────┘                                  └─> T8 (fs+git drivery)
```

T1–T4 jsou nezávislé základy (kromě sdíleného `memory.py` — psát sériově, ať se
needitují naráz). T5/T6 stojí na základech. T7 spojuje. T8 (reálné drivery) a T9
(board) jsou nezávislé. T10 uzavírá.
