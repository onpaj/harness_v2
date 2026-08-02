# Design: bound every unbounded network read in `drivers/`

No UI section — this is a driver-internals robustness fix with no user-facing
surface. The board's behavior under a *healthy* network is unchanged; the only
externally observable difference is what happens under a *dead* one (a hang
becomes a bounded, retryable failure).

## Component design

Three existing components change shape; nothing new is introduced. Each
keeps its current public method surface — only the constructor gains one
keyword-only parameter, and the `urllib` calls inside gain one keyword
argument each.

### `HttpGithubClient` (`src/harness/drivers/github_client.py`)

**Responsibility, unchanged:** the real `GithubClient` implementation —
translate the ABC's methods into `api.github.com` HTTP calls via an
injectable `opener`.

**Boundary change:** the class now owns a `_timeout: float`, set once at
construction and applied uniformly to every request it issues. No method
gains a per-call timeout override — a single client-wide bound is
sufficient; nothing in the codebase needs a different timeout for, say,
`list_issues` vs `create_pull_request`.

```python
def __init__(
    self,
    token: str,
    *,
    api: str = "https://api.github.com",
    opener: Any = None,
    timeout: float = 30.0,
) -> None:
    self._token = token
    self._api = api.rstrip("/")
    self._opener = opener or urllib.request.build_opener()
    self._timeout = timeout
```

