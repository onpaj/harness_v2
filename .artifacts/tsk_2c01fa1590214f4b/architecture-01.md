# Architecture assessment: Jira issue loader (`jira-issues` action)

## Verdict

Approve the shape in `design-01.md`, with the amendments below. This is a
textbook instance of the seam the codebase was already built to take: a new
`TaskSource` reached through a new `Check`, wired only in `cli.py`. I checked
the design's claims against the real files it cites — `github_issues_check.py`,
`ports/source.py`, `scheduled_trigger.py`, `fs_processes.py`, `cli.py:773-831`
— and every load-bearing one holds. No invariant needs to bend; one (#40)
needs a documentation-only edit. Proceed to implementation once the two
decisions flagged under "Must resolve before coding" are made.

## Alignment with existing patterns

The design is not "Jira-shaped code that happens to reuse some types" — it is
the same code, with GitHub's API swapped for Jira's, at every seam that
matters:

- **Driver, not surroundings.** `JiraClient` gets no port (`ports/jira.py`
  does not and should not exist) — exactly like `GithubClient`, which also
  has no dedicated port. Only `JiraIssuesCheck` sits behind a real port
  (`ports/triggers.py::Check`), the same as `GithubIssuesCheck`. This
  respects invariant #39 (a Process compiles to primitives the runtime
  already knows) without any new abstraction.
- **Wiring stays in `cli.py`.** The factory closure pattern in
  `_process_check_factories` (`cli.py:773-831`) is check-name-agnostic
  already — adding `jira-issues` is one more dict entry and one more
  env-var-gated client construction, not a new function. `app.build()`
  (`cli.py:1711`) needs zero changes, confirmed by reading the call site: it
  already just forwards whatever `extra_checks` dict it's handed.
- **Fail fast at the same layer GitHub does.** `github-issues` without
  `GITHUB_TOKEN` raises `ProcessValidationError(field="check")` from inside
  the factory closure, at process build/write time
  (`cli.py:800-801`). The design's `jira_issues_factory` reproduces this
  exactly. This is the correct layer — not `JiraIssuesCheck.__init__`, not
  `evaluate()` — because it's what makes the action fail loudly on `harness
  serve`/admin-save rather than silently at the next scheduled tick.
- **FR-4 correctly collapses to a no-op.** I reread `effective_sink_kind`
  (`ports/source.py:25-37`) myself: it is an unconditional dict lookup with
  no allow-list, so it already returns `"jira"` today for any task whose
  `data.source.kind` is `"jira"`. The one real allow-list,
  `fs_processes.py:66`'s `_ACCEPTED_SINK_KINDS = {"none", "slack",
  "github"}`, gates a Process's own *declared* `sink.kind` — and the design
  is right not to touch it, because there is no `JiraReflector` to route to
  yet. Adding `"jira"` there now would let a process compile with a sink
  that silently does nothing, which is precisely the anti-pattern
  `_parse_sink` already warns about. This is the one place in the whole
  design where the instinct to "mirror GitHub everywhere" would have been
  wrong, and the design correctly resists it.
- **Dedup correctness is real, not a nitpick.** I independently confirmed
  against `docs/superpowers/specs/2026-07-22-processes-design.md:95-126` and
  `fs_processes.py:135` that `dedup` defaults to `"per-interval"` and that
  ingestion-style checks (one task per matched item, not one per tick) need
  `"per-state"` explicitly. This is a correctness requirement of the
  mechanism, not a Jira-specific concern, and the example JSON in this
  design must carry `"dedup": "per-state"` or the whole feature silently
  degenerates to "at most one Jira issue ingested per interval, ever."
- **Repository stamping reuses, not invents, a mechanism.** `Observation.repository`
  flowing through to `ScheduledTrigger._task_for` (`obs.repository or
  self._repository`, `scheduled_trigger.py:111`) is the exact mechanism
  `--heal-repo`/invariant #25 already uses to give a repository-less task a
  worktree. One `repository: str` constructor param on `JiraIssuesCheck` is
  the minimal, precedented answer to "a Jira issue has no intrinsic repo
  axis" — not a new concept.

Net: this design earns a genuine "swap the driver" verdict, not a "mirror by
name, drift architecturally" one. The three deliberate deviations from
GitHub's shape (single `repository` param instead of per-repo slug
resolution; no `_ACCEPTED_SINK_KINDS` change; a smaller `FakeJiraClient`
surface) are each forced by a real difference in the two systems, not
convenience.

## Proposed architecture

No component beyond what `design-01.md` lists is needed. Restated as the
integration map:

```
processes/jira-ingest.json
   └─ compile_process() [fs_processes.py, unchanged]
        └─ ScheduledTrigger [unchanged] ── check: JiraIssuesCheck ── client: JiraClient
                                                     (new, ports/triggers.Check)   (new, no port)
                                                          │
                                                          ▼
                                                  Observation(state_key="jira:KEY",
                                                              repository=<param>,
                                                              data.source={kind:"jira",...})
                                                          │
                                              SourcePoller [unchanged] ── inbox
