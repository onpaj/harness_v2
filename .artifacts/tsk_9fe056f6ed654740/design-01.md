# Design — per-process target repository selection (finishing PR #108)

Builds on `plan-01.md`'s FR-1..FR-7. Resolves its three open questions and
gives the concrete shapes (form, schema, function signatures) the dev step
implements against. Does not re-list developer tasks — see the plan's "Rough
plan" for that.

## Open questions — resolved

1. **Single repository vs. multi-repo fan-out.** Single, confirmed. A process
   carries at most one `repository` name; `ScheduledTrigger`/`Task` already
   have exactly this shape (`repository: str | None`), and an observation's
   own `repository` (e.g. `github-issues`, one per issue) still wins. Fan-out
   to N repositories per process is out of scope (would need N observations
   per occurrence — a materially bigger change, flagged as a follow-up, not
   built here).
2. **Strictness of repository validation.** Strict wherever a registry is
   available — unlike `target`, which stays lenient at admin-save time only
   because the served-workflow set genuinely isn't knowable from a filesystem
   driver. `repos.json` *is* available at both compile sites (startup and
   admin-save), so there's no reason to be lenient; an admin-time typo should
   fail the same way a startup-time one does.
3. **UI control shape.** A segmented "All repositories" / "Specific
   repository" toggle plus a `<select>`, reusing the form's existing idiom
   (identical structurally to the cadence and target-kind toggles already on
   this page) — not PR #108's checkbox-list, which implied multi-repo
   fan-out this design doesn't build.

## UX/UI

### Wireframe — new "Repository" section

Inserted between **Target** and **Options** in `admin/process_form.html`
(renumbers those two sections' headers by one; every other section is
unchanged):

```
┌─ 4  Repository ────────────────────────────────────────────────┐
│  Which repo a produced task's worktree attaches to — an        │
│  observation with its own repo (e.g. GitHub issues) still wins.│
│                                                                  │
│   ( • ) All repositories        (   ) Specific repository       │
│                                                                  │
│   ┌────────────────────────────────────────┐  (shown only when │
│   │ harness_v2                          ▾   │   "Specific" is   │
│   └────────────────────────────────────────┘   selected)        │
│                                                                  │
│   [if repos.json has no entries:]                                │
│   "No repositories registered in repos.json — add one to        │
│    target a specific repo." (Specific option disabled)          │
│                                                                  │
│   [errors.repository, if any, renders here]                     │
└──────────────────────────────────────────────────────────────────┘
```

Section numbering becomes: new process = 1 Name, 2 Schedule, 3 Action,
4 Target, **5 Repository**, 6 Options; editing an existing process =
1 Schedule, 2 Action, 3 Target, **4 Repository**, 5 Options. (Today's
Options section is `{{ 5 if is_new else 4 }}`; it becomes
`{{ 6 if is_new else 5 }}`.)

### Component hierarchy (within `process_form.html`)

```
<form id="process-form">
  ├─ section Name              (unchanged, is_new only)
  ├─ section Schedule          (unchanged)
  ├─ section Action            (unchanged)
  ├─ section Target            (unchanged)
  ├─ section Repository        (NEW)
  │    ├─ .seg[role=radiogroup name=repo_scope]      "all" | "specific"
  │    ├─ .field#repository-field
  │    │    └─ select#repository[name=repository]     options = repository_options
  │    │         (+ the current value grafted in if it fell out of the
  │    │          registry after being set — see `all_repository_options`
  │    │          below, mirroring the existing `all_checks` pattern)
  │    └─ .field-error (errors.repository)
  └─ section Options           (renumbered, otherwise unchanged)
</form>
<script> … existing inline block, extended (see below) </script>
```

No separately-loaded JS file — everything lives in the form's existing
inline `<script>`, exactly like cadence/target-kind/dedup/sink already do.
`src/harness/api/static/process_form.js` is deleted (it manipulates
`#repositories-field` / `scope` / `repositories`, none of which will ever
exist under this design either — the stub's multi-select shape is
superseded, not revived).

### Key interactions

