# Plan: bound every unbounded network read in `drivers/`

## Summary

`HttpGithubClient` (`src/harness/drivers/github_client.py`) opens every HTTP
request through `urllib.request.build_opener()` without ever passing a
`timeout`, so a dead-but-still-`ESTABLISHED` TCP connection (a silent NAT/idle
drop — routine on this always-on home-router Mac) blocks the read forever.
That blocking `http.client` read executes synchronously on the asyncio event
loop thread, so once it wedges, uvicorn stops servicing *any* HTTP request —
the whole board hangs with no HTTP status, no 500, no refused connection, and
0.0% CPU, exactly as observed on 2026-08-02 (wedged 2026-07-31 16:08 for
~34h). `HttpJiraClient` and `SlackWebhookSink.post_json` share the identical
gap. The fix is to give every one of these calls an explicit, finite
`timeout` so a dead connection surfaces as a normal, catchable
`TimeoutError`/`socket.timeout` instead of a permanent stall.

## Context

This is a harness bug (robustness gap), not a feature. Scope is deliberately
small: add timeouts, prove they work, don't refactor the clients' shape or
add new configuration surface. `CLAUDE.md`'s module map lists these three
drivers as having "no dedicated port" (`GithubClient`/`JiraClient` are plain
ABCs, `SlackWebhookSink` implements `TaskSource` directly) — none of them sit
behind a port whose signature this change would need to touch, so this stays
inside `drivers/` and its tests.

## Findings from reading the current code (grounding, not assumption)

- `github_client.py` has **13** `self._opener.open(request` call sites (not
  14 as the raw report estimated — the report's own line numbers, e.g. "492,
  499, 654", don't match this file's current 546 lines; the file has grown/
  shrunk since whatever revision the report was written against). Exact
  current sites: lines 347, 369, 381, 401, 408, 417, 424, 447, 458, 477, 494,
  516, 532. Three of those (401, 408, 516) are the bare, non-context-manager
  form the report specifically flagged.
- `jira_client.py` has **2** sites: line 178 (context manager), line 214
  (bare) — `HttpJiraClient` mirrors `HttpGithubClient`'s shape exactly, per
  its own module docstring.
- `slack_sink.py` has **1** site: line 50, inside the free function
  `post_json(url, payload)` (the default `post` callable
  `SlackWebhookSink` uses).
- Neither `HttpGithubClient` nor `HttpJiraClient` is constructed with an
  `opener=` override anywhere in `cli.py` (12 call sites, e.g.
  `HttpGithubClient(token)`, `HttpJiraClient(base_url, email, api_token)`) —
  all positional/required args only. Adding a keyword-only `timeout: float =
  30.0` to each constructor is therefore invisible to every existing caller;
  no wiring in `cli.py`/`app.py` needs to change.
- Existing unit tests construct these clients with fake in-memory `opener`
  objects whose `.open(self, request)` method takes **no** `timeout`
  parameter (16 such fake classes across `tests/test_github_client.py` and
  `tests/test_jira_client.py`). Passing `timeout=` at the call site as
  planned will raise `TypeError: open() got an unexpected keyword argument
  'timeout'` against every one of those fakes unless they're updated too —
  this is in scope, not incidental.
  `tests/test_slack_sink.py` similarly monkeypatches
  `urllib.request.urlopen` with a single-argument lambda that needs the same
  treatment.
- Every place in the codebase that actually invokes one of these clients
  already isolates exceptions broadly: `SourcePoller.tick` (`except
  Exception`), `Consumer._deliver` (`except Exception` → writes `failed`),
  `PrWatcher`/`MergeReconciler`/`IssueReconciler` tick loops (`except
  Exception` per item), and `CompositeEventSink.emit` (`except Exception` per
  sink, which is what protects `SourceReflectorSink`'s calls into
  `GithubLabelReflector.report_progress`/`finish`). So a `TimeoutError`
  raised where today a block would hang forever is **already** structurally
  a retryable, isolated step/tick failure, not a process crash — the
  acceptance criterion about "fail that one call, not the process" is
  satisfied by existing architecture once the timeout itself exists. This
  needs confirming with a small targeted test, not new exception-handling
  code.

