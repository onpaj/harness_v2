# ADR-0015: GitHub task sources as data, with a write-side SourceAdmin

Status: Accepted

## Context

A GitHub `TaskSource` — the periodic ingestion that turns a labelled issue into a
task (ADR-0010) — was configurable only through CLI flags (`--github-workflow`,
`--github-step`, `--github-label`) plus which repos in `repos.json` happen to have
a GitHub origin. Everything *else* the operator tunes is data: agents
(`agents/*.json`, ADR-0007), workflows (`workflows/*.json`), triggers
(`triggers/*.json`, ADR-0014). Sources were the odd one out — invisible to the
board, un-editable without restarting with different flags.

Two questions followed from "make sources data too":

1. Is GitHub issue-loading a **source** or a **trigger**? A trigger already fires
   on a schedule and produces tasks (ADR-0014), and "a scheduled periodic task
   that loads new issues" sounds like one.
2. If sources become data *and* stay wired from `repos.json`, what stops the two
   from double-scanning the same repo?

## Decision

**GitHub ingestion is a source, declared as data, and stays a full `TaskSource`
— not a `Trigger`.** A `Trigger` reflects nothing outward (ADR-0014, invariant
#36); a GitHub source reflects task state back onto the issue as labels
(`todo → queued → pr-open/failed`, invariants #18–#20). That outward projection
is the whole difference, so GitHub ingestion remains a source even as data.
The *scheduling* the operator wants is the existing poll loop (`SourcePoller` on
`source_interval`) — a source polled on that cadence *is* "a scheduled periodic
task that loads issues". No new loop, no cron.

A source is declared in `sources/*.json`, mirroring `triggers/*.json`: `kind`
(`github`), the `repository` to scan (a repo *name*, resolved to a slug at build
via the `RepositoryRegistry` — invariant #15), a `select_label`, and a `target`
of exactly one of `{"workflow": ...}` / `{"step": ...}` (the same target shape
triggers use; placement stays the dispatcher's — invariants #3/#8). Two readers
split the same way agents/workflows/triggers do:

- `FilesystemSourceRepository.build()` constructs the live `GithubTaskSource`s for
  a run (it needs a `GithubClient` and the registry). Like the CLI-flag path, no
  token → `[]`; a repo with no GitHub origin is skipped with a warning, not fatal.
- `FilesystemSourceAdmin` is the write-side for the board's **Sources** tab —
  `list`/`read_raw`/`write_raw`/`delete` over the exact file text, needing neither
  a client nor the registry. It is the third UI-facing admin port alongside
  `AgentAdmin`/`WorkflowAdmin` (invariant #33, extended), unknown to the dispatcher
  and consumer; `api/` touches only the port, the driver is wired in `serve()`.

Structural validation (`parse_source`) is shared, so the run and the UI reject the
same malformed file with the same message — as `fs_triggers` mirrors `fs_agents`.
Target/repo *existence*, however, is a run-time concern (a repo may not be cloned
here; a workflow may not be served this run): the run fails fast on an unknown
target and warns/skips an unresolvable repo, while the UI shows a non-blocking
warning (the `_new_source_warnings` shape workflows already use).

**Data wins over the auto-scan.** A repo named by any `sources/*.json` is excluded
from the `repos.json` auto-scan (`_github_sources(..., exclude=...)`), so the two
producers never double-scan it. Absent any source files, the auto-scan behaves
exactly as before — the feature is additive and backward-compatible.

## Consequences

- GitHub ingestion is now editable from the board and lives next to agents,
  workflows and triggers as data — the four things the operator tunes are uniform.
- `TaskSource` and ADR-0010 are untouched: a data-built `GithubTaskSource` is the
  same object the flags built, so labels/claim/dedup are unchanged. The only new
  code is a filesystem reader/writer and the wiring, plus the admin port + routes.
- The source/trigger boundary is now explicit: reflects-outward ⇒ source (a full
  three-verb `TaskSource`), reflects-nothing ⇒ trigger. A future non-GitHub source
  kind is a new `kind` in `parse_source` + a driver, not a new subsystem.
- The UI layer stays driver-free: `api/` sees only `SourceAdmin`, the filesystem
  driver is wired in `cli.serve()` (guarded by the existing architecture tests plus
  `test_orchestration_does_not_import_admin_ports`).
- Not covered here: a non-GitHub source as data (only `kind: "github"` in v1), a
  structured form editor (the admin edits raw JSON, like workflows), and exposing
  every `GithubTaskSource` knob (claimed/pr/failed labels use their defaults;
  step-labels use the CLI default map).