- **Toggle sync** (mirrors `syncCadence()`): on `repo_scope` change, set
  `select#repository.disabled = (repo_scope !== 'specific')`. Disabling the
  select is a UX affordance only — the actual "all vs specific" decision is
  made server-side from the submitted `repo_scope` radio (see below), so a
  stale/leftover select value can never leak through even with JS
  misbehaving. This is the fix for PR #108's stub, which relied on `disabled`
  alone and left the two `name`s (`scope`, `repositories`) unconnected to any
  real field.
- **Initial state on load**: `repo_scope` radios check `all` when
  `fields.repository` is empty, `specific` otherwise; the select starts
  `disabled` in the "all" case. This makes editing an existing
  repository-scoped process render correctly without a JS tick.
- **No registry configured / empty `repos.json`**: `repository_options` is
  `()`. The "Specific repository" radio renders `disabled` with a hint
  explaining why; "All repositories" is the only real choice. (A process file
  that already names a repository not in the registry — e.g. hand-edited
  before the registry existed, or edited on a different machine — still
  displays correctly: see the `all_repository_options` graft below.)
- **Live summary line**: extended to append `, targeting repo
  <strong>NAME</strong>` when `repo_scope === 'specific'` and a repository is
  chosen — same idiom as the existing dedup/sink summary clauses.
- **Server-side authority**: exactly as the old stub's own comment intended
  ("the server is the actual source of truth for what scope means") — the
  `repo_scope` radio is parsed server-side into the single `repository`
  string before touching `ProcessFields`; see Component design.

## Component design

### `ports/process_admin.py`

```python
@dataclass(frozen=True)
class ProcessFields:
    ...
    dedup: str = "per-interval"
    repository: str = ""
    """The repository name a produced task's worktree attaches to, or ""
    for "all repositories" (no process-level default — an observation's own
    repository, or none at all, applies unchanged). Validated as a name
    against RepositoryRegistry wherever one is available to the caller."""


class ProcessAdmin(ABC):
    ...
    @abstractmethod
    def repository_names(self) -> tuple[str, ...]:
        """The repository names the form offers for "Specific repository",
        sorted — mirrors check_names()/sink_kinds(). Backed by
        RepositoryRegistry.names() where the driver was given one; empty
        when none was configured, in which case only "All repositories" is
        offered and repository validation at write() falls back to lenient
        (matching every other known_*=None escape hatch)."""
```

`_EmptyProcessAdmin` (`api/app.py`) gets `repository_names() -> ()`.

### `drivers/fs_processes.py`

**New validator**, alongside `_parse_dedup`/`_parse_sink`:

```python
def _parse_repository(
    where: str, repository: object, known_repositories: set[str] | None
) -> str | None:
    """None (absent key) is valid and means no process-level default.
    A present value must be a non-empty string; if `known_repositories` is
    given it must also be a member. Returns the validated name, or None."""
    if repository is None:
        return None
    if not isinstance(repository, str) or not repository:
        raise ProcessValidationError(
            f"process {where} has an invalid repository: {repository!r}",
            field="repository",
        )
    if known_repositories is not None and repository not in known_repositories:
        raise ProcessValidationError(
            f"process {where} names repository {repository!r}, which is not "
            "in the repository registry",
            field="repository",
        )
    return repository
```

**`compile_process`** gains `known_repositories: set[str] | None = None` and
reads the file's own field, folding it in front of the existing `repository=`
kwarg (which stays a fallback default — the precedence chain FR-1 specifies):

```python
def compile_process(
    name, raw, *, clock, checks=BUILTIN_CHECKS,
    repository: str | None = None,          # unchanged param, now a fallback only
    worktree_root=None, known_targets=None,
    known_repositories: set[str] | None = None,   # NEW
    where=None,
) -> ScheduledTrigger:
    ...
    sink = _parse_sink(where, raw.get("sink"))
    file_repository = _parse_repository(where, raw.get("repository"), known_repositories)
    return ScheduledTrigger(
        ...,
        repository=file_repository or repository,   # was: repository (the kwarg alone)
        ...,
    )
```

`ScheduledTrigger` itself needs **no change** — its existing `_task_for`
already computes `obs.repository or self._repository`
(`drivers/scheduled_trigger.py:111`). Composing the two gives exactly the
three-way precedence FR-7 requires, with zero core-orchestration edits:

