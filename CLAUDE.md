# harness_v2 — orientation for Claude

**agentharness**: a stateless, CLI-driven multi-agent orchestration platform.
Agents are declared as YAML; each has a durable queue; each invocation is one
`claude -p` subprocess that reads a task, works in its own git worktree, commits,
and hands off to the next agent.

Design spec: `docs/superpowers/specs/2026-07-19-agent-harness-design.md`
Implementation plan: `docs/superpowers/plans/2026-07-19-agent-harness.md`

## Three invariants — do not break these

1. **CLI-only.** Claude is reached *only* by spawning the `claude` binary, and
   only from `src/agentharness/runner/executor.py`. No Anthropic SDK, no HTTP to
   an Anthropic endpoint. A test asserts no other module even mentions
   "anthropic".
2. **Stateless agents.** The argv never contains `--resume` or `--continue`; a
   test asserts this. Continuity comes only from committed artifacts, never from
   session memory.
3. **The harness never writes to `main`.** Runs commit to `run/<task_id>`
   branches off a bare mirror; completed traces merge only as far as the repo's
   `integration_branch`. Promotion to `main` is a human PR.

## Working here

```sh
.venv/bin/pytest -q          # 368 tests, no subscription usage
.venv/bin/pytest -m live -v  # opt-in; spawns a real claude -p, costs usage
```

Python is **3.11** (`/Users/rem/.local/bin/python3.11`); there is no `uv` on this
machine, so it is a plain `venv` + `pip install -e ".[dev]"`.

Tests use `FakeExecutor`, which returns a scripted `result.json` without
spawning anything. That is what lets the full queue → dispatch → git → merge path
be exercised for free. Never add a test that calls the real CLI without the
`live` marker.

## Module map

Dependencies flow strictly downward; there are no cycles.

| Layer | Modules |
|---|---|
| Foundation | `config`, `ids`, `models` (`models` imports nothing from the package) |
| Registries | `registry/agents`, `registry/repos` |
| Git plane | `git/{mirror,lock,worktree,merge}` — `mirror.git()` is the only place git is invoked |
| Storage | `store/{db,runs}` (SQLite), `queue/{base,filesystem}` |
| Execution | `runner/{executor,prompt,result,runner}` |
| Orchestration | `dispatch/{dispatcher,routing,retry,limits}` |
| Edges | `scheduler/`, `obs/`, `web/`, `cli` |

## Gotchas found the hard way

- **`merge_leaves` takes the repo lock itself.** Wrapping it in another
  `repo_lock` self-deadlocks — `flock` contends across file descriptors even
  within one process.
- **A child worktree inherits its ancestors' `.harness/runs/<trace>/<task>/`
  directories** from `base_ref`. Never locate a run's artifact dir by globbing;
  read the path out of the prompt, as a real agent does.
- **`runs` has a foreign key onto `tasks`.** Record the task before the run.
- **SQL `NOT IN` with NULLs** silently returns nothing — root tasks have a NULL
  `parent_task_id`, so `trace_leaf_branches` must filter them out.
- **A mirror's default fetch refspec would prune locally-created `run/*`
  branches.** `fetch()` restricts to `refs/heads/*` from origin.

## Operator

Ondrej Pajgrt — "Ondrej" / "Rem". GitHub `onpaj`. Europe/Prague. Machine-level
context (NanoClaw, podman quirks) lives in `~/CLAUDE.md`.

v1 of this idea lives at `onpaj/harness` and **coexists** with this repo — it
keeps running its own pipeline; there is no migration and no shared code.
