# Resolve merge conflict on PR #106

## Conflicted files

Only two files carried real `<<<<<<<`/`=======`/`>>>>>>>` markers after the
merge of `origin/main` into this branch:

- `CLAUDE.md` — two conflicting hunks (invariants 37-42, and the module-map
  bullets for `fs_triggers.py`/`fs_processes.py`/`label_issue.py`)
- `src/harness/models.py` — the `Workflow.finishers`/`descriptions` field
  block

Everything else in the two feature sets (this branch's triage/`FinisherBinding`/
`label-issue` work, `origin/main`'s cron-cadence/outcome-vocabulary work)
merged cleanly line-by-line, since the two branches touched different files
almost everywhere.

## Resolution

**`src/harness/models.py`**: both sides added a field to `Workflow`. HEAD
added `finishers: dict[str, FinisherBinding]` (the structured
kind+config finisher binding this branch's feature needs); `origin/main`
added `finishers: dict[str, str]` (a simpler shape, superseded) plus a new
`descriptions: dict[str, str]` field (unrelated — used by
`Workflow.description_for`/outcome-vocabulary prompt hints). Verified against
the rest of the already-merged tree (`app.py`, `cli.py`,
`drivers/fs_workflows.py` all already consume `workflow.finishers` as
`FinisherBinding` objects, and `models.py`'s own `outcomes_for`/
`description_for` methods already expect `descriptions` to exist) — so the
correct resolution keeps HEAD's `FinisherBinding` shape for `finishers` and
adds `origin/main`'s `descriptions` field alongside it. Both sides' behavior
is preserved.

**`CLAUDE.md`**: prose-only. For invariants 37-42 and the module-map bullets,
kept `origin/main`'s wording throughout (it's the superset — cron cadence,
`effective_sink_kind`, `outcomes_for` — and its invariant #41/bullet already
described the simpler `finishers` shape origin/main had), then reinstated
this branch's invariant #41 wording (the `FinisherBinding`-based factory
registry, matching the actual code) and re-added the `fs_processes.py`
cross-file `github-issues` label-collision guard sentence and the
`drivers/label_issue.py` bullet, both features unique to this branch and
confirmed present in the already-merged `drivers/fs_processes.py` and
`drivers/label_issue.py` source.

## Fallout beyond the conflict markers

`origin/main` had removed the closed `Outcome` enum in favor of plain string
constants (`DONE`, `REQUEST_CHANGES` in `harness.models`) as part of its
outcome-vocabulary work — a non-conflicting change in `models.py` since this
branch's new code didn't touch that region. But this branch's new code
(`src/harness/drivers/label_issue.py`) and its new tests
(`tests/test_label_issue_behavior.py`, `tests/test_triage_process_e2e.py`,
plus one case each in `tests/test_app.py` and `tests/test_cli.py`) still
referenced `Outcome.DONE`/`Outcome.REQUEST_CHANGES`/`result.outcome.value`.
Updated all of them to the plain-string constants (`DONE`/`REQUEST_CHANGES`,
`result.outcome`) to match the merged model.

## Verification

`.venv/bin/pytest -q` (fresh `.venv`, `pip install -e ".[dev]"`):
**1331 passed, 1 skipped**. No conflict markers remain anywhere in the tree.
