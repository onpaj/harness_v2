# Review (round 2): Jira issue loader (`jira-issues` action)

## Verdict

`done`. `development-02.md` resolves the single `request_changes` finding
from `review-01.md` completely, correctly, and with no new issues introduced.

## What was checked

`review-01.md` flagged exactly one gap: the `project`+`label`
convenience-form JQL silently dropped the status filter
(`AND statusCategory != Done`) that `architecture-01.md`'s "Prerequisites
before implementation begins" section marked as must-resolve-before-coding.
Verified the fix directly against source, not just the development artifact's
claims:

- **`src/harness/drivers/jira_issues_check.py:42-45`** — the convenience-form
  JQL is now `f'project = {project} AND labels = "{label}" AND
  statusCategory != Done'`. Confirmed by reading the file and by
  `git show d94ba25 -- src/harness/drivers/jira_issues_check.py`.
- **`jql=` override path is untouched** — `self._jql = jql or <compiled
  string>`; an explicit override is stored verbatim with no clause appended,
  matching the review's accepted resolution ("an operator handing a raw JQL
  string owns its correctness completely").
- **Module docstring** now explains the *why* (Jira labels routinely outlive
  workflow status, unlike GitHub's `state="open"` filter which is automatic).
- **`docs/adr/0020-jira-second-ingestion-source.md`** — the "Selection: JQL
  wins" bullet now records the decision and reasoning explicitly, giving the
  prerequisite a durable resolution beyond a code comment, as the review's
  second acceptable option required (here done in addition to the code fix,
  not instead of it).
- **Two new tests** in `tests/test_jira_issues_check.py` assert the built
  `_jql` string directly, both with and without the clause:
  `test_convenience_form_jql_excludes_done_issues` and
  `test_explicit_jql_is_not_augmented_with_a_status_filter`. Read both —
  they assert on `check._jql` exactly as the review requested, not on a
  weaker proxy.
- **No unrelated changes** — `git show d94ba25 --stat` touches only
  `jira_issues_check.py`, the ADR, the test file, and the new
  `development-02.md` artifact. Nothing else in the tree moved.

## Verification run independently

```
env -u GITHUB_TOKEN -u HARNESS_HEAL_REPO .venv/bin/pytest -q
```
→ **1379 passed, 1 skipped** — matches `development-02.md`'s claim exactly
(2 more than `development-01.md`'s 1377, exactly the 2 new JQL tests).

Also reproduced, as a sanity check, that the 8 `test_cli.py` failures seen
when running with this shell's ambient `GITHUB_TOKEN`/`HARNESS_HEAL_REPO`
set are pre-existing and env-dependent, not caused by this change — same
failure set both review rounds have already isolated and explained.

## Outstanding

None. All prior review findings are addressed; no new correctness,
architecture, or invariant issues found in this round.

```json
{"outcome": "done", "summary": "development-02.md fully and correctly resolves review-01.md's sole request_changes finding: the project+label convenience-form JQL now appends `AND statusCategory != Done` (jira_issues_check.py:42-45), an explicit jql= override is left unaugmented, the reasoning is documented in both the module docstring and ADR-0020, and two new tests assert the built _jql string directly. Verified independently against source (not just the development artifact's claims) and by re-running the full suite with GITHUB_TOKEN/HARNESS_HEAL_REPO unset: 1379 passed, 1 skipped, matching the claimed count exactly. No unrelated changes, no new issues."}
```
