# Multi-repo GitHub source — design

## Problem

The harness ingests GitHub issues from a **single** repository. The `run`
command takes one `--github-repo owner/name` slug and one `--github-repository`
name, and `cli._github_source` builds exactly one `GithubTaskSource`. Adding a
second repo has no path today.

This is a CLI limitation, not an architectural one. Everything below the CLI is
already multi-repo:

- `build()` takes `sources: list[TaskSource]` and creates one `SourcePoller`
  per source; all pollers feed the one inbox.
- `SourceReflectorSink(sources)` takes a list and reflects each event to every
  source, relying on each source's `_mine()` guard to ignore foreign tasks.
- `repos.json` already maps *multiple* names → paths, and
  `RepositoryRegistry.resolve()` resolves a repo path **per task**, so a single
  harness already drives worktrees across many repos (invariant #15).

## Goal

`harness run` scans **all** repositories listed in `repos.json` for issues
labeled `harness:todo`, polling each on the `--source-poll` interval. Adding a
repo to the harness means adding it to `repos.json` — nothing else to configure.

## Key decision: where the GitHub slug comes from

`repos.json` maps `name → local_path`; it holds no GitHub `owner/repo` slug,
which is a third identifier distinct from both the repos.json name (`heblo`) and
the path. The slug is **derived from each clone's git `origin` remote** — so
`repos.json` stays exactly as it is (`{name: path}`) and remains the single
source of truth. A repo whose origin is not a GitHub URL is skipped with a
warning.

Rejected alternatives: extending the `repos.json` schema to carry the slug (a
change rippling through the registry, `harness init`, and docs, duplicating a
fact git already knows); a separate `github.json` mapping (a second config file
to keep in sync — YAGNI).

## Components

### 1. `drivers/git_remote.py` (new)

A focused module split into a pure parser and a thin git-runner so the parser is
unit-testable without a subprocess:

```python
def parse_github_slug(remote_url: str) -> str | None
def github_slug(path: Path) -> str | None
```

- `parse_github_slug` maps a remote URL to `"owner/repo"`, or `None` when the
  host is not `github.com`. Handles both remote forms and a trailing `.git`:
  - `git@github.com:onpaj/Anela.Heblo.git` → `onpaj/Anela.Heblo`
  - `https://github.com/onpaj/Anela.Heblo.git` → `onpaj/Anela.Heblo`
  - `git@gitlab.com:foo/bar.git` → `None`
- `github_slug` runs `git -C <path> config --get remote.origin.url`, then
  parses the result. On any failure (not a git repo, no `origin`, git missing)
  it returns `None` rather than raising — a repo we can't classify is simply not
  scanned.

This is a driver (git + GitHub knowledge). It is imported by `cli.py` (edges),
never by the core.

### 2. `RepositoryRegistry.names()` (port + both drivers)

The registry gains enumeration so wiring can iterate the config:

```python
@abstractmethod
def names(self) -> list[str]: ...
```

- `FilesystemRepositoryRegistry.names()` reads the JSON object's keys (reusing
  the same parse/validation path as `resolve`; a missing or broken config
  yields an empty list rather than raising, so a first run without repos.json
  just scans nothing).
- `MemoryRepositoryRegistry.names()` returns its dict keys.

