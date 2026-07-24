# Design: unify outbound reflection on one effective-sink-kind routing rule

No user interface is introduced or reshaped by this change beyond one label
string in an existing radio group (`process_form.html`'s sink option-cards) —
covered under Component design below, not broken out as a UX section.

## Component design

### `ports/source.py` — new pure function `effective_sink_kind`

This is the seam everything else routes through, so it lives beside
`dedup_key` — the module already the shared, dependency-free home for
routing helpers both `github_source.py` and `slack_sink.py` import from.

```python
def effective_sink_kind(task: Task) -> str | None:
    """The destination a sink routes on: `data.sink.kind` if the task carries
    an explicit sink, else `data.source.kind` (the degenerate case where the
    destination coincides with the origin). `None` when neither is present."""
    sink = task.data.get("sink")
    if isinstance(sink, dict) and sink.get("kind"):
        return sink["kind"]
    return task.data.get("source", {}).get("kind")
```

Responsibility: exactly one thing — resolve the routing key. It does not
resolve *where* to deliver (issue number, webhook URL) — that stays each
driver's own concern, because only `github` has the source-issue asymmetry
described below. No I/O, no exceptions raised (mirrors `dedup_key`'s posture
of being a plain, total function over `Task`).

Both callers reduce to a one-line `_mine`:

```python
# GithubLabelReflector
def _mine(self, task: Task) -> bool:
    if effective_sink_kind(task) != self.kind:
        return False
    return task.data.get("source", {}).get("repo") == self._repo

# SlackWebhookSink
def _mine(self, task: Task) -> bool:
    return effective_sink_kind(task) == self.kind
```

`GithubLabelReflector` keeps an extra repo check because one reflector
instance is scoped to one repo (`self._repo`, from `repos.json`) — that's
identity-of-instance, not identity-of-kind, and orthogonal to this task.
`_issue()` is untouched: it still reads `task.data["source"]["issue"]`
directly, never through the helper. This is the asymmetry the spec's "sink
seam" flagged and the task explicitly preserves: `github` can name a
destination (via `effective_sink_kind`), but it can never *supply* one — the
only issue a task has is its origin issue. `SlackWebhookSink` has no such
split because a webhook URL isn't per-task data at all; it's injected once at
construction (`SLACK_WEBHOOK_URL`).

### `drivers/github_source.py` — `GithubLabelReflector._mine`

One-line change, shown above. `report_progress`/`finish`/`_set_state`/`_issue`
are untouched. `GithubTaskSource` (the inbound half) is untouched entirely —
FR-5 is a no-diff acceptance criterion, not a design decision.

### `drivers/slack_sink.py` — `SlackWebhookSink._mine`

One-line change, shown above. Everything else (posting, the in-process
dedup ledger, `_name`) is untouched.

### `drivers/fs_processes.py` / `ports/process_admin.py` — `github` as a schema-valid sink kind

- `_ACCEPTED_SINK_KINDS = {"none", "slack", "github"}`.
- `FilesystemProcessAdmin.sink_kinds()` returns `("github", "none", "slack")` —
  alphabetically sorted, matching `check_names()`'s `tuple(sorted(...))`
  convention (resolves the plan's open question: consistency with the
  sibling method outweighs preserving `("none", "slack")` insertion order,
  since nothing depends on `sink_kinds()`'s order except the admin form's
  card sequence, which is free to reflow).
- `_parse_sink` needs no other change: it already validates
  `sink.get("kind") not in _ACCEPTED_SINK_KINDS` generically — widening the
  set is the entire fix. `ScheduledTrigger` already stamps whatever `sink`
  dict it's given into `data.sink` unconditionally (it has no branch on
  `kind`), so no change there either.

Deliberately *not* changed: `ScheduledTrigger`/`_task_for` gains no logic to
populate `data.source` for a process-born task. A Process-declared
`{"sink": {"kind": "github"}}` is schema-valid and round-trips through the
admin, but produces tasks `GithubLabelReflector` will never match (no
`data.source.repo`/`issue` to resolve an issue from). This is recorded as
out-of-scope in the plan and is a deliberate, not accidental, gap — see
"Non-goals" below and the process-form UI note.

### `process_form.html` — the sink option-cards

The template already has a generic `{% else %}` branch in the sink
radio-group (`option-card__title = {{ option }}`, generic description), so
`github` renders correctly with **zero template change required for
correctness** — `sink_kinds()` returning `"github"` is enough for it to
appear as a selectable card.

Design decision: add a dedicated branch anyway, because the generic
`{% else %}` card's copy ("Reflects progress to this destination") would be
actively misleading for `github` today — a Process-declared `github` sink is
schema-valid but inert (previous section), and the generic copy would read
as a working promise. Add:

