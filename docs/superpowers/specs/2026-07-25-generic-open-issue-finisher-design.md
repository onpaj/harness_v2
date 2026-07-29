# A generic `open-issue` finisher, and workflow serving as data

Status: draft
Date: 2026-07-25

## Goal

`open-issue` is a finisher kind in name only. `OpenIssueBehavior`
(`behaviors/open_issue.py`) is the healer's outbound leg with a generic label
on it: it reads the marker out of `task.data["heal"]["of"]`, scans artifacts
for a step literally named `heal`, takes its repository from a wiring-time
`--heal-repo`, forces the `harness:self-heal` label onto every issue it opens,
and files **exactly one** issue per task. ADR-0016 promised "a new finishing
action is a registry entry plus a binding in the workflow JSON, never a new
branch" — but the only issue-filing action in the system cannot be bound to
any step that isn't the healer's.

The trigger for fixing this is a second consumer: a **daily rotating
architecture review** of `Anela.Heblo`. A scheduled Process wakes an agent once
a day, points it at one of 29 backend modules, and the agent files 0–5 GitHub
issues for the architecture violations it finds. That is the same shape as the
healer — an agent drafts, the worker files (invariants 9/26) — differing only
in repository, label, step name, and cardinality. All four are exactly what
`OpenIssueBehavior` hardcodes.

This spec makes `open-issue` a genuine finisher kind, driven entirely by its
`FinisherBinding.config`, and expresses the healer as one binding of it. If
heal cannot be written as config, the abstraction is wrong.

A second change rides along because the first one enables it: **`harness run`
serves every workflow file on disk**, and the `--workflow` / `--all-workflows`
flags are removed. See §6.

## Non-goals

- No new port. `IssueTracker.open_issue` stays a per-issue verb; the loop lives
  in the behavior, where the policy (marker derivation, label filtering) belongs.
- No change to the artifact layout. Artifacts stay `<step>-<NN>.md`.
- No semantic dedup of findings against previously-filed issues. The marker
  gives per-task idempotency only; recognising "we filed this last month" stays
  the persona's job, via `gh issue list` in its own prompt.
- The arch-review persona itself is data (`agents/`, `workflows/`,
  `processes/`), not part of this change.

## 1. The artifact contract

The step writes its ordinary markdown report to the relpath `compose_prompt`
gave it, and ends the file with a fenced `json` block holding an **array of
issue drafts**:

````markdown
# Architecture Review: Analytics (2026-07-25)

...the human-readable report...

```json
[
  {
    "title": "Analytics: AnalyticsQueryHandler mixes fetching, aggregation and formatting",
    "body": "## Finding\n...concrete file paths and line ranges...",
    "labels": ["tech-debt"]
  }
]
```
````

The finisher takes the **last** fenced `json` block in the artifact — the same
rule `_extract_verdict` already applies to the agent's final message
(`drivers/claude_cli.py`, `_FENCED_JSON`). One convention, learned once.

The block lives inside the `.md` artifact rather than in a `.json` file of its
own because `artifacts_layout.STEP_ATTEMPT` matches `^(?P<step>.+)-(?P<nn>\d+)\.md$`
and `compose_prompt` names that exact relpath to the agent. Adding a second
artifact kind would touch the write side, the view and the board for no gain
the fenced block does not already provide.

Per-draft fields:

| Field | Required | Meaning |
|---|---|---|
| `title` | yes, non-empty | Issue title. Also the input to the marker hash. |
| `body` | no, defaults `""` | Issue body, markdown. |
| `labels` | no, defaults `[]` | Per-issue labels, filtered against the binding's allowlist. |

**`[]` is a success, not a degenerate case.** The arch-review routine's quality
bar explicitly says "if the module is genuinely clean, file zero" — an empty
array is how the agent says that, and the finisher returns `done` with the
summary `no issues to file`.

## 2. The binding config

`FinisherBinding.config` (ADR-0018 — everything but `"kind"` in the object form)
carries three keys:

