# Development — attempt 02 (revision: fix verify-01 failures)

Attempt 01 implemented the token-usage feature in full (commit `13c29f9`) and
`development-01.md` describes that work — it is unchanged by this attempt.
This revision addresses `verify-01.md`, which reported 8 failures in
`tests/test_cli.py` plus an `OSError` (port already in use) cascading from one
of them.

## Diagnosis

None of the 8 failures are related to the token-usage feature. Every one of
`src/harness/{ports/agent,drivers/claude_cli,models,consumer,behaviors/agent,
ports/board,projection}.py` is untouched by this revision, and the token-usage
tests (`test_claude_cli.py`, `test_agent_ports.py`, `test_agent_behavior.py`,
`test_models.py`, `test_consumer.py`, `test_projection.py`, `test_board_port.py`)
all still passed in attempt 01's run and continue to pass here.

The failures are all in `tests/test_cli.py`, and all trace to one cause: this
machine's shell has `HARNESS_HEAL_REPO=onpaj/harness_v2` and `GITHUB_TOKEN=...`
set in its ambient environment (confirmed via `env`). `cli.py`'s `_run` reads
`heal_repo = args.heal_repo or os.environ.get("HARNESS_HEAL_REPO")`
unconditionally (`src/harness/cli.py:1628`), so any test that omits
`--heal-repo` expecting "no heal repo wired" instead picks up the ambient env
var — served workflows silently gain `heal`, an `open-issue` finisher gets
registered, and a stderr warning gets printed that the affected tests didn't
expect. One of the 8 (`test_run_all_workflows_without_heal_repo_fails_fast_on_the_heal_workflow`)
consequently stopped fail-fasting and reached a real `serve()`/`uvicorn` call,
which then collided on a bound port — that `OSError` is a downstream symptom
of the same root cause, not a ninth, independent bug.

Confirmed by reproduction:
- `.venv/bin/python -m pytest -q tests/test_cli.py -k "<the 7 non-OSError tests>"` fails
  identically to `verify-01.md` under the ambient env.
- The same run with `env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN` passes.
- Attempt 01 already noted (in `development-01.md`) that this reproduces
  identically against the pre-feature tree, i.e. it predates this issue's
  changes entirely.

Since the verify step runs in this same ambient environment, the tests
themselves need to be immune to it — not just documented as "run with `env -u`".
The established pattern already in `test_cli.py` is per-test
`monkeypatch.delenv("GITHUB_TOKEN", raising=False)` for tests whose assertions
depend on GitHub-token absence; the fix applies the same treatment for
`HARNESS_HEAL_REPO`.

## What changed

**`tests/test_cli.py`** — added `monkeypatch.delenv("HARNESS_HEAL_REPO",
raising=False)` (and, where the assertion also depends on it,
`monkeypatch.delenv("GITHUB_TOKEN", raising=False)`) to the 8 affected tests,
so each deterministically tests the "no heal repo" / "no token" scenario it
names, regardless of the ambient shell:

- `test_run_registers_label_issue_finisher_only_with_a_token`
- `test_run_without_heal_repo_wires_no_open_issue_finisher`
- `test_run_serves_multiple_workflows_with_repeated_flag`
- `test_run_with_no_workflow_flag_serves_default_and_resolver`
- `test_run_all_workflows_without_heal_repo_fails_fast_on_the_heal_workflow`
  (also gained the `monkeypatch` fixture parameter, which it didn't request before)
- `test_run_single_custom_workflow_ignores_github_workflow_default`
- `test_run_resolves_default_workflow_when_omitted`
- `test_run_with_no_workflow_harness_defaults_to_none`

No production code changed in this revision — attempt 01's implementation
stands as described in `development-01.md`.

## Verification

```sh
.venv/bin/python -m pytest -q
```
Run with this machine's actual ambient environment (`HARNESS_HEAL_REPO` and
`GITHUB_TOKEN` both set, exactly as `verify-01.md`'s command ran):
`1374 passed, 1 skipped, 1 warning` — zero failures, including
`tests/test_architecture.py` (25/25).

Also re-ran just the 8 previously-failing tests in isolation under the same
ambient env to confirm each individually: all pass.
