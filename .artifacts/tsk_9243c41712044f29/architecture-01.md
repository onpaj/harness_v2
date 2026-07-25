# Architecture assessment — manual "Add issue" button on the Ahanas board

Reviewed against `plan-01.md` and `design-01.md`, and against the current state of
`src/harness` (not just the plans). The UX, the port shape (`IssueImport`), the
`GithubClient.get_issue` addition, the `Task`/`data.source`/`dedup_key` reuse, and
the ref grammar are all sound and need no change. **One part of the wiring
narrative in both documents is architecturally wrong as written and must be
corrected before implementation starts** — section 1 below is the load-bearing
finding; everything else is confirmation or minor sharpening.

## 1. Where `GithubIssueImportService` gets built — the plan's FR-3/FR-7 is wrong

### The problem

Both `plan-01.md` (FR-7) and `design-01.md` say `cli._run` builds one
`GithubIssueImportService`, "reading `harness.inbox`/`harness.step_queues`/
`harness.done`/`harness.failed`/`harness.healed`/`harness.archived` off the
already-built `Harness` object."

That doesn't work, for two independent reasons:

**(a) Those attributes don't exist.** `Harness.__init__` (`app.py:118-172`) stores
the queues as `self._inbox`, `self._step_queues`, `self._done`, `self._failed`,
`self._healed` — private. The only queue exposed publicly is `self.archived`
(and that's a duplicate assignment, not a documented public surface). There is
no `harness.inbox` to read.

**(b) Even if they were exposed, this is the exact case the codebase has already
solved once, deliberately, the other way.** `FailedTasksCheck` needs the
harness's own live `failed`/`healed`/`events` — and the ADR-0018 comment in
`app.py` (lines 619-625) explains why it's built *inside* `build()` rather than
in `cli.py` like `github-issues`/`github-conflicts`:

> "Compiled here, inside `build()`, rather than by `cli.py` ... because the
> `failed-tasks` check needs the harness's own live `failed`/`healed_queue`/
> `events` — ports only `build()` itself constructs, not something `cli.py`
> can hand it independently."

`GithubIssueImportService` needs the identical thing: `inbox` (to put the task),
and for FR-4's idempotency scan, `step_queues`/`done`/`failed`/`healed`/
`archived` too — none of which exist until `build()` constructs them. `cli.py`
holding a `Harness` after the fact doesn't give it back-door access to those
internals, by design (this is why they're private). The plan's wiring
narrative re-introduces exactly the mistake ADR-0018 already ruled out for a
structurally identical component.

### The complication `FailedTasksCheck` didn't have

`FailedTasksCheck` has zero GitHub-specific dependencies (`failed`, `healed`,
`events`, `clock` — all generic ports), so `app.py` builds it directly, by
name, with no help from `cli.py`. `GithubIssueImportService` isn't that simple:
it also needs a `GithubClient` and a `RepositoryRegistry` — dependencies that
are `cli.py`'s to construct (a real `HttpGithubClient(token)`, `repos.json`),
never `app.py`'s. Today `app.py`/`build()` has **zero GitHub-specific
imports** — no `github_client`, `github_source`, `github_forge`, nothing. That
separation is deliberate and worth preserving: it's why `extra_checks` and
`finishers` exist as factory-injection points in the first place — `cli.py`
closes over the GitHub-specific pieces it built, hands `build()` a callable,
and `build()` never has to know what GitHub is.

### The fix: a factory, not a raw object, not a read-back

Give `build()` a new optional parameter, following the exact shape of
`extra_checks: dict[str, CheckFactory]` and `finishers`:

```python
# ports/issue_import.py
IssueImportFactory = Callable[..., IssueImport]
# invoked once, by build(), as:
#   factory(inbox=inbox, step_queues=step_queues, done=done, failed=failed,
#           healed=healed_queue, archived=archived, events=events, clock=clock)
```

`cli._run` builds the factory (closing over the `github_client`/`registry` it
already constructs there for `_process_check_factories`/`_github_sources` —
no second client, per the plan's original intent) and passes it into `build()`:

```python
# cli.py
def _issue_import_factory(args, root, registry, client):
    if client is None:
        return None
    workflow, step = args.github_workflow, args.github_step
    if workflow is None and step is None:
        workflow = DEFAULT_WORKFLOW
    worktree_root = args.worktree_root or str(root / "worktrees")

    def factory(*, inbox, step_queues, done, failed, healed, archived, events, clock):
        return GithubIssueImportService(
            client=client, registry=registry,
            inbox=inbox, step_queues=step_queues, done=done, failed=failed,
            healed=healed, archived=archived, events=events, clock=clock,
            workflow=workflow, step=step, worktree_root=worktree_root,
        )
    return factory
```

```python
# app.py, inside build(), right after inbox/step_queues/done/failed/healed_queue/
# archived exist (same spot FailedTasksCheck's factory closes over them, ~line 626)
# and after `events` has been wrapped in CompositeEventSink (~line 422) — the
# factory's `events` must be the composed sink, not the raw one, or the emitted
# "ingested" event never reaches the projection/board.
issue_import = (
    issue_import_factory(
        inbox=inbox, step_queues=step_queues, done=done, failed=failed,
        healed=healed_queue, archived=archived, events=events, clock=clock,
    )
    if issue_import_factory is not None
    else NullIssueImport()
)
```

`Harness` gains one new constructor field and public attribute:
`self.issue_import = issue_import` — same footing as `self.control` and
`self.process_checks`, both of which exist for the same reason (something
needs to be handed to `serve()`/`create_app` that only `build()` could
construct).

`serve()` (still in `cli.py`) then does `create_app(..., issue_import=
harness.issue_import)` — never `None`, mirroring how `harness.control` is
always a real `TaskControlService`, never optional.

This keeps every existing boundary intact: `app.py` never imports anything
GitHub-specific (the factory's *body* lives in `cli.py`; `build()` only calls
a `Callable` it was handed, exactly like `finisher_registry[binding.kind](...)`
already does for finishers); `GithubIssueImportService` — a driver — is still
only ever *constructed* from `app.py` or `cli.py`, satisfying
`test_only_app_and_cli_wire_drivers` unchanged; `api/` still only sees the
`IssueImport` port, satisfying `test_api_does_not_import_drivers`.

### `NullIssueImport` — one class, not two

Both `build()`'s own default (no `GITHUB_TOKEN`) and `api/app.py::create_app`'s
default parameter (the existing `_NullTaskControl`/`_EmptyArtifactView`/
`_EmptyAgentAdmin` idiom, for callers — chiefly tests — that construct the app
without a full harness) need the identical "not configured, clear error"
behavior. Rather than duplicate it, define it once in `ports/issue_import.py`
as a concrete `NullIssueImport(IssueImport)`, alongside the ABC. There's
already a precedent for a port module hosting a trivial, dependency-free
concrete class next to its ABC: `Trigger(TaskSource)` in `ports/source.py`.
Both `build()` and `create_app` import it from the port — no driver import in
either direction, no duplicated logic.

```python
class NullIssueImport(IssueImport):
    def add(self, ref: str) -> IssueImportResult:
        return IssueImportResult(
            ref=ref, ok=False,
            error="GitHub is not configured on this harness (no GITHUB_TOKEN)",
        )
```

## 2. Where this port sits in the invariant taxonomy

The design doc hedges between two shapes: "mirrors `TaskControl`'s shape
exactly" (its UI-facing talking point) versus "mirrors `AgentAdmin`/
`WorkflowAdmin`/`ProcessAdmin`'s footing" (its wiring talking point, invariant
#33). Given section 1's resolution, `IssueImport` is neither, cleanly — it's
closer to a third, already-precedented shape:

- **Not `TaskControl`'s shape**: `TaskControlService` is generic core logic
  (`task_control.py`, imports only ports) built unconditionally by `build()`
  from nothing but queues — no external system. `IssueImport`'s only
  implementation is inherently GitHub-specific; there is no queue-driver-
  agnostic "core" logic to extract.
- **Not `AgentAdmin`'s shape**: the admin ports' drivers (`FilesystemAgentAdmin`
  et al.) are wired *exclusively in `cli.py`'s `serve()`*, entirely outside
  `build()`, because they need nothing `build()` constructs. `IssueImport`
  can't be wired that way — it needs the live queues, which don't exist until
  `build()` runs.
- **Matches `FailedTasksCheck`'s shape** (ADR-0018): built inside `build()`
  because of the live-queue dependency, exposed as a public `Harness`
  attribute for `serve()` to thread onward — with the one addition that its
  GitHub-specific ingredients arrive as a factory built in `cli.py`, exactly
  like `extra_checks`.

Recommend adding one invariant to `CLAUDE.md` documenting this shape
explicitly (a natural #43), so the next feature needing both "external system"
and "live queue" dependencies doesn't re-litigate this:

> **A component needing both a live harness queue and an external-system
> dependency is built inside `build()` from a factory `cli.py` supplies** —
> neither wired standalone in `cli.py`'s `serve()` (that shape needs no queue)
> nor built directly by name inside `build()` (that shape needs no external
> client). `IssueImport`/`GithubIssueImportService` follows this now,
> alongside `FailedTasksCheck`'s no-external-dependency variant of the same
> idea. `IssueImport` is touched only by `api/routes.py`'s
> `POST /issues/import` handler and `build()`'s own wiring — `dispatcher.py`/
> `consumer.py` don't import it; guard it in `test_architecture.py` the same
> way invariant #23/#32/#34 guard `TaskControl`/`MergeChecker`/`IssueChecker`.

## 3. Everything else in the plan/design — confirmed against the actual code

- **`GithubClient.get_issue`**: the 404→`None` idiom to extend
  (`get_issue_state`, `github_client.py:353-`) is exactly as described; adding
  a sibling method is mechanical. No issue.
- **Ref parsing / `github_slug`**: `github_slug(path) -> str | None`
  (`drivers/git_remote.py:45`) and the injectable `slug_of` pattern are used
  identically by `GithubConflictsCheck`, `GithubIssuesCheck` and `GithubForge`
  — `GithubIssueImportService` resolving `registry.names()` →
  `github_slug(registry.resolve(name))` to match a pasted slug is consistent,
  no new pattern introduced.
- **`Task` construction and `data.source` shape**: matches
  `GithubTaskSource.poll()` (`github_source.py:150-176`) field for field —
  `id`, `workflow_template`, `step`, `created`, `repository`, `worktree`,
  `dedup_key`, `data.source.{kind,repo,issue,url}`. Confirmed against
  `models.Task`'s actual dataclass fields (`models.py:100-122`); no new field
  needed.
- **Dedup key reuse**: `dedup_key("github", slug, number)` from
  `ports/source.py:39` is exactly what `GithubTaskSource`/`GithubIssuesCheck`
  already stamp — sharing it is correct and is what makes downstream
  consumers (dispatcher, `GithubLabelReflector`, `IssueReconciler`,
  `MergeReconciler`) need zero changes, as both documents claim.
- **FR-4's dedup scan set — widen it slightly.** `SourcePoller._seed_pollers()`
  (`app.py:192-209`) and `IssueReconciler`'s sweep both only cover
  `inbox`/`step_queues`/`done`/`failed` — not `healed`/`archived`. The plan's
  FR-4 already says to scan all six queues the constructor is given; keep that
  as the deliberate, wider choice (a pasted ref for an issue that's already
  *resolved* — archived or healed — should still say "already queued," not
  spawn a confusing second task), rather than narrowing it to match the
  poller's four. Worth a one-line note in the driver's docstring explaining
  why it's a superset of `IssueReconciler.queues`, so a future reader doesn't
  "fix" it down to four.
- **`"ingested"` event reuse** (`queue=TODO_COLUMN, task=task.to_dict()`,
  invariant #7): correct and required — this is what makes the board's
  existing SSE path pick up the new task with zero client-side changes, as
  confirmed by `board.html`'s `sse:board` → `/fragment/board` wiring
  (`board.html:16-25`).
- **UI**: `page-header__action` is a real, existing class (confirmed in
  `api/templates/admin/agents_list.html:16-18`, the "+ New agent" button) —
  the design's wireframe is not inventing a new visual idiom. The `<dialog>`
  open/close/keyboard idiom mirrors `#detail`'s existing script block
  (`board.html`) faithfully. No concerns.
- **Route**: `POST /issues/import` as a new handler inside `build_html_router`,
  taking `issue_import: IssueImport` as a parameter alongside the existing
  `control`/`agent_admin`/etc. — same signature shape already used throughout
  `api/routes.py`. Sequential per-ref processing for FR-4's same-batch dedup
  is correct and cheap at pasted-batch scale.

## Prerequisites before implementation starts

1. `ports/issue_import.py`: `IssueImportResult`, `IssueImport` (ABC),
   `NullIssueImport`, `IssueImportFactory` type alias.
2. `app.py`: `build()` gains `issue_import_factory: IssueImportFactory | None =
   None`; constructs `issue_import` right after the queue block (after
   `events` is wrapped in `CompositeEventSink`, alongside where the
   `FailedTasksCheck` factory closes over the same queues); `Harness` gains
   the `issue_import` field/attribute.
3. `cli.py`: `_issue_import_factory` helper (mirrors `_process_check_factories`'
   shape: built from `args`/`root`/`registry`/`client`, returns `None` when no
   token); `_run` passes it into `build(...)`; `serve()` threads
   `harness.issue_import` into `create_app`.
4. `drivers/github_issue_import.py`: `GithubIssueImportService` — this part of
   the plan's FR-3 (parse → resolve repo → `get_issue` → dedup-scan → build
   `Task` → best-effort claim label → `inbox.put` + `events.emit("ingested",
   ...)`) is unchanged by this assessment; only *who constructs it and when*
   changes.
5. `api/app.py` / `api/routes.py` / `board.html`: unchanged from the plan/
   design, modulo `issue_import` now always being a concrete, non-`None`
   `Harness` attribute by the time `serve()` builds `create_app`.

## Risks

- **Ordering bug inside `build()`**: if the factory is invoked before `events`
  is wrapped in `CompositeEventSink`, the emitted `"ingested"` event never
  reaches the projection and the new task is invisible on the board until the
  next unrelated event triggers a refresh. Guard with a test that submits via
  `IssueImport.add()` and asserts the task shows up in `BoardProjection`
  without any other event firing first.
- **`worktree_root` mismatch**: `_github_sources` derives
  `args.worktree_root or str(root / "worktrees")`, a value `build()` doesn't
  otherwise see (it derives its own paths from `layout`). The factory must
  close over the same computation `cli.py` already does for
  `GithubTaskSource`, not silently default to `layout.worktrees`, or a task
  created via manual import could get a worktree path inconsistent with one
  created via automatic ingestion when `--worktree-root` is overridden.
- **Test coverage for the factory seam itself**: add one test in
  `tests/test_app.py` (or wherever `build()` is unit-tested) asserting that
  `build(..., issue_import_factory=None)` yields `harness.issue_import` as a
  `NullIssueImport`, and that a supplied factory receives the exact queues
  `build()` constructed (not fresh ones) — this is the seam most likely to
  silently drift if `build()`'s internals are refactored later.

```json
{"outcome": "done", "summary": "Wrote architecture-01.md: confirmed the IssueImport port, GithubClient.get_issue, Task/data.source/dedup_key reuse and UI plan are sound, but corrected the plan/design's wiring — GithubIssueImportService cannot be built in cli.py reading back harness.inbox et al. (those are private and, per ADR-0018's FailedTasksCheck precedent, anything needing build()'s live queues must be constructed inside build() itself). Specified the fix: a cli.py-built IssueImportFactory closure (mirroring extra_checks/finishers) that build() invokes once its queues exist, exposing harness.issue_import publicly like harness.control/process_checks — keeping app.py free of any GitHub-specific import. Proposed a NullIssueImport shared between build()'s default and create_app's, and a candidate invariant #43 documenting this 'live queue + external dependency' component shape."}
```
