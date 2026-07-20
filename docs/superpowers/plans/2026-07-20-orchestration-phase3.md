# Fáze 3 — skutečný agent přes `claude -p`: Implementation Plan

> **For agentic workers:** implementuj task po tasku. Každý task: napiš padající
> test → spusť (červená) → implementuj → spusť (zelená) → commit. Kroky mají
> checkbox (`- [ ]`).

**Goal:** Vyměnit `DummyBehavior` za `ClaudeCliBehavior`, který práci kroku svěří
agentovi spuštěnému přes `claude -p`. Agent per fronta jako data (`AgentSpec` +
`AgentCatalog`), sdílený `AgentRunner`, `RepositoryRegistry` (jméno repa →
cesta), artefakty verzované ve worktree pod `.artifacts/<id>/`.

**Spec:** `docs/superpowers/specs/2026-07-20-orchestration-phase3-design.md`

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`. `claude` CLI se volá
přes `subprocess`/`asyncio.create_subprocess_exec` — žádná nová produkční
závislost. Reálný `claude` v test sadě NEBĚŽÍ — pohání ji `FakeAgentRunner`.

## Global Constraints

- **Rozhodovací role z fází 1–2 platí.** Consumer nevětví na hodnotě outcome.
  Status mění dispatcher. `lastOutcome` zapisuje consumer.
- **Vyměňuje se driver, ne okolí.** `ClaudeCliBehavior` nezná subprocess ani CLI
  flagy — zná jen `AgentRunner`. Dispatcher/consumer neimportují nové porty.
- **Commit dělá behavior driver, ne consumer, ne agent.** Agent artefakty i kód
  jen zapíše; `git add`/`commit` spouští worker.
- **Persona je data.** V `ClaudeCliBehavior` není větev podle jména agenta.
- **`task.repository` je jméno, ne cesta.** Cesty řeší `RepositoryRegistry`.
- **Testy nesahají na skutečný čas ani na reálný `claude`.** Real-FS/git testy
  smějí `tmp_path` (jako fáze 2). `FakeAgentRunner` je čistý Python.
- Čas je ISO 8601 UTC se sufixem `Z`.
- Vývoj na `claude/harness-part-three-brainstorm-5w3lwu` (na `main` po fázi 2).

---

### Task 1: Port `AgentRunner` / `AgentCatalog` + `AgentSpec`

Základ. Žádná závislost na ostatních tascích.

**Files:** `src/harness/ports/agent.py`, `src/harness/drivers/memory.py`,
`tests/test_agent_ports.py`.

**Interfaces:**
- `AgentSpec(name, prompt, model=None, fallback_model=None,
  allowed_tools=(), allowed_outcomes=(Outcome.DONE,))` — frozen dataclass.
  `allowed_outcomes: tuple[Outcome, ...]`.
- `AgentRun(outcome: Outcome, summary: str, raw: str = "")` — frozen.
- `AgentRunner(ABC)`: `async run(*, prompt, spec, cwd, timeout) -> AgentRun`.
- `AgentCatalog(ABC)`: `get(name) -> AgentSpec`; `AgentNotFound(Exception)`.
- `MemoryAgentCatalog(dict[str, AgentSpec])` v `memory.py`: `get` vrátí spec nebo
  vyhodí `AgentNotFound`.
- `FakeAgentRunner` v `memory.py`: konstruuje se skriptem
  `runs: dict[str, AgentRun]` nebo default `AgentRun`; `run` zaznamená volání do
  `self.calls` a vrátí naskriptovaný `AgentRun`. Volitelně `writes:
  dict[str, str]` (relpath→obsah), které při `run` zapíše do `cwd` — simuluje
  agenta produkujícího artefakty/kód. Bez subprocessu.

- [ ] **Step 1:** Testy — `AgentSpec` drží pole a defaulty; `MemoryAgentCatalog`
  roundtrip + `AgentNotFound`; `FakeAgentRunner` vrátí naskriptovaný run,
  zaznamená call, a když má `writes`, zapíše soubory do `cwd` (`tmp_path`).
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: porty AgentRunner/AgentCatalog + AgentSpec + fakes`.

---

### Task 2: Port `RepositoryRegistry`

