# Self-healing — implementation plan

Spec: `docs/superpowers/specs/2026-07-21-self-healing-design.md`
Date: 2026-07-21

Build order is bottom-up: port → drivers → behavior → trigger → workflow/persona
→ wiring → e2e. Each task lands with its own tests, `pytest -q` green, and a
conventional-commit subject. Nothing here touches `main`'s existing flow unless
`heal` is wired (default off), so every step is safe to commit incrementally.

## Task 1 — the `IssueTracker` port

**Add** `src/harness/ports/issues.py`: `IssueRef(number, url)`, `IssueError`,
`IssueTracker` (ABC) with `open_issue(repo, *, title, body, labels, marker) -> IssueRef`.

- Docstring states the idempotency-by-marker contract (spec §"The `IssueTracker` port").
- No import of anything outside `abc`/`dataclasses`/`harness.models` if needed.

**Test** (`tests/test_issue_port.py`): the ABC can't be instantiated; a trivial
subclass satisfies the signature.

Commit: `feat: add IssueTracker port for opening advisory issues`

## Task 2 — `MemoryIssueTracker` (fake)

**Add** `MemoryIssueTracker` to `drivers/memory.py`: issues in a list, `open_issue`
searches by `marker` (return existing) else appends a new `IssueRef` with a
synthetic url. Expose `.opened` for assertions.

**Test** (`tests/test_issue_memory.py`): open once → one issue; open again with the
same marker → same `IssueRef`, list length unchanged; different marker → second issue.

Commit: `feat: add MemoryIssueTracker fake`

## Task 3 — `FileIssueBehavior`

**Add** `src/harness/behaviors/issue.py`, modeled on `behaviors/landing.py`:

- Ctor: `clock, artifacts: ArtifactView, tracker: IssueTracker, repo, labels`.
- `run(task)`: pick the latest `diagnose` artifact via `artifacts.list(task.id)`;
  derive title (first `# ` line, else `data.request` first line) and body (artifact
  content + a `data.source` footer if present); call
  `tracker.open_issue(repo, title=…, body=…, labels=…, marker=data.heal_of or task.id)`;
  return `BehaviorResult(DONE, f"opened issue {ref.url}")`.
- Imports **ports only** (invariant: `test_behaviors_import_only_ports_not_drivers`).

**Test** (`tests/test_issue_behavior.py`): with a `MemoryArtifactStore` seeded with a
diagnose artifact + a `MemoryIssueTracker`, `run` opens one issue with the right
title/body/marker and returns `done`. A tracker that raises `IssueError` propagates
(the consumer will fail the task).

Commit: `feat: add FileIssueBehavior — worker opens the healer's issue`

## Task 4 — `SelfHealSink`

**Add** `src/harness/drivers/self_heal.py`: `SelfHealSink(EventSink)`.

- Ctor: `inbox, events, clock, ids, heal_workflow, heal_repository, worktree_root`.
- `emit`: ignore non-`failed`; parse `task`; `_should_heal` guard (workflow ==
  heal_workflow → skip; `data.heal is False` → skip); `_compose` a heal `Task`
  (spec §"What the heal task carries"); `inbox.put`; `events.emit("ingested", …,
  queue=TODO_COLUMN, task=…)`.
- `_failure_report(failed, reason)`: a compact string — id, workflow, failing
  step (`failed.status`), reason, and the consumer-history summaries.

**Test** (`tests/test_self_heal.py`, in-memory queue + recording sink):
- a `failed` event for an ordinary task → one heal task in the inbox with
  `workflow_template="heal"`, `repository=<harness>`, `data.heal_of`, `data.request`;
  an `ingested` event was emitted with `queue=TODO_COLUMN`.
- a `failed` event for a task already on the `heal` workflow → **nothing** put
  (recursion guard).
- `data.heal is False` → nothing put.
- a non-`failed` event, or one with no `task` → nothing put.

Commit: `feat: add SelfHealSink — turn a failure into a heal task`

## Task 5 — `GithubClient.create_issue` / `search_issue_by_marker`

**Extend** `drivers/github_client.py`:

- ABC: `create_issue(repo, *, title, body, labels) -> Issue`;
  `search_issue_by_marker(repo, marker) -> Issue | None`.
- `FakeGithubClient`: implement both (create appends with an incrementing number
  and the `harness:self-heal` label; search scans bodies for the marker).
