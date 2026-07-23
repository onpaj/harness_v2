# ADR-0005: UI never imports a driver

Status: Accepted

## Context

The operator board (`api/`) needs to show where every task is, its history,
its artifacts, and — once added — its live stage output and a restart control.
None of that requires the UI to know whether tasks are JSON files on disk,
whether artifacts live in a git worktree, or whether the agent runs `claude -p`
in a subprocess. If the UI imported drivers directly, swapping any one of those
implementations (the stated goal of ADR-0001) would risk breaking the board
alongside the orchestration core.

## Decision

Neither `api/` nor `projection.py` imports anything from `drivers/`. The board
reads exclusively through three read-only ports: `BoardView` (where a task is),
`ArtifactView` (what it produced), and `StageOutputView` (what the running
stage is doing right now, see ADR-0012); it writes through one write-side port,
`TaskControl` (see ADR-0011). `tests/test_architecture.py::test_api_does_not_
import_drivers` and `test_projection_does_not_import_drivers` parse the AST of
every file under `api/` and of `projection.py` and fail if a `harness.drivers.*`
import appears anywhere in either. A narrower test,
`test_api_reads_artifacts_only_through_view`, additionally checks that `api/`
never imports the write-side `ArtifactStore`/`ArtifactSlot` names from
`ports.artifacts` — the board must be able to show an artifact, never create
one.

## Consequences

- The board's templates and routes are identical in shape whether the harness
  is running against in-memory drivers in a test or the real filesystem/git
  drivers in production — the only thing that changes is which driver is wired
  to the port in `app.py`/`cli.py`.
- A new UI-facing capability (e.g. live stage output, added after the board
  already shipped) must first get its own read-only port before `api/` can use
  it — exactly what happened when `StageOutputView` was introduced.
- The UI can never accidentally start depending on filesystem paths, git
  internals, or subprocess details, because it has no import path to reach
  them even by mistake.
