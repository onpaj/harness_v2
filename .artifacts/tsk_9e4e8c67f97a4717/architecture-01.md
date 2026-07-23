# Architecture assessment: continuously reflect task state onto the source GitHub issue's labels

## Verdict

`plan-01.md` and `design-01.md` are well-grounded and I endorse the approach:
extract a `GithubLabelReflector` that owns the entire "state → label" mapping,
have `GithubTaskSource` compose it instead of duplicating it, and register the
reflector standalone in `cli.py` only when `--no-github-source` disables
classic ingestion. No port, no schema, no dispatcher/consumer change. This is
the right shape and the right size for the problem — I'm not proposing a
different architecture, only correcting one inheritance choice and closing out
the plan's open questions with explicit decisions so development isn't left to
re-litigate them.

All findings below are re-grounded directly against `origin/main` (`46e0c5d`),
not against this branch — this worktree is still 57 commits behind, confirmed
again (`git fetch origin && git log --oneline HEAD..origin/main | wc -l` → 57).
I independently read `ports/source.py`, `drivers/github_source.py`,
`drivers/mergeability_watcher.py`, `drivers/scheduled_trigger.py`,
`drivers/github_issues_check.py`, `drivers/source_reflector.py`,
`drivers/fs_processes.py`, `cli.py`'s `_github_sources`/`_mergeability_sources`/
`_run`, `source_poller.py`, `tests/test_architecture.py`, and ADR-0010/0015 to
confirm the plan's and design's claims and to check the one thing they didn't:
what the *existing* multi-source precedent in this codebase looks like.

## 1. Alignment with existing patterns and integration points

The codebase already has the exact precedent this feature needs, and it's
worth naming explicitly because it de-risks the whole design:

**`GithubMergeabilityWatcher` already coexists with `GithubTaskSource` in the
same `sources` list, both with GitHub-flavored `kind`s, both independently
calling `add_label`/`remove_label` on the same client.** `_run` builds them
side by side (`github = ...; mergeability = _mergeability_sources(...);
sources = github + mergeability`), and `SourceReflectorSink.emit()` fans
`report_progress`/`finish` out to *every* source in the list unconditionally —
routing is entirely the receiving source's own `_mine()` guard, not a keyed
dispatch by `kind` (the `TaskSource.kind` docstring reads as if routing is
keyed; the actual `emit()` loop is not — it calls all sources and lets each
one no-op). This means:

- Registering a second GitHub-kind `TaskSource` (the new reflector) alongside
  others in the fan-out is not a novel pattern, it's the established one.
- `_github_reflectors` mirroring `_github_sources`'s and
  `_mergeability_sources`'s enumeration shape (same token/no-token, same
  per-repo slug resolution, same "skip with a warning already emitted
  elsewhere" comment convention) is exactly the right integration point — a
  fourth function in the family, not a new mechanism.
- The dedup/claim concern (`_claimed` ledgers, `dedup_key`) that both
  `GithubTaskSource.poll()` and `GithubIssuesCheck.evaluate()` carry is
  irrelevant to the reflector: it never calls `poll()` for real, so it needs
  none of that machinery. This is a meaningful simplification over
  `GithubTaskSource`, not a stripped-down copy of it.

Integration points, confirmed by direct reading, not inference:

- `ports/source.py` — `TaskSource`/`Trigger`/`Progress`/`FinishResult` are
  sufficient as-is; no change needed (§2 has one correction to *which* of
  these two base classes the new driver should extend).
- `drivers/source_reflector.py`'s `SourceReflectorSink.emit()` needs no
  change — it already fans out to whatever is in `sources`.
- `cli.py`'s `_run` already threads `sources` through four contributors
  (`github`, `mergeability`, `_scheduled_sources`, `_process_sources`); adding
  a fifth contributor (`reflectors`) is additive, not a restructuring.
- `fs_processes.py`'s `_ACCEPTED_SINK_KINDS = {"none"}` stays untouched — ADR-
  0015 explicitly names GitHub→GitHub as the same-origin case that "needs
  declare nothing," so this feature is the confirmation of that design intent,
  not a reason to widen the sink schema.
