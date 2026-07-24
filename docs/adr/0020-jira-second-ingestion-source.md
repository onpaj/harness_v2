# ADR-0020: Jira as a second ingestion source over the ADR-0015 action seam

Status: Accepted

## Context

Ingestion has had exactly one real-world source since ADR-0015: GitHub, via
the `github-issues` Process action (`GithubIssuesCheck`). Teams that track
work in Jira instead of GitHub Issues had no equivalent path in — the
Process/`Check`/`TaskSource` seam existed to generalize this ("a new source
is a new driver, wired only in `cli.py`/`app.py`"), but it had never been
exercised for a second real source. This ADR records the shape chosen for
that second source, and the decisions that shape needed to make where Jira
and GitHub genuinely differ.

## Decision

Jira ingestion mirrors GitHub's shape component-for-component, with three
deliberate deviations forced by real differences between the two systems —
not by convenience.

- **`drivers/jira_client.py`** — `JiraClient` (ABC), `JiraIssue` (frozen
  dataclass), `FakeJiraClient`, `HttpJiraClient`. Structurally identical to
  `github_client.py`'s shape: one ABC naming the minimal surface, one
  in-memory fake, one stdlib-`urllib` real client — no new production
  dependency. **No new port** — `JiraClient` lives in `drivers/` exactly as
  `GithubClient` does, on the same precedent: a port exists where the
  orchestration core or a behavior needs to depend on a capability
  abstractly, and nothing there needs `JiraClient` — only one driver
  (`JiraIssuesCheck`) does, behind the `Check` port it already has.
- **`drivers/jira_issues_check.py`** — `JiraIssuesCheck(Check)`, structurally
  identical to `GithubIssuesCheck`: runs a query, skips anything already
  claimed this run (an in-process ledger cutting off search's
  read-after-write lag), claims by a label swap, emits one `Observation` per
  issue with `data.source = {kind: "jira", site, key, url, project}` and
  `state_key = "jira:{key}"`.
- **Wiring stays in `cli._process_check_factories`** — one more dict entry
  (`"jira-issues"`), closing a `JiraClient` (built from `JIRA_BASE_URL`/
  `JIRA_EMAIL`/`JIRA_API_TOKEN`, mirroring the `client`/`GITHUB_TOKEN`
  pattern) into a factory. Missing credentials fail fast with
  `ProcessValidationError(field="check")`, exactly like `github-issues`
  without `GITHUB_TOKEN`. `app.build()` needs no change — it already accepts
  whatever merged `extra_checks` dict this function returns.
- **Claim mechanism: label-swap, not status transition.** Keeps
  `JiraIssuesCheck` structurally identical to `GithubIssuesCheck` and avoids
  coupling ingestion to a Jira workflow-status scheme the operator would
  otherwise have to configure. A transition-based claim is the natural
  companion to a future outbound `JiraReflector`, not this increment.
- **Selection: JQL wins over `project`+`label`.** Both are supported —
  `jql` when given, else a compiled `project = {project} AND labels =
  "{label}" AND statusCategory != Done`. `project` is required whenever
  `jql` is absent, checked in the factory (build time), not in `evaluate()`
  (runtime). The status-category clause is deliberate, not decorative:
  without it, a resolved/closed issue that still carries the select label
  (labels on Jira issues routinely outlive the workflow status — nothing
  requires an operator to strip a label on close) would be re-ingested as a
  fresh task on every tick for as long as the label sticks around, with no
  equivalent to GitHub's `list_issues(..., state="open")` filter. An
  explicit `jql` override is trusted as-is and gets **no** implicit status
  clause appended — an operator handing a raw JQL string owns its
  correctness completely, the same way `github-issues`'s `label` param is
  trusted without a hidden second filter layered on top.
- **Deviation 1 — one `repository` param per Process, not a per-issue slug
  derivation.** A GitHub issue is intrinsically scoped to the repo it lives
  in (`github_slug()` derives it from the registry); a Jira issue carries no
  such axis. `JiraIssuesCheck` takes a single `repository: str` and stamps
  it onto every `Observation` it emits — the same mechanism `--heal-repo`
  already uses to give a repository-less heal task a worktree (invariant
  #25), not a new one. A richer `project → repository` map is deferred:
  multi-project fan-out is one Process file per project in v1, the same way
  `github-issues` already handles "different repos need different labels"
  (its own cross-file label-collision guard, invariant #39).
- **Deviation 2 — `_ACCEPTED_SINK_KINDS` does *not* gain `jira`.**
  `ports/source.py::effective_sink_kind` is an unconditional dict lookup
  with no allow-list on its `data.source.kind` fallback, so a Jira-sourced
  task already reflects as `"jira"` there with zero code change — every
  existing reflector's `_mine` gate simply doesn't match it, the same
  no-op treatment a foreign/`slack`-only kind already gets. The one real
  allow-list, `fs_processes.py::_ACCEPTED_SINK_KINDS`, gates a Process's own
  *declared* `sink.kind` — and stays `{"none", "slack", "github"}` for now,
  because no `JiraReflector` exists to route a declared `sink: {"kind":
  "jira"}` to. Adding the kind there today would let a process compile with
  a sink that silently does nothing, exactly the anti-pattern
  `_parse_sink`'s own docstring already warns about for `github`.
- **Deviation 3 — a smaller `FakeJiraClient` than `FakeGithubClient`.**
  `GithubClient` also backs the forge and two reconcilers, so its fake
  carries PR/reconciler-shaped state. `JiraClient`'s only caller in this
  increment is `JiraIssuesCheck`, so the fake needs only `search_issues`/
  `add_label`/`remove_label`.
- **No custom exception type.** `HttpGithubClient` was assumed (in the
  design/architecture steps preceding this one) to wrap errors in a
  driver-local exception; rereading the real code shows it does not — a
  non-2xx response propagates as the raw `urllib.error.HTTPError` except
  where a specific status is a legitimate outcome (404 on `remove_label`,
  swallowed). `HttpJiraClient` mirrors that precedent exactly rather than
  inventing a parallel taxonomy: `remove_label` swallows 404, everything
  else propagates unwrapped.
- **Jira Cloud only.** `/rest/api/3`, Basic auth with an email + API token.
  Server/Data Center (PAT auth, `/rest/api/2`) is a later `HttpJiraClient`
  variant behind the same ABC — no interface change anticipated.
- **`harness init` does not ship a `jira-ingest.json` template.** Unlike
  `agents/*.json` (portable across repos), a Jira process needs a real
  `project` key and `repository` name to be anything but a broken example —
  shipping it inert-by-default risks an operator enabling it
  half-configured. A documented example is enough, the same treatment
  `triggers/` gets (starts empty).

## Consequences

- Ingestion now has two real-world sources behind the same seam, proving out
  ADR-0015's "a new source is a new driver, wired only in `cli.py`" claim for
  real rather than by inspection — no orchestration code (`dispatcher.py`,
  `consumer.py`, `router.py`, `source_poller.py`) changed, no port signature
  changed.
- A `jira-issues`-driving process **must** set `"dedup": "per-state"`
  (omitting it defaults to `"per-interval"`, which collapses every issue
  matched in one tick into a single dedup identity — a correctness
  requirement of `ScheduledTrigger`'s dedup mechanism itself, not unique to
  Jira; `github-issues` example processes carry the identical footgun).
- Two `jira-issues` processes on the same Jira site racing over the same
  `label`/`claimed_label` are **not** caught by `fs_processes.py`'s
  cross-file collision guard — that guard is `github-issues`-specific by
  name. Accepted as a known gap for v1, mirroring the residual footgun
  `github-issues` already has across different repos sharing a label.
  Generalizing the guard is out of scope here.
- Jira's Atlassian Document Format `description` field is parsed into plain
  text by a minimal recursive walk (`content[].content[].text`), not passed
  through as raw ADF JSON — a richer render is a later refinement, not a v1
  blocker.
- `data.source.key` is a **string** (`"PROJ-123"`), not GitHub's int
  `number`. Every current reader of `data.source`
  (`github_issue_checker.py`, `github_merge_checker.py`,
  `source_reflector.py`) pattern-matches on `kind == "github"` before ever
  touching an issue identifier, so a `kind: "jira"` task is simply invisible
  to all of them today — correct, since no Jira reconciler exists yet.
- Outbound reflection (`JiraReflector`: transitioning the Jira issue,
  posting a PR-link comment) is explicitly deferred — this ADR covers
  ingestion only. Follow-up ADR when that lands.
- CLAUDE.md's module map gains `drivers/jira_client.py` and
  `drivers/jira_issues_check.py`; invariant #40's prose is refined (not
  renumbered) to distinguish the sink allow-list (unchanged: `none`/
  `slack`/`github`) from `data.source.kind`'s unconstrained fallback path
  (now legitimately `jira` too).