Every one of the 13 `self._opener.open(request` call sites (lines 347, 369,
381, 401, 408, 417, 424, 447, 458, 477, 494, 516, 532 — confirmed by reading
the current file, superseding the bug report's stale "14 sites" count) becomes
`self._opener.open(request, timeout=self._timeout)`, including the three
bare (non-context-manager) calls at 401 (`add_label`), 408 (`remove_label`,
inside its own `try`), and 516 (`update_branch`, inside its own `try`). The
`with ... as response:` sites and the bare fire-and-forget sites take the
identical keyword — `open()`'s signature doesn't distinguish them.

No other line in the method bodies changes: the timeout is a transport
concern, orthogonal to the existing 404-swallowing and 422-swallowing
`try/except urllib.error.HTTPError` blocks already in `get_issue_state`,
`get_issue`, `remove_label` and `update_branch`. A `socket.timeout` /
`TimeoutError` is not an `HTTPError` (it never reached a response), so it
passes straight through those `except` clauses unmodified and propagates to
the caller exactly like any other non-HTTP-error `OSError` already would.

### `HttpJiraClient` (`src/harness/drivers/jira_client.py`)

Structurally identical treatment — the module's own docstring already
states it "mirrors `github_client.py`'s shape exactly," so the design
mirrors it here too:

```python
def __init__(
    self,
    base_url: str,
    email: str,
    api_token: str,
    *,
    opener: Any = None,
    timeout: float = 30.0,
) -> None:
    ...
    self._timeout = timeout
```

Two call sites: `search_issues` (line 178, context manager) and
`_update_labels` (line 214, bare — the shared implementation both
`add_label` and `remove_label` funnel through). `remove_label`'s
404-swallowing `try/except` around `_update_labels` is untouched for the
same reason as above.

### `post_json` / `SlackWebhookSink` (`src/harness/drivers/slack_sink.py`)

`post_json` is a free function, not a class method, so the timeout is a
function parameter rather than stored state:

```python
def post_json(url: str, payload: dict, *, timeout: float = 30.0) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    urllib.request.urlopen(request, timeout=timeout)
```

`SlackWebhookSink` itself is untouched beyond this — it calls
`self._post(self._webhook_url, {...})` with two positional args today and
will keep doing so, since `timeout` defaults. It does not need its own
`timeout` constructor parameter: `SlackWebhookSink` is a best-effort,
outbound-only sink (invariant #21 — `report_progress`/`finish` isolation is
already handled by `CompositeEventSink.emit`'s per-sink `except Exception`),
so there is no caller that needs to configure it differently from the
default. If a future change wants that knob, it composes cleanly as
`SlackWebhookSink(webhook_url=..., post=lambda u, p: post_json(u, p,
timeout=5.0))` — no signature change required today.

### Interaction with existing exception isolation (no new code)

This is the component-design decision worth stating explicitly, because
it's what keeps this change small: **no new try/except is added anywhere**.
Reading the call graph confirms every path from these three clients already
terminates in a broad, per-item `except Exception`:

- `HttpGithubClient`/`HttpJiraClient` methods are called directly inside
  `GithubIssuesCheck.evaluate()` / `GithubConflictsCheck.evaluate()` /
  `JiraIssuesCheck.evaluate()`, which run inside `ScheduledTrigger.poll()`
  (`drivers/scheduled_trigger.py:89`, no try/except of its own — deliberately
  thin), which is called from `SourcePoller.tick()`
  (`source_poller.py:61`, `except Exception as error`).
- The same clients back `GithubTaskSource`/`GithubLabelReflector`'s
  `report_progress`/`finish`, reached through `SourceReflectorSink`, itself
  one sink among several inside `CompositeEventSink.emit`
  (`composite_events.py:23`, `except Exception` per sink).
- `PrWatcher`, `MergeReconciler`, `IssueReconciler` each wrap their
  per-task checker calls in their own `except Exception`
  (`pr_watcher.py:49`, `merge_reconciler.py:71`, `issue_reconciler.py:58`).
- A step's own use of `IssueTracker`/`GithubForge` (which also go through
  `HttpGithubClient`) executes inside `Consumer._deliver`'s
  `except Exception` (`consumer.py:80`), which the dispatcher already turns
  into a normal `failed` outcome per invariant #3.

So a `socket.timeout`/`TimeoutError` raised where a call used to hang
forever lands in one of these existing handlers and becomes a normal,
already-logged, already-retryable failure of *that one tick/step/sink* —
never a process crash, and never a wedge. The design intentionally does not
add a dedicated `except TimeoutError` anywhere: doing so would either
duplicate what invariant #21/#3 already guarantee, or (worse) special-case
timeouts for retry/backoff behavior that's explicitly out of scope. The
acceptance criterion "a timeout must fail that one call, not the process"
is satisfied by architecture already in place; this design's job is only to
prove it with a test (see below), not to build it.

## Interfaces

Three constructor/function signatures change, all backward compatible
(new parameter is keyword-only with a default, so every existing call site
in `cli.py` — 12 of them, none passing `opener=` today — keeps compiling
unmodified):

| Component | Before | After |
|---|---|---|
| `HttpGithubClient.__init__` | `(self, token, *, api=..., opener=None)` | `(self, token, *, api=..., opener=None, timeout: float = 30.0)` |
| `HttpJiraClient.__init__` | `(self, base_url, email, api_token, *, opener=None)` | `(self, base_url, email, api_token, *, opener=None, timeout: float = 30.0)` |
| `post_json` | `(url, payload)` | `(url, payload, *, timeout: float = 30.0)` |

The injected `opener`'s implicit contract (a duck-typed protocol, not a
formal one — no ABC exists for it, matching the codebase's existing style)
gains one requirement: `.open(request)` becomes `.open(request, timeout=...)`.
Every real `urllib.request.OpenerDirector` already accepts this keyword, so
only the **test doubles** need updating (see below) — production code paths
need no change beyond passing the argument.

No change to any of the three ABCs (`GithubClient`, `JiraClient`,
`TaskSource`) or to any caller's method signatures — `list_issues`,
`add_label`, `search_issues`, `report_progress`, etc. all keep their exact
current shape. This stays entirely inside the three driver files plus their
tests; no port changes, no `app.py`/`cli.py` wiring changes.

## Data schemas

None change. No `Task`/`AgentSpec`/queue JSON field is added, read, or
written by this change — `timeout` is a transport-layer number that never
touches a persisted document, matching the plan's explicit non-functional
requirement ("no new configuration surface"). The only "schema" this design
touches is the **in-memory test-double protocol** described above
(`opener.open(request, timeout=...)`), which is not a data schema in the
persisted sense.

## Test-double and regression-coverage design

Sixteen existing fake `opener`/`Opener` classes (12 in
`tests/test_github_client.py`, 4 in `tests/test_jira_client.py`, one
`monkeypatch.setattr(urllib.request, "urlopen", lambda request: ...)` in
`tests/test_slack_sink.py`) currently define `open(self, request)` with no
`timeout` parameter. Once the call sites pass `timeout=`, every one of
these breaks with `TypeError: open() got an unexpected keyword argument
'timeout'` unless updated to accept it — this is a required, in-scope part
of the change, not incidental breakage. The shape of that update is
mechanical and uniform: `def open(self, request, timeout=None):` (accept
and ignore, or record it when a test specifically wants to assert on it),
and the `urlopen` lambda becomes `lambda request, timeout=None: ...`.

Two orthogonal things need proving per client (github, jira; slack
optionally, since it shares the identical stdlib gap but is lower-stakes as
a best-effort sink), and the design keeps them as two distinct assertions
rather than one blended test:

1. **The configured value actually reaches the transport.** A fake opener
   records the `timeout` kwarg it was called with; construct the client
   with an explicit non-default `timeout=`, make one call, assert the
   recorded value matches. This proves the plumbing without any real I/O
   or waiting.
2. **A stuck peer surfaces as a prompt, bounded `TimeoutError`.** A fake
   opener whose `open()` raises `TimeoutError`/`socket.timeout` directly (no
   real sleep needed — simulating what a real dead socket would eventually
   raise) proves the exception type a caller must be prepared to catch;
   optionally, one test per client uses a genuinely sleeping fake against a
   sub-100ms configured timeout (a narrow, deliberate real-time exception,
   consistent with how `test_smoke.py`/`test_smoke_git.py` already use real
   `asyncio.sleep`) to prove the *value* is honored end-to-end by the real
   `urllib` machinery, not just plumbed through as an inert kwarg. This
   second variant is the one genuine real-time test in the whole change and
   should stay minimal (one per client, not a matrix).

A third test, placed wherever `SourcePoller`'s existing test coverage
already exercises a raising source (or extended if it doesn't quite fit),
proves the isolation claim from the component-design section above: a
source/check that raises `TimeoutError` is caught by `SourcePoller.tick`
and the loop proceeds to its next iteration rather than propagating. Given
the call-graph reading above, this is expected to already pass against the
current code — the test's value is locking the guarantee in for this
specific exception type, not adding new isolation logic.

## Non-functional constraints carried into implementation

- Default `timeout=30.0` for both HTTP clients (and for `post_json`) — well
  above any real API response time, so no currently-passing integration or
  smoke test should need tuning.
- No new environment variable, CLI flag, or JSON config field —
  `tests/test_hermetic_environment.py`'s enumeration of
  `src/harness`-read config variables needs no update.
- No new production dependency — stays on stdlib `urllib`, matching every
  module docstring's existing "don't reach for `requests`/`httpx`" note.
- Out of scope, confirmed unchanged by this design: retry/backoff on
  timeout, a dedicated port for `GithubClient`/`JiraClient`, any change to
  `HttpGithubClient`/`HttpJiraClient`'s public method surface beyond the
  constructor, and the unrelated stale-template 500 mentioned in the bug
  report only as a distinguishing signal.
