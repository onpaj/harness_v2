# ADR-0001: Ports and adapters

Status: Accepted

## Context

Every phase of this project has swapped a driver for another without touching
the orchestration around it: in-memory queues became filesystem queues, a dummy
behavior became a real `claude -p` agent, a fake forge became real GitHub. None
of those swaps required editing `dispatcher.py` or `consumer.py`. That is only
possible because every moving part — queues, workflows, the enqueue strategy,
the consumer behavior, the event sink, the clock, the workspace, artifacts, the
forge, the board, the agent, the repository registry, the task source — sits
behind an abstract interface in `ports/`, and the orchestration core imports
only those interfaces, never a concrete implementation.

## Decision

Neither `dispatcher.py` nor `consumer.py` may import anything from
`drivers/`. Wiring a concrete driver to a port happens exclusively in `app.py`
(and, for the CLI's real run, in `cli.py`). This is not a convention held by
discipline alone — `tests/test_architecture.py::test_orchestration_does_not_
import_drivers`, `test_ports_do_not_import_drivers`, and
`test_only_app_and_cli_wire_drivers` parse the AST of every module under
`src/harness/` and fail the build the moment a driver import leaks into the
core.

## Consequences

- A new driver (a different queue backend, a different forge, a different
  agent runner) is always a new file under `drivers/` plus a wiring change in
  `app.py`/`cli.py` — never a change to `dispatcher.py`, `consumer.py`, or any
  port's interface.
- The orchestration core can be tested entirely against in-memory fakes
  (`drivers/memory.py`), with no disk, no subprocess, and no network — which is
  exactly how the unit/integration suite runs today.
- The cost is indirection: every new capability (agent, repository registry,
  task source, task control) first needs a port defined before any driver can
  implement it, even when there is only one driver so far.
