# Architecture assessment: bound every unbounded network read in `drivers/`

## Verdict

**Approved as designed, no changes required.** This is a correctly-scoped,
invariant-compliant driver-internals fix. I re-verified the design's factual
claims directly against the current source rather than trusting the artifact
prose, and every one checks out.

## Verification against the actual codebase

Re-ran the counts the design/plan rely on, against `HEAD`, not against the
bug report's (stale) numbers:

- `grep -n "_opener.open(" src/harness/drivers/github_client.py` → exactly
  13 sites, matching the design's revised count (not the bug report's "14").
  Line numbers match: 347, 369, 381, 401, 408, 417, 424, 447, 458, 477, 494,
  516, 532. The three bare (non-context-manager) sites are 401, 408, 516 —
  confirmed by inspection, matching the design's claim.
- `jira_client.py`: exactly 2 sites (178 context-manager, 214 bare),
  `slack_sink.py`: exactly 1 (`urlopen` at line 50 inside `post_json`).
  Confirmed.
- Test doubles: `grep -c "def open(self"` → 12 in `test_github_client.py`, 4
  in `test_jira_client.py` = 16 total, matching the design's count exactly.
  `test_slack_sink.py` monkeypatches `urlopen` with a single-arg lambda,
  confirmed at line 157.
- Exception isolation: confirmed `except Exception` (broad, per-item) exists
  at every site the design names —
  `source_poller.py:61`, `consumer.py:80`, `composite_events.py:23`,
  `pr_watcher.py:49`, `merge_reconciler.py:71`, `issue_reconciler.py:58`.
  The design's central claim — that no new exception-handling code is
  needed because isolation is already structurally present — holds.
- Backward compatibility: `cli.py`'s ~10 `HttpGithubClient(...)`/
  `HttpJiraClient(...)` construction sites are all positional-only, and none
  passes `opener=`. A new keyword-only `timeout: float = 30.0` is therefore
  invisible to every existing caller. (Minor: the design/plan say "12" call
  sites in `cli.py`; actual count is 10. Immaterial — the relevant fact,
  that none pass `opener=` or would collide with a new keyword-only param,
  is correct either way. Not worth a correction pass.)

## Alignment with `CLAUDE.md` invariants

Walked the full invariant list; only a few are in play, and all are
satisfied by construction:

- **Module map / layering.** `github_client.py`, `jira_client.py`,
  `slack_sink.py` are all `drivers/`. The change touches only their
  constructors and internal call sites — no port (`ports/*.py`) changes, no
  `dispatcher.py`/`consumer.py` changes, no `app.py`/`cli.py` wiring changes.
  Nothing here is guarded by `test_architecture.py`'s import-boundary checks
  because nothing here crosses a boundary; this stays entirely inside one
  driver's own file plus its tests, which is exactly what "swap a driver,
  never its surroundings" (invariant #1) permits without a wiring change —
  no port signature is touched, so there is nothing to swap.
- **Invariant #21** (outward projection isolation, `CompositeEventSink`) and
  the general "one bad task/check must not stop the loop" pattern repeated
  across `source_poller.py`/`consumer.py`/the three reconcilers — this is
  the invariant the design leans on to justify adding *no* new
  exception-handling code, and the verification above confirms it's real,
  not assumed.
- **No port changes** means invariants #18/#20/#32/#34 (which govern who may
  import `ports/source.py`, `ports/merge.py`, `ports/issue_state.py`) are
  untouched — `GithubClient`/`JiraClient` remain plain ABCs with no
  dedicated port, exactly as the plan notes, and this change doesn't alter
  that.
- **Hermetic environment test** (`tests/test_hermetic_environment.py`): no
  new env var is introduced (`timeout` is a constructor/function parameter,
  not environment-sourced config), so no update needed there. Confirmed by
  reading the design's non-functional section — correct.
- **No new persisted schema.** Neither `Task.data` nor any queue JSON field
  changes shape. Correctly out of scope per the design.

## Design quality notes

- The three-component treatment (`HttpGithubClient`, `HttpJiraClient`,
  `post_json`) is structurally uniform and mirrors an existing documented
  fact (`jira_client.py`'s own docstring says it mirrors
  `github_client.py`'s shape) rather than inventing a new pattern — good,
  minimal-surprise design.
- Choosing a single client-wide `timeout` (constructor-level) over a
  per-call override is the right call: nothing in the codebase's current
  call patterns needs a different bound for, say, `list_issues` vs.
  `create_pull_request`, and a per-call knob would be unused complexity.
- Explicitly declining to add retry/backoff or a dedicated
  `except TimeoutError` handler is correct scoping — those would either
  duplicate the isolation invariant already in place or introduce new
  behavior (retry cadence) the bug report never asked for.
- The two-tier test design (assert the timeout value reaches the fake
  transport, separately assert a stuck/raising fake surfaces as
  `TimeoutError` promptly) correctly avoids a real 30-second wait while
  still using one narrow, deliberate real-time sub-100ms test per client to
  prove the value is honored end-to-end — consistent with how
  `test_smoke.py`/`test_smoke_git.py` are the codebase's sanctioned
  exceptions to "no real sleeping in tests."

## Risks and mitigations

- **Risk:** the mechanical fake-opener update (16 call sites across two
  test files) is repetitive and easy to get inconsistent (e.g. one fake
  recording `timeout` under the wrong attribute name). *Mitigation:* keep
  the signature update uniform (`def open(self, request, timeout=None):`)
  and prefer extending only the specific fakes that need to assert on the
  value, leaving the rest as accept-and-ignore — this is already how the
  design frames it.
- **Risk:** a currently-passing test elsewhere in the suite constructs one
  of these clients with a custom `opener` whose `.open()` is strict about
  its signature (e.g. via `unittest.mock` with `spec=`) and isn't among the
  16 already identified. *Mitigation:* running the full suite
  (`.venv/bin/pytest -q`) after the change, as the plan's step 7 already
  specifies, catches this immediately — cheap, already planned.
- **Risk (accepted, not mitigated):** 30s is still a long time to hold a
  step/tick/sink call open under a genuinely dead peer — the board recovers
  from a wedge, but a single check tick can still stall up to 30s before
  the loop moves on. This is explicitly the bug report's own suggested
  value and well within the plan's non-functional constraint ("no
  currently-passing integration/smoke test should need its own timeout
  tuned"); tightening it is a legitimate future tuning question, not a
  defect in this design.

## Prerequisites before implementation begins

None outstanding — the design already resolved plan's two open questions
(default value: 30.0s, uniform across all three; FR-6's location: existing
per-call-site isolation reading suffices, no new orchestration test file
needed beyond locking in the guarantee for `TimeoutError` specifically).
Implementation can proceed directly against the design's file list:
`github_client.py`, `jira_client.py`, `slack_sink.py`, plus
`test_github_client.py`, `test_jira_client.py`, `test_slack_sink.py`.

```json
{"outcome": "done", "summary": "Verified design-01.md and plan-01.md against the current source: all site counts (13 github, 2 jira, 1 slack), test-double counts (16 fake openers), and exception-isolation claims (except Exception at source_poller/consumer/composite_events/pr_watcher/merge_reconciler/issue_reconciler) are accurate. No CLAUDE.md invariant is touched — change stays entirely inside drivers/ with no port, wiring, or schema changes, and is backward compatible since no cli.py call site passes opener= or would collide with the new keyword-only timeout param. Design approved as-is; wrote full assessment to architecture-01.md."}
```