- `test_architecture.py`'s guards (`test_orchestration_does_not_import_
  source_port`, `test_only_app_and_cli_wire_drivers`) constrain *where* the
  new class may be referenced (never `dispatcher.py`/`consumer.py`, wired only
  in `cli.py`) — the design already respects this; nothing here needs a new
  guard, since the new class is wired identically to three existing ones.

## 2. Proposed architecture

### 2.1 Components (as designed, one change below)

```
                     ┌────────────────────────────┐
  dispatched/        │   SourceReflectorSink       │   (unchanged)
  finished/failed  ──▶   .emit() fans out to        │
  events             │   every source in the list  │
                     └──────────────┬───────────────┘
                                    │ report_progress / finish,
                                    │ each source's own _mine() decides
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
    GithubTaskSource      GithubLabelReflector    GithubMergeabilityWatcher
    (ingests + reflects,  (reflects only,          (unrelated: PR labels)
     when classic          registered only when
     ingestion is on)      classic ingestion is off)
              │                     ▲
              └── composes ─────────┘
                  (delegates report_progress/finish)
```

`GithubTaskSource` and `GithubLabelReflector` are never both registered for
the same repo — mutual exclusion is enforced by gating both on
`args.no_github_source` in `cli.py`, the same signal that already means
"ingestion is delegated to a Process." This keeps the "exactly one reflecting
source per repo" property structurally true (by construction in `_run`, not
by a runtime check) — the cheapest kind of invariant to keep honest.

### 2.2 Key decision — endorsed as designed: composition, not duplication

`GithubTaskSource` retains its full public interface and delegates
`report_progress`/`finish` to an internally-held `GithubLabelReflector`. I
considered and reject the alternative of leaving `GithubTaskSource`'s label
logic in place and having the new reflector duplicate it: that would create
two implementations of the state→label mapping to keep in sync, which is
exactly the kind of drift this task exists to fix (the current bug *is* two
things — ingestion and reflection — being accidentally coupled into one
class; don't fix that by creating a second coupling). Composition with
delegation is the correct refactor: one implementation, two call sites, zero
behavior change to `GithubTaskSource`'s existing tests.

### 2.3 Key decision — corrected: base class is `TaskSource`, not `Trigger`

This is the one place I'm overriding the design. `design-01.md` §2.1 has
`GithubLabelReflector` subclass `Trigger`, reasoning that it gets `poll()`'s
"free no-op" and that this documents intent by analogy with `ScheduledTrigger`.
Having read `ports/source.py`'s actual docstring for `Trigger`, this is
backwards:

> `Trigger`: "A `TaskSource` that produces tasks but reflects nothing back
> outward... `report_progress`/`finish` are concrete no-ops here."

`Trigger` names the **inbound-only** shape: real `poll()`, no-op reflection.
`GithubLabelReflector` is the exact mirror image — **outbound-only**: no-op
`poll()` (always `[]`), real `report_progress`/`finish`. Subclassing `Trigger`
buys nothing mechanically (both inherited no-ops are immediately overridden,
so the "free no-op" is never used) and actively mislabels the class to the
next reader: anyone who knows this codebase's vocabulary (`Trigger` =
inbound-only, cf. `ScheduledTrigger`) will misread `GithubLabelReflector(
Trigger)` as "produces tasks," which is precisely wrong.

Confirmed by the existing precedent: `GithubMergeabilityWatcher`, the
codebase's other outbound-plus-inbound-ish source with real logic in both
directions, subclasses `TaskSource` directly, not `Trigger` — it implements
`poll()` for real *and* `report_progress`/`finish` for real. `GithubLabelReflector`
should do the same, with `poll()` implemented explicitly:

```python
class GithubLabelReflector(TaskSource):
    """Reflects a task's progress/outcome onto its source GitHub issue's
    labels — the outbound half only. Never produces a task (`poll()` is
    always `[]`), so it can be registered alongside any inbound producer
    without double-claiming anything. Matches a task purely by
    `task.data.source` (kind + repo) — the same guard GithubTaskSource uses —
    so it doesn't care who created the task, only where it's headed.
    """

    kind = "github"

    def poll(self) -> list[Task]:
        return []

    def report_progress(self, task: Task, progress: Progress) -> None: ...
    def finish(self, task: Task, result: FinishResult) -> None: ...