```html
{% elif option == 'github' %}
<span class="option-card__title">GitHub labels</span>
<span class="option-card__desc">Only takes effect on tasks with a GitHub origin — a no-op for a schedule or check-born task.</span>
```

This resolves the plan's third open question in favor of a caveat: the
option-card's own description is the only place an operator sees this
before saving, and the honest phrasing costs one template branch. No JS
change — `updateSummary()`'s sink-name interpolation (`'reporting to
<strong>' + esc(sink) + '</strong>'`) already handles an arbitrary kind
string.

### ADR-0018 — sink vs. finisher boundary

New file `docs/adr/0018-sink-reflects-a-step-acts.md` (resolves the plan's
first open question in favor of a new file over an addendum to ADR-0015):
ADR-0015 is about the Process aggregate as a compile-time authoring concept;
this decision is a runtime-behavior boundary between two already-shipped
mechanisms (sink, finisher) that happens to be triggered by this task's
unification. It matches the one-decision-per-file granularity of 0016/0017
better than growing 0015's scope retroactively.

Content (short, ADR-0016-shaped): a **step or finisher does work and can
fail the task** (`open-pr` landing calls `Forge.open_pull_request`, which
raises `ForgeError` into `failed/` — invariant per `ClaudeCliBehavior`/
landing). A **sink only reflects already-decided state and can never fail or
route a task** — `report_progress`/`finish` return `None`, and
`CompositeEventSink`/`SourcePoller.tick` isolate any exception a sink driver
raises so it can never affect dispatch. Both "change a GitHub label" and
"open a GitHub PR" call the GitHub API, but they sit on opposite sides of
this line: the former is idempotent, best-effort, and off the routing path;
the latter is authoritative and can move a task to `failed/`. This is *why*
`github`-as-sink (labels) is a `TaskSource`/reflection concept while
`open-pr` (PR creation) is a finisher/`ConsumerBehavior` concept, even though
both are "GitHub."

## Data schemas

No `Task` schema change — this task is purely a routing-rule change over
fields that already exist.

**`task.data` (clarified, not modified):**

```jsonc
{
  "source": { "kind": "github", "repo": "...", "issue": 123, "url": "..." },
  // OR absent for a hand-submitted / process-born task with no origin
  "sink": { "kind": "slack" }
  // present only when a Process declares a non-"none" sink; absent otherwise
}
```

**Effective sink kind** (derived, no new field):

| `data.sink` | `data.source` | effective kind |
|---|---|---|
| `{"kind": "slack"}` | `{"kind": "github", ...}` | `"slack"` (explicit wins) |
| absent / `None` / `{}` / `{"kind": None}` | `{"kind": "github", ...}` | `"github"` (default) |
| `{"kind": "github"}` | absent | `"github"` (explicit, but inert — no issue to target) |
| absent | absent | `None` (no sink matches) |

**Process schema (`processes/*.json`)** — `sink.kind` enum widens:

```jsonc
{ "sink": { "kind": "none" | "slack" | "github" } }
```

No new keys, no shape change — `_ACCEPTED_SINK_KINDS` is the only schema
surface touched.

**`ProcessFields.sink_kind`** (`ports/process_admin.py`) — same `str` field,
now round-trips `"github"` in addition to `"none"`/`"slack"`; no dataclass
change.

**Events / payloads** — `SourceReflectorSink.emit`'s `Progress`/`FinishResult`
payloads are unchanged; only which registered `TaskSource` accepts a given
task (via `_mine`) shifts.

## Non-goals (carried from the plan, restated for the design record)

- Making a Process-declared `github` sink functional end-to-end (needs a
  repo/issue association for process-born tasks — separate design, likely
  riding the not-yet-built `github-issues` check).
- The stateful create-then-update Slack sink and a dedicated `Reflector`
  port.
- Any change to `GithubTaskSource.poll()` / claim-by-label ingestion.

## Test surface (naming only — task breakdown is the development step's job)

- `ports/source.py`: new tests for `effective_sink_kind` covering the four
  rows of the table above.
- `test_github_source.py`: existing reflector tests pass unmodified (default
  path); add explicit-sink-matches and explicit-sink-overrides-default cases.
- `test_slack_sink.py`: existing tests pass unmodified (all set `data.sink`
  explicitly); add a default-to-`source.kind` case.
- `test_fs_processes.py`: `github` sink compiles and stamps
  `data.sink == {"kind": "github"}`; unknown kind still rejected.
- `test_fs_process_admin.py`: `sink_kinds() == ("github", "none", "slack")`;
  a `test_github_sink_round_trips` mirroring the existing Slack one.
- `test_architecture.py`: unchanged expectations — no new import into
  `router.py`/`dispatcher.py`/`consumer.py`.