| Key | Required | Default | Meaning |
|---|---|---|---|
| `label` | yes | — | The label every issue from this binding carries, and the scope of the idempotency search. |
| `from_step` | no | absent | The step whose artifact holds the drafts. **Presence selects the shape** — see §3. |
| `allowed_labels` | no | `[]` | Allowlist for per-draft `labels`. Empty means the binding takes no per-issue labels at all. |

`config` is validated by the factory at `build()` time, not at parse time —
the posture ADR-0018 set (`_parse_action` does not validate check params
either). A binding with no `label` fails the build.

The two bindings this spec must express:

```json
// workflows/heal.json — replace shape, one issue, unchanged behaviour
"finishers": {
  "file-issue": { "kind": "open-issue", "from_step": "heal", "label": "harness:self-heal" }
}
```

```json
// workflows/arch-review.json — wrap shape, 0..N issues
"finishers": {
  "review": {
    "kind": "open-issue",
    "label": "arch-review",
    "allowed_labels": ["tech-debt", "refactoring", "code-quality", "architecture",
                       "complexity", "duplication", "domain-logic", "business-logic",
                       "maintainability", "design-patterns"]
  }
}
```

## 3. Two shapes, selected by `from_step`

ADR-0018 established that a finisher factory can either replace a step's
behavior or wrap it. `open-issue` supports both, and the config selects which:

- **`from_step` present → replace.** The factory never calls the `inner()`
  thunk, so no agent is resolved for the bound step — which is what keeps
  heal's agent-less `file-issue` step working (`_write_default_agents` writes
  no agent file for it, and eagerly resolving one would raise `AgentNotFound`;
  this is the exact trap ADR-0018 documents for the landing step). The
  behavior reads the artifact of the *named* step and returns `done`.
- **`from_step` absent → wrap.** The factory calls `inner()`, the step's own
  persona runs and returns a verdict, then the finisher reads the artifact
  that step just wrote and files from it. The **inner result's outcome is
  returned unchanged** so routing is untouched — the same contract
  `label-issue` honours.

The wrap shape is what lets arch-review be a **one-step** workflow. Without
it, it would need a second, agent-less `file-issue` step purely to have
something to bind to.

**Filing is not conditional on the outcome.** A wrapped step reporting
`request_changes` still files what it wrote; the persona controls filing by
writing `[]`. One lever, not two. Outcome-gating, if ever wanted, is another
config key — not a redesign.

## 4. Repository identity

`OpenIssueBehavior` currently takes its repo from `--heal-repo`, whose value
must be **both** a GitHub `owner/repo` slug (it is passed straight to
`IssueTracker.open_issue`) **and** a `repos.json` key (`cli.py` calls
`registry.resolve(heal_repo)`, and `GitWorkspace.attach` resolves
`task.repository` through the same registry — invariant 25 stamps the heal
repo onto the heal task). Those two identities are not the same string, and on
the live install they disagree: `HARNESS_HEAL_REPO=onpaj/harness_v2` against a
`repos.json` keyed `harness_v2`. The result is a startup warning
(29 occurrences in `logs/harness.error.log`) and self-healing silently inert —
heal tasks fail to attach a worktree, land in `failed/`, and the recursion
guard retires them to `healed/` with nothing filed.

The finisher therefore **derives the slug from the task**: `task.repository` →
`RepositoryRegistry.resolve` → path → `github_slug(path)` from the clone's
`origin` remote. This is exactly how `GithubForge` picks the repo for a PR, and
how `GithubIssuesCheck` / `GithubConflictsCheck` / `GithubIssueImport` already
work. `repos.json` keeps holding paths only; the slug is never duplicated in
config. `harness_v2` → `/Users/rem/harness-app` → `onpaj/harness_v2`, which is
the value the env var was trying to express.

**Architecture constraint.** `github_slug` lives in `drivers/git_remote.py`,
and `test_behaviors_import_only_ports_not_drivers` forbids `behaviors/` from
importing a driver. So `OpenIssueBehavior` does **not** resolve the slug
itself: wiring (`cli.py`) builds a `Callable[[str], str]` closing over the
registry and injects it, the same way every other driver dependency reaches a
behavior. This keeps `open_issue.py` in `behaviors/` rather than following
`label-issue` into `drivers/`.

