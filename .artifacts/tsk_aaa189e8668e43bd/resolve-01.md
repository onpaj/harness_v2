# Merge conflict resolution — PR #126

## Scope

The merge of `origin/main` into this branch (`harness/tsk_2c01fa1590214f4b`) produced
exactly one conflicted file: `CLAUDE.md`. Everything else in `git status` (the new
`.artifacts/*`, `src/harness/...`, `tests/...` files) was already staged cleanly by
git's merge — no markers, no action needed there.

## The conflict

`CLAUDE.md`'s module-map table had one conflicting row, the `Drivers` line, where two
features added a driver module on each side of the merge:

- **HEAD** (this branch, Jira ingestion feature, ADR-0020) added `jira_client,jira_issues_check`
  to the driver list.
- **origin/main** (the manual "Add issue" button feature) added `github_issue_import`
  to the driver list.

Both additions are independent, unrelated modules — neither replaces or depends on the
other. The narrative bullets describing both `drivers/jira_client.py`/`drivers/jira_issues_check.py`
and `drivers/github_issue_import.py`, plus the `ports/issue_import` entry in the `Ports`
row above, were already present outside the conflict markers (git merged those parts
cleanly), so the only fix needed was to union both driver names into the single
`Drivers` table cell.

## Resolution

Combined the list to include every driver named on either side:

```
| Drivers | `drivers/{fs_queue,fs_workflows,fifo_strategy,dummy_behavior,stdout_events,system_clock,memory,git_workspace,fake_forge,claude_cli,fs_agents,fs_repos,worktree_artifacts,source_reflector,github_client,github_source,github_forge,github_issues,github_issues_check,github_conflicts_check,jira_client,jira_issues_check,failed_tasks_check,github_merge_checker,github_issue_checker,launchd,composite_events,git_remote,projection_events,stage_output,scheduled_trigger,checks,fs_triggers,fs_processes,slack_sink,uv_updater,label_issue,github_issue_import}` |
```

No conflict markers remain anywhere in the tree (verified with a recursive grep for
`<<<<<<<`/`=======`/`>>>>>>>`; the only other hits were an unrelated historical artifact
file from a prior task and a decorative `====` divider line in `sse.js`, neither part
of this merge).

## Verification

Ran the full suite with the environment's `HARNESS_HEAL_REPO`/`GITHUB_TOKEN` unset (as
CLAUDE.md's working notes prescribe, since a stray `HARNESS_HEAL_REPO` in the shell
otherwise perturbs 8 `test_cli.py` cases unrelated to this conflict):

```
1455 passed, 1 skipped, 1 warning in 51.37s
```

`tests/test_architecture.py` (which guards the module-map/import invariants this file
documents) passes as part of that run.
