# Review: Jira issue loader (`jira-issues` action)

## Verdict

`request_changes`. The implementation is otherwise a faithful, well-tested
mirror of `GithubIssuesCheck`/`GithubClient` — architecture and invariants are
respected, the full suite passes (1377 passed, 1 skipped, confirmed by
re-running with `GITHUB_TOKEN`/`HARNESS_HEAL_REPO` unset) — but it silently
drops one of the two decisions `architecture-01.md` explicitly flagged as
**"must resolve before coding"**, and the drop is a real correctness gap, not
a cosmetic one.

## What's wrong

`architecture-01.md`'s "Prerequisites before implementation begins" section
(and `design-01.md`'s "Open questions carried forward", item 3) both raise the
same still-open question and neither treats it as optional:

> Should the `project`+`label` convenience form inject a status filter (e.g.
> `AND statusCategory != Done`)? Recommend **yes** — without it, a
> resolved/closed issue that still carries the select label ... gets
> re-ingested ... which is a materially worse failure mode than GitHub's
> equivalent (`list_issues(..., state="open")` already filters this). This is
> a one-line addition ... cheap to decide now.

`development-01.md`'s "Correction against design-01.md/architecture-01.md"
section addresses the *other* flagged prerequisite (the exception-type
question) in detail, including a documented, verified resolution in ADR-0020.
The status-filter question gets no mention anywhere — not in
`development-01.md`, not in ADR-0020, not as a code comment explaining a
deliberate "no." Checked directly:

- `src/harness/drivers/jira_issues_check.py:42` —
  `self._jql = jql or f'project = {project} AND labels = "{label}"'` — no
  status-category clause.
- `grep -rn "statusCategory"` across the repo only matches the two artifact
  files that raised the question (`design-01.md`, `architecture-01.md`);
  nothing in `docs/adr/0020-*.md` or the source.

This isn't a stylistic gap. The `project`+`label` convenience form (one of
the two supported selection modes, per FR-2/ADR-0020) will re-ingest a
resolved/closed/re-opened-then-reclosed Jira issue as a fresh task for as
long as it carries the select label, with no equivalent to GitHub's
`state="open"` filter that the design explicitly compared it against. Given
the architecture step called this "cheap to decide now and correct
pre-emptively rather than patch in after a bug report," and the plan/design/
architecture chain treated it as a blocking prerequisite rather than a
nice-to-have, this needs an explicit resolution before the step is done —
either the one-line JQL addition the architecture recommended, or a
documented, reasoned decision to skip it (e.g., if operators are expected to
always pair the convenience form with a clean label lifecycle) recorded in
ADR-0020 the same way the exception-type question was resolved.

## What to fix

1. Either add the recommended status filter to the convenience-form JQL in
   `JiraIssuesCheck.__init__` (`jira_issues_check.py:42`), e.g.
   `f'project = {project} AND labels = "{label}" AND statusCategory != Done'`,
   with a test asserting the built JQL string, **or** add an explicit,
   reasoned "no" to ADR-0020's decision list addressing this exact question
   (not silence) — either resolves the open prerequisite; only the current
   silent omission is the problem.
2. No other changes requested — wiring, tests (28 new, mirroring the GitHub
   suites structurally), ADR content for the other two deviations, CLAUDE.md
   updates (module map + invariant #40 prose), and the exception-type
   correction are all sound and verified against the real source.

## What's solid (verified independently, not just re-asserted)

- **No new port for `JiraClient`** — matches the `GithubClient` precedent;
  `test_architecture.py` still only reaches `Check`/`ports/triggers.py`.
  `jira_issues_check.py` imports only `harness.drivers.jira_client` and
  `harness.ports.triggers` — never `cli`, confirmed by reading the file.
- **Wiring confined to `cli.py`** — `_process_check_factories` gets one more
  closure (`jira_issues_factory`) and `_run` builds the `jira_client` from
  `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` exactly parallel to the
  existing `github_client` construction immediately above it
  (`cli.py:1766-1773`). `app.build()` untouched.
- **Fail-fast shape matches `github-issues` exactly**: no credentials →
  `ProcessValidationError(field="check")`; bad/missing `repository`, non-string
  `label`/`claimed_label`/`jql`/`project`, or neither `jql` nor `project` →
  `field="params"` (`cli.py:843-889`).
- **Provenance/dedup**: `state_key=f"jira:{key}"`, `data.source = {kind:
  "jira", site, key, url, project}`, `repository` stamped from the configured
  param — matches FR-2/ADR-0020 and reuses the `--heal-repo` mechanism
  (invariant #25), not a new one.
- **Exception-type correction is real, not asserted**: independently checked
  `github_client.py` — `add_label`/`search_issues`/`default_branch`/etc. never
  wrap `urllib.error.HTTPError`; only `get_issue_state`/`remove_label` swallow
  a 404. `HttpJiraClient` mirrors this shape exactly (`jira_client.py:197-214`).
  The plan's FR-1 acceptance criterion ("driver-local error type... no bare
  urllib.error.* leaks") was written on a false premise about
  `HttpGithubClient`; correcting it in ADR-0020 rather than inventing a new
  exception taxonomy nobody else in the codebase uses is the right call.
- **ADF parsing**: `_adf_to_text` is a genuine recursive text extraction
  (`jira_client.py:119-138`), not a raw-JSON passthrough, addressing the risk
  `architecture-01.md` flagged.
- **`_ACCEPTED_SINK_KINDS` correctly left untouched** — `jira` is not added,
  matching both design and architecture's reasoning (no `JiraReflector`
  exists yet to route to).
- **Full suite green**: re-ran `pytest -q` with `GITHUB_TOKEN`/
  `HARNESS_HEAL_REPO` unset → 1377 passed, 1 skipped, matching
  `development-01.md`'s claim and its noted (and here independently
  reproduced) explanation for the 8 env-dependent failures when those vars
  are set.

```json
{"outcome": "request_changes", "summary": "Implementation mirrors GithubIssuesCheck/GithubClient soundly and the full suite passes, but it silently drops the status-filter decision (`AND statusCategory != Done` on the project+label convenience JQL) that architecture-01.md explicitly flagged as a 'must resolve before coding' prerequisite and recommended adding — without it, resolved/closed Jira issues carrying the select label get re-ingested as tasks with no GitHub-equivalent `state=open` filter. Fix: add the recommended status filter to JiraIssuesCheck's convenience-form JQL (jira_issues_check.py:42) with a test on the built JQL string, or record an explicit reasoned decision against it in ADR-0020 instead of leaving it unaddressed."}
```
