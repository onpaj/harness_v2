# Design — manual "Add issue" button on the Ahanas board

Concrete design for the plan in `plan-01.md`. That document already settled the
functional requirements, the port/driver shapes and the rough build order; this
document works out the operator-facing UX, the exact boundaries between the
new pieces, and the wire/data shapes each one passes across those boundaries.
It does not restate developer tasks — see `plan-01.md`'s "Rough plan" for that.

## UX/UI

### Wireframes

**Board header — idle state** (`board.html`, `.page-header`). The button sits
in the existing header, to the right of the title, using the same
`page-header__action` idiom already used for "+ New agent" / "+ New workflow"
/ "+ New process" on the admin list pages:

```
┌─────────────────────────────────────────────────────────────────┐
│  Board                                        [ + Add issue ]   │
├─────────────────────────────────────────────────────────────────┤
│  [todo]     [in-progress]     [review]     [done]     [failed]  │
│   ...          ...              ...          ...        ...     │
└─────────────────────────────────────────────────────────────────┘
```

**`<dialog id="add-issue">` — freshly opened**, modeled directly on
`<dialog id="detail">`'s existing full-screen-on-phone / centered-on-desktop
sheet:

```
┌───────────────────────────────────────────────────┐
│  Add issue                                    ✕   │
├───────────────────────────────────────────────────┤
│  Paste one or more GitHub issue refs, separated    │
│  by commas, spaces or newlines:                    │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │ onpaj/harness_v2#42                            │ │
│  │ https://github.com/onpaj/harness_v2/issues/57  │ │
│  │                                                 │ │
│  │                                                 │ │
│  └───────────────────────────────────────────────┘ │
│  owner/repo#number or a full issue URL, one or     │
│  more per line.                                     │
│                                                     │
│                              [ Cancel ] [ Add ]    │
│                                                     │
│  ┌─ results (empty until first submit) ──────────┐ │
│  └─────────────────────────────────────────────────┘
└───────────────────────────────────────────────────┘
```

**`<dialog id="add-issue">` — after submitting a mixed batch** (2 good, 1 bad;
dialog stays open, textarea untouched so the operator can fix the bad line
without retyping the good ones):

```
┌───────────────────────────────────────────────────┐
│  Add issue                                    ✕   │
├───────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────┐ │
│  │ onpaj/harness_v2#42                            │ │
│  │ https://github.com/onpaj/harness_v2/issues/57  │ │
│  │ onpaj/not-a-repo#9                             │ │
│  └───────────────────────────────────────────────┘ │
│                              [ Cancel ] [ Add ]    │
│                                                     │
│  ┌─ results ───────────────────────────────────────┐
│  │ ✓ onpaj/harness_v2#42 — queued as tsk_ab12cd    │ │
│  │ ✓ github.com/.../issues/57 — already queued     │ │
│  │   as tsk_9f0e21                                  │ │
│  │ ✗ onpaj/not-a-repo#9 — repo "onpaj/not-a-repo"  │ │
│  │   is not registered (check repos.json)          │ │
│  └───────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────┘
```

