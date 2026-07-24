# Plan: Jira issue loader (`jira-issues` action)

## Summary

Add Jira Cloud issue ingestion as a new Process **action**, mirroring the
existing `github-issues` action one component at a time: a stdlib-only
`JiraClient` behind an ABC, a `JiraIssuesCheck(Check)` that lists labelled
issues and claims them by a label swap, and wiring in `cli.py` that closes
the client into a `jira-issues` `CheckFactory`. No orchestration code changes;
this is purely a new driver behind the existing `TaskSource`/`Check` seams
(ADR-0010, ADR-0015). This request is design-only — no code is written in
this step.

## Context

Ingestion today has one real-world source, GitHub, reached through the
`github-issues` Process action (`GithubIssuesCheck`, ADR-0015/ADR-0018).
Teams that track work in Jira instead of GitHub Issues have no equivalent
path in. The architecture already generalized ingestion into a `Check` +
`Process` seam specifically so a second source is "a new driver, wired only
in `cli.py`/`app.py`" — this plan is that generalization exercised for real.

## Functional requirements

**FR-1 — `JiraClient` port-less driver ABC.**
A `drivers/jira_client.py` module exposing an ABC `JiraClient` with:
- `search_issues(jql: str) -> list[JiraIssue]`
- `add_label(key: str, label: str) -> None`
- `remove_label(key: str, label: str) -> None`

plus `JiraIssue` (frozen dataclass: `key: str`, `summary: str`,
`description: str`, `url: str`, `labels: tuple[str, ...]`, `project: str`).
Two implementations: `FakeJiraClient` (in-memory dict, for tests) and
`HttpJiraClient` (stdlib `urllib` only, Jira Cloud REST `/rest/api/3`, Basic
auth with `email:api_token` — no new production dependency, mirroring
`HttpGithubClient`).
*Acceptance:* `FakeJiraClient.add_label`/`remove_label` are idempotent (calling
either twice is a no-op, not an error) — this is what makes the claim step
retry-safe. `HttpJiraClient` raises a driver-local error type on a non-2xx
response; no bare `requests.exceptions.*`/`urllib.error.*` leaks past the
module boundary (mirrors how `GithubClient`'s callers only ever see its own
exceptions).

**FR-2 — `JiraIssuesCheck(Check)`.**
A `drivers/jira_issues_check.py` module, structurally identical to
`GithubIssuesCheck`:
- Constructor takes `client: JiraClient`, `registry: RepositoryRegistry`,
  `repository: str` (the *single* registered repo this Process's issues
  attach to — see Open Question 1; unlike GitHub there is no per-issue slug
  to derive one from), `label`, `claimed_label`, optional `jql`.
- `evaluate()`: build the query (`jql` if given, else
  `project = {project} AND labels = {label}` — `project` is required when
  `jql` is absent), call `search_issues`, skip anything already in the
  in-process `_claimed: set[str]` ledger (keyed by `key`, the Jira twin of
  GitHub's `(slug, number)` ledger — search has the same read-after-write lag
  `list_issues` has), else claim (`remove_label` + `add_label`) and emit one
  `Observation` per issue: `state_key=f"jira:{key}"`, `repository=<the
  configured repo name>`, `data={"title": summary, "body": description,
  "source": {"kind": "jira", "site": base_url_or_site_id, "key": key, "url":
  url, "project": project}}`.
*Acceptance:* two `evaluate()` calls in the same process with an unchanged
issue set produce zero new observations on the second call (ledger); after a
simulated restart (`_claimed` reset, `SourcePoller._seen` still primed with
the prior `dedup_key`), the same issue re-observed still yields no new task
(cross-restart at-most-once, same mechanism as GitHub's label swap +
`_seen`).

**FR-3 — Wiring in `cli._process_check_factories`.**
Extend the existing function (no new function — mirrors how
`github-conflicts` sits alongside `github-issues` in the same factory dict)
with a `jira-issues` entry:
- Build a `JiraClient` from `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`
  (all three required; injectable for tests the same way `client:
  GithubClient | None` is today).
- The factory raises `ProcessValidationError(..., field="check")` when any
  of the three env vars is missing, mirroring the `GITHUB_TOKEN` fail-fast
  exactly — this is what makes `jira-issues` list in the admin dropdown
  (`ProcessAdmin.check_names()`) while still failing loudly and specifically
  on save/build without credentials.
- Validates `params["repository"]` names a repo the `RepositoryRegistry`
  actually has (fail with `field="params"` otherwise) and that `label`/
  `claimed_label`/`jql`/`project`, where present, are strings.
*Acceptance:* a `processes/*.json` naming `jira-issues` with no `JIRA_*` env
set fails `compile_process`/`FilesystemProcessAdmin.write` with a
`field="check"` error and does not affect any other configured process
(mirrors the existing `github-issues`-without-`GITHUB_TOKEN` test).

