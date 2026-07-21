# Self-healing — implementation plan

Spec: `docs/superpowers/specs/2026-07-21-self-healing-design.md`
Date: 2026-07-21

Model: a `healer` agent **assigned to the `failed` queue**. A `Healer` core loop
claims a failed task, runs the healer persona, files an issue via `IssueTracker`,
and settles the task onto a new terminal `healed/` queue. `failed/` drains; no
heal task is ever spawned, so there is no recursion to guard.

Build order is bottom-up: port → drivers → persona → loop → wiring → e2e. Each
task lands with its own tests, `pytest -q` green, and a conventional-commit
subject. Nothing changes the existing flow unless the healer is wired (default
off), so every step is safe to commit incrementally.

## Task 1 — the `IssueTracker` port

**Add** `src/harness/ports/issues.py`: `IssueRef(number, url)`, `IssueError`,
`IssueTracker` (ABC) with `open_issue(repo, *, title, body, labels, marker) -> IssueRef`.
Docstring states the idempotency-by-marker contract (spec §"The `IssueTracker` port").

**Test** (`tests/test_issue_port.py`): the ABC can't be instantiated; a trivial
subclass satisfies the signature.

Commit: `feat: add IssueTracker port for opening advisory issues`

## Task 2 — `MemoryIssueTracker` (fake)

**Add** `MemoryIssueTracker` to `drivers/memory.py`: issues in a list, `open_issue`
searches by `marker` (return existing) else appends a new `IssueRef`. Expose
`.opened` for assertions.

**Test** (`tests/test_issue_memory.py`): open once → one issue; open again same
marker → same `IssueRef`, list unchanged; different marker → second issue.

Commit: `feat: add MemoryIssueTracker fake`

## Task 3 — `GithubClient.create_issue` / `search_issue_by_marker`

**Extend** `drivers/github_client.py`:
- ABC: `create_issue(repo, *, title, body, labels) -> Issue`;
  `search_issue_by_marker(repo, marker) -> Issue | None`.
- `FakeGithubClient`: create appends with an incrementing number and the
  `harness:self-heal` label; search scans bodies for the marker.
- `HttpGithubClient`: `create_issue` → `POST /repos/{repo}/issues`;
  `search_issue_by_marker` → reuse `list_issues(repo, label="harness:self-heal")`
  and match the marker substring in the body. Stdlib only.

**Test** (extend `tests/test_github_client.py`): fake create+search round-trip;
Http create/search against the injected `opener` (no network) asserting method,
URL, JSON body.

Commit: `feat: add create_issue/search_issue_by_marker to GithubClient`

## Task 4 — `GithubIssueTracker`

**Add** `drivers/github_issues.py`: `GithubIssueTracker(IssueTracker)` over
`GithubClient`. `open_issue`: `search_issue_by_marker` → return its `IssueRef` if
found; else append `<!-- harness-heal:<marker> -->` to the body and `create_issue`;
wrap client errors in `IssueError`.

**Test** (`tests/test_github_issues.py`, `FakeGithubClient`): open → creates with
the marker embedded; open again same marker → returns the existing one, no second
create; a client that raises → `IssueError`.

Commit: `feat: add GithubIssueTracker driver`

## Task 5 — the `healer` persona

**Add** `harness init` output: `agents/healer.json` (spec §"The `healer` persona"),
`allowed_outcomes: ["done", "request_changes"]`. No workflow file — the healer is
a loop, not a workflow step.

**Test** (extend `tests/test_cli.py`): after `init`, `healer.json` parses to an
`AgentSpec` with both outcomes.

Commit: `feat: ship healer persona from harness init`

## Task 6 — the `Healer` core loop

**Add** `src/harness/healer.py`: `Healer` + `_heal_prompt`.

- Ctor: `failed, healed, runner, spec, tracker, repo, scratch_root, events, clock,
  strategy, labels, timeout`.
- `tick()`: select+`claim` from `failed`; make `scratch_root/<id>`; build
  `_heal_prompt(task, spec)`; `await runner.run(...)`; `_act_on` (verdict `done`
  → read `scratch/issue.md`, `tracker.open_issue(..., marker=task.id)`; else no
  issue); `_settle` → transfer to `healed/` with a history entry (issue URL or the
  `heal-failed`/`nothing-actionable` note) and emit a `healed` event.
- **Errors** (runner raises/timeout, `IssueError`) are caught in `tick` and turned
  into a `heal-failed` settle — never re-queued to `failed/`.
- Imports **ports/models only** (guarded like `SourcePoller`).

