# github-conflicts action — design (2026-07-23)

## Problem

Resolving a conflicted PR is already a workflow (`resolver`: `resolve → land`)
with a working conflict-resolving agent (`ResolveConflictBehavior`). But the
*detection* that feeds it — "which open harness PRs are conflicted?" — is a
bespoke `TaskSource`, `GithubMergeabilityWatcher`, wired one-per-repo in
`cli._mergeability_sources`. It cannot be authored as a Process: the operator
can express `harness:todo` ingestion as `processes/*.json` (via the
`github-issues` check) but not conflict resolution.

This is the same shape `github-issues` already solved for issue ingestion. Do
the same for conflict detection: express it as a **`Check`**, so the resolver
becomes an ordinary Process.

## Scope

**In:** a `github-conflicts` action (a `Check`), its wiring registration, and a
`resolve-conflicts` process that targets the `resolver` workflow. Retire the
bespoke `GithubMergeabilityWatcher` wiring for *detection*.

**Out (deliberately deferred):**
- **Outbound reflection** (the old `harness:resolving` label). That belonged to
  the watcher's `report_progress`/`finish`; it is now the **sink** seam's job
  (ADR-0015/#40). No GitHub-label sink kind exists yet (only `slack`), so this
  iteration drops the label until a `github-label` sink lands. The action stays
  a pure detector — no outbound coupling.
- The `resolve → land` merge-loss bug (issue #86). Independent; not touched here.
- Retiring `MergeReconciler`/`PrWatcher` (the finisher/archival side).

## Design

### The check — `GithubConflictsCheck(Check)`

A direct sibling of `GithubIssuesCheck` (`drivers/github_conflicts_check.py`).
`evaluate()`:

1. For each repo `name` in `registry.names()`, resolve its GitHub `slug`
   (`github_slug(registry.resolve(name))`); skip non-GitHub repos.
2. `client.list_pull_requests(slug, head_prefix=…)` → `PullRequestInfo`
   (`number, url, head_branch, head_sha, base_branch, mergeable_state`).
3. Per PR, branch on `mergeable_state`:
   - `"behind"` → **side-effect, no task**: `client.update_branch(slug, number)`
     (in a per-PR try/except so one bad PR doesn't sink the tick), then continue.
     This preserves the watcher's auto-update behavior. Side-effects inside
     `evaluate()` are the established pattern (`GithubIssuesCheck` swaps a label).
   - `"dirty"` → emit one `Observation`:
     ```python
     Observation(
         state_key=f"{slug}:{number}:{head_sha}",   # per-head-SHA dedup
         repository=name,                            # logical repo → RepositoryRegistry
         data={
             "branch": head_branch,
             "title": f"resolve merge conflict on PR #{number}",
             "source": {"kind": "mergeability", "repo": slug,
                        "pr": number, "url": url, "base": base_branch},
         },
     )
     ```
   - anything else (`clean`/`blocked`/`unstable`/`unknown`) → skip (v1 scope).

The emitted task's `data` is exactly what the resolver back half already reads:
`ResolveConflictBehavior` reads `data.source.base`; `GitWorkspace.attach` reads
`data.branch`. No back-half change.

An in-process `_seen: set[str]` ledger on the check (keyed by
`f"{slug}:{number}:{head_sha}"`) guards against `list_pull_requests`
read-after-write lag re-emitting the same conflict within a process, mirroring
`GithubIssuesCheck._claimed`.

### Dedup

`dedup: "per-state"` in the process; `state_key = slug:number:head_sha`. One
resolve task per conflict-at-a-head. When the PR head advances, the key changes
and a fresh conflict re-queues. (The pathological "head never advances" case is
issue #86, out of scope here.)

### Wiring

In `cli._process_sources`, register alongside `github-issues`:

```python
def github_conflicts_factory(params: dict) -> GithubConflictsCheck:
    if client is None:
        raise ProcessValidationError(
            "github-conflicts action requires GITHUB_TOKEN", field="check")
    return GithubConflictsCheck(
        client=client, registry=registry,
        head_prefix=params.get("head_prefix", "harness/"),
    )

checks = {**BUILTIN_CHECKS,
          "github-issues": github_issues_factory,
          "github-conflicts": github_conflicts_factory}
```

`BUILTIN_CHECKS` stays client-free (guarded by `test_architecture.py`).

### The process — `processes/resolve-conflicts.json`

```json
{
  "name": "resolve-conflicts",
  "trigger": {"interval": "60s"},
  "action": {"check": "github-conflicts", "params": {"head_prefix": "harness/"}},
  "target": {"workflow": "resolver"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

Seeded by `harness init` next to `harness-todo.json` (guarded by `exists()`).

### Finisher-as-data / sinks (accounted for, not changed)

- The `resolver` workflow's `land` step resolves through the finisher registry;
  with no explicit `finishers` binding it defaults to `"open-pr"` (ADR-0016) —
  backward compatible, no change.
- Outbound reflection is the sink seam's responsibility now; this action emits
  none, leaving the `harness:resolving` reflection to a future `github-label`
  sink binding on the process. Clean separation, no bespoke coupling reintroduced.

### Retirement

Remove `GithubMergeabilityWatcher` from the *detection* wiring
(`cli._mergeability_sources`) once the process is seeded, so conflict detection
has exactly one path. (The class + its finisher-side companions can be deleted
in a later pass together with the sink follow-up; this iteration only stops
wiring it as a detection source, to avoid double-minting.)

## Testing

- `test_github_conflicts_check.py` — unit, with a `FakeGithubClient`:
  emits one observation per `dirty` PR with the right `state_key`/`data`;
  `behind` PRs trigger `update_branch` and emit nothing; `clean`/other skipped;
  a per-PR `update_branch` failure doesn't drop the rest of the tick; the
  `_seen` ledger suppresses a repeat within the process.
- `test_fs_processes.py` — a `resolve-conflicts`-shaped process compiles to a
  `ScheduledTrigger` targeting the `resolver` workflow with `per-state` dedup.
- `test_cli.py` — `github-conflicts` registered; missing-`GITHUB_TOKEN` process
  fails fast at build with `ProcessValidationError`.
- Full suite green (`.venv/bin/pytest -q`).