**Files:** `src/harness/ports/repos.py`, `src/harness/drivers/memory.py`,
`src/harness/drivers/fs_repos.py`, `tests/test_repos.py`.

**Interfaces:**
- `RepositoryRegistry(ABC)`: `resolve(name) -> Path`;
  `RepositoryNotFound(Exception)`.
- `MemoryRepositoryRegistry(dict[str, Path])` v `memory.py`.
- `FilesystemRepositoryRegistry(config: Path)` v `fs_repos.py`: čte JSON
  `{"<name>": "<path>"}`; `resolve` vrátí `Path` nebo `RepositoryNotFound`.
  Rozbitý/chybějící config → `RepositoryNotFound` s jasnou zprávou.

- [ ] **Step 1:** Testy — memory resolve + not found; fs čte JSON (`tmp_path`),
  resolve vrátí cestu, neznámé jméno → `RepositoryNotFound`, rozbitý JSON → totéž.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: port RepositoryRegistry + fs a in-memory driver`.

---

### Task 3: `ClaudeCliRunner` — reálný driver kolem `claude -p`

Čisté funkce (build args, parse verdict) jdou testovat bez subprocessu; tenkou
subprocess slupku pokryje opt-in smoke (Task 8), ne `pytest -q`.

**Files:** `src/harness/drivers/claude_cli.py`, `tests/test_claude_cli.py`.

**Interfaces:**
- `build_argv(*, prompt, spec, output_format="json") -> list[str]` — čistá
  funkce. Skládá `["claude", "-p", prompt, "--output-format", "json",
  "--permission-mode", "bypassPermissions", "--setting-sources", "project"]` a
  přidá `--append-system-prompt <spec.prompt>`, `--model <spec.model>` (když
  není None), `--fallback-model …` (když není None), `--allowedTools <…>` (když
  neprázdné).
- `parse_verdict(stdout, *, allowed) -> AgentRun` — čistá funkce. Z JSON obálky
  `claude -p` vytáhne finální text, z něj `{outcome, summary}`; `outcome` mimo
  `allowed` → `VerdictError`; chybějící/nečitelný JSON → `VerdictError`. `raw`
  nese stdout.
- `ClaudeCliRunner(AgentRunner)`: `run` složí `argv`, spustí
  `asyncio.create_subprocess_exec` v `cwd` s `timeout` (kill + `VerdictError`
  při vypršení), na nenulový exit vyhodí `AgentError`, jinak vrátí
  `parse_verdict(stdout, allowed=spec.allowed_outcomes)`.
- Konvence verdiktu: agent v personě má skončit blokem
  ` ```json {"outcome": "...", "summary": "..."} ``` `; `parse_verdict` bere
  poslední takový blok. (Persona v default katalogu to instruuje — Task 6.)

- [ ] **Step 1:** Testy (čisté funkce, žádný subprocess) — `build_argv` obsahuje
  správné flagy pro spec s/bez modelu, tools, fallbacku; `parse_verdict` přečte
  validní verdikt, `done`/`request_changes` mapuje na `Outcome`, mimo `allowed`
  → `VerdictError`, rozbitý JSON → `VerdictError`.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: ClaudeCliRunner — build_argv, parse_verdict, subprocess`.

---

### Task 4: Attempt helper — artefakty ve worktree

Zápisová strana `ArtifactStore` z fáze 2 se scvrkává na výpočet cesty pokusu ve
worktree. Read-side `ArtifactView` dostane driver čtoucí `.artifacts/` ve worktree.

**Files:** `src/harness/drivers/worktree_artifacts.py`,
`src/harness/ports/artifacts.py` (jen pokud je potřeba doladit `ArtifactView`),
`tests/test_worktree_artifacts.py`.

**Interfaces:**
- `next_attempt(worktree: Path, task_id: str, step: str) -> tuple[int, str]` —
  spočítá existující `.artifacts/<task_id>/<step>-*.md`, vrátí `(NN, relpath)`
  kde `relpath = ".artifacts/<task_id>/<step>-<NN:02d>.md"`. Task-level artefakty
  (bez suffixu) helper neřeší — píše je přímo agent podle persony.