```
effective repository on a produced task
    = obs.repository                         (a check's own observation, e.g. github-issues)
      or file_repository                     (this process's "repository" key)
      or repository                          (the compile_process/build() caller's kwarg default)
      or None
```

`ProcessValidationError`'s docstring enum extends to
``interval|cron|check|params|target|dedup|sink|trigger|repository``.

**`FilesystemProcessRepository.build`** gains `known_repositories: set[str] |
None = None`, threaded straight through `_build_one` → `compile_process` —
identical shape to how `known_targets` already flows.

**Round-trip** (`_raw_from_fields` / `_fields_from_raw`):

```python
def _raw_from_fields(fields: ProcessFields) -> dict:
    raw = {
        "trigger": ...,
        "action": ...,
        "target": ...,
        "dedup": fields.dedup,
        "sink": {"kind": fields.sink_kind},
    }
    if fields.repository:
        raw["repository"] = fields.repository
    return raw

def _fields_from_raw(raw: dict) -> ProcessFields:
    return ProcessFields(
        ...,
        dedup=raw.get("dedup", "per-interval"),
        repository=raw.get("repository") or "",
    )
```

Omitting the key when empty (rather than writing `"repository": ""`) keeps
every process file written before this feature byte-for-byte reproducible on
a resave, and keeps a hand-edited file's *absence* of the key indistinguishable
from an admin-saved "All repositories".

**`FilesystemProcessAdmin`**:

```python
def __init__(self, root, *, checks=None, registry: RepositoryRegistry | None = None):
    ...
    self._registry = registry

def repository_names(self) -> tuple[str, ...]:
    return tuple(sorted(self._registry.names())) if self._registry else ()

def write(self, name, fields):
    ...
    known_repositories = set(self._registry.names()) if self._registry else None
    try:
        compile_process(
            name, raw, clock=_LocalClock(), checks=self._checks,
            known_targets=None, known_repositories=known_repositories,
        )
    except ProcessValidationError as error:
        raise ProcessAdminValidationError({error.field or "_": str(error)}) from None
    ...
```

This is the resolution of open question #2: `known_targets` stays `None`
(lenient — a filesystem-only driver can't know the *running* harness's served
workflows) while `known_repositories` is strict whenever `registry` was
supplied, because `repos.json` is static config the admin driver can read
directly, the same file the real run reads.

### Wiring

`app.build(..., repository_registry: RepositoryRegistry | None = None)`:
only used to compute `known_repositories` for the internal
`FilesystemProcessRepository(...).build(...)` call —

```python
known_repositories = set(repository_registry.names()) if repository_registry else None
process_sources = process_repo.build(
    clock=clock, checks=checks, repository=None,
    worktree_root=str(layout.worktrees), known_targets=known_targets,
    known_repositories=known_repositories,
)
```

`cli.serve(..., registry: RepositoryRegistry | None = None)`: passed straight
into `FilesystemProcessAdmin(harness.layout.processes,
checks=harness.process_checks, registry=registry)`.

`cli._run()`: already holds `registry = FilesystemRepositoryRegistry(layout.repos)`
(line 1587) — passes `repository_registry=registry` into its `build(...)` call
and `registry=registry` into its `serve(...)` call. Both are new optional
trailing parameters; every existing caller (tests, other entry points) that
omits them keeps today's `known_repositories=None` / no-admin-registry
behavior exactly.

### `api/routes.py`

A single helper reused by both the JSON-body parser and the HTML-form
parser, so "all vs. specific" is resolved identically regardless of entry
point:

```python
def _repository_from_payload(payload) -> str:
    """"" means "all repositories". A JSON API body has no repo_scope key at
    all, so it falls straight through to reading `repository` as-is — the
    scope discriminator only exists for the HTML form's two-control UI."""
    scope = payload.get("repo_scope")
    if scope is not None and (scope or "all").strip() != "specific":
        return ""
    return (payload.get("repository") or "").strip()
```

- `_process_fields_dict`: add `"repository": fields.repository`.
- `_process_fields_from(payload)`: add `repository=_repository_from_payload(payload)`.
  (JSON API clients: no `repo_scope`, so this is just `payload.get("repository")`,
  stripped — unchanged surface for them.)