**FR-4 — `kind = "jira"` accepted by outbound routing.**
`ports/source.py::effective_sink_kind` must not reject a task whose
`data.source.kind == "jira"` — it already defaults an unrecognized/absent
`data.sink` to `data.source.kind`, and `SourceReflectorSink`/reconcilers
(`MergeReconciler`, `IssueReconciler`) must silently ignore a `jira`-kind
task exactly as they already ignore a `slack`-only or foreign-kind task
today (no `JiraTaskSource`/`JiraLabelReflector` exists yet to claim it).
Invariant #40's list of accepted kinds is extended to `none`/`slack`/
`github`/`jira`.
*Acceptance:* a task with `data.source = {"kind": "jira", ...}` and no
sink-side driver wired reaches `done`/`archived` through the normal flow
without any reflector raising or logging an error about an unknown kind.

**FR-5 — `Process` authoring shape for Jira ingestion.**
A `processes/jira-ingest.json` is a valid example a user can adapt (not
shipped by `harness init`, per Open Question / decision below):
```json
{
  "trigger": {"interval": "60s"},
  "action": {"check": "jira-issues",
             "params": {"project": "PROJ", "label": "harness-todo",
                         "repository": "my-service"}},
  "target": {"workflow": "default"},
  "sink": {"kind": "none"}
}
```
*Acceptance:* `FilesystemProcessRepository.build()` compiles this file into a
`ScheduledTrigger` exactly like any other process file — no special-casing in
`compile_process` beyond the new factory key.

## Non-functional requirements

- **No new production dependency.** `HttpJiraClient` is stdlib `urllib` +
  `json` only, same constraint `HttpGithubClient` operates under.
- **Secrets never touch a JSON file.** `JIRA_BASE_URL`/`JIRA_EMAIL`/
  `JIRA_API_TOKEN` are read from the environment only, exactly like
  `GITHUB_TOKEN`/`SLACK_WEBHOOK_URL` — never accepted as a `params` value in
  a process file, and `FilesystemProcessAdmin` must not round-trip them.
- **At-most-once ingestion across restarts**, matching the existing GitHub
  and self-heal guarantees (invariants #21, #38).
- **Fail fast, fail loud.** A misconfigured or credential-less `jira-issues`
  action must fail at process build/write time (`ProcessValidationError`),
  never silently at runtime inside `evaluate()`.

## Data model

- `JiraIssue` (new, `drivers/jira_client.py`): `key: str`, `summary: str`,
  `description: str`, `url: str`, `labels: tuple[str, ...]`, `project: str`.
- `Task.data.source` for a Jira-born task:
  `{"kind": "jira", "site": <base url or site id>, "key": "PROJ-123", "url":
  ..., "project": "PROJ"}` — `key` is a **string**, unlike GitHub's integer
  `issue`; every downstream reader of `data.source` must treat the id as
  opaque (no reconciler/reflector code assumes `issue` is numeric today —
  confirmed by reading `github_issue_checker.py`/`github_merge_checker.py`,
  which key off `data["pr"]`/GitHub-specific fields, not a generic numeric
  issue id).
- `Observation.repository`: the single configured `repository` param, since
  a Jira issue carries no intrinsic repo/slug the way a GitHub issue does.

## Interfaces

- New driver module `drivers/jira_client.py` (ABC + fake + http impl).
- New driver module `drivers/jira_issues_check.py` (`JiraIssuesCheck`,
  `"jira-issues"` factory key).
- `cli._process_check_factories` gains a `jira-issues` entry alongside
  `github-issues`/`github-conflicts`.
- `ports/source.py::effective_sink_kind` doc/accepted-kinds comment extended
  to include `"jira"` (no signature change — it already falls through to
  `data.source.kind` for an unrecognized sink).
- Process JSON shape: `action.check = "jira-issues"`,
  `action.params = {project?, label?, claimed_label?, jql?, repository}`.

## Dependencies and scope

**Depends on:** the existing Process/`Check`/`TaskSource` seam (ADR-0010,
ADR-0015, ADR-0018) — no changes to `dispatcher.py`, `consumer.py`,
`router.py`, `source_poller.py`, or any port signature.

**In scope (v1):**
1. `JiraClient` ABC + `FakeJiraClient` + `HttpJiraClient`.
2. `JiraIssuesCheck` + its `jira-issues` factory wiring in `cli.py`.
3. `effective_sink_kind`/invariant #40 doc update to accept `jira`.
4. Tests mirroring the `github-issues`/`GithubIssuesCheck` suite.
5. One new ADR documenting Jira as the second ingestion source.

**Explicitly out of scope (follow-up):**
- Outbound reflection (`JiraReflector`, transitioning issues / posting PR
  links back to Jira) — v1 is ingestion-only, exactly as the request states.
- A `project → repository` mapping richer than one `repository` param per
  Process (multi-project fan-out needs one Process file per project in v1).
- Jira Server/Data Center support (PAT auth, `/rest/api/2`) — v1 targets
  Cloud only.
- A default `processes/jira-ingest.json` template shipped by `harness init`
  — deferred because it needs site-specific config the init step can't infer
  (see Open Questions).
- Claiming via status transition instead of label swap.

## Rough plan

1. **ADR-0020** — "Jira as a second ingestion source over the ADR-0015 action
   seam." Records the label-swap-vs-transition and JQL-vs-project+label
   decisions from this doc so they don't need re-litigating in review.
2. **`drivers/jira_client.py`** — `JiraIssue`, `JiraClient` ABC,
   `FakeJiraClient`, `HttpJiraClient`. Unit tests against the fake; a
   narrowly-scoped test of `HttpJiraClient`'s request shaping (URL, JQL
   encoding, Basic-auth header) without a live network call, mirroring
   however `HttpGithubClient` is tested today.
