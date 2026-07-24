# Architecture: unify outbound reflection on one effective-sink-kind routing rule

## Verdict

Design-01's shape is correct and I re-verified every file/line it cites directly
against `origin/main@e4485d6` (this worktree's `HEAD` predates all of it by 75
commits). Build exactly what design-01 specifies. This document exists to:
(1) confirm that grounding independently so development doesn't have to re-derive
it, (2) turn two of design-01's open questions into hard requirements now that
I've read the actual sink option-card CSS and the `github-issues` check, and
(3) flag one sequencing risk (FR-0) that is the only thing that can derail this.

## Alignment with existing patterns

The codebase already has the exact shape this task asks for, twice:

- `ports/source.py::dedup_key` is precedent for "a pure routing helper lives in
  the port module, both drivers import it." `effective_sink_kind` is the same
  kind of function, same location, same style (no I/O, total over `Task`).
- ADR-0007 (persona as data) → ADR-0016 (finisher as data) is precedent for
  "differences between drivers of the same port are data, not a name-keyed
  branch." This task is the same move applied to sink routing: `github` stops
  being a special inbound-adjacent concept and becomes one more value a
  dict-lookup can return.
- `Trigger`/`TaskSource` already documents the target shape in its own
  docstring (`ports/source.py`): *"`kind` is the key for projection routing:
  the reflector calls only the adapter whose `kind` matches
  `task.data.source.kind`."* That docstring is itself now slightly stale (it
  should say "effective sink kind," not "`data.source.kind`") — update it
  alongside `effective_sink_kind`'s own docstring so the port's self-description
  doesn't lag the code it documents, the way the processes-design spec already
  has (see Risks below).