- `_process_fields_from_form(form)`: add
  `repository=_repository_from_payload(form)`. `form` (Starlette's
  `FormData`) supports `.get` the same as a `dict`, so the one helper serves
  both call sites, including the two error-redisplay fallbacks that already
  reuse `_process_fields_from({key: form.get(key) for key in form.keys()})` —
  those retain `repo_scope` in the reconstructed dict, so redisplay after an
  unrelated params-JSON error still reflects "all" correctly instead of
  leaking a disabled `<select>`'s stale value.
- `_process_form_context`: add `"repository": fields.repository` and
  `"repository_options": list(repository_options)` (new keyword parameter);
  also grafts the current value in if it fell out of the registry, mirroring
  the template's existing `all_checks` pattern:
  `all_repository_options = repository_options if (not repository or repository
  in repository_options) else [repository] + repository_options` — computed
  in the template itself (Jinja), exactly where `all_checks` already is,
  not in Python, so it stays a pure rendering concern.
- `_process_form_response`: pass `repository_options=process_admin.repository_names()`.
- `_NEW_PROCESS_FIELDS`: no change needed — `ProcessFields`'s
  `repository: str = ""` default already applies.

### `templates/admin/process_form.html`

```html
{% set all_repository_options = repository_options if (not repository or repository in repository_options) else [repository] + repository_options %}
...
<section class="form-section {{ 'error' if errors.repository }}">
  <header class="form-section__head">
    <h2><span class="form-section__num">{{ 5 if is_new else 4 }}</span> Repository</h2>
    <p class="hint">Which repo a produced task's worktree attaches to — an
      observation with its own repo (e.g. GitHub issues) still wins.</p>
  </header>
  <div class="seg" role="radiogroup" aria-label="Repository scope">
    <label class="seg__option">
      <input type="radio" name="repo_scope" value="all" {{ 'checked' if not repository }}>
      <span>All repositories</span>
    </label>
    <label class="seg__option">
      <input type="radio" name="repo_scope" value="specific"
             {{ 'checked' if repository }} {{ 'disabled' if not all_repository_options }}>
      <span>Specific repository</span>
    </label>
  </div>
  <div class="field {{ 'error' if errors.repository }}" id="repository-field">
    <label for="repository">Repository</label>
    <select id="repository" name="repository" {{ 'disabled' if not repository }}>
      {% for option in all_repository_options %}
      <option value="{{ option }}" {{ 'selected' if option == repository }}>{{ option }}</option>
      {% endfor %}
    </select>
    {% if not all_repository_options %}
    <div class="hint">No repositories registered in <code>repos.json</code> — add one to target a specific repo.</div>
    {% endif %}
    {% if errors.repository %}<div class="field-error">{{ errors.repository }}</div>{% endif %}
  </div>
</section>
```

(Options section headers become `{{ 6 if is_new else 5 }}`.)

Inline `<script>` additions, following the file's existing idioms exactly:

```js
var repoScopeInputs = form.querySelectorAll('input[name="repo_scope"]');
var repositorySelect = document.getElementById('repository');
function currentRepoScope() {
  var checked = form.querySelector('input[name="repo_scope"]:checked');
  return checked ? checked.value : 'all';
}
function syncRepoScope() {
  repositorySelect.disabled = currentRepoScope() !== 'specific';
}
Array.prototype.forEach.call(repoScopeInputs, function (radio) {
  radio.addEventListener('change', function () { syncRepoScope(); updateSummary(); });
});
repositorySelect.addEventListener('change', updateSummary);
syncRepoScope();
```

And in `updateSummary()`:

```js
if (currentRepoScope() === 'specific' && repositorySelect.value) {
  text += ', targeting repo <strong>' + esc(repositorySelect.value) + '</strong>';
}
```

### Cleanup

`src/harness/api/static/process_form.js` is deleted — its DOM targets
(`#repositories-field`, `scope`, `repositories`) never existed and never
will under this design (single-select, not a checkbox list); everything
lives in the form's own inline script per the pattern above.

## Data schemas

### `processes/<name>.json` (file schema addition)

