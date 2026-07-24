# Design: Jira issue loader (`jira-issues` action)

No UI section — this feature has no user-facing surface beyond the existing
admin dropdowns (`ProcessAdmin.check_names()`/the process JSON form), which
already render generically from data and need no new screen or component.

This design is grounded directly against the real implementations of
`GithubIssuesCheck` (`src/harness/drivers/github_issues_check.py`),
`GithubClient`/`FakeGithubClient`/`HttpGithubClient`
(`src/harness/drivers/github_client.py`), `cli._process_check_factories`
(`src/harness/cli.py:773-831`), `ports/source.py::effective_sink_kind`, and
`fs_processes.py`'s `compile_process`/`_parse_sink`/`_ACCEPTED_SINK_KINDS`. Two
corrections against `plan-01.md` fall out of reading the real code (called out
inline below): there is no `CheckSpec`/`SPEC` construct anywhere in the
codebase — every check factory validates its own `params` dict inline, no
declarative schema type exists to mirror — and FR-4 (accepting `kind: "jira"`)
turns out to need **no code change at all**, only a documentation update,
because `effective_sink_kind` is an unconditional dict lookup with no
allow-list.

## Component design

### 1. `drivers/jira_client.py` — `JiraClient` ABC + `FakeJiraClient` + `HttpJiraClient`

Mirrors `github_client.py`'s shape exactly: one ABC naming the minimal surface,
one in-memory fake, one stdlib-`urllib` real implementation. No new production
dependency (no `requests`, no `jira` SDK) — same constraint `HttpGithubClient`
operates under.

```python
@dataclass(frozen=True)
class JiraIssue:
    key: str            # "PROJ-123" — a string, unlike GitHub's int `number`
    summary: str        # -> task title
    description: str    # -> task body
    url: str
    labels: tuple[str, ...]
    project: str        # the project key, e.g. "PROJ"


class JiraClient(ABC):
    @abstractmethod
    def search_issues(self, jql: str) -> list[JiraIssue]:
        """Issues matching a JQL query. The loader's twin of `list_issues`."""

    @abstractmethod
    def add_label(self, key: str, label: str) -> None:
        """Add a label. Adding one already set is a no-op (idempotent)."""

    @abstractmethod
    def remove_label(self, key: str, label: str) -> None:
        """Remove a label. A missing one is a no-op (idempotent)."""
```

`add_label`/`remove_label` take the issue `key` directly (not `(repo, key)` —
Jira has no separate repo/slug axis the way GitHub's `(repo, number)` pair
does; the site is fixed per client instance). `search_issues` returns already
label-filtered, open-only results — Jira's JQL expresses both the label match
and (implicitly, since a resolved/done issue is normally excluded by a
sensible default JQL) the "still to do" filter, so unlike `list_issues(repo,
label=...)` there is no separate `state="open"` GitHub-style flag: the caller
supplies a JQL that already encodes what "still open" means for their Jira
project (v1's `project = {project} AND labels = {label}` convenience form does
not itself filter by status — see Open Question 3 below, carried from
`plan-01.md`, still open and worth a decision before implementation: should
the convenience form add `AND statusCategory != Done`?).

`FakeJiraClient(issues: list[JiraIssue] | None = None)` — same shape as
`FakeGithubClient`: an in-memory `dict[str, JiraIssue]` keyed by `key`,
`add_issue`/`close_issue`-style test helpers as needed by the check's tests
(only `search_issues`/`add_label`/`remove_label` are exercised by
`JiraIssuesCheck`, so the fake needs no PR/reconciler-shaped surface at all —
a much smaller fake than `FakeGithubClient`, which also serves the forge and
two reconcilers).

