# Development: bound every unbounded network read in `drivers/`

## Summary

Implemented the plan/design/architecture exactly as approved: added a
keyword-only, defaulted `timeout` to `HttpGithubClient`, `HttpJiraClient` and
`post_json` (the Slack webhook transport), threaded it through every
`_opener.open(...)` / `urlopen(...)` call site, updated the existing fake
test doubles to accept the new keyword, and added regression tests proving
(a) the configured value reaches the transport and (b) a genuinely dead peer
now raises `TimeoutError` within a small bound instead of blocking forever.
No port, wiring, or schema changes — matching invariant #1 ("swap a driver,
never its surroundings") and the design's explicit scoping.

## Files changed

**Production code:**
- `src/harness/drivers/github_client.py` — `HttpGithubClient.__init__` gains
  `timeout: float = 30.0` (keyword-only), stored as `self._timeout`; all 13
  `self._opener.open(...)` call sites (both `with ... as response:` and the 3
  bare fire-and-forget forms) now pass `timeout=self._timeout`.
- `src/harness/drivers/jira_client.py` — identical treatment on
  `HttpJiraClient`, both of its 2 call sites (`search_issues`,
  `_update_labels`).
- `src/harness/drivers/slack_sink.py` — `post_json(url, payload, *, timeout:
  float = 30.0)`, passed to `urllib.request.urlopen(request, timeout=timeout)`.
  `SlackWebhookSink` itself is unchanged — it still calls `self._post(url,
  payload)` positionally and inherits the default.

**Tests:**
- `tests/test_github_client.py` — updated all 12 fake `open(self, request)`
  test doubles to `open(self, request, timeout=None)`; `FakeOpener` now
  records `self.timeouts` alongside `self.requests`. Added a `# --- timeout
  ---` section with 4 new tests:
  - `test_http_client_defaults_to_a_30_second_timeout`
  - `test_http_client_configured_timeout_reaches_every_call_site`
  - `test_http_client_raised_timeout_is_not_swallowed`
  - `test_http_client_dead_peer_raises_timeout_within_a_bound_not_forever` —
    a real local TCP server that accepts the connection and never writes
    back (the same "ESTABLISHED, 0 bytes in flight" shape from the bug
    report), driving the *real* `urllib` opener (not a fake) with
    `timeout=0.05`; asserts `TimeoutError` is raised and elapsed time is
    bounded (`< 2.0s`), proving the fix end-to-end rather than just that the
    kwarg is plumbed through.
- `tests/test_jira_client.py` — same treatment: 4 fake doubles updated,
  `FakeOpener.timeouts` added, the same 4 timeout tests (github → jira
  equivalents).
- `tests/test_slack_sink.py` — `urlopen` monkeypatch updated to accept/record
  `timeout`; added `test_post_json_dead_peer_raises_timeout_within_a_bound_not_forever`,
  the same real-socket regression for the Slack webhook transport.
- `tests/test_source_poller.py` — added `TimingOutSource` (a `TaskSource`
  whose `poll()` raises `TimeoutError`) and
  `test_poll_that_raises_timeout_error_returns_false_and_emits_source_error`,
  locking in that `SourcePoller.tick`'s existing `except Exception` isolates
  a `TimeoutError` exactly like any other source failure — a normal,
  retryable tick failure, never a process crash. No new exception-handling
  code was needed or added, confirming the design's central claim.

## Verification

```sh
.venv/bin/pytest -q tests/test_github_client.py tests/test_jira_client.py tests/test_slack_sink.py tests/test_source_poller.py
# 106 passed

.venv/bin/pytest -q tests/test_architecture.py tests/test_hermetic_environment.py
# 31 passed — no invariant/architecture regression, no new env var introduced

.venv/bin/pytest -q
# 1519 passed, 1 skipped (HARNESS_SMOKE_CLAUDE opt-in), 122.81s
```

Also confirmed directly:

```sh
grep -n "_opener.open(\|urlopen(" src/harness/drivers/*.py | grep -v "timeout="
# (no output — every call site in drivers/ now carries an explicit timeout=)
```

Note: this worktree had no `.venv` checked out yet, so one was created
(`python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"`) to run the
suite — this is local environment setup, not a change to the repo.

## How to verify manually

1. `.venv/bin/pytest -q` — full suite green.
2. Read `src/harness/drivers/github_client.py:307-320` for the constructor,
   and `grep -n "timeout=self._timeout" src/harness/drivers/github_client.py`
   to see all 13 call sites carrying it.
3. Run the new dead-peer regression tests individually to see them complete
   in ~1.5s each rather than hanging:
   `.venv/bin/pytest -q tests/test_github_client.py tests/test_jira_client.py tests/test_slack_sink.py -k "dead_peer"`

## Scope notes (confirmed unchanged from plan/design)

- No new environment variable, CLI flag, or JSON config field — the timeout
  is a constructor/function default only.
- No retry/backoff added — a timeout fails that one call once; the existing
  tick/poll cadence handles retry, per the explicit out-of-scope note.
- No new port for `GithubClient`/`JiraClient` — they remain plain ABCs.
- `cli.py`'s ~10 `HttpGithubClient(...)`/`HttpJiraClient(...)` construction
  sites needed no change — all keyword-only, defaulted, backward compatible.

```json
{"outcome": "done", "summary": "Added a keyword-only, defaulted timeout=30.0 to HttpGithubClient (13 call sites), HttpJiraClient (2 sites) and slack_sink.post_json (1 site), so a dead-but-ESTABLISHED GitHub/Jira/Slack connection now raises TimeoutError within a bound instead of blocking the event loop forever. Updated all 16 existing fake-opener test doubles plus the urlopen monkeypatch to accept the new timeout kwarg. Added regression tests per client proving both that the configured value reaches the transport and that a real dead-peer socket (accept, never write) triggers TimeoutError promptly via a sub-100ms timeout — no real hang anywhere in the suite. Added a TimeoutError-specific isolation test on SourcePoller.tick confirming the existing except Exception handling already converts it into a normal retryable failure, with no new exception-handling code required. Full suite: 1519 passed, 1 skipped, no architecture/hermetic-environment regressions."}
```
