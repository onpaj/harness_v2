# Automatic PR merging — plan

*2026-07-27* — spec: `docs/superpowers/specs/2026-07-27-automerge-design.md`,
decision record: `docs/adr/0023-merging-is-a-fourth-port.md`.

Status: **done** (single increment).

## Increment 1 — the whole path, shipped withheld

### Base

- [x] `merge_verdict.py` — package-free parser for the review's fenced json
      verdict (`MergeVerdict`, `parse_verdict`, `VerdictError`). `issue_drafts.py`'s
      twin; an empty artifact is an *error* here (no legitimate "wrote nothing"
      path), confidence is clamped rather than rejected, `true` is rejected
      rather than read as 1.0.

### Port

- [x] `ports/pr_merge.py` — `PullRequestMerger`, `MergeRef`, `MergeError`,
      `MergeRefused`, `MERGE_METHODS`. The sha pin and the refusal/fault split
      are stated in the contract, not left to drivers.
- [x] `test_architecture.py` — `dispatcher.py`/`consumer.py` may not import it
      (invariant #44).

### Drivers

- [x] `GithubClient`: real `merge_pull_request` verb + `PullRequestNotMergeable`;
      `PullRequestInfo` gains `title`/`body`/`labels`/`draft` (all defaulted).
- [x] The old `FakeGithubClient.merge_pull_request` *test helper* renamed
      `mark_merged` — it simulates GitHub merging out of band, a different
      thing from the harness merging. Two call sites updated.
- [x] `drivers/github_pr_merger.py` — `GithubPullRequestMerger`, translating
      `PullRequestNotMergeable` → `MergeRefused` and everything else →
      `MergeError`.
- [x] `MemoryPullRequestMerger` in `drivers/memory.py`, honouring both contract
      properties (moved head refuses; already-merged returns the existing merge).
- [x] `drivers/github_mergeable_check.py` — `GithubMergeableCheck` + `SPEC`,
      the `clean` complement of `github-conflicts`.

### Behavior

- [x] `behaviors/merge_pr.py` — `MergePrBehavior`, the `merge-pr` finisher.
      Replace shape with `from_step`, wrap shape without (the `open-issue`
      split). Records the confidence and reasoning into the merge commit body.

### Wiring

- [x] `cli._process_check_factories` — `github-mergeable`, a `CheckDefinition`
      so the dashboard's process form renders its params from data.
- [x] `cli._run` — the `merge-pr` finisher, registered unconditionally (falls
      back to `MemoryPullRequestMerger` without a token, like `open-issue`),
      validating `method`/`min_confidence`/`dry_run` at **wiring** time.
- [x] `harness init` — seeds `workflows/automerge.json` +
      `agents/merge-review.json`; deliberately seeds **no** Process.

### Tests

- [x] `test_merge_verdict.py` (14) — every malformed shape, clamping, the
      boolean trap, last-block-wins.
- [x] `test_github_mergeable_check.py` (15) — provenance, the state partition,
      draft/label/prefix exclusions, re-push re-review.
- [x] `test_merge_pr_behavior.py` (14) — the asymmetry table from the spec;
      every refusal path asserts `merger.merged == []`.
- [x] `test_github_pr_merger.py` (10) — the refusal/fault translation, the sha
      actually reaching the request body.
- [x] `test_cli.py` (+8) — seeding, no seeded Process, the withheld default,
      binding validation, token-free registration.
- [x] `test_architecture.py` (+1) — invariant #44.

### Docs

- [x] ADR-0023, spec, this plan, CLAUDE.md (module map, invariant #44, the
      responsibility bullet, three gotchas).

## Enabling it (operator)

1. Protect the target branch first — `clean` is only as strong as the repo's
   protection rules (see the gotcha in CLAUDE.md).
2. Create `processes/automerge.json` (dashboard process editor, or by hand):

```json
{
  "trigger": { "interval": "5m" },
  "action": { "check": "github-mergeable", "params": { "head_prefix": "harness/" } },
  "target": { "workflow": "automerge" },
  "sink": { "kind": "none" }
}
```

3. Watch the board. Each candidate PR gets a review and a recorded decision;
   nothing merges while `dry_run` is true.
4. When the recorded decisions justify it, set
   `workflows/automerge.json` → `finishers.merge.dry_run` to `false`.
   Tune `min_confidence` in the same place.

## Follow-ups (not built)

- Post the review back to GitHub as a real PR review
  (`GithubClient.create_review`; nothing else changes).
- React to review comments on an open PR — a separate inbound action.
- Skip a PR whose own harness task is still in flight (the general
  "live-task-consulting mode" follow-up).