## Functional requirements

**FR-1 — `HttpGithubClient` takes an injectable, defaulted timeout and
applies it to every request.**
Add `timeout: float = 30.0` (keyword-only) to `HttpGithubClient.__init__`,
store it as `self._timeout`, and pass `timeout=self._timeout` at all 13
`self._opener.open(request, ...)` call sites (both the `with ... as
response:` form and the 3 bare fire-and-forget calls).
Acceptance:
  - `grep -c "_opener.open(" src/harness/drivers/github_client.py` sites all
    carry a `timeout=` argument (verified by a test reading the source, or
    simply by every calling test still passing).
  - A test constructs `HttpGithubClient("tok", opener=<fake>, timeout=<X>)`
    and asserts the fake sees `timeout=X` on the request it's asked to open.

**FR-2 — `HttpJiraClient` gets the identical treatment.**
Same `timeout: float = 30.0` constructor param, applied at both of its 2
call sites (`search_issues`'s context-manager call, `_update_labels`'s bare
call — which is also what `add_label`/`remove_label` funnel through).
Acceptance: same shape as FR-1, against `HttpJiraClient`.

**FR-3 — `slack_sink.post_json` never calls `urlopen` unbounded.**
Add a `timeout: float = 30.0` parameter to `post_json(url, payload, *,
timeout=30.0)` and pass it to `urllib.request.urlopen(request,
timeout=timeout)`.
Acceptance: a test monkeypatching `urllib.request.urlopen` asserts it's
invoked with a `timeout` keyword.

**FR-4 — a real hang converts to a prompt, catchable timeout — proven, not
assumed.**
At least one regression test per client (`github_client`, `jira_client`, and
ideally `slack_sink`) exercises a fake opener/transport that simulates a
peer that accepted the connection but never writes back (a stub whose
`.open()`/`urlopen()` raises `socket.timeout`/`TimeoutError` itself, or
blocks for longer than a deliberately tiny configured `timeout` — e.g.
`timeout=0.01`, one of the CLAUDE.md-sanctioned narrow real-time exceptions
if a genuine block is needed to prove the value is honoured rather than
ignored) and asserts:
  - the call raises `TimeoutError` (`socket.timeout` is a subclass) within
    a bound proportional to the configured timeout, not indefinitely;
  - the exception isn't swallowed or re-wrapped into something a caller
    can't recognize as transient.
This must not be a real 30-second wait; either assert on the `timeout=`
value handed to a fake opener (no real waiting), or use a sub-100ms
configured timeout against a fake that genuinely sleeps past it.

**FR-5 — existing test doubles keep working.**
Update all 16 fake `opener`/`Opener` classes in `tests/test_github_client.py`
and `tests/test_jira_client.py` (`def open(self, request)` →
accept/ignore a `timeout` keyword) and the `urlopen` monkeypatch in
`tests/test_slack_sink.py`, so the new keyword argument at each call site
doesn't break any currently-passing test.
Acceptance: `.venv/bin/pytest -q tests/test_github_client.py
tests/test_jira_client.py tests/test_slack_sink.py` passes unmodified in
intent (same assertions, now timeout-aware fakes).

**FR-6 — a timeout is a normal step/tick failure, confirmed.**
Add or extend one test at the level where a client call actually happens
inside the orchestration (e.g. `GithubIssuesCheck.evaluate()` or
`SourcePoller.tick()` with a fake source/client that raises
`TimeoutError`) proving the surrounding loop catches it, logs/emits it, and
continues — rather than propagating out and killing the process. Given the
finding above, this is expected to already pass; the test exists to lock in
that guarantee for this specific exception type, not to add new
exception-handling code.

## Non-functional requirements

- **No behavior change for the healthy path.** A default of 30s is well
  above any real GitHub/Jira/Slack response time; no currently-passing
  integration/smoke test should need its own timeout tuned.
- **No new configuration surface.** No new environment variable, CLI flag,
  or `repos.json`/process JSON field. `CLAUDE.md`'s hermetic-environment
  test (`tests/test_hermetic_environment.py`) enumerates every config
  variable `src/harness` reads from the environment — this change adds
  none, so that test needs no update. If a later increment wants the
  timeout operator-configurable, that's a separate, deliberate follow-up.