- `HttpGithubClient`: `create_issue` → `POST /repos/{repo}/issues`;
  `search_issue_by_marker` → reuse `list_issues(repo, label="harness:self-heal")`
  and match the marker substring in the body. Stdlib only.

**Test** (extend `tests/test_github_client.py`): fake create + search round-trip;
Http create/search against the injected `opener` (no network), asserting method,
URL and JSON body.

Commit: `feat: add create_issue/search_issue_by_marker to GithubClient`

## Task 6 — `GithubIssueTracker`

**Add** `drivers/github_issues.py`: `GithubIssueTracker(IssueTracker)` over
`GithubClient`. `open_issue`: `search_issue_by_marker` → return its `IssueRef` if
found; else append the `<!-- harness-heal:<marker> -->` line to the body and
`create_issue`; wrap client errors in `IssueError`.

**Test** (`tests/test_github_issues.py`, `FakeGithubClient`): open → creates with the
marker embedded; open again same marker → returns the existing one, no second
create; a client that raises → `IssueError`.

Commit: `feat: add GithubIssueTracker driver`

## Task 7 — the `heal` workflow + `healer` persona

**Add** `harness init` output:
- `workflows/heal.json`: start `diagnose`; edges `diagnose --done--> file_issue`,
  `diagnose --request_changes--> end`, `file_issue --done--> end`.
- `agents/healer.json`: the persona (spec §"The `healer` persona"),
  `allowed_outcomes: ["done", "request_changes"]`.
- register the harness repo name (default `"harness"`) in the generated `repos.json`.

**Test** (extend `tests/test_cli.py`): after `init`, `heal.json` loads as a valid
`Workflow` with those edges; `healer.json` parses to an `AgentSpec` with both
outcomes.

Commit: `feat: ship heal workflow and healer persona from harness init`

## Task 8 — wiring in `app.py`

**Extend** `build(...)`:
- new params `issue_tracker: IssueTracker | None`, `heal: HealConfig | None`
  (dataclass: `workflow`, `repository`, `file_issue_step="file_issue"`,
  `labels=("harness:self-heal",)`).
- when `heal` is set: build `FileIssueBehavior` and special-case
  `heal.file_issue_step` in `behavior_for` (next to `landing_step`); after the
  composite is built, append a `SelfHealSink` bound to it and the inbox.
- `heal=None` default → no behavior change (assert via the existing e2e still green).

**Test** (extend `tests/test_app.py`): `build(..., heal=HealConfig(...),
issue_tracker=MemoryIssueTracker())` serves the heal workflow and registers the
sink; `build(...)` without them is unchanged (no heal queues, no sink).

Commit: `feat: wire SelfHealSink and FileIssueBehavior in app.build`

## Task 9 — end-to-end

**Add** `tests/test_self_heal_e2e.py` on in-memory drivers (Memory queue/workspace/
artifacts, `FakeClock`, `FakeAgentRunner` scripted for the `healer`,
`MemoryIssueTracker`):

- Induce a failure (a behavior that raises, or a workflow with a missing edge).
- Assert: heal task created → `diagnose` runs → on `done` routes to `file_issue`
  → issue opened on the harness repo with the marker; on `request_changes` routes
  to `end` with no issue.
- Assert the recursion guard end-to-end: the heal task failing yields no further
  heal task.
- Assert marker dedup: a second identical failure/file_issue returns the same issue.

Commit: `test: end-to-end self-healing (failure -> heal -> issue)`

## Task 10 — architecture guards + docs

**Extend** `tests/test_architecture.py`: `dispatcher`/`consumer` import neither
`harness.ports.issues` nor `harness.drivers.self_heal`; `behaviors/issue.py` imports
no driver (already covered generically — add a named assertion for readability).

**Update** `CLAUDE.md`: add invariants 24–27 (from the spec), the new modules to the
module map, and a "Gotchas" note ("the healer heals everything but itself").

Commit: `docs: record self-healing invariants and module map`

## Sequencing notes

- Tasks 1–6 are independent of the harness loop and land in isolation.
- Task 4 (`SelfHealSink`) and Task 3 (`FileIssueBehavior`) can be built in parallel;
  both are needed before Task 8.
- The opt-in `claude -p` smoke (`HARNESS_SMOKE_CLAUDE=1`) can grow a healer case
  later; it is not required for completion.
- Keep every commit's subject a conventional-commit type so
  `python-semantic-release` cuts the right bump (`feat:` here → minor).