3. **`drivers/jira_issues_check.py`** — `JiraIssuesCheck`. Unit tests:
   one `Observation` per labelled issue, correct `data.source`/`state_key`,
   claim swaps labels, `_claimed` ledger suppresses a same-run re-poll,
   missing `project` with no `jql` raises a clear config error.
4. **`cli._process_check_factories`** — add the `jira-issues` factory
   (env-var read, fail-fast `ProcessValidationError` without credentials,
   `params` validation for `repository`/`label`/`claimed_label`/`jql`/
   `project`). Test: process-compile test for a `jira-ingest.json` fixture,
   both with and without `JIRA_*` set.
5. **`ports/source.py`** — extend the `effective_sink_kind` docstring/
   accepted-kinds list to include `jira`; confirm (via existing/new test)
   that a `jira`-kind task with no reflector wired passes through
   `SourceReflectorSink` as a no-op, matching `slack`'s current treatment
   when no webhook is configured.
6. **`test_architecture.py`** — confirm (no new test code needed if the
   existing glob-based checks are generic, otherwise extend the glob) that
   `drivers/jira_*` import only sibling drivers + ports, never `cli`, and
   that `dispatcher.py`/`consumer.py` gain no reference to "jira".
7. **CLAUDE.md module map** — add `jira_client.py`/`jira_issues_check.py`
   rows to the driver table and the `jira` kind to the invariant #40 line,
   following this same commit (per the "docs/bookkeeping" note in the
   original request).
8. **Example `processes/jira-ingest.json`** — added to docs/an example
   fixture directory, not to `harness init`'s generated defaults (decision
   below).

## Open questions

1. **Repo mapping (decided for v1, flagged for revisit).** Use one required
   `repository` param per Process, exactly mirroring how `--heal-repo`
   stamps a repository-less heal task (invariant #25) — the simplest shape
   that needs no new mechanism. A `project → repository` map is deferred;
   multi-project users write one Process file per project in v1.
2. **Claim mechanism (decided for v1).** Label-swap, not status transition —
   keeps `JiraIssuesCheck` structurally identical to `GithubIssuesCheck` and
   avoids coupling ingestion to a Jira workflow-status scheme the operator
   would otherwise have to configure. Transition-based claim (and reflection)
   is the natural follow-up once outbound `JiraReflector` is built.
3. **Selection form (decided for v1).** Support both: `jql` wins when
   present, else `project`+`label` compiles to
   `project = {project} AND labels = "{label}"`. `project` is required
   whenever `jql` is absent (fail fast in the factory, not at `evaluate()`
   time).
4. **Jira Cloud vs. Server/DC (decided for v1).** Cloud only
   (`/rest/api/3`, Basic auth with API token). Server/DC is a later
   `HttpJiraClient` variant behind the same ABC — no interface change
   anticipated, so this doesn't block v1's design.
5. **Numeric-id assumptions downstream (checked, resolved).** Grepped
   `merge_reconciler.py`, `issue_reconciler.py`, `github_merge_checker.py`,
   `github_issue_checker.py` — none assume `data.source`'s issue identifier
   is numeric; they either key off `data["pr"]` (a different field) or
   pattern-match `kind == "github"` specifically before touching
   `issue`/`repo`. A `jira`-kind task is simply invisible to both today,
   which is correct for v1 (no Jira reconciler exists yet).
6. **Should `harness init` ship a `jira-ingest.json` template?** Recommend
   **no** for v1 — unlike `agents/*.json` (portable across repos), a Jira
   process needs a real `project` key and `repository` name to be anything
   but a broken example; shipping it inert-by-default risks an operator
   enabling it half-configured. Ship a documented example instead (README /
   spec), same treatment triggers get today (`triggers/` starts empty).
