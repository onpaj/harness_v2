# Review: raise the per-agent timeout default from 1800s to 5400s

## Conformance to the finding / spec

The finding scoped this strictly as a configuration/step-granularity change,
naming two levers (raise the timeout, or decompose the step) with no
prescribed value, and required no orchestration code to be touched.

The diff (commit `593f655`) does exactly this:

- `src/harness/cli.py:2139` — `--agent-timeout` argparse default `1800.0` → `5400.0`
- `src/harness/app.py:355` — `build(..., agent_timeout: float = ...)` default `1800.0` → `5400.0`
- `src/harness/behaviors/agent.py:37` — `ClaudeCliBehavior.__init__(..., timeout: float = ...)` default `1800.0` → `5400.0`

All three sites moved together, matching design-01.md's table exactly. Chose
the "raise the timeout" lever over step decomposition, which the finding
explicitly allows ("either or a combination... no specific new timeout value
or decomposition is prescribed"). `AgentSpec.timeout`'s per-step override
mechanism (`agents/<step>.json`) is untouched, so an operator can still tune
a single repository's `development` step independently — the escape hatch
design-01.md relies on is intact.

Verified against the live tree: `dispatcher.py`, `consumer.py`, `router.py`
and every behavior's decision logic are untouched — `git diff` confirms only
`app.py`, `behaviors/agent.py`, `cli.py` and the two test files changed
(besides the artifact file). No architecture-guarding test
(`test_architecture.py`) references timeout, and the full run of it still
passes (27/27).

## Completeness

- Both test sites the design called out were updated and now assert
  `5400.0`: `tests/test_cli.py::test_run_defaults_agent_timeout_to_5400` (renamed
  from `..._to_1800`) and `tests/test_agent_behavior.py`'s
  `call["timeout"] == 5400.0` assertion.
- The two other `1800`-valued test sites (`test_scheduled_trigger.py`'s
  hour-bucket comment, `test_triggers_port.py`'s `parse_interval("30m")`)
  are confirmed unrelated (cron/interval bucketing and duration parsing) and
  correctly left alone — grep confirms no other in-scope site was missed.
- `CHANGELOG.md` was correctly left untouched: `git log -- CHANGELOG.md`
  shows every entry comes from an automated `chore(release):` commit, never
  a feature/fix PR, exactly as the development step's summary claims.
- Full suite: `1509 passed, 1 skipped` (re-ran independently, matches the
  development step's report).

## Correctness

No logic changes — three numeric defaults moved, resolution order in
`app.py`'s `behavior_for` (`spec.timeout if spec.timeout is not None else
agent_timeout`) is untouched. No new error handling, concurrency, or security
surface is introduced by a default-value change. `AgentSpec`'s `None` =
"inherit global default" semantics are unaffected — only the value the
default resolves to changes.

## Non-blocking observation

The development step's own commit message states it "used a fix:-prefixed
commit instead" of hand-editing `CHANGELOG.md` — but commit `593f655`'s
subject line is not conventional-commit-prefixed. This has no functional
effect: past releases (e.g. `56a50b8 feat: ... (#30)`) show this repo's `main`
receives one squash-merged commit per PR, authored by the human merging via
GitHub's UI using the PR title — not any individual step's commit inside the
task branch. Landing (a later step in this task, not yet run) derives the PR
title from `task.data`, not from this commit's text, so the release will
still hinge on that PR title being conventional-commit-compliant, not on this
commit's own wording. Worth the landing step keeping in mind; not a defect in
this diff.

```json
{"outcome": "done", "summary": "Verified the three timeout-default sites (cli.py, app.py, behaviors/agent.py) all moved together to 5400.0s exactly as designed, no orchestration code touched, both required test assertions updated, CHANGELOG.md correctly left alone (chore(release)-only), and the unrelated 1800-valued test sites correctly left out of scope. Full suite re-run green: 1509 passed, 1 skipped. Approved."}
```
