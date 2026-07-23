# Development-02: fix `--github-workflow` regression flagged by review-01

Addresses the single `request_changes` finding in `review-01.md`: the R3
`--github-workflow` membership check in `cli.py:_run` fired unconditionally,
so `harness run --workflow hotfix` (no `GITHUB_TOKEN`, no other GitHub flags)
failed startup because the flag's *default* value ("default") no longer
matched the single served workflow ("hotfix") — even though no
`GithubTaskSource` is ever built in that case. This broke FR-6's requirement
that explicit single-workflow runs behave exactly as before.

## Root cause

`--github-workflow` was declared with `default=DEFAULT_WORKFLOW`, so
`args.github_workflow` was always a concrete string, indistinguishable from an
operator explicitly passing `--github-workflow default`. The R3 check
(`args.github_workflow not in served_names`) couldn't tell "operator cares
about this" from "argparse filled in a default nobody asked for."

## Fix

**`src/harness/cli.py`**
- `--github-workflow`'s argparse default changed from `DEFAULT_WORKFLOW` to
  `None`. `None` now unambiguously means "operator didn't pass this flag."
- In `_run`, the membership check now reads
  `if args.github_workflow is not None and args.github_workflow not in served_names`
  — it only fires when the operator explicitly named a GitHub workflow, which
  is exactly the case where the check is meaningful (an explicit choice that
  turns out to not be served). Immediately after, `args.github_workflow =
  args.github_workflow or DEFAULT_WORKFLOW` resolves the value back to the
  same default as before for everything downstream (`_github_sources`'
  `GithubTaskSource(workflow=args.github_workflow, ...)`), so GitHub-sourced
  tasks still land on `"default"` when nothing was specified.
- This was chosen over gating on `_github_sources(...)` returning something
  non-empty (the review's alternative suggestion) because that ties the
  check's behavior to registry/token state, which is a weaker, more
  indirect signal and would also have made the *existing* regression test
  (`test_run_rejects_github_workflow_not_in_served_set`, which passes no
  token and registers no repo) stop catching the mismatch it's meant to
  catch. Gating on "was the flag explicitly passed" is a direct, stable
  signal and needed no changes to that existing test.

## Test added

**`tests/test_cli.py`**
- `test_run_single_custom_workflow_ignores_github_workflow_default`: no
  `GITHUB_TOKEN`, `run --root <tmp> --workflow hotfix` (single, non-default
  workflow, no `--github-workflow` flag at all) — asserts exit 0, no stderr,
  and the harness actually served `{"hotfix"}`. This is the exact FR-6
  scenario the review reproduced manually; it would have failed before this
  fix (exit 2, "not served" on stderr) and passes after.
- `test_run_rejects_github_workflow_not_in_served_set` (existing, unchanged)
  continues to cover the case that must still fail: `--github-workflow`
  passed explicitly and not in the served set.

## Verification

```sh
.venv/bin/pytest -q
```

Result: **491 passed, 1 skipped** (490 from development-01 + the new
regression test; the skip is the opt-in `HARNESS_SMOKE_CLAUDE=1` test).

Also reproduced the review's exact manual repro, before and after:

```sh
# before the fix (as in review-01):
$ GITHUB_TOKEN= .venv/bin/python -m harness.cli run --root /tmp/hc_test --workflow hotfix --api-port 0
error: --github-workflow 'default' is not served by this harness (served: hotfix)

# after the fix:
$ GITHUB_TOKEN= .venv/bin/python -m harness.cli run --root /tmp/hc_test --workflow hotfix --api-port 0 --agent dummy --forge fake
started workflows=['hotfix']
```

## Files changed

- `src/harness/cli.py` — `--github-workflow` default `None`; `_run` validates
  membership only when explicitly passed, then resolves to `DEFAULT_WORKFLOW`.
- `tests/test_cli.py` — new regression test for the FR-6 scenario.

No other files touched; `development-01.md`'s implementation of FR-1..FR-5
and the rest of FR-6 is unchanged and still holds per review-01's "What
holds" section.