`HttpJiraClient(base_url, email, api_token, *, opener=None)` — real client
against `{base_url}/rest/api/3`. Basic auth via `email:api_token` (Jira
Cloud's documented API-token scheme), stdlib `urllib.request` exactly like
`HttpGithubClient`:
- `search_issues`: `GET /rest/api/3/search?jql=<urlencoded>&fields=summary,description,labels,project`
  (`description` in the v3 API is Atlassian Document Format, not markdown —
  the client extracts a plain-text approximation for the task body; a richer
  ADF→text render is a follow-up, not a v1 blocker, but must not raise on a
  present-but-non-string `description`).
- `add_label`/`remove_label`: `PUT /rest/api/3/issue/{key}` with a `labels`
  update op (`{"update": {"labels": [{"add": "..."}]}}` /
  `{"remove": "..."}`) — Jira's REST update-op idiom, the twin of GitHub's
  dedicated `.../labels` endpoints. A remove on an already-absent label must
  not raise (mirrors `HttpGithubClient.remove_label`'s 404-swallow — Jira's
  update op is naturally idempotent here since it targets a value, not an
  index, so no special-case error handling is expected, but this must be
  confirmed against the real API's behavior during implementation, not
  assumed).

No new port. `JiraClient` lives entirely in `drivers/`, exactly as
`GithubClient` does — there is no `ports/jira.py`, mirroring how GitHub's
client ABC also has no dedicated port (only the *check*, `GithubIssuesCheck`,
sits behind the generic `Check`/`TaskSource` ports).

### 2. `drivers/jira_issues_check.py` — `JiraIssuesCheck(Check)`

Structurally identical to `GithubIssuesCheck`, with one structural difference
driven by a real constraint: GitHub's check iterates `registry.names()` and
derives each repo's slug via `github_slug()`, because a GitHub issue is
intrinsically scoped to the repo it lives in. A Jira issue carries no such
repo axis, so `JiraIssuesCheck` takes one `repository: str` constructor
param naming the single registered repo every issue it emits attaches to
(Open Question 1, decided in `plan-01.md` — confirmed against the real
`GithubIssuesCheck` code, not assumed).

```python
class JiraIssuesCheck(Check):
    def __init__(
        self,
        *,
        client: JiraClient,
        repository: str,
        label: str = "harness-todo",
        claimed_label: str = "harness-queued",
        jql: str | None = None,
        project: str | None = None,
    ) -> None:
        if jql is None and project is None:
            raise ValueError("jira-issues requires 'jql' or 'project'")
        self._client = client
        self._repository = repository
        self._label = label
        self._claimed_label = claimed_label
        self._jql = jql or f'project = {project} AND labels = "{label}"'
        self._claimed: set[str] = set()  # already-claimed issue keys this run

    def evaluate(self) -> list[Observation]:
        observations = []
        for issue in self._client.search_issues(self._jql):
            if issue.key in self._claimed:
                continue
            self._claimed.add(issue.key)
            self._client.remove_label(issue.key, self._label)
            self._client.add_label(issue.key, self._claimed_label)
            observations.append(
                Observation(
                    state_key=f"jira:{issue.key}",
                    repository=self._repository,
                    data={
                        "title": issue.summary,
                        "body": issue.description,
                        "source": {
                            "kind": "jira",
                            "site": <client's configured base_url/site id>,
                            "key": issue.key,
                            "url": issue.url,
                            "project": issue.project,
                        },
                    },
                )
            )
        return observations
```

Note the `Observation.repository` field is read directly by
`ScheduledTrigger._task_for` (`repository=obs.repository or self._repository`,
`drivers/scheduled_trigger.py:111`) — the check's per-observation
`repository` always wins there; the trigger-level `repository` (a
`FilesystemProcessRepository.build()`-wide default, itself unused by any
current process) is the fallback. So a single, fixed `self._repository`
stamped onto every `Observation` this check emits is exactly the mechanism
already in place, not a new one — the same generic seam autoheal's
`--heal-repo` uses (invariant #25) to give a repository-less task a worktree.

The constructor validates `jql is None and project is None` at construction —
i.e. inside the `cli._process_check_factories` factory closure, which runs at
process *build/write* time, not inside `evaluate()` — so a misconfigured
process fails fast exactly like a missing `GITHUB_TOKEN` does (mirrors FR-3's
fail-fast requirement).

`site` in `data.source` needs a concrete value: the client should expose its
own configured `base_url` (e.g. as a public attribute or a
`site` property) so the check can read it without a second constructor
param duplicating what the client already knows — avoids a second place
where the Jira site URL could drift out of sync between the client and the
check.

### 3. Wiring — `cli._process_check_factories` (no new function)

Extends the existing function exactly as `github-conflicts` sits alongside
`github-issues` in the same returned dict (`src/harness/cli.py:773-831`):

```python
def jira_issues_factory(params: dict) -> JiraIssuesCheck:
    if jira_client is None:
        raise ProcessValidationError(
            "jira-issues action requires JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN",
            field="check",
        )
    repository = params.get("repository")
    if not isinstance(repository, str) or not repository:
        raise ProcessValidationError(
            "jira-issues action requires params.repository", field="params",
        )
    if repository not in registry.names():
        raise ProcessValidationError(
            f"jira-issues action names an unknown repository {repository!r}",
            field="params",
        )
    label = params.get("label", "harness-todo")
    claimed_label = params.get("claimed_label", "harness-queued")
    jql = params.get("jql")
    project = params.get("project")
    if not isinstance(label, str) or not isinstance(claimed_label, str):
        raise ProcessValidationError(
            "jira-issues action requires label/claimed_label to be strings",
            field="params",
        )
    if jql is None and project is None:
        raise ProcessValidationError(
            "jira-issues action requires params.jql or params.project",
            field="params",
        )
    return JiraIssuesCheck(
        client=jira_client, repository=repository,
        label=label, claimed_label=claimed_label, jql=jql, project=project,
    )
```

`jira_client` is built once at the top of `_process_check_factories`, mirroring
the existing `client`/`GITHUB_TOKEN` pattern exactly:

```python
if jira_client is None:  # injectable for tests, same shape as `client: GithubClient | None`
    base_url = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    jira_client = (
        HttpJiraClient(base_url, email, token)
        if base_url and email and token
        else None
    )
```

`_process_check_factories`'s signature gains one new injectable keyword,
`jira_client: JiraClient | None = None`, alongside the existing `client:
GithubClient | None = None` — both default `None` and are resolved from the
environment when absent, exactly parallel. Its docstring line ("each closed
over a `GithubClient` + the repo registry") gets a one-clause update to also
name the new `JiraClient` closure.

`app.build()` needs **no change** — it already accepts the merged
`extra_checks` dict `_process_check_factories` returns (`cli.py:1711`,
`extra_checks = _process_check_factories(args, registry, client=github_client)`
just gains the one new dependency threaded through the same call).

### 4. `kind = "jira"` outbound routing — **no code change**

Rereading `ports/source.py::effective_sink_kind` (lines 25-37) shows it is an
unconditional dict lookup:

```python
def effective_sink_kind(task: Task) -> str | None:
    sink = task.data.get("sink")
    if isinstance(sink, dict) and sink.get("kind"):
        return sink["kind"]
    return task.data.get("source", {}).get("kind")
```

There is no allow-list here to extend — `plan-01.md`'s FR-4 ("invariant #40's
list of accepted kinds is extended to include jira") turns out to describe a
**documentation-only** change, not a code change: `effective_sink_kind` will
already return `"jira"` for a Jira-sourced task with no code change at all.
`SourceReflectorSink`/`MergeReconciler`/`IssueReconciler` compare this value
against their own `kind` (`"github"`, etc.) and simply don't match — the
existing "foreign kind is silently ignored" behavior, verified by reading
`source_reflector.py`'s `_mine` gate.

The one place an actual allow-list *does* exist is
`fs_processes.py::_ACCEPTED_SINK_KINDS = {"none", "slack", "github"}`
(line 66), which gates the Process's own `sink.kind` field — i.e. whether a
process file may *declare* `"sink": {"kind": "jira"}"`. v1's Jira process
always declares `"sink": {"kind": "none"}"` (no outbound reflector exists
yet), so **this set does not need "jira" added for v1** either — adding it
now would be premature: it would make a `sink: {"kind": "jira"}` process
compile successfully with no driver to ever route on it, silently inert,
exactly the failure mode `_parse_sink`'s docstring warns against for `github`
today. Add `"jira"` to `_ACCEPTED_SINK_KINDS` only alongside the future
`JiraReflector` (outbound reflection, explicitly out of scope for v1 per
`plan-01.md`), not in this increment.

Net effect: FR-4 collapses to **CLAUDE.md wording only** — invariant #40's
prose should still be updated to say `data.source.kind` may legitimately be
`"jira"` now (so a future reader isn't surprised), but no test, no source
change.

### 5. Process authoring shape

Unchanged from `plan-01.md`'s example — validated against the real
`compile_process`/`_parse_action` code path, which is check-name-agnostic
(`checks[check_name](action.get("params", {}))`, `fs_processes.py:199`):

```json
{
  "trigger": { "interval": "60s" },
  "action":  { "check": "jira-issues",
               "params": { "project": "PROJ", "label": "harness-todo",
                           "repository": "my-service" } },
  "target":  { "workflow": "default" },
  "sink":    { "kind": "none" }
}
```

`FilesystemProcessRepository.build()`'s cross-file collision guard (the
`github-issues` label-collision check, `fs_processes.py:290` `seen` dict) is
**GitHub-`github-issues`-specific by name** — it does not generically cover
every check kind. A Jira-equivalent collision (two `jira-issues` processes
racing over the same `label`/`claimed_label` on the same Jira site) is
**not caught** by the existing guard and is explicitly not proposed here as
new scope; call this out as a known gap in the design (mirrors the residual
footgun `fs_processes.py`'s own docstring already accepts for `github-issues`
across different repos using the same label).

## Data schemas

### `JiraIssue` (new, `drivers/jira_client.py`)

| field | type | notes |
|---|---|---|
| `key` | `str` | e.g. `"PROJ-123"` — **not** an int, unlike GitHub's `number` |
| `summary` | `str` | → `Observation.data["title"]` |
| `description` | `str` | → `Observation.data["body"]`; plain-text extracted from ADF |
| `url` | `str` | the issue's browser URL |
| `labels` | `tuple[str, ...]` | |
| `project` | `str` | the project key |

### `Task.data.source` for a Jira-born task

```json
{
  "kind": "jira",
  "site": "https://acme.atlassian.net",
  "key": "PROJ-123",
  "url": "https://acme.atlassian.net/browse/PROJ-123",
  "project": "PROJ"
}
```

`key` is a string — every current downstream reader of `data.source`
(`github_issue_checker.py`, `github_merge_checker.py`, `source_reflector.py`)
was reread for this design and confirmed to pattern-match on
`kind == "github"` before ever touching `issue`/`repo`, so a `kind: "jira"`
task is simply invisible to them today, correctly (no Jira reconciler exists
yet — same conclusion `plan-01.md`'s Open Question 5 reached, now confirmed
against the actual reconciler source rather than assumed).

### `Task.dedup_key` (via `ScheduledTrigger._dedup_key`, `per-state`)

`dedup_key("scheduled:<process-name>", "wf:<workflow-or-step>", "jira:PROJ-123")`
— the process must declare `"dedup": "per-state"` for a Jira ingestion process
(the example above should be corrected to include this — `plan-01.md`'s
example JSON omits `"dedup"`, which defaults to `"per-interval"` and would be
**wrong** for issue ingestion: `per-interval` fires at most once per interval
bucket regardless of how many issues match, collapsing every issue in a tick
into a single dedup identity. `github-issues`-driving processes must specify
`"dedup": "per-state"` too — worth double-checking the shipped example/docs
for `github-issues` follow the same rule, since this is a correctness
requirement of the mechanism itself, not unique to Jira).

### `Observation` (per issue, emitted by `JiraIssuesCheck.evaluate()`)

```python
Observation(
    state_key=f"jira:{issue.key}",
    repository="my-service",   # the configured `repository` param, verbatim
    data={
        "title": issue.summary,
        "body": issue.description,
        "source": {"kind": "jira", "site": ..., "key": issue.key,
                   "url": issue.url, "project": issue.project},
    },
)
```

## Corrections against `plan-01.md` (for the review to weigh)

1. **No `CheckSpec`/`SPEC` construct exists in the codebase.** Every check
   factory (`BUILTIN_CHECKS`, `_process_check_factories`) validates its own
   `params` dict inline with `params.get(...)` + ad-hoc `isinstance` checks —
   there is no declarative schema type for the admin form to introspect
   beyond `ProcessAdmin.check_names()` (just the sorted key list) and
   `sink_kinds()`. Drop `SPEC = CheckSpec(...)` from the design; params
   validation for `jira-issues` is hand-written inline in the factory closure,
   exactly like `github-issues`'s own `label`/`claimed_label` type check.
2. **FR-4 needs no code change** (see Component 4 above) — only a CLAUDE.md
   wording update to invariant #40's prose. This shrinks the v1 diff by one
   whole area plan-01.md scoped as its own functional requirement.
3. **`_ACCEPTED_SINK_KINDS` should *not* gain `"jira"` in v1** — it gates
   whether a process may *declare* a Jira sink, which has no driver yet
   (`JiraReflector` is explicitly deferred). Adding the kind now would let a
   process silently declare an inert sink, the exact anti-pattern
   `_parse_sink`'s own docstring calls out for `"github"` on a non-GitHub-
   sourced task.
4. **The example `processes/jira-ingest.json` must set `"dedup":
   "per-state"` explicitly** — omitting it (as `plan-01.md`'s example does)
   defaults to `"per-interval"`, which is wrong for per-issue ingestion (see
   Data Schemas above). This should also prompt a check of whatever the
   shipped `github-issues` example/docs use, to confirm they aren't carrying
   the same latent mistake.

## Open questions carried forward from `plan-01.md` (unchanged, still need a decision before implementation)

1. Repo mapping: one `repository` param per Process (decided).
2. Claim mechanism: label-swap, not status transition (decided).
3. Selection form: `jql` wins over `project`+`label` (decided) — **newly
   surfaced sub-question**: should the `project`+`label` convenience form also
   inject a status-category filter (e.g. `AND statusCategory != Done`), since
   unlike GitHub's `list_issues(..., state="open")` there is no implicit
   "open only" filter in a bare JQL label match?
4. Jira Cloud only for v1 (decided).
5. No numeric-id assumptions downstream (confirmed against the real
   reconciler/checker source, not just grepped for keywords).
6. `harness init` should not ship a `jira-ingest.json` template (decided).
