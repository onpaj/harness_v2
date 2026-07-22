# Review: serve multiple workflows in a single running harness

Verified `development-01.md`'s implementation against `plan-01.md` (FR-1..FR-6),
`design-01.md` and `architecture-01.md`, by reading the actual diff (`git diff
HEAD~1`) file by file and reproducing the CLI behavior directly, not just
reading the development summary.

## What holds

- `ports/workflows.py` / `drivers/fs_workflows.py`: `WorkflowRepository.names()`
  and `ServedWorkflowRepository` match design-01/architecture-01 exactly,
  including refinement R1 (`_served = tuple(dict.fromkeys(names))` dedupes the
  served list before it can appear twice in an error message).
- `drivers/memory.py`: `MemoryWorkflowRepository.names()` — an untracked gap
  in the design docs, correctly caught and fixed during implementation (the
  new abstract method would otherwise make it uninstantiable); no behavior
  change to its existing callers.
- `projection.py`: `_reachable_order` extraction and `column_order`/
  `BoardProjection` taking `Sequence[Workflow]` match design-01 verbatim,
  including the "first workflow's steps first, then next workflow's
  not-yet-seen steps" union rule (FR-4).
- `app.py`: `build()`'s `str | Sequence[str]` widening, resolution via the
  raw repository before wrapping, union `step_queues` via dict comprehension,
  `Dispatcher` wired to the **wrapped** `ServedWorkflowRepository` (verified
  the parameter actually reaches `Dispatcher.__init__(workflows=...)`, not
  the raw one — this was flagged as the single highest-risk wiring point in
  architecture-01 and it's correct), `Harness.workflows: dict[str, Workflow]`,
  and the `started` event's `workflows=sorted(...)` payload all match
  design-01/architecture-01 exactly (FR-3, FR-4, FR-5, interfaces).
- `cli.py`: repeatable `--workflow`, `--all-workflows`, mutual-exclusion (R2),
  empty-discovery startup error (R2), and `_init`'s two `.workflow` reads
  fixed to `.workflows[args.workflow]` all match.
- Tests: `test_fs_workflows.py`, `test_projection.py`, `test_app.py`,
  `test_cli.py` cover FR-6's list point by point — two workflows served
  together with a shared step name routing through the shared queue
  end-to-end, unserved-but-existing-workflow failure message shape,
  genuinely-nonexistent-workflow failure, `BoardProjection` column union
  with no duplicates, and `names()`/`ServedWorkflowRepository` unit coverage.
  Full suite: 490 passed, 1 skipped, reproduced locally.

## Bug: `--github-workflow` validation breaks ordinary single-custom-workflow runs (R3)

`cli.py:682-688` checks `args.github_workflow not in served_names`
**unconditionally**, on every `harness run` invocation, regardless of whether
GitHub polling will actually be active. `--github-workflow` defaults to
`DEFAULT_WORKFLOW` ("default") (`cli.py:826` area), and GitHub sources are
only ever built later, gated purely on `GITHUB_TOKEN` being set
(`_github_sources`, `cli.py:358-360`: returns `[]` with no token, no error).

Reproduced directly:

```
$ GITHUB_TOKEN= .venv/bin/python -m harness.cli run --root /tmp/hc_test --workflow hotfix --api-port 0
error: --github-workflow 'default' is not served by this harness (served: hotfix)
$ echo $?
2
```

**Failure scenario:** an operator runs `harness run --workflow hotfix`
(a single, non-default workflow — no `GITHUB_TOKEN` set, no intention of
using GitHub source polling at all, exactly the "explicit `--workflow`"
case FR-6 requires to "behave exactly as before"). Before this change, the
harness started fine. After this change, it refuses to start at all, because
`--github-workflow`'s default value ("default") no longer matches the
single served workflow ("hotfix"), even though that check is entirely
irrelevant here — no `GithubTaskSource` would ever be constructed.

This is a real regression against FR-6 ("Single-workflow runs (default, or
explicit `--workflow`) behave exactly as before — a regression check, not
just a new-feature check") and NFR "No behavior change for the common case."
It isn't caught by the test suite: `test_run_rejects_github_workflow_not_in_served_set`
only exercises the case where `--github-workflow` is passed *explicitly* and
mismatches; there's no test for a single custom `--workflow` left at its
`--github-workflow` default, which is the actually-common case this breaks.

**Fix:** scope the check to when GitHub integration is actually in play —
e.g. only validate `args.github_workflow` once `_github_sources` would
produce something (after computing `sources`, or by checking `GITHUB_TOKEN`
presence up front), or only validate when `--github-workflow` was passed
explicitly (`argparse` default `None` + `args.github_workflow or
DEFAULT_WORKFLOW` at the point of use, checking membership only in the
explicit-value branch). Either way, a harness with no GitHub source active
must not fail to start over a mismatch that can never manifest, and a test
covering "single non-default `--workflow`, no GitHub flags, no token" must
be added alongside the fix.

## Verdict

`request_changes` — everything else in the implementation matches the
architecture and plan cleanly, but the `--github-workflow` startup check
(architecture-01's R3) is implemented too broadly and breaks ordinary,
previously-working single-custom-workflow runs. This is the only change
needed before landing.