The registry is still touched only by wiring/behavior (invariant #17); the
dispatcher and consumer do not see it.

### 3. `cli._github_sources` (replaces `_github_source`)

```python
def _github_sources(
    args, root, registry, *, slug_of=github_slug
) -> list[TaskSource]:
```

Behavior:

- No `GITHUB_TOKEN` in the environment → return `[]` and the harness runs on
  `harness submit` alone. With the `--github-repo` flag gone there is no longer
  an intent signal to warn against, so this is silent (the operator opts in by
  exporting the token, as `.conductor/settings.toml` does via `gh auth token`).
- For each `name` in `registry.names()`: resolve the path, call
  `slug_of(path)`. If it returns a slug, build one `GithubTaskSource(
  repo=slug, repository=name, worktree_root=..., select_label=args.github_label,
  workflow=args.github_workflow, step_labels=DEFAULT_STEP_LABELS, ...)`.
  Otherwise print `warning: <name> has no GitHub origin, not scanned` and skip.
- The slug-deriver is injected (`slug_of`, default `github_slug`) so tests build
  sources without shelling out to git.
- `worktree_root` is shared across all repos: every task gets
  `<worktree_root>/<task_id>` and `task_id` is globally unique, so there is no
  cross-repo collision.

Removed CLI flags: `--github-repo`, `--github-repository`. Kept and shared
across all scanned repos: `--github-label` (default `harness:todo`),
`--github-workflow`, `--worktree-root`, `--source-poll`.

`_run` already constructs `registry = FilesystemRepositoryRegistry(layout.repos)`
and passes `sources=` into `build()`; it now passes the list from
`_github_sources`.

### 4. `GithubTaskSource._mine` — repo-scoping fix

`SourceReflectorSink` reflects every event to every source and relies on
`_mine()` to filter. With N sources all of `kind="github"`, the current
kind-only guard makes every source claim every github task and write labels
using its own `self._repo` — cross-writing between repos. The fix scopes the
guard to the source's own repo:

```python
def _mine(self, task: Task) -> bool:
    src = task.data.get("source", {})
    return src.get("kind") == self.kind and src.get("repo") == self._repo
```

Each source's in-process `_claimed` ledger is already per-instance, so
ingestion is isolated without further change.

### 5. `.conductor/settings.toml`

The run command drops `--github-repo`/`--github-repository`:

```
GITHUB_TOKEN="$(gh auth token)" .venv/bin/harness run \
  --root "$CONDUCTOR_WORKSPACE_PATH/.harness"
```

All repos in `repos.json` (currently `harness_v2` and `heblo`) are scanned on
the `--source-poll` interval (default 30 s). `harness_v2` being scanned is
intended — the harness dogfoods its own `harness:todo` issues.

## Data flow

```
harness run
  → _github_sources(registry)
      for name in registry.names():
        path = registry.resolve(name)
        slug = github_slug(path)          # git origin → owner/repo | None
        if slug: GithubTaskSource(repo=slug, repository=name, ...)
  → build(sources=[...])
      → one SourcePoller per source, each polling its repo every source_interval
      → all feed the single inbox
  → dispatcher/consumers process repo-agnostically; each task carries its own
    repository name, resolved to a worktree per task
  → SourceReflectorSink reflects progress/finish to every source; each source's
    repo-scoped _mine() applies labels only to its own repo's issues
```

## Error handling

- Repo not a git repo / no `origin` / non-GitHub origin → `github_slug` returns
  `None`, the repo is skipped with a warning; other repos are unaffected.
- Missing/broken `repos.json` → `names()` returns `[]`, no sources, harness runs
  on `submit` alone.
- A `poll()` failure on one repo is already isolated by `SourcePoller.tick`
  (catches, emits `source_error`, returns `False`); the other repos' pollers are
  unaffected.

## Testing

- `parse_github_slug` (pure unit): SSH form, HTTPS form, trailing `.git`,
  non-github host → `None`, malformed → `None`.
- `RepositoryRegistry.names()`: `FilesystemRepositoryRegistry` over a temp
  config (populated, empty, missing, broken) and `MemoryRepositoryRegistry`.
- `_github_sources` with a fake registry and an injected fake `slug_of`: builds
  one source per GitHub repo, skips non-GitHub repos (warning on stderr),
  returns `[]` without a token.
- `GithubTaskSource._mine` repo-scoping: a task whose `source.repo` is repo A is
  not `_mine` for a source bound to repo B.
- Integration over two repos via `FakeGithubClient`: each source ingests only
  its own issues, and `report_progress`/`finish` label only the originating
  repo's issue — no cross-repo label writes.

## Files touched

- `src/harness/drivers/git_remote.py` — new
- `src/harness/ports/repos.py` — add `names()`
- `src/harness/drivers/fs_repos.py` — implement `names()`
- `src/harness/drivers/memory.py` — implement `names()`
- `src/harness/drivers/github_source.py` — repo-scope `_mine`
- `src/harness/cli.py` — `_github_source` → `_github_sources`, drop two flags
- `.conductor/settings.toml` — simplify the run command
- `tests/` — new `test_git_remote.py`; extend `test_repos.py`, `test_cli.py`,
  `test_github_source.py`, and a two-repo integration test

## Invariants preserved

- #15: a task carries a repo **name**, not a path; the worktree path is derived
  by the harness. Unchanged — the slug is used only to talk to GitHub, never
  stored on the task as a path.
- #17: `RepositoryRegistry` stays unknown to the dispatcher/consumer; `names()`
  is used only by CLI wiring.
- #18–#21: `TaskSource` remains the single outward port; how state is rendered
  (a label) stays inside the driver; the reflector is unchanged; a per-repo
  source failure stays isolated.