- `WorktreeArtifactView(worktrees_root: Path)` — `ArtifactView` čtoucí z
  `<worktrees_root>/<task_id>/.artifacts/<task_id>/`: `list` vrátí `ArtifactRef`
  (step, attempt, name) parsované z jmen souborů; `read` vrátí obsah. Task-level
  soubory (bez `-NN`) → `attempt = 0` nebo vlastní příznak (dohodnout: `attempt
  = -1` značí task-level). `read` neexistujícího → `None`.

- [ ] **Step 1:** Testy (`tmp_path`) — `next_attempt` roste 01→02 podle
  existujících souborů; `WorktreeArtifactView.list/read` přečte ploché soubory,
  odliší task-level od step-attempt, neexistující → `None`.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: attempt helper a WorktreeArtifactView`.

---

### Task 5: `ClaudeCliBehavior`

**Files:** `src/harness/behaviors/agent.py`,
`src/harness/behaviors/__init__.py`, `tests/test_agent_behavior.py`.

**Interfaces:**
- `ClaudeCliBehavior(*, clock, workspace, runner: AgentRunner, spec: AgentSpec,
  timeout: float = 600.0)`.
- `run(task)`:
  1. `handle = workspace.attach(task)` (reset-on-reattach řeší `GitWorkspace`,
     Task 6).
  2. `attempt, relpath = next_attempt(handle.path, task.id, task.status)`.
  3. `prompt = compose_prompt(task, step=task.status, artifact_relpath=relpath)`
     — vysvětlí agentovi úkol kroku, kam zapsat artefakt, ať přečte předchozí
     `.artifacts/<id>/`, a ať skončí verdikt blokem.
  4. `run = await runner.run(prompt=prompt, spec=spec, cwd=handle.path,
     timeout=timeout)`.
  5. `handle.commit(run.summary)` — commit dělá worker.
  6. `return BehaviorResult(run.outcome, run.summary)`.
- Výjimka z runneru (`AgentError`/`VerdictError`/timeout) probublá — consumer ji
  zvládne přes `_fail`. `ClaudeCliBehavior` nevětví na hodnotě outcome.

- [ ] **Step 1:** Testy (`tmp_path` + `GitWorkspace` nebo real-FS handle +
  `FakeAgentRunner`) — po `run` byl volán agent se správným cwd; prompt nese
  attempt relpath; když `FakeAgentRunner.writes` obsahuje artefakt, po `run`
  existuje `.artifacts/<id>/<step>-01.md` a je commitnutý; návrat je
  `BehaviorResult` se summary; `FakeAgentRunner` s verdiktem mimo `allowed`
  (nastavený tak, že runner vyhodí) → výjimka probublá.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: ClaudeCliBehavior — agent píše, worker commituje`.

---

### Task 6: `GitWorkspace` přes registry + reset; wiring; default agenti

**Files:** `src/harness/drivers/git_workspace.py`, `src/harness/app.py`,
`src/harness/cli.py`, `tests/test_git_workspace.py`, `tests/test_app.py`.

**Interfaces:**
- `GitWorkspace(registry: RepositoryRegistry, worktrees_root: Path)`:
  - `attach(task)`: `base = registry.resolve(task.repository)`;
    `worktree = worktrees_root / task.id`; neexistuje-li,
    `git -C base worktree add worktree -b harness/<task_id>`; existuje-li,
    **reset-on-reattach**: `git -C worktree reset --hard HEAD` + `git -C worktree
    clean -fd` (bez `-x`). Handle jako dřív.
- `build(...)`: přidává `runner`, `catalog: AgentCatalog`, `registry:
  RepositoryRegistry`, `worktrees_root`, `agent_timeout`. Default in-memory /
  fake pro `build` bez konfigurace; `harness run` injektuje `ClaudeCliRunner`,
  `FilesystemAgentCatalog`, `FilesystemRepositoryRegistry`, `GitWorkspace`,
  `WorktreeArtifactView`.
- Per-step behavior: `behavior_for(step)` → `LandingBehavior` pro `landing_step`,
  jinak `ClaudeCliBehavior(spec=catalog.get(step), …)`. Chybí-li spec →
  `AgentNotFound` (fail fast při buildu).
- **Landing** ztrácí kopírovací krok — artefakty už ve worktree jsou; jen otevře
  PR. Uprav `LandingBehavior` a jeho testy.
