# Design: continuously reflect task state onto the source GitHub issue's labels

No UI surface — this is a driver-only fix behind the existing `TaskSource` port
(ADR-0010) and the existing `sources` composition in `cli.py`. The `api/` board
and its projection are untouched (invariant #5), so there is no wireframe/
component-hierarchy section.

## 1. Grounding (read from `origin/main`, not this branch)

`plan-01.md` already establishes that this worktree is 57 commits behind
`origin/main`, and that every file this design names — `ports/source.py`,
`drivers/github_source.py`, `drivers/source_reflector.py`,
`drivers/github_issues_check.py`, `drivers/fs_processes.py`, `cli.py`'s source
wiring, ADR-0010/0014/0015, invariants #18–#21/#35–#40 — exists only there.
Development's first step is the merge; this design is written against the code
as it stands on `origin/main` (re-confirmed by `git show origin/main:<path>`
while authoring this document), not against this branch's history.

**Root cause, confirmed by reading the actual classes:**

- `SourceReflectorSink.emit()` (`drivers/source_reflector.py`) fans
  `report_progress`/`finish` out to **every** `TaskSource` in the `sources`
  list on every `dispatched`/`finished`/`failed` event; each source's own
  `_mine(task)` guard (`task.data.source.kind`/`repo` match) decides whether it
  *acts*. Routing is per-task-data, not per-originating-producer.
- `GithubTaskSource` (`drivers/github_source.py`) is the **only** class that
  knows how to turn a `Progress`/`FinishResult` into a label change. It is
  registered per repo in `cli.py._run` only when `--no-github-source` is
  **absent** (`github = [] if args.no_github_source else _github_sources(...)`).
- `GithubIssuesCheck` (the Process `github-issues` action, run when
  `--no-github-source` is passed so the two claimers don't race on the same
  `harness:todo` label) stamps `data.source = {"kind": "github", "repo": ...,
  "issue": ..., "url": ...}` on every task it creates — **identical in shape**
  to what `GithubTaskSource.poll()` stamps.
- The `ScheduledTrigger` a Process compiles to (`drivers/scheduled_trigger.py`)
  is a `Trigger` (`ports/source.py`): `report_progress`/`finish` are inherited
  no-ops (invariant #36) — it was never meant to reflect.
- So: with `--no-github-source`, `data.source` is stamped correctly, but no
  registered `TaskSource` in the fan-out has a matching `_mine()` that also
  knows how to write a label — the gap is a **missing registration**, not a
  routing bug, a schema gap, or something `sink`/`data.sink` needs to solve.

**Why this needs no `sink`/schema change.** ADR-0015 already anticipates
exactly this shape: "a real sink... routes through `SourceReflectorSink` on a
destination identity... that **defaults to `source.kind`**, so same-origin
processes need declare nothing." GitHub-in → GitHub-out is precisely the
same-origin case. Invariant #40 stays true unchanged — a Process's `sink`
stays `none`/absent, and the `ScheduledTrigger` a Process compiles to still
reflects nothing itself. Reflection instead comes from a **second, independent
`TaskSource`** registered into the same `sources` list, matched purely by
`task.data.source` — the same mechanism that already makes `GithubTaskSource`
reflect for a task it didn't itself create. No `Reflector` port, no
`data.sink` write, no widening of `_ACCEPTED_SINK_KINDS` in `fs_processes.py`.

## 2. Component design

### 2.1 New component: `GithubLabelReflector`

**Location:** `src/harness/drivers/github_source.py` (same file as
`GithubTaskSource` — both are "GitHub + labels", and `GithubTaskSource` will
compose this class directly; see §2.2).

**Responsibility:** the *entire* "state → label" mapping for a single
`(GithubClient, repo)` pair, and nothing else. It has no ingestion
responsibility — it never lists issues, never creates a `Task`, never claims
anything.

```python
class GithubLabelReflector(Trigger):
    """Reflects a task's progress/outcome onto its source GitHub issue's
    labels. Pure outbound half of the GitHub round-trip — `poll()` never
    produces a task, so it can be registered alongside any inbound producer
    (GithubTaskSource, GithubIssuesCheck, or neither) without double-claiming
    anything. Matches a task purely by `task.data.source` (kind + repo), the
    same guard GithubTaskSource already uses — it doesn't care who created the
    task, only where it's headed."""

    kind = "github"

    def __init__(
        self,
        *,
        client: GithubClient,
        repo: str,
        claimed_label: str = "harness:queued",
        pr_label: str = "harness:pr-open",
        failed_label: str = "harness:failed",
        step_labels: dict[str, str] | None = None,
    ) -> None: ...

    def poll(self) -> list[Task]:
        return []

    def report_progress(self, task: Task, progress: Progress) -> None: ...
    def finish(self, task: Task, result: FinishResult) -> None: ...

    def _set_state(self, number: int, target: str) -> None: ...  # remove _managed - {target}, add target
    def _mine(self, task: Task) -> bool: ...                     # source.kind == "github" and source.repo == self._repo
    def _issue(self, task: Task) -> int: ...
```

It subclasses `Trigger` (not `TaskSource` directly) purely for `poll()`'s free
no-op — `Trigger.poll()` is still abstract, so `GithubLabelReflector` must
still supply its own `poll() -> []`; the benefit is documenting *why* it's
`[]` (compare invariant #36's `ScheduledTrigger`) rather than saving a line.

Field-for-field, this is the label half of `GithubTaskSource` today, with the
ingestion-only knobs (`clock`, `workflow`, `step`, `repository`,
`worktree_root`, `select_label`) dropped — they don't exist without `poll()`
doing real work.

### 2.2 Changed component: `GithubTaskSource`

Refactored to **compose** a `GithubLabelReflector` internally rather than
duplicate its label logic (`_set_state`/`_mine`/`_managed`/
`report_progress`/`finish`):

```python
class GithubTaskSource(TaskSource):
    kind = "github"

    def __init__(self, *, client, clock, repo, workflow=None, step=None,
                 repository, worktree_root, select_label="harness:todo",
                 claimed_label="harness:queued", pr_label="harness:pr-open",
                 failed_label="harness:failed", step_labels=None) -> None:
        ...
        self._reflector = GithubLabelReflector(
            client=client, repo=repo, claimed_label=claimed_label,
            pr_label=pr_label, failed_label=failed_label,
            step_labels=step_labels,
        )

    def poll(self) -> list[Task]: ...          # unchanged — claims + builds Task

    def report_progress(self, task, progress) -> None:
        self._reflector.report_progress(task, progress)

    def finish(self, task, result) -> None:
        self._reflector.finish(task, result)
```

`GithubTaskSource`'s constructor signature, public behavior and
`tests/test_github_source.py` are unchanged — this is an internal
delegation, not a behavior change. Exactly one implementation of "how a state
maps to a label" exists after this change (FR-5 in the plan).

### 2.3 Changed component: `cli.py` wiring

New helper, mirroring `_github_sources`'s enumeration shape exactly (same
"no token → `[]`", "repo with no GitHub origin → skip with a warning already
emitted by `_github_sources`" pattern):

```python
def _github_reflectors(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    slug_of=github_slug,
    client: GithubClient | None = None,
) -> list[TaskSource]:
    """One GithubLabelReflector per repo in repos.json with a GitHub origin —
    the outbound half of GitHub reflection, registered whenever classic
    ingestion (GithubTaskSource) is NOT also registered for that repo, so
    exactly one reflecting source per repo ever exists (no doubled label
    calls)."""
    if client is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return []
        client = HttpGithubClient(token)

    sources: list[TaskSource] = []
    for name in registry.names():
        slug = slug_of(registry.resolve(name))
        if slug is None:
            continue  # already warned about by _github_sources for the same repo
        sources.append(
            GithubLabelReflector(
                client=client,
                repo=slug,
                step_labels=DEFAULT_STEP_LABELS,
            )
        )
    return sources
```

`_run`'s composition (today: `github = [] if args.no_github_source else
_github_sources(...); sources = github + mergeability`) becomes:

```python
github = [] if args.no_github_source else _github_sources(args, root, registry)
reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
sources = github + reflectors + mergeability
```

No new CLI flag: `--no-github-source` already means "ingestion is delegated
elsewhere" (to a Process's `github-issues` action), which is exactly the
configuration under which the gap exists. When `--no-github-source` is
**absent**, `GithubTaskSource` itself still reflects (via its composed
`GithubLabelReflector`, §2.2) — behavior is byte-for-byte unchanged from
today (FR-1/AC2 in the plan).

### 2.4 Unchanged components (confirmed, not modified)

- `SourceReflectorSink` — no change; it already fans out to every registered
  source and already isolates nothing per-source (that hardening is flagged
  as an out-of-scope open question in the plan, not part of this design).
- `ports/source.py` (`TaskSource`, `Trigger`, `Progress`, `FinishResult`) — no
  change; `GithubLabelReflector` fits the existing `Trigger` shape exactly.
- `fs_processes.py` (`_ACCEPTED_SINK_KINDS`, `_validate_sink`) — no change;
  `sink` stays `{"kind": "none"}`/absent-only. `GithubIssuesCheck` — no
  change; it still only ingests + claims.
- `dispatcher.py` / `consumer.py` / `router.py` — untouched; `test_
  architecture.py`'s existing guards for invariants #18–#20 (`TaskSource`
  touched only by `SourcePoller`/`SourceReflectorSink`, wired in `app.py`)
  keep passing with zero changes to their assertions, since the new class is
  wired the identical way an existing `TaskSource` is.

## 3. Data / schema

No new fields anywhere.

- `task.data.source` keeps its existing shape (`{kind, repo, issue, url}`),
  written identically by `GithubTaskSource.poll()` and
  `GithubIssuesCheck.evaluate()` today — `GithubLabelReflector._mine()` reads
  it exactly as `GithubTaskSource._mine()` does now.
- Process `sink` stays `{"kind": "none"}`/absent — unwidened, per invariant
  #40 and the plan's explicit scope boundary (a non-GitHub sink is a separate,
  later increment).
- Labels reflected (unchanged from `GithubTaskSource` today, via
  `DEFAULT_STEP_LABELS` in `cli.py`): `harness:queued` (claim, written by the
  inbound side — `GithubTaskSource.poll()` or `GithubIssuesCheck.evaluate()`,
  not by the reflector) → `harness:in-progress` / `harness:in-review` /
  `harness:landing` (steps present in `DEFAULT_STEP_LABELS`; a step absent
  from the map gets no label change — pre-existing "coarse default" behavior,
  not a gap introduced or fixed here) → `harness:pr-open` (success) /
  `harness:failed` (failure). `_set_state` keeps the "remove every managed
  label but the target, add the target" shape, so this sequence stays
  idempotent under a repeated `report_progress`/`finish` call for the same
  state (FR-2 / invariant #21).

## 4. Interfaces (call shape, for development's reference)

```
GithubLabelReflector(client, repo, claimed_label="harness:queued",
                      pr_label="harness:pr-open", failed_label="harness:failed",
                      step_labels=None) -> Trigger

  .poll() -> []
  .report_progress(task: Task, progress: Progress) -> None   # no-op if not _mine(task)
  .finish(task: Task, result: FinishResult) -> None           # no-op if not _mine(task)

_github_reflectors(args, root, registry, *, slug_of=github_slug,
                    client=None) -> list[TaskSource]
```

`cli.py._run`'s `sources` list gains, in the `--no-github-source` branch only,
one `GithubLabelReflector` per GitHub-origin repo in `repos.json` — same
enumeration `_github_sources` already performs, so coverage is all-or-nothing
across repos, with no partial-repo case.

## 5. Test surface implied by this design (for development, not prescriptive)

- `tests/test_github_source.py` — unmodified, must stay green (proves the
  `GithubTaskSource` refactor is behavior-preserving).
- A new test module (or an added section of `tests/test_github_source.py`)
  covering `GithubLabelReflector` directly: step-label transitions, `finish`
  ok/not-ok, idempotent double-call producing no net label change, foreign
  `kind`/foreign `repo`/no-`data.source` → zero `add_label`/`remove_label`
  calls, unknown step → no label change.
- `tests/test_cli.py` — `_github_reflectors` returns one reflector per
  GitHub-origin repo; `_run`'s composed `sources` contains reflectors iff
  `--no-github-source` is set, and never both a `GithubTaskSource` and a
  `GithubLabelReflector` for the same repo.
- `tests/test_processes_e2e.py` — extend with a `github-issues` process
  end-to-end: `FakeGithubClient` seeded with one `harness:todo` issue,
  `drive_until_quiet`, assert the label sequence `harness:todo` →
  `harness:queued` → (per-step labels as the task advances) →
  `harness:pr-open`/`harness:failed`.