Visual language: the ✓ lines use `--text`/`--text-2` (a queued task is a normal
outcome, not a warning — including the `already queued` case, which is
success, not an error, matching FR-2's AC), the ✗ line reuses the existing
`.field-error`/`--failed-fg` color already used for form validation and the
`banner.error` block on the admin forms — no new color token.

### Component hierarchy

```
board.html
├── .page-header
│   ├── <h1>Board</h1>
│   └── <button id="add-issue-open" class="btn small primary page-header__action">  (new)
├── #board                                                         (existing, unchanged)
└── <dialog id="add-issue">                                        (new, sibling of #detail)
    ├── header (title + ✕ close, same markup shape as .task-detail__header)
    ├── <form hx-post="/issues/import" hx-target="#add-issue-results" hx-swap="innerHTML">
    │   ├── <textarea name="refs" placeholder="…">
    │   └── .form-actions: [Cancel] [Add issue]  (submit button labelled "Add issue")
    └── <div id="add-issue-results">              → swapped by _issue_import_result.html
        └── <ul class="issue-import-results">
            └── <li class="ok|error"> one per submitted ref (new template, new)
```

`_issue_import_result.html` is a small, self-contained fragment analogous to
`_task.html`: it never re-renders the textarea or the dialog chrome, only the
results list, so a submit doesn't clobber what the operator typed.

### Key interactions

1. **Open**: click "+ Add issue" → `dialog.showModal()`, same idiom as opening
   `#detail` (a plain `addEventListener('click', ...)` on the button, calling
   `showModal()` on `#add-issue` — no htmx round trip needed to open it, since
   there is nothing to fetch yet, unlike `#detail` which loads a task
   fragment).
2. **Submit**: htmx `hx-post="/issues/import"` with the textarea's raw text,
   swapping only `#add-issue-results` (`hx-target`/`hx-swap="innerHTML"`) — the
   dialog itself never closes or re-renders on submit, mirroring how
   restart/delete swap `#detail`'s content in place rather than closing it.
3. **Board refresh**: each successfully queued issue triggers the same
   `"ingested"` event `SourcePoller` already emits (plan-01.md FR-3 step 7), so
   the existing `sse:board` → `/fragment/board` swap picks up the new `todo`
   card with zero new client-side wiring — the operator sees the new card
   appear behind the still-open dialog.
4. **Close**: the ✕ button and clicking the backdrop close the dialog exactly
   like `#detail`'s existing delegated handlers (`event.target.id ===
   'add-issue'` / a `.dialog-close`-equivalent class) — reuse the same
   delegated-listener pattern, added as one more block in `board.html`'s
   existing `<script>`, not a new script file.
5. **Re-submit after fixing a bad line**: the operator edits the textarea in
   place (unchanged, since it isn't cleared) and clicks "Add issue" again;
   the previous results list is replaced wholesale by the new one — no
   attempt to merge/append across submits, keeping the fragment stateless.
6. **Keyboard**: `<dialog>` gives focus trapping and Esc-to-close for free
   (same as `#detail` today); the button and textarea are reachable by Tab in
   document order, satisfying the "reachable via keyboard" acceptance
   criterion with no custom focus-management code.

## Component design

```
                     ┌─────────────────────────┐
  api/routes.py  ──▶ │  IssueImport (port)     │ ◀── cli.py wires the real
  (POST /issues/     │  ports/issue_import.py  │     driver only when
  import)            └───────────┬─────────────┘     GITHUB_TOKEN is set
                                  │
                                  ▼
                     ┌─────────────────────────┐
                     │ GithubIssueImportService│
                     │ drivers/                │
                     │ github_issue_import.py  │
                     └───┬───────┬───────┬─────┘
                         │       │       │
              ┌──────────┘       │       └───────────┐
              ▼                  ▼                    ▼
     GithubClient.get_issue  RepositoryRegistry   TaskQueue×6 + EventSink + Clock
     (new abstract method)  .resolve/.names       (inbox, step queues, done,
                                                    failed, healed, archived)
```

**`IssueImport` (`ports/issue_import.py`)** — the write-side port `api/` talks
to, mirroring `TaskControl`'s shape exactly (invariant #23's sibling for a
create verb instead of restart/delete):
- Responsibility: turn one operator-supplied ref string into one outcome,
  never more, never raising.
- Boundary: knows nothing of GitHub, queues, or HTML — a pure `str → dataclass`
  contract from the caller's point of view.
- Consumers: `api/routes.py`'s `POST /issues/import` handler only. Nothing
  else in `api/` touches it (mirrors `AgentAdmin`/`WorkflowAdmin`/
  `ProcessAdmin`'s "UI-facing admin port" footing, invariant #33 — except this
  one creates a task rather than editing config, so it lives in
  `ports/issue_import.py`, not renamed into an `_admin` module).

**`GithubIssueImportService` (`drivers/github_issue_import.py`)** — the only
implementation, wired exclusively in `cli.py`:
- Responsibility: ref parsing, repo resolution, the GitHub point-lookup,
  dedup-by-scan across every queue, `Task` construction, best-effort claim
  label, `inbox.put` + `events.emit("ingested", ...)`.
- Boundary: depends only on ports already in scope for `cli.py`'s wiring
  (`GithubClient`, `RepositoryRegistry`, `TaskQueue`, `EventSink`, `Clock`) —
  no new port needed for any of its own dependencies, since every one of them
  is already a port `GithubTaskSource`/`GithubIssuesCheck` depend on today.
- Not a `TaskSource`: it has no `poll()` loop and is driven by a single
  synchronous HTTP request, not the source-polling cadence — a distinct
  interface (`IssueImport`) rather than shoehorning a fourth verb onto
  `TaskSource`.

**`GithubClient.get_issue` (extends the existing `drivers/github_client.py`
ABC)** — the one new capability the whole feature actually needed that wasn't
already there: a point lookup by number, independent of label. `FakeGithubClient`
and `HttpGithubClient` both implement it; every other component in this
design is composition of existing ports plus this one method.