**Test** (`tests/test_healer.py`, in-memory queues + `FakeAgentRunner` +
`MemoryIssueTracker` + `FakeClock`):
- verdict `done` (agent "writes" `issue.md`) → one issue opened with `marker=id`;
  task now in `healed/`, gone from `failed/`; `healed` event emitted.
- verdict `request_changes` → no issue; task in `healed/`.
- runner raises → no issue (or `IssueError`) → task still in `healed/` with a
  `heal-failed` history entry; **not** in `failed/`.
- empty `failed/` → `tick()` returns False.
- lost `claim` race → `tick()` returns False, no settle.
- second `tick` for the same marker (simulated re-add) → existing issue returned.

Commit: `feat: add Healer loop assigned to the failed queue`

## Task 7 — wiring in `app.py`

**Extend** `build(...)`:
- new params `issue_tracker: IssueTracker | None`, `heal: HealConfig | None`
  (dataclass: `spec_name="healer"`, `repository`, `labels=("harness:self-heal",)`).
- when both set: build the `healed/` `FilesystemTaskQueue`; resolve the healer
  `AgentSpec` via the catalog; construct `Healer(...)`; store it so `Harness.run`
  gathers a heal loop (mirror the source loop).
- `Harness`: hold `healer: Healer | None`; `run()` adds `_heal_loop` to
  `asyncio.gather` when present; `recover()` includes the healer's `failed`
  `.processing/` (already covered — `failed` is in the recover set? it is not
  today; **add `failed`/`healed` to `recover()`** so a crash mid-heal returns the
  task to `failed/`). Add `HEALED = "healed"` reserved status to `models.py`.
- `heal=None` default → no healer, no `healed/` queue, unchanged behavior.

**Test** (extend `tests/test_app.py`): `build(..., heal=HealConfig(...),
issue_tracker=MemoryIssueTracker())` constructs a `Healer` and a `healed/` queue;
plain `build(...)` constructs neither. Existing e2e stays green.

Commit: `feat: wire the Healer loop and healed/ terminal in app.build`

## Task 8 — CLI

**Extend** `cli.py`: `harness run` builds `GithubIssueTracker(HttpGithubClient(...))`
when `GITHUB_TOKEN` is set (else `MemoryIssueTracker`) and passes
`heal=HealConfig(repository=<harness repo>, ...)` when a `--heal`/`--heal-repo`
flag is given. Without it, no healer.

**Test** (extend `tests/test_cli.py`): `run` with the heal flag injects a `Healer`;
without it, none.

Commit: `feat: wire self-healing into harness run`

## Task 9 — end-to-end

**Add** `tests/test_self_heal_e2e.py` on in-memory drivers (Memory queue/artifacts,
`FakeClock`, `FakeAgentRunner` scripted for the healer, `MemoryIssueTracker`):
- Induce a failure (a raising behavior, or a workflow with a missing edge) so a
  task reaches `failed/`.
- Assert the running harness: healer claims it → files an issue (verdict `done`)
  → task ends in `healed/`, issue opened with the marker and a `data.source`
  backlink when present.
- Assert `request_changes` → healed with no issue.
- Assert a heal-time error settles to `healed/` and never loops back to `failed/`.

Commit: `test: end-to-end self-healing (failed queue -> healer -> issue)`

## Task 10 — architecture guards + docs

**Extend** `tests/test_architecture.py`: `dispatcher`/`consumer` import neither
`harness.ports.issues` nor `harness.healer`; `healer.py` imports only ports/models
(a named assertion like `test_source_poller_imports_only_ports_and_models`).

**Update** `CLAUDE.md`: invariants 24–27 (from the spec), the new modules in the
module map, the `healed/` terminal in "What is responsible for what", and a
"Gotchas" note ("the healer never writes back to failed/, so it cannot loop").

Commit: `docs: record self-healing invariants and module map`

## Sequencing notes

- Tasks 1–4 (port + drivers) are independent of the harness loop and land in
  isolation.
- Task 6 (`Healer`) depends on 1 (port) and a fake runner; it's the heart.
- Watch `recover()` in Task 7: `failed`/`healed` must join the recover set so a
  crash mid-heal returns the task to `failed/` (its `.processing/` today is never
  swept because nothing consumed `failed`).
- The opt-in `claude -p` smoke (`HARNESS_SMOKE_CLAUDE=1`) can grow a healer case
  later; not required for completion.
- Keep every commit's subject a conventional-commit type so
  `python-semantic-release` cuts the right bump (`feat:` here → minor).