```

Everything else in design-01.md §2.1 (constructor knobs, `_set_state`/
`_mine`/`_issue`, field-for-field parity with `GithubTaskSource`'s label
half) stands as written. This is a one-line class-declaration change plus a
docstring rewrite, not a redesign — flagging it now so development doesn't
have to make the same judgment call mid-implementation.

### 2.4 Decisions on the plan's open questions (resolved, not left open)

The plan flags three open questions "for design to override." Design left
them at their defaults; I'm ratifying all three as final, so development
proceeds without re-deciding:

1. **`DEFAULT_STEP_LABELS` widening to cover `plan`/`design`/`architecture`:
   no.** Out of scope. The acceptance criteria's step sequence is explicitly
   illustrative ("e.g."), and widening label coverage changes observable
   GitHub-facing behavior beyond "restore what regressed" — that's a
   separate, reviewable decision (more label churn, more API calls per task,
   a UX call about noise) that shouldn't ride in on a bug-fix commit.

2. **Module home: same file as `GithubTaskSource` (`drivers/github_source.py`):
   yes.** The two classes are tightly coupled by composition (§2.2) and by
   the shared "GitHub + labels" concern; splitting them into sibling files
   would just add an import for no isolation benefit — nothing else in the
   codebase will ever depend on `GithubLabelReflector` without also touching
   `GithubTaskSource`'s neighborhood.

3. **Gate registration on `--no-github-source` rather than registering
   unconditionally: yes.** Confirmed against `_run`'s actual composition —
   `_github_sources` and a hypothetical always-on `_github_reflectors` would,
   for the classic-ingestion case, both put a GitHub-kind source with
   `_mine() == True` for the same repo into `sources`, doubling every
   `add_label`/`remove_label` call per event (each source runs its own
   independent `_set_state`, so this isn't cosmetically doubled work, it's
   twice the API calls against GitHub's rate limit for identical effect).
   Gating is strictly better and costs nothing — the flag already means "I've
   delegated ingestion elsewhere."

**Fourth open question — per-source exception isolation in
`SourceReflectorSink.emit()` — decision: leave out of this change's scope,
confirmed correct by re-reading `emit()` directly.** It's a plain
uncaught-propagates `for` loop today; a throwing source mid-loop skips the
rest of that tick's fan-out for *all* sources, including unrelated ones like
`GithubMergeabilityWatcher`. This is a real, pre-existing latent gap, and this
task's change doesn't worsen it structurally — the risk is proportional to
the number of registered sources, and gating (decision 3 above) keeps that
number the same as today in both configurations (classic: `GithubTaskSource`;
process-based: `GithubLabelReflector` — never both). Fold this into scope only
if development finds it genuinely trivial (a four-line `try/except` per
source in the loop); otherwise track it separately. Do not let it block or
expand this change.

## 3. Implementation guidance

**Where new code belongs**

- `src/harness/drivers/github_source.py` — add `GithubLabelReflector`
  (subclassing `TaskSource`, per §2.3) below `GithubTaskSource`; refactor
  `GithubTaskSource.__init__` to construct one and delegate
  `report_progress`/`finish` to it (per §2.2, as designed).
- `src/harness/cli.py` — add `_github_reflectors`, sibling to
  `_github_sources`/`_mergeability_sources`, same signature shape
  (`args, root, registry, *, slug_of=github_slug, client=None`). In `_run`,
  change:
  ```python
  github = [] if args.no_github_source else _github_sources(args, root, registry)
  sources = github + mergeability
  ```
  to:
  ```python
  github = [] if args.no_github_source else _github_sources(args, root, registry)
  reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
  sources = github + reflectors + mergeability
  ```
  (Order among `github`/`reflectors`/`mergeability` doesn't matter —
  `emit()`'s fan-out has no ordering dependency; keep them adjacent for
  readability, matching design-01.md.)

**Contracts (unchanged from design-01.md §4, restated for reference)**

```
GithubLabelReflector(client, repo, claimed_label="harness:queued",
                      pr_label="harness:pr-open", failed_label="harness:failed",
                      step_labels=None) -> TaskSource   # not Trigger — see §2.3

  .poll() -> []
  .report_progress(task, progress) -> None   # no-op unless _mine(task)
  .finish(task, result) -> None              # no-op unless _mine(task)