**`api/routes.py` — `POST /issues/import`**:
- Responsibility: read the form field, split it into refs (`re.split(r"[,\s]+",
  text)`, filter empty), call `issue_import.add(ref)` once per ref
  **sequentially** (never `asyncio.gather`/thread pool — sequential is what
  makes FR-4's same-batch dedup correct without a lock), render the results
  fragment.
- Boundary: imports `harness.ports.issue_import.IssueImport` only — no driver
  import (test_architecture.py's existing glob check covers this the same way
  it already covers `AgentAdmin` et al.).

**`api/app.py` — `_NullIssueImport`**:
- Responsibility: the same "always a concrete port" fallback idiom as
  `_NullTaskControl`/`_EmptyArtifactView`. `add(ref)` always returns
  `IssueImportResult(ref=ref, ok=False, error="GitHub is not configured on
  this harness (no GITHUB_TOKEN)")` — a board with no token still renders the
  button and dialog, but every submit reports the same clear reason instead of
  a 500 or a silently-missing route.

**`board.html` / `_issue_import_result.html`**:
- Responsibility: presentation only. No decision-making — every line in the
  results list is a direct rendering of one `IssueImportResult`, in submission
  order, with no client-side re-interpretation of `ok`/`already_queued`/`error`.

### Interfaces (signatures)

```python
# ports/issue_import.py
@dataclass(frozen=True)
class IssueImportResult:
    ref: str
    ok: bool
    task_id: str | None = None
    already_queued: bool = False
    error: str | None = None

class IssueImport(ABC):
    @abstractmethod
    def add(self, ref: str) -> IssueImportResult: ...
```

```python
# drivers/github_client.py (addition to the existing ABC)
class GithubClient(ABC):
    @abstractmethod
    def get_issue(self, repo: str, number: int) -> Issue | None: ...
```

```python
# api/routes.py (new, inside build_html_router; issue_import: IssueImport param)
@router.post("/issues/import", response_class=HTMLResponse)
def import_issues(request: Request, refs: str = Form("")) -> HTMLResponse: ...
```

## Data schemas

### `IssueImportResult` (transient DTO — port return value, never persisted)

| field           | type          | meaning                                                          |
|-----------------|---------------|-------------------------------------------------------------------|
| `ref`           | `str`         | the input echoed back, so the UI can key a result row to its line |
| `ok`            | `bool`        | `False` only for a genuine failure (bad syntax, unknown repo, 404, network error) |
| `task_id`       | `str \| None` | set whenever `ok` is `True` (fresh or already-queued)              |
| `already_queued`| `bool`        | `True` when `task_id` names a pre-existing task, not a new one     |
| `error`         | `str \| None` | human-readable, set only when `ok` is `False`                     |

Three renderable states, exhaustive:
- `ok=True, already_queued=False` → "✓ queued as `<task_id>`"
- `ok=True, already_queued=True`  → "✓ already queued as `<task_id>`"
- `ok=False`                       → "✗ `<error>`"

### `POST /issues/import` — request/response

**Request** (`application/x-www-form-urlencoded`, from the dialog's `<form>`):
```
refs=onpaj/harness_v2%2342%0Ahttps://github.com/onpaj/harness_v2/issues/57%0Aonpaj/not-a-repo%239
```
Single field `refs`: the textarea's raw text, un-split.

**Response**: `200 OK`, `text/html`, an HTML fragment (`_issue_import_result.html`),
one `<li>` per non-empty parsed ref, in submission order. An empty/whitespace
submission renders a single "no refs given" line without calling `add()` at
all (FR-6's AC) — the route, not the driver, is what recognizes "nothing to
do" here.

### `Task` created by a successful `add()` — identical shape to auto-ingestion

No new fields on `models.Task`. `GithubIssueImportService` builds exactly what
`GithubTaskSource.poll()` builds today:

```python
Task(
    id=new_task_id(),                                    # tsk_xxxxxxxx
    workflow_template=self._workflow,                     # configured target, FR-7
    step=self._step,
    created=self._clock.now(),
    repository=self._repository,                          # the registry name, e.g. "harness_v2"
    worktree=f"{self._worktree_root}/{task_id}",
    dedup_key=dedup_key("github", slug, number),           # e.g. "github:onpaj/harness_v2:42"
    data={
        "title": issue.title,
        "body": issue.body,
        "source": {
            "kind": "github",
            "repo": slug,        # "owner/repo"
            "issue": number,     # int
            "url": issue.url,
        },
    },
)
```

`data.source`'s four keys (`kind`/`repo`/`issue`/`url`) are byte-for-byte what
`GithubLabelReflector`, `GithubIssueChecker` and `GithubMergeChecker` already
read — this identity is what makes every downstream consumer (dispatcher,
outward label reflection, the merge/issue reconcilers) require zero changes.

### `"ingested"` event payload — reused verbatim

```python
events.emit("ingested", task_id=task.id, queue=TODO_COLUMN, task=task.to_dict())
```
The exact call `SourcePoller.tick()` already makes (invariant #7: a
task-movement event carries both `task` and `queue`) — the projection and the
board's SSE path need no new event type or handler.

### Ref parsing grammar (route-level, not persisted, informs both the
textarea placeholder and the route's splitter)

- Splitter: `re.split(r"[,\s]+", text.strip())`, empty tokens dropped.
- Accepted ref shapes, each resolving to `(slug: str, number: int)`:
  - `owner/repo#number` — e.g. `onpaj/harness_v2#42`
  - `https://github.com/owner/repo/issues/number` (with or without a
    trailing slash/query string)
- Anything else → `IssueImportResult(ok=False, error="not a valid owner/repo#number or issue URL: <ref>")`.

```json
{"outcome": "done", "summary": "Wrote design-01.md covering the Ahanas 'Add issue' UX (wireframes, dialog/results-panel component hierarchy, keyboard/SSE interactions), component boundaries for IssueImport/GithubIssueImportService/GithubClient.get_issue/the new route, and the IssueImportResult/request-response/Task/ingested-event/ref-grammar data schemas."}
```
