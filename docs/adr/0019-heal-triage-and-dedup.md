# ADR-0019: Heal triage and issue dedup

Status: Accepted

## Context

`0018-healing-as-a-process.md` gave the healer a home in the Process idiom,
but its two-step workflow (`heal` → `file-issue` → `end`) had two gaps the
operator found in practice.

First, the `heal` step's persona could only ever draft and file — it had no
"nothing to do here" outcome. A failure the healer judged external, transient,
or simply not a harness bug still had to be filed, or the task's own request
had to be routed around entirely. Left unaddressed, this meant the healer
filed nothing useful for genuinely operational failures: either every failure
became a filed issue (noise) or the operator had to widen the persona's
judgment inside a single `done`/`request_changes` outcome pair that was never
designed to carry a triage decision.

Second, the fired heal task carried no `repository` — nothing told the
healer which repo's open issues it might be about to duplicate. Naively
widening the healer to *always* file (to close the first gap) without also
letting it check for an existing, correlated issue would trade "files
nothing for operational failures" for "files a duplicate issue on every
recurrence of the same harness bug" — the same noise problem from the other
direction. A harness bug that fails repeatedly (the common case — a bug
doesn't fix itself between ticks) would file one issue per tick until a human
intervened, defeating the point of an advisory issue tracker.

## Decision

Three changes, landed as a sequence of tasks on this branch and described
here as one shape:

- **The heal task carries the harness `repository`.** The `failed-tasks`
  check's `Observation` (and so `ScheduledTrigger._task_for`) now stamps a
  `repository` — read from the check's own `params.repository` — onto the
  fresh heal task it fires, the same field every ordinary agent step already
  reads to attach a worktree (invariant #15). This is additive: an autoheal
  process with no `repository` param (`params: {}`, the pre-existing default)
  fires a heal task with `repository=None`, unchanged from before this
  change.

- **`heal` triages into `file` or `skip`.** The `heal` step's persona is
  rewritten to a three-way diagnosis (a fixable harness bug, an
  operational/tuning problem worth filing, or external/transient/impossible)
  and reports one of two outcomes: `file` (drafts the issue, as before, and
  moves on to dedup) or `skip` (nothing to file — routes straight to `end`).
  Where the old workflow's `done`/`request_changes` pair borrowed the
  generic persona vocabulary, `file`/`skip` name the actual decision.

- **A new `dedup` step forks on `unique`/`duplicate`.** `heal`'s `file`
  outcome now routes into `dedup` rather than directly into `file-issue`.
  The `dedup` persona (`Read`, `Bash` tools; no worktree beyond what it needs
  to shell out to `gh`) reads the harness repo's currently-open issues and
  compares them against the drafted issue, returning `unique` (routes on to
  `file-issue`, which opens it exactly as before) or `duplicate` (routes
  straight to `end`, silently — no issue opened, no board-visible failure,
  just a quiet settle).

- **The vocabulary lives in the workflow, not the personas.** `heal`'s and
  `dedup`'s outcome sets (`file`/`skip`, `unique`/`duplicate`) are declared as
  edges in `workflows/heal.json`; `Workflow.outcomes_for` derives them live
  and `ClaudeCliBehavior` feeds them into the prompt and the runner's verdict
  check (invariant #42, `0018-workflow-owns-outcome-vocabulary.md`). The
  personas describe judgment in prose — "finish with the outcome that files
  it" / "the outcome that treats this as a duplicate" — never a hardcoded
  outcome literal. `AgentSpec.allowed_outcomes` for both steps is only the
  workflow-less fallback (`agents/heal.json`'s and `agents/dedup.json`'s own
  `allowed_outcomes` on disk are the CLI's clamped-to-loadable placeholder,
  never the live vocabulary a served workflow actually enforces).

- **The three-step shape is proven end to end on in-memory drivers.**
  `tests/test_self_heal_e2e.py` mirrors `src/harness/cli.py`'s shipped
  `HEAL_DEFINITION` verbatim and drives a `MemoryAgentCatalog` carrying both
  a `heal` and a `dedup` `AgentSpec`, scripting `FakeAgentRunner` per step to
  exercise the three routing paths: `file`→`unique` (one issue opened, task
  ends clean), `file`→`duplicate` (zero issues, task ends clean, `dedup`
  provably ran), and `skip` (zero issues, `dedup` provably never ran). Because
  `ClaudeCliBehavior` resolves each step's accepted outcomes from the
  workflow's own edges rather than the persona's static `allowed_outcomes`,
  and the dispatcher only advances a task along an edge the workflow actually
  declares (`router.route` → `Workflow.target`), a scripted outcome the
  workflow doesn't declare for that step fails to route as the test expects —
  so driving these three paths through the real router/dispatcher is itself
  the end-to-end proof of invariant #42, not just of the heal/dedup wiring.

## Consequences

- Self-healing now has real operational visibility: a triaged-away failure
  (`skip`) and a deduplicated one (`duplicate`) both settle silently, while a
  genuinely new, fixable harness bug (`file`→`unique`) still files exactly
  one advisory issue — the signal-to-noise problem `0018` didn't fully solve
  is closed without adding a second issue tracker or a threshold/count
  mechanism (an explicitly deferred follow-up).
- **Silent-on-duplicate is a deliberate trade, not an oversight.** A
  recurring harness bug produces one filed issue on first observation and
  then zero board-visible signal on every subsequent tick — there is
  currently no "seen again" comment or recurrence counter on the original
  issue (also an explicitly deferred follow-up, alongside differentiated
  labels per triage outcome). An operator relying on issue activity as a
  liveness signal for a recurring bug will not see one.
- Supersedes the two-step-heal portion of `0018-healing-as-a-process.md`
  (its "Target — a two-step `heal` workflow" decision bullet) by reference,
  per ADR-0000's additive convention — not by deletion. The rest of `0018`
  (the `failed-tasks` check, the `open-issue` finisher, the recursion guard,
  the Process-compilation wiring) is unchanged and still authoritative.
- **The check's `repository` param must equal `HARNESS_HEAL_REPO`.** Both
  `_ensure_autoheal_process`'s generated `processes/autoheal.json` (`action.
  params.repository`) and the `open-issue` finisher's `OpenIssueBehavior(repo=
  ...)` are wired from the same `heal_repo` variable in `cli.py`'s
  `--heal-repo`/`HARNESS_HEAL_REPO` path, so they cannot drift when the CLI
  generates the file. A hand-edited `processes/autoheal.json` (never
  clobbered by the CLI, per `0018`) can drift the two apart: the heal task
  would then attach a worktree in one repo while the drafted issue is filed
  against another. This is an operator-authored-file risk, not something the
  harness validates today.
- **That same slug must also be registered in `repos.json`, or heal tasks
  never attach a worktree.** The `repository` stamp (invariant #25) is only
  meaningful once `GitWorkspace.attach` can resolve it via
  `RepositoryRegistry` — an unregistered `HARNESS_HEAL_REPO` slug makes
  `resolve()` raise `RepositoryNotFound` for every heal task, which then
  fails to attach, lands in `failed/`, and is retired to `healed/` by the
  recursion guard (invariant #25) with no issue ever filed: self-healing
  goes silently inert. `cli._run` now checks this at startup and prints a
  `warning:` to stderr (never a hard error — the rest of the harness still
  starts) when the configured `heal_repo` isn't in the registry.
- The invariant #24–#27 prose in `CLAUDE.md` is refined (not renumbered) to
  describe the three-step `heal → dedup → file-issue` shape and the
  `repository`-carrying check.
