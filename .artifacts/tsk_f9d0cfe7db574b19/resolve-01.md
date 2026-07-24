# Merge conflict resolution — PR #110

Merging `origin/main` into `harness/tsk_60ba6c45666647cd` (the `default` →
`development` workflow rename branch) produced conflicts in three files.

## `src/harness/cli.py`

**Conflict 1 — module constants.** HEAD had renamed `DEFAULT_WORKFLOW` to
`"development"`; `origin/main` had independently added the `_SECRETS_TEMPLATE`
(service secrets file scaffolding) in the same spot, still with the old
`DEFAULT_WORKFLOW = "default"`. Resolution: kept `_SECRETS_TEMPLATE` from
`origin/main` and kept `DEFAULT_WORKFLOW = "development"` from HEAD.

**Conflict 2 — `_run()`.** HEAD added a single line,
`_migrate_legacy_workflow(layout, args.workflow)`, copying a legacy
`default.json` forward to `development.json`. `origin/main` had, in the same
spot, added a new `--workflow`/`--all-workflows` multi-workflow-serving
feature (`_resolve_served_workflows`, plus the `--github-workflow` validation
that follows) — which also turned `--workflow` from a single string
(`args.workflow`) into a repeatable list (`args.workflows`), so the old call
no longer type-checks.

Resolution: kept both pieces of logic, in order (migrate before resolving
served names, since `_resolve_served_workflows`'s implicit-default fallback
probes for `development.json`'s existence). Adapted the migration call to the
new list-shaped args: it now fires when `--all-workflows` is not set and
either no `--workflow` was given or `development` is explicitly among the
given values.

Additionally discovered and fixed a real interaction between the two
features: `origin/main` also introduced `ServedWorkflowRepository`, which
makes the dispatcher reject any task whose `workflow_template` is outside the
served set. That breaks the migration's stated guarantee (a legacy
`workflow_template="default"` task must keep dispatching after the rename).
Fixed by extending `served_names` to also include the legacy name `"default"`
whenever `development` is being served via the implicit/explicit-default path
and `workflows/default.json` still exists on disk.

## `src/harness/drivers/github_source.py`

HEAD had added `workflow`/`repository`/`worktree_root`/`select_label`
parameters (with the renamed `"development"` default) to
`GithubLabelReflector.__init__`. `origin/main` had, independently, refactored
`GithubLabelReflector` into an outbound-only reflector with no such
parameters, moving them to the newly split-out `GithubTaskSource` class
(which composes `GithubLabelReflector`). The class's own docstring already
says it "never produces a task" and needs no workflow/repo/worktree
knowledge, confirming the params don't belong there.

Resolution: took `origin/main`'s shape (no such params on
`GithubLabelReflector`), and instead applied the intended rename to
`GithubTaskSource.__init__`'s `workflow` parameter default, changing it from
`"default"` to `"development"`.

## `tests/test_cli.py`

Two independently-added test blocks collided at the same insertion point (no
real logical conflict — one added migration tests, the other added review-
persona tests). Resolution: kept both blocks, one after the other.

Also updated `test_run_migrates_legacy_default_workflow_without_prior_init`'s
`fake_serve` stub to accept the two extra parameters (`pr_poll_interval`,
`reconcile_interval`) that `origin/main`'s PR-watcher/merge-reconciler work
added to the real `serve()` signature — otherwise the monkeypatched fake
would raise a `TypeError` on the extra positional arguments now passed by
`_run`.

Finally, fixed four other tests (`test_run_serves_multiple_workflows_with_repeated_flag`,
`test_run_with_no_workflow_flag_serves_default_and_resolver`,
`test_run_all_workflows_serves_every_definition_found`,
`test_run_resolves_default_workflow_when_omitted`) that were written against
`origin/main` before the rename existed and hardcoded the literal `"default"`
as the expected served/served-by-default workflow name; updated them to
`"development"` to match the renamed default.

## Verification

Full suite: `1223 passed, 1 skipped`. No `<<<<<<<`/`=======`/`>>>>>>>` markers
remain anywhere in the tree (the two hits `git grep` still finds are a
literal string in the resolver persona prompt in `cli.py` and a test in
`test_git_workspace.py` that asserts real conflict markers appear in a real
git conflict — both pre-existing and unrelated to this merge).