## 5. Markers, labels, and idempotency

**Marker.** `f"{task.id}:{sha1(title.encode()).hexdigest()[:8]}"`, embedded in
the issue body as an HTML comment, as today.

- *Task-scoped*, so a re-run after a crash re-finds the issues this task
  already opened and files nothing new — the guarantee the heal marker
  (`task.data["heal"]["of"]`) provides today, preserved for a task that files
  several.
- *Content-scoped within the task*, so reordered or partially-changed findings
  still match the right issue. A positional `task.id:index` would silently
  return the wrong existing issue when a re-run reorders its findings.
- A re-worded title on a re-run files a duplicate. Accepted: a re-run only
  happens after a failure, and the alternative (an agent-supplied stable key)
  trusts the LLM to be consistent across days, which silently duplicates when
  it drifts.

**Marker prefix** becomes `harness-issue:` (was `harness-heal:`). No existing
issue carries the old prefix on this install, since self-healing has never
successfully filed one.

**Labels.** Every issue gets `config.label`, which is also the label the
idempotency search scopes to — `search_issue_by_marker` scans open issues
carrying it rather than using the Search API. Per-draft labels are intersected
with `config.allowed_labels`; anything else is **dropped and named in the
summary**, never sent. This is a deliberate guard: GitHub rejects an unknown
label with a 422, which would raise `IssueError` and fail the whole step over
one hallucinated word.

`SELF_HEAL_LABEL` stops being forced onto every issue and stops being the
hardcoded search scope. It survives only as the string heal's binding passes.

### Port and driver changes

- `IssueTracker.open_issue(...)` gains `scope_label: str`. Idempotency is now
  documented as per `(repo, scope_label, marker)`.
- `GithubClient.search_issue_by_marker(repo, marker)` gains `label` — three
  implementations: the ABC, `FakeGithubClient`, `HttpGithubClient`.
- `MemoryIssueTracker` follows the port.

## 6. Workflow serving becomes data

Today `_resolve_served_workflows` (`cli.py`) probes for `development.json` and
serves only that, unless `--workflow` (repeatable) or `--all-workflows`
overrides it. Two hand-rolled patches then add back what the data obviously
needs: `resolver` is force-added when `workflows/resolver.json` exists, and
`heal` when a heal repo is set. Both are approximations of one rule.

**New rule: the served set is every `workflows/*.json`.** `--workflow` and
`--all-workflows` are **removed** from `run`. An empty or missing `workflows/`
directory means workflow-less mode on the catalog agents (FR-6), *not* today's
`--all-workflows` "no definitions is a startup error".

(`init --workflow`, `agent init --workflow` and `submit --workflow`/`--step`
are different flags on different subcommands and are untouched.)

**This change and §1–§5 enable each other.** The test
`test_run_all_workflows_without_heal_repo_fails_fast_on_the_heal_workflow`
documents why serving-everything could not already be the default: every
`harness init` root has
`workflows/heal.json`, whose `file-issue` step binds `open-issue`, and that
kind was only registered when `--heal-repo` was set — so serving the whole
directory exited 2 on a normal root. Once the finisher derives its repo from
the task, its factory registers unconditionally and the failure mode is
designed out.

It also removes a known operational hazard: the `workflow 'resolver' does not
exist` crash-loop (board down, `com.harness` exit 2 respawning) was a
force-add naming a file the root had never been seeded with. Serving what
exists cannot produce it.

**Cost.** A stale or experimental file in `workflows/` now gets live step
queues and board columns, and its finisher bindings join the cross-workflow
conflict check — so a contradictory leftover that was previously ignored now
fails the build at startup. This is the intended posture (fail fast on
incoherent data), but it is a behaviour change for a root with junk in
`workflows/`. On the live install the served set is unchanged: `development`,
`heal`, `resolver` are all already served.

### `--heal-repo` / `HARNESS_HEAL_REPO` is removed