No new port, no new architectural layer, no new test-boundary category is
needed. `test_architecture.py`'s existing import-boundary tests
(`test_orchestration_does_not_import_source_port`, `test_only_app_and_cli_wire_drivers`)
already cover the invariant this task must not break (#19/#20) — nothing new to
add there, only to keep green.

## Proposed architecture

One new pure function, two one-line call-site changes, one accepted-value-set
widening. No new class, no new port, no new file except the ADR.

```
ports/source.py
  + def effective_sink_kind(task: Task) -> str | None      # NEW

drivers/github_source.py
  GithubLabelReflector._mine                                # CHANGED (routes through helper)

drivers/slack_sink.py
  SlackWebhookSink._mine                                    # CHANGED (routes through helper)

drivers/fs_processes.py
  _ACCEPTED_SINK_KINDS                                       # CHANGED (+"github")

ports/process_admin.py
  FilesystemProcessAdmin.sink_kinds()                        # CHANGED (+"github")

api/templates/admin/process_form.html
  sink option-cards                                          # CHANGED (dedicated github branch)

docs/adr/0018-sink-reflects-a-step-acts.md                    # NEW
docs/superpowers/specs/2026-07-22-processes-design.md         # CHANGED (dated update note)
CLAUDE.md invariant #40                                       # CHANGED (reworded)
```

### Key decision: where does `effective_sink_kind` live, and what does it own

**Chosen**: `ports/source.py`, as a module-level function beside `dedup_key`,
with the exact signature design-01 gives:

```python
def effective_sink_kind(task: Task) -> str | None:
    sink = task.data.get("sink")
    if isinstance(sink, dict) and sink.get("kind"):
        return sink["kind"]
    return task.data.get("source", {}).get("kind")
```

**Rejected alternative — a method on `TaskSource`.** Tempting because both
callers are `TaskSource` subclasses, but wrong: `_mine` in `GithubLabelReflector`
does more than compare kinds (it also checks `self._repo`), so a base-class
method would either have to become a template-method hook (over-engineering for
two call sites) or would only cover half of `_mine`'s job anyway. A free
function callers compose into their own `_mine` is the minimal shape — it
matches how `dedup_key` is used today (composed into a caller's own dedup-key
build, not inherited).

**Rejected alternative — a `Reflector`/`SinkRouter` port.** The plan/design
already correctly scope this out, and the processes-design spec explicitly
warns against it ("a port whose only implementation is a no-op is
over-engineering"). One dict-lookup function is the entire routing surface
today; do not introduce a class for it.

**What it deliberately does NOT own**: resolving *where* to deliver (issue
number, webhook URL). That split is correct and must be preserved —
`GithubLabelReflector._issue()` keeps reading `task.data["source"]["issue"]`
directly, never through the helper, because only GitHub has the
same-as-origin asymmetry. Do not "clean this up" by routing `_issue()` through
some generalized destination resolver; there is nothing to generalize with a
single consumer of that data.

### `_mine` after the change — the two shapes side by side

```python
# GithubLabelReflector — keeps the extra repo scoping (instance identity,
# because one reflector is bound to one repo from repos.json)
def _mine(self, task: Task) -> bool:
    if effective_sink_kind(task) != self.kind:
        return False
    return task.data.get("source", {}).get("repo") == self._repo

# SlackWebhookSink — no instance scoping needed, one webhook URL total
def _mine(self, task: Task) -> bool:
    return effective_sink_kind(task) == self.kind
```

This asymmetry (repo-scoped vs. not) is real and must survive — it is not
something this task should try to unify further. A multi-repo deployment
constructs one `GithubLabelReflector` per repo (`cli._github_reflectors`
already does this); `SlackWebhookSink` is a singleton because a webhook URL is
process-global config, not per-task data.

### Process/admin schema widening — exact edits

- `drivers/fs_processes.py`: `_ACCEPTED_SINK_KINDS = {"none", "slack", "github"}`.
  `_parse_sink` needs no other change — it already validates generically against
  this set.
- `ports/process_admin.py`: `sink_kinds()` returns `("github", "none", "slack")`
  — alphabetical, matching `check_names()`'s `tuple(sorted(...))` convention.
  Take this as settled, not open: `sink_kinds()`'s return order has exactly one
  consumer (the admin form's card sequence), nothing depends on stability of
  that order, and matching the sibling method's convention is worth more than
  preserving today's incidental `("none", "slack")` sequence.

### `process_form.html` — confirmed safe, and now a required change

I checked the actual CSS (`api/static/app.css:526-530`):

```css
.option-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
.option-cards--two { grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }
```

`auto-fill`, not a hard 2-column grid — a third card wraps cleanly with zero
CSS change despite the `--two` class name (that name is legacy from when there
were exactly two kinds; do not rename it, that's an unrelated cosmetic
diff). This confirms design-01's claim.

Escalating design-01's "add a dedicated branch anyway" from a nice-to-have to
**required**: the existing generic `{% else %}` branch's copy — *"Reflects
progress to this destination"* — is not just imprecise for `github`, it is
actively wrong for the common case (see Risks: `GithubIssuesCheck` below), so
shipping without the dedicated branch would ship a misleading label on a form
an operator uses to configure real GitHub label writes. Add:

```html
{% elif option == 'github' %}
<span class="option-card__title">GitHub labels</span>
<span class="option-card__desc">Reflects to the task's origin issue. No-op unless the action populates a GitHub source (e.g. github-issues).</span>
```

Use this wording, not design-01's ("only takes effect on tasks with a GitHub
origin — a no-op for a schedule or check-born task") — see Risks below for why
the "schedule or check-born" framing is inaccurate for one specific, already-
shipped check.

### ADR-0018

New file, matching the 0016/0017 one-decision-per-file granularity, per
design-01. Content is settled by design-01; nothing to add here except: file it
under `docs/adr/0018-sink-reflects-a-step-acts.md` and cross-link it from the
processes-design spec's sink-seam update note (one link both ways, matching how
0015/0016 already cross-reference each other).

## Implementation guidance — sequencing

1. **FR-0 first, and verify it explicitly before writing any new code.** This
   is not a formality — I confirmed by diffing this worktree against
   `origin/main` that `drivers/slack_sink.py`, `drivers/fs_processes.py`,
   `ports/process_admin.py` don't exist here at all, and `github_source.py`
   here is the pre-refactor single-class version (`GithubTaskSource` doing both
   inbound and outbound directly — no `GithubLabelReflector` class exists yet).
   Every acceptance criterion in plan-01/design-01 is written against files
   that must be merged in before they can be edited. Confirm merge success by
   running the two things CLAUDE.md calls out as the untouchable regression
   guards: `.venv/bin/pytest -q` and `tests/test_architecture.py`, both green,
   *before* touching `_mine` anywhere.
2. `ports/source.py::effective_sink_kind` + its unit tests. This has zero
   dependents yet, so it's risk-free to land and verify in isolation.
3. `GithubLabelReflector._mine` and `SlackWebhookSink._mine` together (one
   commit-worthy unit) — they're the same one-line change pattern, and
   design-01's regression guard (existing test suites pass unmodified) only
   means something if both are checked at once against the full suite.
4. `fs_processes.py` / `process_admin.py` schema widening + `process_form.html`.
5. Docs: spec update, CLAUDE.md invariant #40, ADR-0018.
6. Final full-suite pass, plus the grep design-01 already specifies (no
   `data.source`/`data.sink` read in `router.py`/`dispatcher.py`/`consumer.py`).

## Risks and mitigations

**Risk — FR-0 merge conflicts or scope creep.** 75 commits is a lot of
surface; a naive merge could pull in unrelated work-in-progress. Mitigation:
this is a fast-forward-shaped problem, not a real merge — the task branch has
no commits of its own yet touching these files (only `plan-01.md`/`design-01.md`
artifacts exist so far), so rebasing onto `origin/main` should be conflict-free.
If it isn't, stop and re-scope rather than resolving conflicts blind — that
would mean something else landed on `origin/main` touching this exact area
between grounding and implementation.

**Risk — `GithubIssuesCheck` already produces `data.source`-bearing tasks
today, which changes what "inert" means for a Process+github combination.**
I read `drivers/github_issues_check.py` on `origin/main`: its `evaluate()`
returns `Observation(data={"source": {"kind": "github", "repo": slug, "issue":
n, "url": ...}})`, and `ScheduledTrigger._task_for` spreads `obs.data` onto the
task unconditionally (`data = {**obs.data}`), stamping `sink` only *on top* of
that. This means a Process using `action: {check: "github-issues"}` **already
produces tasks with a genuine GitHub `data.source`, repo and issue included** —
this is not hypothetical, it ships today (`e181607`). Two consequences for this
task specifically:
- The plan/design's "Non-goals" framing — *"a Process-declared github sink is
  schema-valid but inert... `ScheduledTrigger` never stamps `data.source`"* — is
  true for the general case (e.g. `action: {check: "always"}`) but **not** true
  for `action: {check: "github-issues"}`. For that specific, already-shipped
  action, `GithubLabelReflector` already matches such tasks today (its current
  `_mine` reads `data.source` directly), and will continue to match them
  identically after this task through the default-to-source path — no behavior
  change, but also nothing to newly "unlock." Don't let this task's out-of-scope
  note imply the github-issues+sink combination is broken; it isn't, and never
  was.
- **Add this as an explicit regression case**, distinct from the synthetic
  "GitHub-origin task with no explicit sink" test design-01 already calls for:
  build a task the way `ScheduledTrigger._task_for` actually builds one from a
  `GithubIssuesCheck` observation (i.e. `data = {"source": {...}}`, no `sink`
  key at all — not a hand-rolled `Task(data={"source": {...}})` fixture), and
  assert `GithubLabelReflector` still matches it after the `_mine` rewrite. The
  existing unit-test fixtures for both files construct tasks by hand, which is
  fine for FR-2/FR-3's own tests, but this one specific case is worth
  cross-checking against the real producer shape because it's the one place a
  subtle bug (e.g. mis-reading `source` vs `sink` precedence) would silently
  regress a feature that's live in production today, not a new one being added.

**Risk — the processes-design spec has more stale "`none`-only" wording than
just the "sink seam" section the task notes call out.** I grep'd the whole
file: lines 49, 59, 94 (example JSON), 124, 140, 161 (the compilation
validation table), 192, 239, 268 (the spec's own restated invariant), and 291
all still say or imply only `none`/`slack` are valid. The "sink seam" section
already carries a precedent for how to handle this correctly — a dated
`> **Update 2026-07-23 — partially realized.**` blockquote was appended when
`slack` shipped, rather than rewriting the historical narrative. Follow that
exact pattern: append a second dated blockquote (`2026-07-23` again, or the
actual land date) noting `github` is now accepted too and the routing is
unified, rather than editing the prose narrative or the validation table
in place. Do not silently rewrite lines 49/59/94/124/140/161/192/239/268/291 to
read as if `github`/unified-routing were the original design — that erases the
increment history the dated-update convention exists to preserve. This is a
docs-only nuance, not a code risk, but worth calling out so development doesn't
either (a) miss the other stale spots or (b) over-correct and rewrite history.

**Risk — none, but worth stating explicitly: no `Task` schema migration, no
data backfill.** Every existing task on disk or in flight either has `source`,
`sink`, both, or neither, and `effective_sink_kind` is total over all four
combinations (see design-01's table). There is nothing to backfill and no
version gate needed.

## Prerequisites before implementation begins

1. FR-0 merge/rebase onto `origin/main` landed and both `.venv/bin/pytest -q`
   and `tests/test_architecture.py` green on the merged tree — this is a hard
   gate, not a formality, given the 75-commit gap confirmed above.
2. No other prerequisite. This task touches no in-flight work per the current
   `origin/main` history (confirmed via `git log --oneline -- drivers/github_source.py drivers/slack_sink.py drivers/fs_processes.py ports/process_admin.py` showing the last touches predate this task).

## Summary of changes from plan-01/design-01

Everything in plan-01 and design-01 stands. This document adds three concrete
refinements development should treat as requirements, not suggestions:

1. Use the corrected process-form copy above (the "no-op unless the action
   populates a GitHub source" wording), not design-01's "schedule or
   check-born" wording — the latter is inaccurate for `github-issues`.
2. Add the github-issues-shaped regression test described under Risks, in
   addition to (not instead of) design-01's synthetic default-to-source test.
3. When updating the processes-design spec, append a second dated update
   blockquote to "The sink seam" section following the existing pattern —
   don't touch the other stale `none`-only passages elsewhere in the file as
   part of this task; they're pre-existing staleness from the Slack increment,
   out of this task's scope to fully rewrite.
