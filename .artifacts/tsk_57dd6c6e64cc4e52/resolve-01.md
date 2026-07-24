# Resolve merge conflict on PR #106

Merging `origin/main` into this branch produced conflicts in two files. Both
stemmed from the same underlying change: `origin/main` retired the bespoke
`mergeability_watcher` driver (superseded by the `github-conflicts` process
Check), while this branch independently added the `label_issue` finisher
driver. The two changes touched adjacent lines in the same lists/imports with
no actual logical overlap.

## `CLAUDE.md`

Conflict was in the module map's `Drivers` row (line ~148). Resolved by
keeping the union of both sides minus the retired driver: dropped
`mergeability_watcher` from the driver list (removed on `origin/main`, and
`src/harness/drivers/mergeability_watcher.py` plus its tests are deleted in
this merge), kept `label_issue` (added on this branch, still actively wired
in `cli.py`).

## `src/harness/cli.py`

Conflict was in the import block (lines ~42-46): this branch imported both
`LabelIssueBehavior` and `GithubMergeabilityWatcher`; `origin/main` imported
neither (it had already dropped the watcher import as part of retiring the
driver). Resolved by keeping only `LabelIssueBehavior` — confirmed by
grepping the rest of `cli.py` that `LabelIssueBehavior` is used (wired as the
`"label-issue"` finisher at line ~1607) while `GithubMergeabilityWatcher` had
no remaining call site anywhere in the file, consistent with `origin/main`
having fully removed the watcher driver and module.

## Verification

- No `<<<<<<<`/`=======`/`>>>>>>>` markers remain anywhere outside
  `.artifacts/`.
- Rebuilt the venv (`python3.11 -m venv .venv && pip install -e ".[dev]"`)
  and ran the full suite: `1317 passed, 1 skipped`.