After §4 and §6 it gates nothing: not serving the heal workflow, not
registering the finisher kind, not the issue repo. Its last job was stamping
`params.repository` into the `processes/autoheal.json` it auto-seeds — and that
file is already the data. Self-healing becomes configured exactly like every
other Process: a JSON file, editable by hand or through the admin UI. `harness
init` keeps seeding `processes/autoheal.json`; `harness run` no longer writes
it.

## 7. Error handling

| Situation | Result |
|---|---|
| No artifact for the step | Zero issues, `done`. This is heal's `skip` path (the persona writes no file). |
| `[]` | Zero issues, `done`, summary `no issues to file`. |
| Artifact present, no parseable JSON array | `IssueError` → `failed/`. A report whose block is malformed is a real fault. |
| A draft with an empty/missing `title` | `IssueError` → `failed/`. |
| `task.repository` unregistered, or origin not GitHub | `IssueError` → `failed/`. |
| GitHub rejects issue *k* of *n* | Fail fast. Issues `0..k-1` stay open; a re-run re-finds them by marker and resumes. |
| Per-draft label outside the allowlist | Dropped, named in the summary. Never sent. |
| `inner()` raises (wrap shape) | Propagates untouched; nothing is filed. |

`Consumer.tick()` already wraps `behavior.run()` in a blanket
`except Exception → _fail`, so none of these need in-behavior handling beyond
raising the right thing.

## 8. Testing

- **Behavior units** against `MemoryIssueTracker`: both shapes; the three
  zero-issue paths; malformed JSON; allowlist filtering; marker stability
  across a re-run with identical titles (no new issues); resume after a
  partial failure.
- **`test_architecture.py` passes unchanged** — the check on the injected
  slug-callable decision (§4). If it fails, the behavior imported a driver.
- **CLI**: the new serving default, including empty `workflows/` →
  workflow-less. The four `--workflow`/`--all-workflows` tests are deleted or
  inverted (`test_run_all_workflows_serves_every_definition_found` becomes the
  no-flag default; `..._without_heal_repo_fails_fast...` and
  `test_run_rejects_workflow_and_all_workflows_together` are deleted;
  `test_run_all_workflows_with_no_definitions_is_a_startup_error` inverts).
- **Heal e2e** keeps proving one issue is filed, now through the generic path.

## 9. Migration

Repo:

- new ADR covering both halves (generic finisher; serving as data)
- `CLAUDE.md` invariants 24/26/39 and every `--heal-repo` reference reworded
- `_write_default_agents`: the seeded heal persona emits the JSON block
- the seeded `workflows/heal.json`: `finishers` in object form

Live install (`~/harness-root`) — all data, no wrapper edit, which is the point
of removing the flags:

- `secrets.env`: drop the `HARNESS_HEAL_REPO` line
- `processes/autoheal.json`: `params.repository` → `"harness_v2"` (fixes the
  29-warning breakage)
- `workflows/heal.json`: `finishers` object form
- `agents/heal.json`: persona emits the JSON block

`harness-run.sh` is unchanged — it passes no workflow flags today and there are
none to add. That matters because `harness service install` regenerates it.

## 10. What arch-review then costs

Three data files, no code:

- `agents/arch-review.json` — the review persona, `allowed_tools`
  `["Read", "Grep", "Glob", "Bash"]`, `timeout` raised above the 1800 s default
- `workflows/arch-review.json` — one step, the wrap-shape binding from §2
- `processes/arch-review.json` — `{"cron": "0 6 * * *"}`, `command` action
  emitting the day's module, `target: {"workflow": "arch-review"}`,
  `repository: "Anela.Heblo"`, `dedup: "per-interval"`

Two properties of that setup are worth recording because they are not
obvious:

- `dedup` **must** be `per-interval`. `per-state` keys on the observation's
  `state_key` (the module name) and `SourcePoller._seen` is seeded from tasks
  on disk, so every module would be permanently suppressed after one 29-day
  cycle.
- The worktree is created by `git worktree add -b harness/<id>` off the
  registered clone's current HEAD, with **no fetch**. The persona must start
  with an explicit fetch/reset, or it reviews whatever was last pulled. And
  since no worktree is ever removed (invariant 30), this accumulates one full
  working copy per day — pruning is an external concern, deliberately not the
  harness's.
