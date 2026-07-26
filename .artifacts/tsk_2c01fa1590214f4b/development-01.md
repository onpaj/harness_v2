# Development: Jira issue loader (`jira-issues` action)

Implements `plan-01.md`/`design-01.md`/`architecture-01.md` as approved: a new
`JiraClient` driver + `JiraIssuesCheck` action, wired only in `cli.py`, no
orchestration change.

## Files created

- **`src/harness/drivers/jira_client.py`** — `JiraIssue` (frozen dataclass:
  `key`, `summary`, `description`, `url`, `labels`, `project`), `JiraClient`
  (ABC: `site` property + `search_issues`/`add_label`/`remove_label`),
  `FakeJiraClient` (in-memory, `search_issues` matches on a `labels = "..."`
  predicate extracted from the JQL string — good enough to exercise the check
  without implementing Jira's query language), `HttpJiraClient` (stdlib
  `urllib` only, Jira Cloud `/rest/api/3`, Basic auth via `email:api_token`).
  `HttpJiraClient.search_issues` extracts plain text from the Atlassian
  Document Format `description` field via a minimal recursive `_adf_to_text`
  walk. No new production dependency.

  **Correction against `design-01.md`/`architecture-01.md`**: both assumed
  `HttpGithubClient` wraps errors in a driver-local exception type and left
  "confirm the exact exception type" as an open prerequisite. Rereading
  `github_client.py` during implementation shows it does **not** — a non-2xx
  response propagates as the raw `urllib.error.HTTPError` except where a
  specific status is a legitimate outcome (404 on `remove_label`, swallowed).
  `HttpJiraClient` mirrors that exact precedent: `remove_label` swallows 404,
  every other error (including on `add_label`/`search_issues`) propagates
  unwrapped. This is documented as a resolved decision in the new ADR rather
  than left open.

- **`src/harness/drivers/jira_issues_check.py`** — `JiraIssuesCheck(Check)`,
  structurally identical to `GithubIssuesCheck`: builds a JQL (explicit `jql`
  wins, else `project = {project} AND labels = "{label}"`), lists matching
  issues, skips anything in the in-process `_claimed` ledger, claims via
  `remove_label`+`add_label`, emits one `Observation` per issue
  (`state_key="jira:{key}"`, `repository=<configured>`,
  `data.source={kind:"jira", site, key, url, project}`). Raises `ValueError`
  at construction if neither `jql` nor `project` is given.

- **`docs/adr/0020-jira-second-ingestion-source.md`** — records the shape and
  the deviations from GitHub's mirror (no port, single `repository` param, no
  `_ACCEPTED_SINK_KINDS` change, smaller fake, no exception wrapper, Cloud
  only, no `harness init` template), plus the corrected exception-type finding
  above.

- **`tests/test_jira_client.py`** — `FakeJiraClient` label filter/mutate
  behavior; `HttpJiraClient` request shaping (URL, JQL query param, Basic-auth
  header, browse-URL construction, ADF→text extraction, empty-description
  handling, PUT label-update-op bodies, 404-swallow on `remove_label`,
  unwrapped propagation on other errors/`add_label`). 23 tests, mirrors
  `test_github_client.py`'s structure for the subset of surface Jira has.

- **`tests/test_jira_issues_check.py`** — one `Observation` per labelled
  issue with correct provenance; claim swaps labels; ledger suppresses a
  same-run re-poll; no labelled issues → no observations; default vs. custom
  label/claimed_label; explicit `jql` overrides `project`+`label`; missing
  both raises `ValueError`. Mirrors `test_github_issues_check.py`.

## Files changed

- **`src/harness/cli.py`**:
  - New top-level import `from harness.drivers.jira_client import
    HttpJiraClient, JiraClient`.
  - `_process_check_factories` gains a keyword-only `jira_client: JiraClient
    | None = None` param (mirrors `client: GithubClient | None`), builds an
    `HttpJiraClient` from `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` when
    absent, and returns a `"jira-issues"` factory alongside the existing two.
    The factory fails fast with `ProcessValidationError(field="check")`
    without credentials, and `field="params"` for a missing/unknown
    `repository`, non-string `label`/`claimed_label`/`jql`/`project`, or
    neither `jql` nor `project` given — mirroring `github-issues`'s
    fail-fast shape exactly.
  - `_run` constructs a `jira_client` from the same three env vars (mirroring
    the existing `github_client` construction immediately above it) and
    threads it into `_process_check_factories`.