_github_reflectors(args, root, registry, *, slug_of=github_slug,
                    client=None) -> list[TaskSource]
```

**Data flow** — unchanged from design-01.md §1/§3: a `dispatched`/`finished`/
`failed` event carries a `task` dict; `SourceReflectorSink.emit()` rebuilds
the `Task`, maps the event to `Progress`/`FinishResult`, and calls every
registered source; `GithubLabelReflector._mine()` (identical logic to
`GithubTaskSource._mine()`, now shared by delegation) filters to tasks whose
`data.source.kind == "github"` and `data.source.repo == self._repo`; a match
recomputes the full target label via `_set_state` (remove every managed label
but the target, add the target) — stateless, idempotent, no new persistence.

**Test surface** — as scoped in plan-01.md §"Rough plan" steps 4–6 and
design-01.md §5: unit tests for `GithubLabelReflector` (transitions,
idempotent double-call, foreign-kind/repo/no-source no-op, unknown-step
no-op), a `test_cli.py` wiring test (reflectors present iff
`--no-github-source`, never alongside a `GithubTaskSource` for the same
repo), and one `test_processes_e2e.py` extension driving a `github-issues`
process end-to-end against `FakeGithubClient` to observe the label sequence.
`tests/test_github_source.py` must stay green unmodified — it's the proof
that the `GithubTaskSource` refactor is behavior-preserving.

## 4. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Worktree is 57 commits behind `origin/main`; every file this task touches exists only there. | **Prerequisite, not a risk to design around**: `git fetch origin && git merge origin/main` (merge, not rebase — confirmed as this repo's own convention) must be the first commit of implementation, before any edit named here. Run `pytest -q` immediately after merge to confirm a clean baseline. |
| Double registration of a GitHub-kind reflecting source for the same repo (duplicate `add_label`/`remove_label` calls, wasted GitHub API quota). | Structural, not runtime: `github`/`reflectors` are mutually exclusive by construction in `_run` (§2.4 decision 3), both derived from the same `args.no_github_source`. A wiring test asserts this directly. |
| Misreading `GithubLabelReflector` as inbound-capable because of its base class. | Resolved by §2.3 — subclass `TaskSource`, not `Trigger`. |
| `SourceReflectorSink.emit()`'s unguarded loop: one throwing source starves the rest of that tick's fan-out. | Pre-existing, not worsened by this change (source count is unchanged in both configurations). Left out of scope per §2.4 decision 4; flag as a fast-follow if trivial. |
| Behavior drift for classic ingestion (`GithubTaskSource` without `--no-github-source`). | Delegation is a pure refactor — `tests/test_github_source.py` runs unmodified as the regression gate; AC2 in plan-01.md exists specifically to catch this. |
| Scope creep into `DEFAULT_STEP_LABELS`, `_ACCEPTED_SINK_KINDS`, or a new `Reflector` port. | Explicitly closed by §2.4 decision 1 and by design-01.md §2.4 — none of these are touched by this change. |

## 5. Prerequisites before implementation begins

1. Merge `origin/main` into this task's branch (see Risks table) and confirm
   `pytest -q` is green before the first edit.
2. Re-confirm `tests/test_architecture.py`'s exact current guard names post-
   merge (`test_orchestration_does_not_import_source_port`,
   `test_only_app_and_cli_wire_drivers`) — they were read directly off
   `origin/main` for this assessment and should need zero changes, but
   development should verify the names didn't shift in the 57 commits this
   worktree hasn't seen yet.

No other prerequisites. The design is otherwise ready to implement as
written, with the `TaskSource`-not-`Trigger` correction in §2.3 folded in.
