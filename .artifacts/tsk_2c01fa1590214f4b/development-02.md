# Development (revision 2): Jira issue loader (`jira-issues` action)

Addresses `review-01.md`'s single `request_changes` finding: the
`project`+`label` convenience-form JQL silently dropped the status filter
(`AND statusCategory != Done`) that `architecture-01.md`'s "Prerequisites
before implementation begins" section flagged as a must-resolve-before-coding
prerequisite. Everything else in `development-01.md` was verified sound by
the review and is unchanged.

## What changed

- **`src/harness/drivers/jira_issues_check.py:42-45`** — the compiled
  convenience-form JQL is now
  `f'project = {project} AND labels = "{label}" AND statusCategory != Done'`
  (previously missing the status clause). An explicit `jql` override is
  still used verbatim, with no implicit clause appended — an operator
  handing a raw JQL string owns its correctness completely, mirroring how
  `github-issues`'s `label` param carries no hidden second filter.
- **Module docstring** (same file) now documents this: why the clause is
  there (the Jira-side equivalent of GitHub's `list_issues(...,
  state="open")` filter — Jira labels routinely outlive workflow status, so
  without it a resolved/closed issue keeps getting re-ingested every tick
  for as long as it carries the select label) and that `jql` overrides are
  exempt.
- **`docs/adr/0020-jira-second-ingestion-source.md`** — the "Selection: JQL
  wins over `project`+`label`" bullet now records the status-filter decision
  and its reasoning explicitly (previously silent), so the prerequisite the
  architecture step raised has a durable, reasoned resolution rather than
  living only in a code comment.
- **`tests/test_jira_issues_check.py`** — two new tests asserting the built
  `_jql` string directly:
  - `test_convenience_form_jql_excludes_done_issues` — `project="PROJ"`
    (default `label`) compiles to
    `'project = PROJ AND labels = "harness-todo" AND statusCategory != Done'`.
  - `test_explicit_jql_is_not_augmented_with_a_status_filter` — an explicit
    `jql=` is stored unchanged, no clause appended.

No other file changed. `FakeJiraClient._label_predicate` (in
`jira_client.py`) extracts the quoted label via `str.find`/`str.find`, so the
appended `AND statusCategory != Done` clause doesn't affect any existing
label-matching test — verified by re-running the full suite, not just
reasoned about.

## Why this resolves the review's finding (not a new decision)

The review offered two acceptable fixes: add the filter with a test on the
built JQL string, **or** record an explicit reasoned "no" in ADR-0020. This
revision does the former (the architecture step's own recommendation) and
also records the reasoning in the ADR for durability — belt and suspenders,
since the ADR is where a future reader would look for "why does this JQL
have that clause" without reading the driver source.

## How to verify

```sh
.venv/bin/pytest -q tests/test_jira_issues_check.py
.venv/bin/pytest -q   # full suite
```

Full suite (run with `GITHUB_TOKEN`/`HARNESS_HEAL_REPO` unset, matching how
`development-01.md`/`review-01.md` isolated the 8 pre-existing
env-dependent `test_cli.py` failures): **1379 passed, 1 skipped** — 2 more
passing than `development-01.md`'s 1377, exactly the 2 new JQL-string tests
added here; no other test count changed.

```json
{"outcome": "done", "summary": "Addressed review-01.md's request_changes: added the recommended status filter (AND statusCategory != Done) to JiraIssuesCheck's project+label convenience-form JQL (jira_issues_check.py:42-45), left an explicit jql= override unaugmented, documented the reasoning in both the module docstring and ADR-0020's 'Selection: JQL wins' bullet, and added two tests asserting the built _jql string (with and without the clause). Full suite: 1379 passed, 1 skipped (2 more than development-01's 1377, matching the 2 new tests; nothing else regressed)."}
```
