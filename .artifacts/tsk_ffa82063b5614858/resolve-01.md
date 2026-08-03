# Resolve merge conflict on PR #152

## Conflict

Merging `origin/main` into this task's branch produced exactly one conflicted
file: `tests/test_github_client.py`.

Both sides touched the same test,
`test_http_search_issue_by_marker_returns_none_when_no_body_matches`:

- **HEAD** (this branch, the network-timeout work) had rewritten the file's
  tail to add a new `--- timeout ---` section (four tests covering the
  `HttpGithubClient` default/configurable timeout, an unswallowed
  `TimeoutError`, and a real dead-socket regression test), but in doing so
  carried forward a stale call to `search_issue_by_marker("o/r", "<!--
  harness-heal:tsk_9 -->")` with no `label` argument.
- **origin/main** had independently changed `search_issue_by_marker`'s
  signature to require `label` as a mandatory keyword-only argument (see
  `src/harness/drivers/github_client.py:176,387,682`), and updated this same
  test to pass `label=SELF_HEAL_LABEL` — but has none of the new timeout
  tests.

## Resolution

Kept both changes, combined correctly:

- The test body now calls `search_issue_by_marker(..., label=SELF_HEAL_LABEL)`,
  matching origin/main's updated signature (HEAD's version would have raised
  `TypeError: missing required keyword-only argument`).
- HEAD's four new timeout tests (`test_http_client_defaults_to_a_30_second_timeout`,
  `test_http_client_configured_timeout_reaches_every_call_site`,
  `test_http_client_raised_timeout_is_not_swallowed`,
  `test_http_client_dead_peer_raises_timeout_within_a_bound_not_forever`) are
  preserved unchanged, appended after the fixed assertion.

All conflict markers removed; no other files had conflicts.

## Verification

- `tests/test_github_client.py`: 63 passed.
- Full suite (`pytest -q`): **1769 passed, 1 skipped**, no failures, no
  regressions.

## Outcome

`done`