- **`tests/test_cli.py`**: new import `FakeJiraClient`; updated
  `test_process_check_factories_stays_dependency_free_for_builtin_checks` to
  expect `{"github-issues", "github-conflicts", "jira-issues"}`; five new
  tests — builds a `jira-issues` process into a `ScheduledTrigger`; fails
  fast without `JIRA_*` env (`field="check"`); fails on an unregistered
  `repository` (`field="params"`); fails when neither `jql` nor `project` is
  given (`field="params"`).

- **`CLAUDE.md`**:
  - Module map: `jira_client`/`jira_issues_check` added to the `Drivers` row.
  - Two new bullets under "What is responsible for what" describing the two
    modules (mirroring the existing `github_issues_check.py`/
    `github_conflicts_check.py` bullets).
  - Invariant #40's prose reworded to distinguish the sink allow-list
    (`_ACCEPTED_SINK_KINDS`, unchanged: `none`/`slack`/`github`) from
    `data.source.kind`'s unconstrained fallback path, now noting `jira` is a
    legitimate origin kind there, per the design's finding that
    `effective_sink_kind` needed no code change.

## Scope notes (per plan-01.md, unchanged)

- v1 is ingestion-only — no `JiraReflector` (outbound reflection is an
  explicit follow-up).
- One `repository` param per Process; no `project → repository` map.
- Jira Cloud only (`/rest/api/3`); Server/DC is a later `HttpJiraClient`
  variant behind the same ABC.
- No `processes/jira-ingest.json` shipped by `harness init` — an example
  lives in the ADR (`docs/adr/0020-...md`) instead, same treatment
  `triggers/` gets.
- `_ACCEPTED_SINK_KINDS` in `fs_processes.py` intentionally *not* extended
  with `"jira"` — no driver exists to route a declared Jira sink to yet.

## How to verify

```sh
.venv/bin/pytest -q tests/test_jira_client.py tests/test_jira_issues_check.py
.venv/bin/pytest -q tests/test_cli.py tests/test_architecture.py tests/test_adr_docs.py
.venv/bin/pytest -q   # full suite
```

Full suite: **1377 passed, 1 skipped** (run with `HARNESS_HEAL_REPO`/
`GITHUB_TOKEN` unset from the shell — this environment had both set from a
prior session, which makes 8 *pre-existing, unrelated* `test_cli.py` tests
fail on stderr/served-workflow assertions that don't isolate those env vars;
confirmed unrelated to this change by reproducing the same 8 failures against
this same code with those two vars set, and 0 failures with them unset).

`test_architecture.py` needed no changes: its checks are scoped to
`dispatcher.py`/`consumer.py`/specific orchestration-core modules, none of
which changed; the new `drivers/jira_*` modules are unreachable from
orchestration by construction (they're never imported there) and were
manually confirmed to import only `harness.drivers.jira_client`,
`harness.ports.triggers`, and stdlib — never `cli`.

```json
{"outcome": "done", "summary": "Implemented the Jira issue loader per plan-01/design-01/architecture-01: new drivers/jira_client.py (JiraClient ABC + FakeJiraClient + HttpJiraClient, stdlib urllib, Jira Cloud REST v3) and drivers/jira_issues_check.py (JiraIssuesCheck, structurally mirroring GithubIssuesCheck), wired as a jira-issues action in cli._process_check_factories/_run gated on JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN. Added ADR-0020, updated CLAUDE.md's module map and invariant #40, and wrote tests/test_jira_client.py + tests/test_jira_issues_check.py + five new cli.py process-compile tests (28 new tests total). Corrected one open question from architecture-01.md: HttpGithubClient does not wrap errors in a custom exception type, so HttpJiraClient mirrors its real (unwrapped, 404-swallow-on-remove) behavior instead of inventing one. Full suite passes: 1377 passed, 1 skipped."}
```
