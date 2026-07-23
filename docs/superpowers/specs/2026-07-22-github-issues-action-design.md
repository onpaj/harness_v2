# The `github-issues` action — the harness:todo trigger as a Process

Status: approved
Date: 2026-07-22

## Goal

Make the hardcoded **`harness:todo` development trigger authorable as a
`processes/*.json`**. Today the trigger is not data: it is wired by CLI defaults
(`harness run --github-label harness:todo`), which build a `GithubTaskSource`
per repo in `repos.json`. The Process aggregate (ADR-0015) already turns
*scheduled* triggers into data, but v1 ships only the `always` and
`disk-threshold` checks — the **`github-issues` action was a declared seam, not
built**. This increment builds that seam so the operator can write:

```json
{
  "name": "harness-todo",
  "trigger": { "interval": "30s" },
  "action":  { "check": "github-issues", "params": { "label": "harness:todo" } },
  "target":  { "workflow": "default" },
  "dedup":   "per-state",
  "sink":    { "kind": "none" }
}
```

and have `harness run` ingest GitHub issues into the `default` workflow exactly
as the hardcoded source does today.

## Scope

**In scope:**

- A `GithubIssuesCheck` (`Check`) that scans issues by label across the repo
  registry, performs the claim label-swap, and returns one `Observation` per
  issue with full `data.source` provenance.
- Registering that check into the process build as `github-issues`, by closing
  a `GithubClient` + the repo registry into a `CheckFactory` at wiring time
  (the ADR-0015 "dependency bag", realised without widening `CheckFactory`).
- A new `Observation.repository` field so a single multi-repo process stamps each
  task with its own repository, honoured in `ScheduledTrigger._task_for`.
- A `--no-github-source` run flag that skips the built-in `_github_sources`, so a
  process owns ingestion with no double-claim.
- The `~/harness-root/processes/harness-todo.json` file and the
  `harness-run.sh` change that turns the built-in source off.

