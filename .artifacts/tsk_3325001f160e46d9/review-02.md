# Review-02: fix for the `--github-workflow` regression (review-01)

Verified `development-02.md`'s fix against `review-01.md`'s single finding, by
reading the actual diff (`git diff 4eb9bc8 2daaf38`) and reproducing the
behavior directly, not just trusting the development summary.

## The fix

`src/harness/cli.py`:
- `--github-workflow`'s argparse default changed from `DEFAULT_WORKFLOW` to
  `None`, so `args.github_workflow` unambiguously distinguishes "operator
  didn't pass this flag" from "operator explicitly asked for `default`".
- The R3 membership check in `_run` now reads `if args.github_workflow is not
  None and args.github_workflow not in served_names` — fires only on an
  explicit, meaningful mismatch.
- Immediately after, `args.github_workflow = args.github_workflow or
  DEFAULT_WORKFLOW` resolves the value back to the same default as before,
  **before** `_github_sources(args, ...)` is called (confirmed at
  `cli.py:713`, well after the resolution at `cli.py:696`) — every downstream
  reader (`GithubTaskSource(workflow=args.github_workflow, ...)` at
  `cli.py:375`) still sees a concrete string, never `None`.

## Verification performed

- Read the full diff of `cli.py` between the two commits — it's exactly the
  minimal, targeted change described, no unrelated edits.
- Confirmed `args.github_workflow` has only one other read site in the file
  (`cli.py:375`, inside `_github_sources`) and it executes strictly after the
  resolution line, so no code path can observe `None`.
- Ran the full suite: **491 passed, 1 skipped** — matches the claim exactly.
- Reproduced the exact regression scenario manually, before relying on the
  test alone:
  - `run --workflow hotfix --api-port 0 --agent dummy --forge fake` with
    `GITHUB_TOKEN=` and no `--github-workflow` flag → now starts successfully
    (`started workflows=['hotfix']`), where it previously failed with `error:
    --github-workflow 'default' is not served by this harness (served:
    hotfix)`.
  - Confirmed the check still correctly *rejects* an explicit, genuinely
    mismatched value: `--github-workflow default` on the same hotfix-only run
    still exits 2 with the same error message as before — R3's actual purpose
    (catching a real operator mistake) is preserved, only the false-positive
    on the unrequested default is removed.
- Reviewed the new test,
  `test_run_single_custom_workflow_ignores_github_workflow_default` — it
  covers exactly the FR-6 scenario review-01 flagged as missing (single
  non-default `--workflow`, no GitHub flags, no token), asserts exit 0, empty
  stderr, and the harness actually serving `{"hotfix"}`. The pre-existing
  `test_run_rejects_github_workflow_not_in_served_set` is unchanged and still
  covers the explicit-mismatch case.

## What holds from review-01

Everything review-01 already verified as correct (`ports/workflows.py`,
`drivers/fs_workflows.py`, `projection.py`, `app.py`'s union `step_queues`
and `ServedWorkflowRepository` wiring, the rest of `cli.py`'s repeatable
`--workflow`/`--all-workflows` handling) is untouched by this commit —
confirmed the diff touches only `cli.py` (the check + the new default +
help text) and `tests/test_cli.py` (the new test).

## Verdict

`done` — the fix is minimal, correctly scoped to the exact regression
reported, verified live (not just via the test suite), and the full suite is
green. No further changes needed; ready to land.

```json
{"outcome": "done", "summary": "Fix for the --github-workflow regression verified: check now fires only on explicit mismatch, downstream resolution is correct, live repro confirms both the fixed false-positive and the still-working explicit-mismatch rejection, full suite green (491 passed, 1 skipped)."}
```