- `HarnessLayout` += `worktrees`, `agents`, `repos` (config). `harness init`
  zapíše default agenty `agents/<step>.json` (persona instruuje verdikt blok +
  psaní artefaktu do `.artifacts/`; `reviewer` má `allowed_outcomes` done+
  request_changes, ostatní jen done) a prázdný `repos.json` s nápovědou.
- `api/` dostane `WorktreeArtifactView` místo fs artifact store.

- [ ] **Step 1:** Testy — `GitWorkspace.attach` resolvuje jméno přes registry,
  založí worktree na odvozené cestě; druhý `attach` špinavého worktree ho
  resetuje (soubor přidaný mimo commit po `attach` zmizí). `build` přiřadí
  landing vs agent behavior; chybějící agent → chyba. Landing bez kopírování.
- [ ] **Step 2:** Červená → **Step 3:** implementace → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: GitWorkspace přes registry+reset, wiring, default agenti`.

---

### Task 7: E2E na fake runneru

**Files:** `tests/test_phase3_e2e.py`.

- [ ] **Step 1:** E2E na in-memory driverech (`MemoryRepositoryRegistry`,
  `MemoryAgentCatalog`, `FakeAgentRunner` skriptovaný per krok, `GitWorkspace`
  nad tmp repem nebo `MemoryWorkspace` kde stačí, `FakeClock`). Task se jménem
  repa proteče `plan→…→review→land→end`; `reviewer` fake vrátí jednou
  `request_changes`. Ověř:
  - task skončí v `done`;
  - `FakeAgentRunner` byl volán per krok se správným cwd a spec;
  - artefakty (fake `writes`) mají `development-02` i `review-02` vedle `-01`;
  - historie nese `summary` u consumer řádků;
  - forge eviduje jeden PR.
- [ ] **Step 2:** Červená → **Step 3:** doladění wiringu → **Step 4:** zelená.
- [ ] **Step 5:** Commit `feat: e2e fáze 3 na fake agent runneru`.

---

### Task 8: Architektura, opt-in smoke, dokumentace

**Files:** `tests/test_architecture.py`, `tests/test_smoke_git.py` (úprava),
`tests/test_smoke_claude.py` (nový, opt-in), `CLAUDE.md`.

- [ ] **Step 1:** Architektonické testy: `dispatcher.py`/`consumer.py`
  neimportují `ports/agent`, `ports/repos` ani nové drivery; `api/` sahá jen na
  `ArtifactView`; behavior nevětví na outcome (rozšíř stávající test).
- [ ] **Step 2:** `test_smoke_git.py` — přesun artefaktů do `.artifacts/` ve
  worktree (dummy behavior fáze 2 zůstává pro tenhle smoke, nebo se nahradí
  `ClaudeCliBehavior` + `FakeAgentRunner` píšící soubory). Ověř artefakty ve
  worktree, ne v oddělené složce.
- [ ] **Step 3:** `test_smoke_claude.py` — **opt-in**, `@pytest.mark.skipif(not
  os.environ.get("HARNESS_SMOKE_CLAUDE"))`. Spustí reálný `claude -p` na
  triviálním úkolu v tmp repu, ověří verdikt + commit. Nikdy neběží v `pytest -q`
  bez env flagu.
- [ ] **Step 4:** `CLAUDE.md` — mapa modulů o nové porty/drivery, invarianty
  13–17, sekce „Co je za co zodpovědné" o agentovi, registry a artefaktech ve
  worktree. `.venv/bin/pytest -q` zelené.
- [ ] **Step 5:** Commit `docs: CLAUDE.md pro fázi 3; opt-in claude smoke`.

---

## Pořadí a závislosti

```
T1 (Agent porty) ─┬─> T3 (ClaudeCliRunner) ─┐
T2 (RepoRegistry)─┤                          ├─> T5 (ClaudeCliBehavior) ─> T6 (wiring) ─> T7 (e2e) ─> T8 (arch+smoke+docs)
T4 (attempt/View)─┘                          │
                                             └─(T4 → T5, T6)
```

T1–T2 nezávislé (sdílený `memory.py` — psát sériově, ať se needitují naráz).
T3–T4 stojí na T1. T5 spojuje runner+attempt+workspace. T6 zadrátuje registry,
per-step agenty a landing. T7 e2e. T8 uzavírá.
