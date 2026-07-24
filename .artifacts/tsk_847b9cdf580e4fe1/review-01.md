# Review — retire `GithubMergeabilityWatcher`

## Method

Read `plan-01.md`/`design-01.md`/`architecture-01.md`/`development-01.md`, then
independently verified every claim against the actual diff (`git diff
origin/main HEAD`) rather than trusting the development report — full file
reads of `cli.py`, `git_workspace.py`, `ports/source.py`, `github_client.py`,
`CLAUDE.md`, `README.md`, `src/harness_docs_site/architecture.py`, the new/
changed test files, and a full `.venv/bin/pytest -q` run plus the targeted
architecture/docs-model guard tests.

## Acceptance criteria — verified against the diff

- **Watcher deleted.** `drivers/mergeability_watcher.py`,
  `tests/test_mergeability_watcher.py`, `tests/test_mergeability_e2e.py` are
  gone. `cli._mergeability_sources` and its import are gone. `--watch-mergeability`
  / `--no-watch-mergeability` is removed from argparse entirely (not defaulted
  off). `--resolver-workflow` is kept, with reworded help text — confirmed it's
  still read (`args.resolver_workflow`) and still drives `resolver_defined` /
  `served_names`, byte-identical logic, only the comment changed. `git grep -in`
  across `src/`, `tests/`, `CLAUDE.md`, `README.md`, `docs/adr` for
  `mergeability_watcher|GithubMergeabilityWatcher|watch.mergeability` turns up
  only the deliberately-untouched ADR-0014, dated spec docs under
  `docs/superpowers/specs/` (point-in-time design records, same category as
  ADRs — not named in the plan's sweep list), `CHANGELOG.md` (semantic-release
  generated, historical), and a self-referential comment in the new test file.
- **`processes/autoresolver.json` template.** Shipped as a README section
  ("Autoresolving merge conflicts"), not via `harness init` — verified the
  stated reason holds: several `test_cli.py` tests call
  `main(["run", ...])` with no `GITHUB_TOKEN` and assert exit code 0, and a
  `github-conflicts` process with no token fails fast
  (`ProcessValidationError` surfaces through `_process_sources`), so seeding it
  unconditionally would break those tests. The template's JSON shape
  (`trigger.interval`, `action.check`+`params.head_prefix`, `target.workflow`,
  `dedup`, `sink.kind`) matches `fs_processes.compile_process`'s actual parser
  field-for-field.
- **Parity verified with tests, not just narrative.** The new
  `test_skips_a_pr_outside_head_prefix` in `test_github_conflicts_check.py`
  proves `head_prefix` is threaded through rather than hardcoded — closes the
  one real gap `architecture-01.md` flagged. The three new
  `test_processes_e2e.py` tests build the check through the same factory shape
  `cli._process_sources` uses (not a hand-rolled watcher substitute) and assert
  the resolver's actual behavior: the finished task's `data.branch`/
  `data.source` (`kind="mergeability"`, `pr`, `url`, `base` — already asserted
  at the check-unit level by the pre-existing
  `test_emits_one_observation_per_dirty_pr_with_provenance`) flows all the way
  through to `GitWorkspace`'s branch-override actually firing (asserts
  `workspace.handles[task.id].branch` equals the PR's own branch, not a fresh
  `harness/<id>` one) and landing on a single PR; the behind-PR no-op path;
  and, separately, cross-restart dedup via a **fresh** `ScheduledTrigger` +
  fresh `SourcePoller` seeded from disk, proving the guarantee rides
  `SourcePoller._seen` + the bucket/state-keyed `dedup_key`, not the check's
  own in-process ledger. This is exactly the restart scenario the issue asked
  to be confirmed, and it's a distinct check from the check's own in-process
  `_seen` test that already existed.
- **Invariant 31 rewritten, reset logic untouched.** Confirmed by reading
  `git_workspace.py`'s `attach()` before/after — the diff is docstring/comment
  only, zero lines of the actual git-operation sequence changed. The new
  wording correctly attributes the server-side advance to `GithubConflictsCheck`
  calling `GithubClient.update_branch`, keeping the "why the hard reset exists"
  reasoning intact.
- **CLAUDE.md / docs swept.** Module map's driver row, both "what is
  responsible for what" bullets (the watcher's bullet deleted outright, the
  `github_conflicts_check.py` bullet's trailing clause reworded to "the only
  conflict-detection path"), invariant 31 — all confirmed in the diff.
  `docs/adr/0014-*` deliberately left alone, per the plan's explicit
  non-change AC and consistent with how this repo already treats ADRs as
  frozen historical record.
- **Test suite reshaping.** `test_cli.py`'s `_mergeability_sources` import,
  `_mergeability_args` helper, the three `test_mergeability_sources_*` unit
  tests, and `test_run_watch_mergeability_defaults_on_and_can_be_disabled` are
  gone; the surviving resolver-serving test is renamed and simplified to drop
  the now-nonexistent flag argument, coverage intent unchanged. No orphaned
  imports (`FakeGithubClient`/`MemoryRepositoryRegistry` are still used
  elsewhere in the file, confirmed).
- **Docs-site model.** `src/harness_docs_site/architecture.py`'s
  `mergeability-watcher` `Driver` entry is removed — this wasn't named in any
  planning doc, but `development-01.md` flagged it as a coverage gap that would
  otherwise fail `test_shipped_model_is_coherent_with_the_repo` immediately
  after deletion; verified the fix and the passing test.
- **Commit message guidance.** `development-01.md` documents the exact
  `feat!: ... / BREAKING CHANGE: ...` message for whoever lands this PR (this
  repo commits directly to `main` per its own `CLAUDE.md`, but this task itself
  runs through the harness's own worktree/PR pipeline, so the message applies
  at squash-merge time, not to this branch's intermediate commits) — satisfies
  the AC's "don't let semantic-release guess" by making the intended footer
  explicit and documented rather than leaving it to chance.

## Correctness / regression checks

- `git diff origin/main HEAD --stat` matches file-for-file what
  `development-01.md` claims changed — no undocumented drift.
- Full suite: **1207 passed, 1 skipped** (confirmed by re-running, not just
  trusting the report). Targeted re-run of
  `test_architecture.py test_architecture_model.py test_docs_site.py
  test_claude_md_module_map.py`: **56 passed**.
- No merge-conflict markers left in tracked files (the two `<<<<<<<` hits are
  a CLI help string and test fixtures unrelated to this merge).
- The `453066c` merge commit is a genuine two-parent merge (invariant 29,
  merge not rebase), bringing in exactly `origin/main`'s tip — confirmed via
  `git log --format='%P'`.
- No logic changes hidden inside the "prose sweep" sites — confirmed each of
  `git_workspace.py`/`ports/source.py`/`github_client.py`'s diffs is
  comment/docstring only.

## Verdict

Implementation meets every acceptance criterion, is correctly sequenced
(parity tests landed before deletion, per the mandated order), and the full
suite is green. No correctness bugs, no architecture violations, no missing
required tests found.
