# Merge conflict resolution — PR #126

## Conflicted files

Only `CLAUDE.md` had conflict markers (git status showed `UU CLAUDE.md`; every
other changed file was a clean, already-merged modification/addition from the
`verify` gate work on this branch).

## The conflict

Both branches touched the same "Module map" table rows in the same place:

- **HEAD** (this branch) had added `jira_client`/`jira_issues_check` to the
  `Drivers` cell (Jira ingestion work).
- **origin/main** had added `verify` to the `Behaviors` cell and
  `subprocess_command` to the `Drivers` cell (the verify-gate work landing on
  main).

## Resolution

Combined both additions into a single pair of table rows:

- `Behaviors`: `behaviors/{landing,agent,resolve_conflict,verify,open_issue}`
  (keeps `verify` from origin/main).
- `Drivers`: includes both `jira_client`/`jira_issues_check` (from HEAD) and
  `subprocess_command` (from origin/main), alongside every other driver both
  sides already agreed on.

No other lines needed changes — the surrounding `Ports` row already listed
`command` on both sides, so no further merging was required there.

## Verification

- Confirmed no `<<<<<<<`/`=======`/`>>>>>>>` markers remain anywhere in the
  tree (the only other hits were an unrelated pre-existing artifact file and
  a `====` comment separator in `sse.js`, not conflict markers).
- Staged `CLAUDE.md` with `git add`.
- Built a fresh venv (`python3.11 -m venv .venv && pip install -e ".[dev]"`,
  none existed in this worktree) and ran the full suite:
  `env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN .venv/bin/pytest -q` →
  **1476 passed, 1 skipped**.
  (Without unsetting those two ambient env vars — set globally on this
  machine, not by this change — 8 unrelated `test_cli.py` tests fail because
  `harness run` autodetects heal/label-issue wiring from them; this is
  pre-existing environmental leakage, not something introduced by this
  resolution.)