**Out of scope (explicitly, so gaps aren't mistaken for oversights):**

- **Outbound reflection (the sink).** A Process's `sink` is `none` in v1, so the
  process does *ingestion only*. The progress/outcome labels the old source wrote
  (`harness:pr-open`, `harness:failed`, step labels) are **dropped** — an accepted
  regression (operator decision). The one label the *action* still writes is the
  claim swap `harness:todo → harness:queued`, because that is the check's
  at-most-once mechanism, not reflection. Closed-issue retirement is unaffected:
  the independent `GithubIssueChecker.is_open` reconciler reads `task.data.source`,
  which the check stamps identically.
- **The `ProcessAdmin` UI knowing about `github-issues`.** `BUILTIN_CHECKS` stays
  client-free, so the admin's `check_names()` will not list `github-issues` and
  `FilesystemProcessAdmin.write` will reject it (its validation uses the default
  registry with no client). Viewing (`read`) still works. Teaching the admin the
  check needs a client at admin-construction time — a clean follow-up. The
  `harness-todo.json` is hand-authored on disk; it loads and runs correctly at
  `harness run`, which passes the extended check set.
- Migrating `GithubTaskSource` away. With `--no-github-source` it simply is not
  built; the class stays for anyone not opting in (default behaviour unchanged).

## The current behaviour we reproduce

`GithubTaskSource.poll()` (per repo):

1. `list_issues(repo, label="harness:todo")`.
2. For each new issue: `remove_label(todo)`, `add_label(queued)` — the claim.
3. Emit a `Task`: `workflow_template="default"`, `repository=<registry name>`,
   `worktree=<worktree_root>/<task_id>`, `dedup_key=github:<repo>:<number>`,
   `data={title, body, source:{kind:"github", repo, issue, url}}`.
4. An in-process `_claimed` ledger guards `list_issues` read-after-write lag.

`_github_sources` builds one such source per `repos.json` entry with a GitHub
origin (slug from the clone's git origin; `repository` is the registry name).

## Design

### 1. `GithubIssuesCheck` — `src/harness/drivers/github_issues_check.py`

A new module (the names `github_issues.py` = self-heal `GithubIssueTracker` and
`github_issue_checker.py` = `is_open` reconciler are both taken; this is a third,
distinct concern — the inbound label scan as a `Check`). It imports
`github_slug` from `drivers/git_remote` (a driver → driver import, allowed) and
uses the registry's `.names()` / `.resolve()` — no import from `cli`, so
`test_architecture.py` stays green.

```python
class GithubIssuesCheck(Check):
    def __init__(self, *, client, registry, slug_of=github_slug,
                 label="harness:todo", claimed_label="harness:queued"): ...
    def evaluate(self) -> list[Observation]:
        # for each registry repo with a github origin:
        #   for each issue in client.list_issues(slug, label=self._label):
        #     skip if (slug, number) in self._claimed  (read-after-write lag guard)
        #     self._claimed.add((slug, number))
        #     client.remove_label(slug, number, self._label)     # claim...
        #     client.add_label(slug, number, self._claimed_label) # ...swap
        #     yield Observation(
        #       state_key=f"{slug}:{number}",
        #       repository=<registry name>,
        #       data={"title": ..., "body": ...,
        #             "source": {"kind": "github", "repo": slug,
        #                        "issue": number, "url": url}})
```

- Mirrors `poll()` precisely — same claim swap, same `_claimed` ledger semantics,
  same `data.source` shape (so `GithubIssueChecker.is_open` and any future
  reflector recognise these tasks).
- Iterates the registry itself (one process covers every repo), replacing
  `_github_sources`'s per-repo loop. A repo without a GitHub origin is skipped.
- `state_key = "<slug>:<number>"` drives `per-state` dedup: one task per issue,
  re-fireable only if that issue is re-observed after the seen-set forgets it.

### 2. Registering the check via a closed-over factory — `cli.py` `_process_sources`

`CheckFactory = Callable[[dict], Check]` is unchanged. The client dependency is
injected by *closure*, at wiring time:

```python
def _process_sources(args, root, registry, *, clock, known_targets, client=None):
    client = client or (HttpGithubClient(t) if (t := os.environ.get("GITHUB_TOKEN")) else None)
    def github_issues_factory(params):
        if client is None:
            raise ProcessValidationError(
                "github-issues action requires GITHUB_TOKEN", field="check")
        return GithubIssuesCheck(client=client, registry=registry,
                                 label=params.get("label", args.github_label))
    checks = {**BUILTIN_CHECKS, "github-issues": github_issues_factory}
    repo = FilesystemProcessRepository(root / "processes")
    return repo.build(clock=clock, repository=None,
                      worktree_root=args.worktree_root or str(root / "worktrees"),
                      known_targets=known_targets, checks=checks)
```

- No token and a `github-issues` process present → `build()` fails fast with a
  `ProcessValidationError` naming the file and the missing token (fail-fast is
  right: the process was explicitly authored). A run with no `github-issues`
  process is unaffected.
- The `always`/`disk-threshold` factories in `BUILTIN_CHECKS` are untouched.

### 3. `Observation.repository` — `ports/triggers.py` + `scheduled_trigger.py`

`ScheduledTrigger` was built for repo-less scheduled work (`repository=None`).
GitHub ingestion is repo-bearing and multi-repo. Rather than teach the trigger
anything GitHub-specific, add a generic field:

```python
@dataclass(frozen=True)
class Observation:
    state_key: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    repository: str | None = None   # the repo this observation's task belongs to
```

`ScheduledTrigger._task_for`: `repository=obs.repository or self._repository`.
Worktree and workflow still come from the trigger. This is the single core-ish
change; it is behaviour-preserving for every existing check (they leave
`repository=None`, so the trigger's own `repository` still wins).

### 4. `--no-github-source` — `cli.py` argument + `_run`

```python
run.add_argument("--no-github-source", action="store_true", dest="no_github_source",
                 help="skip the built-in GithubTaskSource ingestion (use when a "
                      "github-issues process owns it) — avoids double-claiming")
```

`_run` line ~1366:
```python
github = [] if args.no_github_source else _github_sources(args, root, registry)
sources = github + mergeability
```

Default off → default behaviour byte-for-byte unchanged. Set it once the process
exists.

### 5. The runtime files (`~/harness-root`, not the repo)

- `processes/harness-todo.json` — the definition above (`interval "30s"` matches
  today's `--source-poll 30`).
- `harness-run.sh` — append `--no-github-source` to the `exec harness run …` line.
  (The file is regenerated by `harness service install`; the durable form is a
  `service install` flag, but a direct edit is the immediate step — noted in the
  plan.)

## Data flow (unchanged downstream)

```
ScheduledTrigger(github-issues).poll()  [clock gate, 30s]
  -> GithubIssuesCheck.evaluate()  -> [Observation(repository, state_key, data.source)]
  -> Task(workflow="default", repository=obs.repository, data={**obs.data})
  -> SourcePoller (_seen dedup on dedup_key)  -> inbox
  -> dispatcher/consumer/router  (never learn "process")
  -> GithubIssueChecker.is_open reconciler retires it if the issue closes
```

## Error states

| Situation | Detection | Result |
|---|---|---|
| `github-issues` process, no `GITHUB_TOKEN` | factory raises at `build()` | fail fast at `harness run` start, message names file + token |
| `list_issues` / label API transient failure | exception out of `evaluate()` | `SourcePoller.tick` catches, emits `source_error`, retries next interval (identical to `poll()` today) |
| target workflow `default` unserved this run | `known_targets` at build | `ProcessValidationError` naming the file |
| repo in registry has no GitHub origin | `slug_of` returns `None` | skipped (warning), same as `_github_sources` |

## Testing

- `test_github_issues_check.py` (new): with a `FakeGithubClient` + a fake
  registry — emits one observation per labelled issue with correct
  `repository`/`state_key`/`data.source`; performs the todo→queued swap; the
  `_claimed` ledger suppresses a re-listed issue within a tick; a repo with no
  origin is skipped; multiple repos are all scanned.
- `test_scheduled_trigger.py` (extend): an observation carrying `repository`
  produces a task with that repository; absent → the trigger's `repository`.
- `test_triggers_port.py` (extend): `Observation.repository` defaults to `None`.
- `test_fs_processes.py` / a new `_process_sources` test: a `github-issues`
  process compiles with an injected fake client; fails fast without a client.
- `test_processes_e2e.py` (extend) or a new e2e: a `harness-todo`-shaped process
  driven by a `FakeClock` + `FakeGithubClient` ingests a labelled issue once per
  bucket into the `default` workflow, task carries `data.source`. No real sleep,
  no network.
- `test_architecture.py` must still pass: `github_issues_check.py` is a driver
  imported only by `cli.py` wiring; `scheduled_trigger.py` still imports only
  ports/models/ids; orchestration never imports it.
- Full suite green (baseline: 87 passed across the touched areas; whole suite
  green before merge).

## Invariants

No new invariant. This *fulfils* the seam invariant #39 already records (a
Process compiles to a `ScheduledTrigger`; orchestration never learns "process")
by adding the deferred `github-issues` action. `Observation.repository` is a
generic extension of the existing "an observation shapes its task's data" rule.
Extend the CLAUDE.md note on the action seam to say it is now built for the
inbound half; the sink half (outbound reflection) remains the open seam
(invariant #40).

## Completion check

1. A `processes/harness-todo.json` (`github-issues` / `label harness:todo` /
   `target default` / `per-state`) → `harness run` (with a token) loads it with
   no "unknown check" error and ingests a labelled issue into `default`, the task
   carrying `data.source={kind:github,...}` and `repository=<registry name>`.
2. `--no-github-source` suppresses `_github_sources`; a single labelled issue
   yields exactly one task (no double-claim).
3. Without a token, a run with that process fails fast, message naming the file.
4. Default runs (no `github-issues` process, flag off) behave exactly as before.
5. Full test suite green; architecture tests unchanged.