```jsonc
{
  "trigger": { "interval": "30m" },
  "action": { "check": "always", "params": {} },
  "target": { "workflow": "default" },
  "repository": "harness_v2",       // NEW, optional. Absent = no process default.
  "dedup": "per-interval",
  "sink": { "kind": "none" }
}
```

- Type: string, non-empty, must name an entry in `repos.json` when the
  compiling caller supplied `known_repositories` (both real compile paths do
  — see Wiring).
- Absent key ⇔ `ProcessFields.repository == ""` ⇔ no process-level default;
  behavior is byte-identical to every process file that predates this change.

### `ProcessFields` (Python, `ports/process_admin.py`)

| Field | Type | Default | New? |
|---|---|---|---|
| `repository` | `str` | `""` | **yes** — `""` means "all repositories" |

### REST JSON (`GET/PUT /api/processes/{name}`)

Request/response body gains one key, same convention as every other flat
field:

```jsonc
{
  "name": "nightly-cleanup",
  "cadence": "interval", "interval": "30m", "cron": "",
  "check": "always", "target_kind": "workflow", "target": "default",
  "params": {},
  "sink_kind": "none",
  "dedup": "per-interval",
  "repository": "harness_v2"   // NEW; "" (or omitted on request) = all repos
}
```

### HTML form fields (`POST /admin/processes`, `POST /admin/processes/{name}`)

| Field name | Values | Persisted? |
|---|---|---|
| `repo_scope` | `all` \| `specific` | **no** — transient UI discriminator, resolved server-side into `repository` before it ever reaches `ProcessFields` |
| `repository` | a repository name, or absent/blank | **yes** — becomes `ProcessFields.repository` (`""` when `repo_scope != "specific"`) |

### `Task` / event payloads

**Unchanged.** `Task.repository: str | None` already exists
(`models.py`); this feature only changes *what value* `ScheduledTrigger`
computes for it — `obs.repository or file_repository or repository_kwarg` —
never the shape of the task or any event. No new event payload, no schema
migration, no data backfill: every task in flight or already produced is
unaffected.

### Validation error shape (`ProcessAdminValidationError`)

Unchanged shape (`{field: message}`); one new possible key:

```jsonc
{"repository": "process <name> names repository 'foo', which is not in the repository registry"}
```

mapped by the template onto `errors.repository`, rendered under the new
Repository section exactly like every other field-level error already is.

## Consistency check against invariants

- Invariant #15 (`task.repository` is a name, not a path): preserved —
  the new field is validated as a name against `RepositoryRegistry`, never a
  path; `RepositoryRegistry`/`FilesystemRepositoryRegistry` are unchanged.
- Invariant #33 (`ProcessAdmin` unknown to dispatcher/consumer, filesystem
  driver wired only in `cli.py`): preserved — `repository_names()` is a new
  admin-port method, `RepositoryRegistry` still only reaches
  `FilesystemProcessAdmin` via `cli.serve()`'s new `registry=` parameter,
  never via `api/`.
- Invariant #39 (a Process is a compile-time aggregate): preserved —
  `compile_process` gains a parameter and a field read, no runtime module
  learns the word "process" or "repository selection"; `ScheduledTrigger`'s
  own code is untouched.
- Invariant #40 (sink routing) and #8 (router/dispatcher never read
  `repository`): untouched — this feature only changes what value lands in
  `Task.repository` before the dispatcher ever sees the task, exactly the
  same seam `github-issues`' per-observation repository already uses.

```json
{"outcome": "done", "summary": "Wrote design-01.md: UX wireframe/interactions for a new Repository form section (segmented All/Specific + select, no separate JS file), concrete component design for ProcessFields.repository, compile_process/_parse_repository precedence (obs > file > kwarg composing with ScheduledTrigger's existing logic unchanged), FilesystemProcessAdmin.repository_names()/registry wiring through app.build()/cli.serve()/cli._run(), routes.py's shared _repository_from_payload helper, and full data schemas (process JSON, ProcessFields, REST, form fields, error shape) — resolving all three open questions from plan-01.md (single-repo scope, strict validation when a registry is available, segmented-toggle UI shape) and verifying consistency against invariants #8/#15/#33/#39/#40."}
```

