# Plan — manual "Add issue" button on the Ahanas board

## Summary

Today a GitHub issue only becomes a task through the `github-issues` Process
action, which requires the operator to first attach `harness:todo` to it on
GitHub. This adds a second, manual entry point: a button on the board that
lets the operator paste one or more `owner/repo#number` refs (or issue URLs),
fetches each issue directly by number (no label required), and queues it as a
fresh inbox task with `data.source` stamped exactly like automatic ingestion —
so outward label reflection, dispatching and dedup all keep working unchanged.

## Context

`GithubIssuesCheck`/`GithubTaskSource` are both *scanners*: they list issues by
label and claim by swapping it. There is no *point* lookup ("fetch issue #N on
repo R regardless of label") anywhere in the codebase, and no write path from
`api/` that can create a task from outside data — `TaskControl` only
moves/deletes tasks that already exist. Both gaps need filling. The design
must not let `api/` import a driver (invariant #5, guarded by
`test_api_does_not_import_drivers`), so this needs a new port, following the
exact shape of `AgentAdmin`/`WorkflowAdmin`/`ProcessAdmin` (invariant #33): a
UI-facing write port, a driver behind it wired only in `cli.py`.

## Functional requirements

**FR-1 — `GithubClient.get_issue(repo, number)`: point lookup by number.**
Add an abstract method fetching a single issue regardless of label, mirroring
`get_issue_state`'s "404 → `None`, not an exception" contract.
- AC: `FakeGithubClient.get_issue` returns the issue if present (open or
  closed — closed is a legitimate deliberate re-queue, not filtered out),
  `None` if the number is unknown.
- AC: `HttpGithubClient.get_issue` calls `GET /repos/{repo}/issues/{number}`,
  maps a 404 to `None`, re-raises every other `HTTPError`.
- AC: an item with `"pull_request"` in the payload behaves like `list_issues`
  and is still returned as an `Issue` (a PR number pasted by mistake isn't
  silently swallowed here — filtering PRs only matters for the *scanning*
  path); the import service itself does not special-case it either — that's
  an explicit non-goal (see Open questions).

**FR-2 — `ports/issue_import.py`: the write-side port.**
```python
@dataclass(frozen=True)
class IssueImportResult:
    ref: str                    # echoes the input, so the UI can key results to rows
    ok: bool
    task_id: str | None = None
    already_queued: bool = False
    error: str | None = None

class IssueImport(ABC):
    @abstractmethod
    def add(self, ref: str) -> IssueImportResult: ...
```
One ref in, one result out — batching is the caller's loop, not the port's
concern (keeps the port symmetric with `TaskControl.restart`/`.delete`, each
one task at a time).
- AC: `add()` never raises — every failure mode (bad ref syntax, unregistered
  repo, issue not found, network/auth error) comes back as `ok=False` with a
  human-readable `error`.
- AC: a duplicate (see FR-4) comes back `ok=True, already_queued=True,
  task_id=<existing id>` — success, not an error, so the UI doesn't scare the
  operator over a no-op.

**FR-3 — `drivers/github_issue_import.py`: `GithubIssueImportService`.**
The driver implementing `IssueImport`, built from a `GithubClient`, a
`RepositoryRegistry`, every live/terminal `TaskQueue` the harness holds
(`inbox`, `step_queues`, `done`, `failed`, `healed`, `archived`), `EventSink`,
`Clock`, `worktree_root`, and a default `workflow`/`step` target (mirrors
`GithubTaskSource`'s constructor shape).

`add(ref)` does, in order:
1. Parse `ref` into `(slug, number)` — accepts `owner/repo#123` and a full
   `https://github.com/owner/repo/issues/123` URL. A ref that parses to
   neither shape is a syntax error (`ok=False`), never a crash.
2. Resolve `slug` against the registry: `github_slug(registry.resolve(name))`
   for each `name in registry.names()`, first match wins. No match → error
   naming the slug and pointing at `repos.json` (mirrors the existing
   "not registered" warning shape in `cli._run`'s heal-repo check).
3. `client.get_issue(slug, number)`. `None` → "issue not found" error.
4. Compute `dedup_key("github", slug, number)` (reuse
   `harness.ports.source.dedup_key` — the exact key `GithubTaskSource` and
   `GithubIssuesCheck` would have produced for the same issue, so dedup is
   shared vocabulary, not a second identity scheme) and check it against
   every task currently in any of the queues passed to the constructor
   (`already_queued` path, FR-4).
5. Build a fresh `Task` — same shape as `GithubTaskSource.poll()`'s: `id =
   new_task_id()`, `workflow_template`/`step` = the service's configured
   target, `repository = name`, `worktree = f"{worktree_root}/{task_id}"`,
   `dedup_key` from step 4, `data = {"title": issue.title, "body": issue.body,
   "source": {"kind": "github", "repo": slug, "issue": number, "url":
   issue.url}}`.
6. Best-effort `client.add_label(slug, number, claimed_label)` (default
   `"harness:queued"`, same constant `GithubTaskSource`/`GithubIssuesCheck`
   use) — not required for correctness, but stops the `github-issues` action
   from re-claiming the same issue later if it happens to also carry
   `harness:todo`. A failure here is logged/swallowed, not fatal — the task
   is already real by this point and must not be lost over a label call.
7. `inbox.put(task)`; `events.emit("ingested", task_id=task.id,
   queue=TODO_COLUMN, task=task.to_dict())` — the exact event
   `SourcePoller.tick()` emits for an ingested task, so the board picks it up
   through the same SSE path with no new client-side handling (invariant #7).
8. Return `ok=True, task_id=task.id`.

Every `client`/`registry` call in 2–3 is wrapped so a network/auth exception
becomes `ok=False, error=str(...)`, never propagates.
- AC: repo not registered → clear error naming the slug.
- AC: issue not found (404) → clear error naming repo+number.
- AC: malformed ref (`"not-a-ref"`, `"owner/repo"` with no `#number`) → clear
  error, no exception.
- AC: two refs pointing at the same issue in one batch → first creates a task,
  second reports `already_queued` with the first's task id (see FR-4's
  same-batch race note).
- AC: on success the new task is visible in the inbox and carries `data.source`
  with all four keys, byte-for-byte the same shape `GithubTaskSource` stamps.

**FR-4 — Idempotency.**
`add()` treats an issue already represented by a live or terminal task
(anywhere in `inbox`/step queues/`done`/`failed`/`healed`/`archived`) as
already-queued, not a new task — scanning `dedup_key` across every queue
passed to the constructor at call time (a synchronous, per-request scan; the
queue set is small and this path is operator-triggered, not hot-loop, so no
in-memory ledger is needed the way `SourcePoller._seen` needs one for its
polling hot loop).
- AC: an issue already ingested by `github-issues`/`GithubTaskSource` and
  currently anywhere on the board is reported `already_queued`, not
  duplicated, when pasted into "Add issue".
- AC: two identical refs in the *same* submitted batch: the first `add()` call
  puts the task into `inbox` before the second is evaluated (the route calls
  `add()` sequentially per parsed ref, not concurrently — see FR-6), so the
  second sees it and reports `already_queued` too. No race within one batch.
- AC: re-submitting the exact same ref in a later, separate request also comes
  back `already_queued` (the dedup key persists on the task on disk, so this
  holds across a harness restart between the two submissions).

**FR-5 — Board UI: the "Add issue" button and dialog.**
An "Add issue" button in the board page header (`board.html`, next to the
existing `<h1>Board</h1>`, styled with the existing `.btn`/`.btn.primary`
classes — no new visual language). Clicking it opens a `<dialog>` (same
pattern as `#detail`) containing:
- a `<textarea>` accepting one or more refs, comma/space/newline-separated
  (placeholder text shows the `owner/repo#123` and full-URL forms);
- a submit button posting the textarea's raw value via htmx
  (`hx-post="/issues/import"`, `hx-target` on a results panel inside the same
  dialog, `hx-swap="innerHTML"` — same idiom as the restart/delete buttons);
- a results panel that, after submit, lists one line per parsed ref: ✓ queued
  as `<task id>` / ✓ already queued as `<task id>` / ✗ `<error>` — so a mixed
  batch (2 good, 1 bad) shows exactly which one failed instead of an
  all-or-nothing message. The dialog stays open after submit so the operator
  can read the per-ref outcomes and fix the bad one without retyping the good
  ones (the textarea is not cleared on error).
- AC: the button is visible and reachable via keyboard on the board page in
  both the light and dark theme (uses only existing CSS custom properties —
  `--accent`, `--surface`, `--failed-fg`, etc. — no new hardcoded colors).
- AC: submitting a batch of 3 refs where 1 is invalid shows 2 successes and 1
  clearly labeled failure, and the 2 valid tasks are visible in `todo` on the
  board without a page reload (existing SSE board refresh, driven by the
  `ingested` event from FR-3 step 7).
- AC: works with only a `<dialog>`/htmx round trip — no new JS beyond the
  existing open/close idiom already used for `#detail`.

**FR-6 — `POST /issues/import` route (`api/routes.py`, `build_html_router`).**
Accepts a form field (the textarea's raw text), splits on `[,\s]+` into
non-empty refs, calls `issue_import.add(ref)` once per ref **sequentially**
(so FR-4's same-batch dedup holds without extra locking), collects the
`IssueImportResult`s, renders a new template fragment
(`_issue_import_result.html`) listing them.
- AC: an empty/whitespace-only submission renders "no refs given" without
  calling `add()`.
- AC: the route imports only `harness.ports.issue_import.IssueImport` — no
  driver import in `api/` (guarded by `test_api_does_not_import_drivers`).

**FR-7 — Wiring (`cli.py`, `app.py`).**
- `create_app`/`build_html_router` (`api/app.py`, `api/routes.py`) gain an
  `issue_import: IssueImport | None = None` parameter, defaulted to a new
  `_NullIssueImport` (returns `ok=False, error="GitHub is not configured on
  this harness (no GITHUB_TOKEN)"` for every ref) — same "always a concrete
  port, never `Optional` sprinkled through routes" idiom every other admin
  port already uses in that file.
- `cli._run` builds one `GithubIssueImportService` when a `GITHUB_TOKEN` is
  present (reusing the same `github_client`/`registry` already constructed
  there for `_process_check_factories`/`_github_sources` — no second client),
  reading `harness.inbox`/`harness.step_queues`/`harness.done`/
  `harness.failed`/`harness.healed`/`harness.archived` off the already-built
  `Harness` object, and passes it into `serve(..., issue_import=...)`, which
  threads it to `create_app`. Without a token, `serve` gets `None` and the UI
  falls back to `_NullIssueImport`'s clear "not configured" error — mirrors
  the existing "no token is not fatal" posture (self-heal, GitHub sources).
- The manual-add target workflow/step reuses `args.github_workflow`/
  `args.github_step` (defaulting to `DEFAULT_WORKFLOW` exactly like
  `_github_sources` does) — no new CLI flag. One default target for both the
  automatic and manual GitHub entry points keeps the surface small; an
  operator who wants a different target for manual adds can still route the
  resulting `todo`-column task by hand, same as any other task.
- AC: `test_architecture.py`'s existing glob-based driver-import checks stay
  green with no new exemption needed (the new driver is discovered the same
  way `FilesystemAgentAdmin` etc. already are).

## Non-functional requirements

- **Security**: `HttpGithubClient.get_issue` reuses the existing bearer-token
  auth path — no new credential handling. Repo access is still bounded by
  `repos.json`'s registry (an operator can only pull issues from a repo this
  harness machine already knows about) — pasting an arbitrary `owner/repo`
  not in the registry is a clean error, never a live fetch against an
  unregistered target.
- **Performance**: `add()` is called synchronously from an HTTP request
  handler; each ref costs one or two GitHub API calls (`get_issue` +
  optionally `add_label`). A pasted batch of a few dozen refs is fine
  sequentially; no batching/pagination concern at this scale (an operator
  pasting hundreds of ids by hand is not a case worth optimizing for).
- **Consistency**: the created task is byte-for-byte shaped like an
  auto-ingested one (`data.source`, `dedup_key`), so every downstream
  consumer (dispatcher, `GithubLabelReflector`, `IssueReconciler`,
  `MergeReconciler`) needs zero changes — this is the load-bearing design
  constraint, not an incidental nicety.

## Data model

No new persisted entity. `Task.data.source` gains no new shape — reuses the
existing `{"kind": "github", "repo": <slug>, "issue": <number>, "url": <url>}`
already defined by `GithubTaskSource`/`GithubIssuesCheck`. `IssueImportResult`
is a transient, unpersisted DTO (port return value → HTML fragment), not
stored anywhere.

## Interfaces

- `GithubClient.get_issue(repo: str, number: int) -> Issue | None` (new
  abstract method; `FakeGithubClient` + `HttpGithubClient` implementations).
- `ports/issue_import.py::IssueImport.add(ref: str) -> IssueImportResult`
  (new port).
- `drivers/github_issue_import.py::GithubIssueImportService` (new driver
  implementing the port).
- `POST /issues/import` (new HTML route, form-encoded `refs` field, returns
  an HTML fragment).
- Board UI: new "Add issue" button + `<dialog id="add-issue">` in
  `board.html`, new `_issue_import_result.html` fragment template.

## Dependencies and scope

Depends on: `RepositoryRegistry` (existing), `GithubClient` (existing, gains
one method), `TaskQueue` (existing), the already-wired `github_client`/
`registry` in `cli._run`. No change to `dispatcher`/`consumer`/`router`.

**Out of scope** (explicitly, per the task description):
- The automatic `github-issues` Process action and the `harness:todo` label
  contract — unchanged.
- A repo picker in the UI beyond the explicit `owner/repo#number`/URL form —
  the registry only resolves what's typed, it doesn't drive a dropdown (a
  follow-up, not blocking this).
- Choosing a workflow/step per manual submission from the UI — reuses the
  existing `--github-workflow`/`--github-step` default (see FR-7's note); a
  per-submission picker is a plausible follow-up, not required by the
  acceptance criteria as written.
- Bulk import from a file/CSV — the textarea covers "one or more IDs" already.

## Rough plan

1. `GithubClient.get_issue` — abstract method + `FakeGithubClient` +
   `HttpGithubClient` implementations; unit tests in `test_github_client.py`.
2. `ports/issue_import.py` — `IssueImport` ABC + `IssueImportResult`.
3. `drivers/github_issue_import.py` — `GithubIssueImportService`; unit tests
   (new `tests/test_github_issue_import.py`) covering: happy path, unknown
   repo, unknown issue, malformed ref, already-queued (pre-seeded queue and
   same-batch), the `data.source`/`dedup_key` shape, and the emitted
   `ingested` event.
4. `api/app.py` — `_NullIssueImport`, `create_app(issue_import=...)` wiring.
5. `api/routes.py` — `POST /issues/import` in `build_html_router`; ref
   parsing/splitting; `_issue_import_result.html` fragment.
6. `board.html` — "Add issue" button, `<dialog id="add-issue">`, results
   panel; reuse the existing dialog open/close script idiom (no new JS
   pattern).
7. `cli.py` — build `GithubIssueImportService` in `_run` when a token is
   present; thread `issue_import` through `serve`/`create_app`.
8. `test_architecture.py` — confirm the existing glob-based checks pick up
   the new driver/port with no changes needed; add one only if a check is
   genuinely too narrow (unlikely given the precedent of `AgentAdmin` etc.).
9. Update `CLAUDE.md`'s module map / "what is responsible for what" with the
   new port/driver, following the existing entries' voice (one short
   paragraph each, cross-referencing invariant #33's shape).

## Open questions

- **Bare issue numbers with no `owner/repo` prefix.** The task notes prefer
  the explicit form "if a repo is ambiguous." Default taken here: **always**
  require `owner/repo#number` or a full URL — no bare-number convenience mode
  even when only one repo is registered. Simpler, unambiguous, and consistent
  regardless of how many repos are registered at the time (a registry that
  later grows a second repo doesn't silently change what a previously-typed
  bare number would mean). Revisit if this proves annoying in practice.
- **Closed issues.** Default taken here: `get_issue` and `add()` don't check
  open/closed state — an operator who explicitly pastes an issue number
  presumably means it. If this turns out to be surprising, add a
  `state == "closed"` warning (not a hard error) to the per-ref result line
  in a follow-up; not blocking this task's acceptance criteria.
- **Per-submission workflow/step choice.** Default taken here: reuse
  `--github-workflow`/`--github-step` (FR-7). If operators need to route
  manual adds differently from automatic ones, add a `<select>` to the dialog
  in a follow-up — out of scope for the stated acceptance criteria.