- **No new production dependency.** Stays on stdlib `urllib`, matching every
  design note already in these three files.

## Data model

None — this is a driver-internals fix. No `Task`/`AgentSpec`/queue schema
changes; no new fields on any persisted JSON.

## Interfaces

None user-facing. Internal constructor signatures only:
- `HttpGithubClient.__init__(self, token, *, api=..., opener=None, timeout=30.0)`
- `HttpJiraClient.__init__(self, base_url, email, api_token, *, opener=None, timeout=30.0)`
- `post_json(url, payload, *, timeout=30.0)`

## Dependencies and scope

**In scope:**
- `src/harness/drivers/github_client.py` (13 sites)
- `src/harness/drivers/jira_client.py` (2 sites)
- `src/harness/drivers/slack_sink.py` (1 site)
- `tests/test_github_client.py`, `tests/test_jira_client.py`,
  `tests/test_slack_sink.py` (fake-opener/monkeypatch signature updates +
  new regression tests)
- Possibly one existing orchestration-level test file (`SourcePoller` or a
  `Check` driver's test) extended per FR-6, if no existing coverage already
  proves the isolation.

**Explicitly out of scope:**
- Making the timeout operator-configurable (env var/CLI flag/process JSON).
- Retry/backoff logic on timeout — a timeout should fail that one call
  once; retry cadence is whatever the existing tick/poll loop already does.
- `GithubClient`/`JiraClient` gaining a dedicated port — they stay plain
  ABCs, per current architecture.
- Any change to `HttpJiraClient`'s or `HttpGithubClient`'s public method
  surface beyond the constructor's new keyword-only `timeout`.
- Fixing the unrelated stale-template 500 (`No filter named
  'retired_failure'`) mentioned only as a distinguishing signal in the bug
  report — different root cause, different task.

## Rough plan

1. **`github_client.py`**: add `timeout` param to `HttpGithubClient.__init__`;
   thread `timeout=self._timeout` through all 13 `_opener.open(...)` calls.
2. **`jira_client.py`**: same treatment, both of its 2 sites.
3. **`slack_sink.py`**: add `timeout` param to `post_json`, pass it to
   `urlopen`.
4. **Test doubles**: update the 16 fake opener classes in
   `test_github_client.py`/`test_jira_client.py` to tolerate a `timeout`
   kwarg; update the `urlopen` monkeypatch in `test_slack_sink.py`.
5. **New regression tests**: one per client proving (a) the configured
   timeout value reaches the underlying call, and (b) a stubbed
   hang/timeout surfaces as `TimeoutError` promptly, not a real multi-second
   wait.
6. **Isolation check (FR-6)**: confirm (with a test, extending existing
   coverage if any already fits) that a `TimeoutError` from one of these
   clients is caught at its existing call site (`SourcePoller.tick`,
   `Consumer._deliver`, a `Check.evaluate`, or `CompositeEventSink.emit`)
   rather than propagating to the process.
7. Run the full suite (`.venv/bin/pytest -q`) to confirm nothing else
   assumed the old no-timeout `.open()`/`urlopen()` signature.

## Open questions

- **Default timeout value.** The bug report suggests 30.0s; I've used that
  uniformly for GitHub, Jira and Slack. If the operator wants a shorter
  bound for the Slack webhook (a best-effort notification, not on the
  critical orchestration path) that's a one-line tweak — flagging it here
  rather than guessing a different number without justification.
- **FR-6's exact test location** depends on what, if anything, already
  covers "a source/check call raises and the loop survives" — the
  architecture step should confirm whether `SourcePoller`/`Consumer` already
  have such a test to extend, or whether a new one is needed against
  `GithubIssuesCheck`/`GithubConflictsCheck` specifically (those are the
  driver classes that call `HttpGithubClient` methods directly inside a
  `Check.evaluate()`, per the module map).
- **Task report's "14 sites" vs. actual 13.** Treating the actual, current
  file as ground truth (13 in `github_client.py`) rather than the report's
  count — noting the discrepancy here so the discrepancy is visible rather
  than silently "fixed" to match a stale number.
