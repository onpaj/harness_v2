# Review — attempt 01

## Scope check against the finding

- Acceptance criterion 1 (regression test proving a hung/never-responding endpoint raises a
  timeout within a bound instead of blocking forever): met by
  `test_http_client_dead_peer_raises_timeout_within_a_bound_not_forever` in
  `tests/test_github_client.py` — a real TCP server that accepts and never writes, client
  configured `timeout=0.05`, asserts `TimeoutError` raised and `elapsed < 2.0` (vs. the
  observed ~34h wedge). This is a real-socket test, not just a mock asserting a kwarg was
  passed — it proves `urllib`'s timeout machinery is actually wired through.
- Acceptance criterion 2 (no `_opener.open(`/`urlopen(` call in `drivers/` left without an
  explicit timeout): verified directly —
  `grep -rn "_opener.open(\|urlopen(" src/harness/drivers/*.py | grep -v "timeout="` returns
  nothing. All 13 `github_client.py` sites (including the 4 bare, non-context-manager calls
  at `add_label`, `remove_label`, `update_branch`, `list_pull_requests`'s detail fetch), both
  `jira_client.py` sites, and the 1 `slack_sink.py` `urlopen` are covered — 16/16.
- `timeout` is a keyword-only constructor param defaulted to `30.0` on `HttpGithubClient` and
  `HttpJiraClient`, and a keyword-only param on `slack_sink.post_json` — matches the shape
  design-01.md/architecture-01.md committed to, no port or wiring change.

## Correctness

- `test_http_client_raised_timeout_is_not_swallowed` confirms `TimeoutError` propagates out of
  the client uncaught (not converted into a different exception or eaten).
- `test_poll_that_raises_timeout_error_returns_false_and_emits_source_error` in
  `tests/test_source_poller.py` confirms `SourcePoller.tick()`'s existing `except Exception`
  already treats a `TimeoutError` as a normal retryable tick failure (emits `source_error`,
  returns `False`) — no new exception-handling code was needed or added, matching the design's
  call-graph analysis. This directly closes the "must fail that one call, not the process"
  requirement.
- All 16 existing fake-opener test doubles were updated to accept `timeout=None` — verified via
  diff, no stale double left with the old 1-arg signature that would otherwise raise
  `TypeError` and mask the fix.

## Verification run

`.venv/bin/pytest -q` → **1519 passed, 1 skipped**, no regressions, matches development-01.md's
claim.

## Architecture / invariants

Transport-only change confined to `drivers/`; no port signature, schema, or wiring touched.
Nothing here interacts with any of the numbered CLAUDE.md invariants.

## Verdict

Complete, correctly scoped, and verified — no changes requested.