```

Two new modules only, both under `drivers/`:

- `drivers/jira_client.py` — `JiraIssue` (frozen dataclass), `JiraClient`
  (ABC: `search_issues`, `add_label`, `remove_label`), `FakeJiraClient`,
  `HttpJiraClient`.
- `drivers/jira_issues_check.py` — `JiraIssuesCheck(Check)`.

Everything else is an edit to an existing file: `cli.py` (`_process_check_factories`),
CLAUDE.md (module map + invariant #40 prose), one new ADR.

### Key decision: `JiraClient` gets no port

Considered: giving Jira its own port (`ports/jira.py`) for symmetry with
`ports/repos.py`, `ports/forge.py`, etc.

Rejected, on precedent: `GithubClient` — a comparably rich API surface used
by five different drivers (`github_forge`, `github_issue_checker`,
`github_merge_checker`, `github_issues_check`, `github_conflicts_check`) —
has never been given a port. The pattern in this codebase is: a port exists
where the *orchestration core* (dispatcher/consumer/router) or a *behavior*
needs to depend on the capability abstractly. A `Check` is already the
abstraction the orchestration core depends on; `JiraClient` is an
implementation detail one driver depends on, same as `GithubClient` today.
Introducing `ports/jira.py` now, with a single caller, would be
architecture ahead of need — exactly what the project's "don't design for
hypothetical future requirements" stance rules out. If a second Jira-aware
driver appears later (the `JiraReflector` follow-up will be exactly that),
the ABC already sitting in `drivers/jira_client.py` serves both callers with
no port needed then either — that's the `GithubClient` precedent playing out
a second time.

### Key decision: one `repository` param, not a slug-derivation strategy

Considered: a richer `project → repository` mapping so one Process could
serve multiple Jira projects into different repos.

Rejected for v1, per `plan-01.md`'s Open Question 1 and confirmed here: it
adds a second config surface (a map, not a scalar) for a need with no
concrete requester yet, and the fallback — one Process file per project — is
already fully expressive using existing primitives (multiple `processes/*.json`
files are how `github-issues` already handles "different repos need different
labels", per invariant #39's cross-file collision guard). Revisit only when
an actual multi-project-one-repo-mapping use case shows up.

## Implementation guidance

Follow the plan's step order (`plan-01.md`'s "Rough plan", 1–8); it is
already correctly sequenced client-first, check-second, wiring-third. Two
refinements to that order:

1. **Write the ADR (step 1) after the client + check exist, not before.**
   An ADR that records "label-swap vs. transition" and "JQL vs.
   project+label" decisions is more useful, and cheaper to get right, once
   the two Open Questions below are actually resolved — writing it first
   risks the ADR being edited mid-implementation instead of being the
   settled record it should be. Keep it as its own commit, just move it
   after FR-1/FR-2 land.
2. **Land `HttpJiraClient`'s request-shaping test (URL, JQL encoding,
   Basic-auth header) in the same commit as `FakeJiraClient`**, not
   deferred — this is the one component with no orchestration-level test
   coverage otherwise (nothing exercises `HttpJiraClient` except a live
   Jira, which CI won't have), mirroring however `HttpGithubClient` is
   covered today. Confirm what that coverage actually looks like
   (`tests/` for `HttpGithubClient`) before writing this test, rather than
   assuming a shape.

### Data flow (concrete walk)

1. Operator writes `processes/jira-ingest.json` with `action.check =
   "jira-issues"`, `action.params = {project, label, repository}`,
   `dedup: "per-state"`.
2. `FilesystemProcessRepository.build()` compiles it via `compile_process`
   into a `ScheduledTrigger`, resolving `"jira-issues"` against the merged
   check-factory dict `_process_check_factories` returns — this call fails
   fast, at build time, if `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` are
   unset or `params.repository` doesn't name a registered repo.
3. On its interval, `ScheduledTrigger.poll()` gates on the occurrence
   (unchanged code), then runs `JiraIssuesCheck.evaluate()`.
4. `evaluate()` runs the JQL, skips anything in `self._claimed` this run,
   claims via `remove_label` + `add_label`, and returns one `Observation`
   per issue.
5. `ScheduledTrigger._task_for` builds the `Task`, stamping
   `repository=obs.repository` (the configured repo — never `None` here
   since the factory already validated it) and `dedup_key` from
   `obs.state_key` (`"jira:PROJ-123"`) because `dedup == "per-state"`.
6. `SourcePoller` ingests it into the inbox exactly like any other source —
   no code downstream of this point needs to know "jira" exists.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A `jira-issues` process omits `"dedup": "per-state"` and silently collapses every matching issue in a tick into one task. | The shipped example in the ADR/docs must set it explicitly (design-01 already flags this); consider whether `JiraIssuesCheck`-driving processes should *require* `per-state` at compile time rather than merely defaulting wrong — worth a one-line discussion in the ADR, not necessarily a code change, since `github-issues` carries the identical footgun today and hasn't needed one. |
| Two `jira-issues` processes on the same Jira site race over the same `label`/`claimed_label` (design-01 correctly notes the existing cross-file collision guard is `github-issues`-specific by name, `fs_processes.py:290`, and won't catch this). | Accept for v1 — this is the same residual footgun `github-issues` already has across different repos sharing a label, per that guard's own docstring. Don't scope-creep a generic collision guard into this feature; note it as a known gap in the ADR so it isn't rediscovered as a "bug" later. |
| Jira Cloud's `description` field is Atlassian Document Format (ADF), not plain text/markdown — a naive `str(description)` produces garbage in the task body. | `HttpJiraClient` must extract plain text from ADF (a recursive walk of `content[].content[].text`, the minimal case) rather than passing the raw JSON through. This is real client-shaping work, not a detail — flag it as a concrete implementation task, not an afterthought, and don't let "must not raise on a non-string description" (design-01's phrasing) substitute for actually rendering it readably. |
| `remove_label`/`add_label` non-idempotency assumptions are unverified against the real Jira API (design-01 flags this itself). | Confirm during `HttpJiraClient` implementation, not assumed; if Jira's update-op *does* error on removing an absent label (unlike GitHub's 404-swallow), `HttpJiraClient.remove_label` needs the same try/except-and-ignore shape `HttpGithubClient.remove_label` has — write the test for this behavior explicitly, don't just eyeball the Jira docs. |
| A Jira REST error surfaces as a bare `urllib.error.HTTPError`/`json.JSONDecodeError` past the module boundary, breaking the "callers only ever see the driver's own exceptions" contract `plan-01.md`'s FR-1 acceptance criterion states. | Give `HttpJiraClient` its own exception type (mirror whatever `HttpGithubClient` raises — check its actual exception class before inventing a new name) and wrap every request. |

## Prerequisites before implementation begins

1. **Resolve the two still-open decisions**, both flagged in `design-01.md`'s
   final section and neither trivial to change post-hoc since they shape
   the JQL-building code and the ADR text:
   - Should the `project`+`label` convenience form inject a status filter
     (e.g. `AND statusCategory != Done`)? Recommend **yes** — without it,
     a resolved/closed issue that still carries the select label (an issue
     re-opened workflow, or a label never cleaned up) gets re-ingested
     forever, which is a materially worse failure mode than GitHub's
     equivalent (`list_issues(..., state="open")` already filters this).
     This is a one-line addition to the convenience-form JQL template, cheap
     to decide now and correct pre-emptively rather than patch in after a
     bug report.
   - Confirm the exact exception type `HttpGithubClient` raises today (read
     `github_client.py`, not assumed) so `HttpJiraClient` mirrors it exactly
     rather than inventing a parallel taxonomy.
2. **Read `tests/` for the existing `HttpGithubClient`/`GithubIssuesCheck`
   suites before writing the Jira ones** — the design correctly says "mirror
   the GitHub tests" but the implementer needs the actual test file names
   and fixture shapes, not a description of them, to keep the two suites
   structurally identical (this assessment did not enumerate them; that's
   an implementation-time lookup, not an architecture concern).
3. **No infrastructure/environment prerequisite** — `JIRA_BASE_URL`/
   `JIRA_EMAIL`/`JIRA_API_TOKEN` are runtime-only and the whole test suite
   runs against `FakeJiraClient`; nothing blocks starting the client + check
   modules today.

## Corrections carried forward

`design-01.md`'s two corrections against `plan-01.md` (no `CheckSpec`/`SPEC`
construct exists anywhere in the codebase; FR-4 needs no code change) are
both verified accurate by independent reading of the cited source in this
step, not merely re-asserted. Nothing in this architecture step found a
third correction — the design is implementation-ready modulo the two open
decisions above.

```json
{"outcome": "done", "summary": "Reviewed plan-01.md and design-01.md against the real github_issues_check.py/ports/source.py/scheduled_trigger.py/fs_processes.py/cli.py source; confirmed the design's grounding is accurate (no port for JiraClient, FR-4 is doc-only, dedup:per-state is load-bearing). Wrote architecture-01.md: approves the two-module shape (drivers/jira_client.py, drivers/jira_issues_check.py) with cli.py-only wiring, justifies no-new-port and single-repository-param as precedented (GithubClient, --heal-repo) rather than convenience, flags ADF description parsing and Jira remove_label idempotency as concrete implementation risks, and calls out two still-open decisions (status-filter in the convenience JQL form; HttpGithubClient's actual exception type to mirror) to resolve before coding starts."}
```
