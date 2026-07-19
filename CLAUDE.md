# harness_v2 — orientation for Claude

> **Status: restarted.** `main` was deliberately emptied on 2026-07-19 to begin
> again from scratch. This file is the only thing left. Replace it with real
> content once the new direction takes shape.

## What this is

Not yet defined for the current attempt.

## The previous implementation

A complete first implementation lives on the **`fast-ship`** branch (tip
`7bc0e6e`), pushed to `origin`. It is a working, tested platform — 368 passing
tests — not a sketch. Read it before rebuilding anything, so this attempt starts
from what was learned rather than from zero:

```sh
git show fast-ship:README.md
git show fast-ship:docs/superpowers/specs/2026-07-19-agent-harness-design.md
git show fast-ship:docs/superpowers/plans/2026-07-19-agent-harness.md
git checkout fast-ship -- <path>   # pull back anything worth keeping
```

What it contained: a stateless multi-agent harness where each agent invocation is
one `claude -p` subprocess working in its own git worktree, with per-agent
filesystem queues, an asyncio dispatcher, guard-railed handoffs, a cron
scheduler, a SQLite run store, and a read-only dashboard.

Findings from that build worth not rediscovering:

- `merge_leaves` acquired the repo lock internally; wrapping it in a second
  `flock` self-deadlocked, because `flock` contends across file descriptors even
  within one process.
- A child worktree inherits its ancestors' `.harness/runs/<trace>/<task>/`
  directories from `base_ref`, so a run must never locate its own artifact
  directory by globbing.
- SQL `NOT IN` with NULLs silently matches nothing — root tasks have a NULL
  `parent_task_id`.
- A bare mirror's default fetch refspec prunes locally-created `run/*` branches.
- The real `claude` CLI's headless auth can expire independently of the
  interactive session; a live test must distinguish that from a contract break.

The `.claude/skills/` directory (the AgentHarness skills from `onpaj/harness`)
was also removed here and is recoverable the same way.

## Operator

- **Ondrej Pajgrt** — "Ondrej" / "Rem". GitHub `onpaj`. Timezone Europe/Prague.
- Machine-level context (NanoClaw platform, podman quirks, agents) lives in
  `~/CLAUDE.md`; this file covers only this repo.

## Conventions

- Shell is `zsh`. `docker` on this machine is podman.
- **Commit directly to `main`** in this project — do not branch first, do not
  open a PR, and do not ask. Push when the work is done.
- v1 of this idea lives at `onpaj/harness` and coexists with this repo; it keeps
  running its own pipeline, with no migration and no shared code.
