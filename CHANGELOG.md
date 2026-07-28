# CHANGELOG


## v1.4.2 (2026-07-28)

### Bug Fixes

- **board**: Green card accent only for a task that actually finished
  ([`40b8aac`](https://github.com/onpaj/harness_v2/commit/40b8aac2b0afe2435d9ff7ea149512b48dcc85ea))

The stripe answers "what is happening to this task", but it painted green on any card whose
  `last_outcome` was `done` — including one idle two columns into a workflow, where `done` is only
  the previous step's verdict. Same lie the bare `done` badge told, in colour.

Green is now gated on the card sitting in a terminal column; a step column falls back to the neutral
  stripe, which is the honest answer (waiting its turn). `request_changes` keeps its accent
  everywhere — "it came back" is not something a column name says — and `is-working` still outranks
  both.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Documentation

- Plan terminal-task retention
  ([`85313d1`](https://github.com/onpaj/harness_v2/commit/85313d15fef0a01d512d045a2257509f6f859422))

Four TDD tasks: the RetentionReconciler core, its app.py wiring on the existing reconcile loop, the
  HARNESS_RETENTION_DAYS knob, and the docs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


## v1.4.1 (2026-07-28)

### Bug Fixes

- **board**: Name the step an outcome badge belongs to
  ([`c9177ce`](https://github.com/onpaj/harness_v2/commit/c9177ce86bdc640a9808157f859c4f740e2246a8))

A card's outcome badge is a *step's* verdict, but it rendered the bare word — so a task two columns
  into a workflow, often right next to "processing", read as "this task is done". The board had one
  word for two different things: a step's outcome and the terminal `done` queue.

The badge now names the step that reported it ("plan · done"), with the bare outcome kept as the
  fallback when history has nothing to attribute it to. Both history entry shapes carrying an
  outcome agree on that step — the consumer's delivery entry ran it, the dispatcher's routing entry
  left it — so the last entry with an outcome answers it either way.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Documentation

- Correct automerge's shipping story and document it for operators
  ([`02ae5e7`](https://github.com/onpaj/harness_v2/commit/02ae5e72291bf3ce7fc07d9fbb09fa2ac01b5bcd))

PR #131 made `harness init` seed `processes/automerge.json` and moved the withholding entirely onto
  the binding's `dry_run`, but three places still described the decision as originally accepted —
  ADR-0023's "It ships withheld", the automerge design spec, and a `_init` comment sitting directly
  above the `_ensure_automerge_process` call it claims does not happen.

Also documents automerge in the README, where it had no coverage at all: the four candidate gates,
  the dry-run → armed flip, the operator-sets-the-bar split, and the branch-protection caveat that
  decides how much `clean` is really worth. The adjacent autoresolver section claimed a tokenless
  `github-conflicts` process fails the run; `MissingCredential` has made that a skip-with-warning
  since.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


## v1.4.0 (2026-07-27)

### Bug Fixes

- Widen the self-heal brake to PR-borne tasks
  ([`ac6e216`](https://github.com/onpaj/harness_v2/commit/ac6e216c9ecca33749ca557515659f5176499fee))

Two Checks mint failed-queue tasks from the harness's own pull requests carrying neither a body nor
  data.heal: GithubConflictsCheck stamps source.kind "mergeability" on resolver tasks,
  GithubMergeableCheck stamps "pull-request" on automerge-review tasks. FailedTasksCheck's one-hop
  brake only recognised the issue-body marker, so once the config half of self-heal lands, a
  resolver/automerge failure could re-enter the pipeline unbounded.

Add a third decline path keyed on a new PR_BORN_SOURCE_KINDS constant, alongside the existing
  data.heal and marker guards (unchanged notes and ordering). Also documents allowed_labels'
  deliberate omission from HEAL_DEFINITION's file-issue binding, with a pinning test, mirroring the
  existing AUTOMERGE_DEFINITION dry_run comment.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Documentation

- Correct spec and plan against the real tree
  ([`2f403cb`](https://github.com/onpaj/harness_v2/commit/2f403cb9f4c4f092caf9497129353c4be1401007))

The first draft was researched against ~/harness-app, a worktree base that had drifted behind main.
  The marker is harness-issue:, not harness-heal:; the file-issue binding's label is also the
  idempotency search scope, so flipping it to a label the ingester deletes would open duplicate
  issues. Route via allowed_labels instead, and widen the brake to every harness-filed issue.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Correct spec references and sync stale plan strings
  ([`c4978dc`](https://github.com/onpaj/harness_v2/commit/c4978dc164a4778b0e512560cf46350071a9a167))

The spec's §1 claimed cli._autoheal_repo reads back processes/autoheal.json's
  action.params.repository; no such function exists. State the real path instead: app.build() passes
  it to FailedTasksCheck(repository=...), it rides Observation.repository, ScheduledTrigger stamps
  it onto the task, and OpenIssueBehavior resolves it via slug_for(task.repository).

The plan's Task 6 verification steps quoted settle-note text and a heal-declined occurrence count
  that no longer match what ships after the brake was widened to PR-borne tasks. Sync both so an
  operator's grep still matches. Also state plainly that the brake takes effect the moment the
  release installs, not only once the config half (Task 5) lands.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Describe all three failed-tasks recursion guards
  ([`38cf07a`](https://github.com/onpaj/harness_v2/commit/38cf07a94b9de1f87d074fda53b4ab40b9fccf8e))

Invariant 25 and the failed-tasks Gotchas bullet still said "two markers guard two distinct cycles"
  and only described the data.heal and harness-issue-marker guards. A third guard (PR-born
  source.kind, added when the brake was widened to resolver/automerge tasks) was missing from both.
  Updated both to describe all three declines and the one rule they express, keeping the existing
  monotonic-drain claims intact.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Implementation plan for routing self-heal into the pipeline
  ([`5fe2489`](https://github.com/onpaj/harness_v2/commit/5fe24899d09aeadf724837d234d688c5884383cb))

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Record the second recursion guard in invariant 25
  ([`b59aada`](https://github.com/onpaj/harness_v2/commit/b59aada69a56e217c674049512209c0d99edc41f))

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Spec for routing self-heal issues into the development pipeline
  ([`dad0cde`](https://github.com/onpaj/harness_v2/commit/dad0cde4dc3dbd44c4b3d9423240b3becbb4201a))

The healer files a diagnosed issue and stops; the operator's relabel to harness:todo is the only
  thing between a harness failure and a proposed fix. Point the file-issue finisher at harness:todo
  directly, and pair it with a one-hop brake so a failed fix attempt cannot file a fresh issue and
  feed the pipeline itself.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Features

- Bound self-healing to one hop per root failure
  ([`8f16bb6`](https://github.com/onpaj/harness_v2/commit/8f16bb6536a26de26f9472ca55d0af5e501c244b))

A fix task born from a healer-filed issue carries no data.heal, so once the healer files into an
  ingested label its own output can fail back into failed/ and file a fresh issue, unbounded.
  Decline any failed task whose body carries the healer's marker, settling it to healed/ with a
  distinct note.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Draft self-heal issues as work orders
  ([`1fa7203`](https://github.com/onpaj/harness_v2/commit/1fa720380ff1c11f6d7a05a7301e9ebf438567b0))

The healer's issue is becoming the input to the development pipeline's plan step rather than
  something a person reads first, so specify the body's shape: symptom, reproduction, proposed
  change, acceptance criteria, and which kind of finding it is.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Refactoring

- Single-source the PR-born source.kind constants
  ([`9f522db`](https://github.com/onpaj/harness_v2/commit/9f522db407fa213723304dc5fb09a92ca806d742))

FailedTasksCheck's PR_BORN_SOURCE_KINDS was an independent copy of the "mergeability"/"pull-request"
  literals each owning driver stamped inline, so a rename in either driver could silently disarm the
  recursion guard with no test catching it. GithubConflictsCheck and GithubMergeableCheck each now
  export their own SOURCE_KIND constant, used at the stamp site; failed_tasks_check.py builds
  PR_BORN_SOURCE_KINDS by importing both, mirroring how MARKER_PREFIX is already shared. Two tests
  in test_failed_tasks_check.py now reference the imported constants instead of bare strings, so
  renaming either identifier breaks import/collection immediately.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


## v1.3.0 (2026-07-27)

### Features

- **board**: Group columns by kind and explain what each one means
  ([#133](https://github.com/onpaj/harness_v2/pull/133),
  [`33097ad`](https://github.com/onpaj/harness_v2/commit/33097ad87e21d0901ad2d6870b909d7882b92896))


## v1.2.0 (2026-07-27)

### Features

- Ship a working automerge Process on every repo, fix dedup, keep a missing credential non-fatal
  ([#131](https://github.com/onpaj/harness_v2/pull/131),
  [`854a54a`](https://github.com/onpaj/harness_v2/commit/854a54a34330a3c049fdb375670176d8375312c6))


## v1.1.0 (2026-07-27)

### Features

- Automatic PR merging as a Process, gated by an operator-set threshold
  ([#130](https://github.com/onpaj/harness_v2/pull/130),
  [`24dd015`](https://github.com/onpaj/harness_v2/commit/24dd0157b16b5fb1a3b47d1bab072fdd3b2f1e8b))


## v1.0.0 (2026-07-26)

### Bug Fixes

- Keep an unknown finisher kind fatal, make dropped-workflow Processes inert
  ([`403274d`](https://github.com/onpaj/harness_v2/commit/403274d1c410ce33b30fa11e441d8e8b88e32050))

Two Important findings against 274a5a5's warn-instead-of-crash-loop change:

1. `_validate_served_workflows` caught every `ValueError` from `validate_workflow_finishers`, so an
  unknown finisher kind (a value that's set and wrong, e.g. a typo or `label-issue` with no
  `GITHUB_TOKEN`) silently dropped its workflow instead of failing the run — the wrong side of the
  operator's fail-fast/warn-only-on-missing rule, and it made `build()`'s own unknown-kind check
  unreachable via the CLI. Fixed with a distinct type, `UnknownFinisherKind`, raised only for the
  unregistered-kind case and re-raised (not caught) by the served-set filter; `build()`'s own
  diagnostic enumeration (`known: ...`) is restored in the fatal message.

2. Dropping `heal` from the served set left `processes/autoheal.json` targeting a workflow that no
  longer exists in the served set — `FilesystemProcessRepository`'s flat membership test only still
  passed by coincidence (the `heal` *agent* catalog entry shares the name), so `--agent dummy` or
  `--agent claude` with no `agents/heal.json` still crash-looped exactly as before. Fixed by
  threading the dropped-workflow set from `cli._run` through `build()` into
  `FilesystemProcessRepository.build()`, which now skips a process targeting one of them instead of
  failing the whole build; the served-workflow drop warning now also names the dependent
  process(es).

Test changes: the heal-drop test in test_cli.py no longer mocks `build` (only `serve`), is
  parametrized over `--agent claude`/`--agent dummy`, and a new test covers `agents/heal.json`
  removed — the composite path these tests claim to prove is now actually exercised. Added coverage
  for the unknown-kind and label-issue-without-a-token fatal paths. test_app.py's factory-call test
  now asserts a call count, not just an in-factory assertion.

Corrected three documentation sites that asserted an unknown kind still fails the build while the
  shipped code had stopped doing that: CLAUDE.md's finisher invariant, cli.py's
  `_validate_served_workflows` docstring, and ADR-0022.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Make unknown-finisher-kind fatality order-independent, reconcile autoheal warnings
  ([`947ae2a`](https://github.com/onpaj/harness_v2/commit/947ae2aa0b577c2b0eb0599609e0524e9fac8542))

validate_workflow_finishers did the unknown-kind lookup and the factory invocation in a single loop
  over a workflow's finisher bindings, so whichever binding a dict iterated first decided the
  outcome: a config-shaped ValueError from an earlier binding could escape before a later binding's
  unknown kind was ever seen, silently dropping the workflow (exit 0) instead of failing the whole
  run (exit 2) purely depending on JSON key order. Restored build()'s own two-pass shape: a complete
  unknown-kind check over every binding first, then a separate pass invoking factories.

Also stop _warn_missing_autoheal_repository from warning about a "failed-tasks" process whose target
  workflow was already dropped by _validate_served_workflows -- that process is inert once its
  workflow is gone, so claiming it "will run heal/dedup on every failure" directly contradicted the
  drop warning already printed for the same root.

Updated two stale test docstring cross-references to a since-removed test name
  (test_a_binding_without_a_label_fails_the_build) to point at the tests that now cover that
  behavior.

- Warn instead of crash-loop on an unwirable workflow or repo-less autoheal
  ([`274a5a5`](https://github.com/onpaj/harness_v2/commit/274a5a5605add3a08afbe5aef93c9ddbf53e8026))

Two places violated ADR-0021/0022's own principle that a value set but wrong is a typo and fails
  fast, while a value that's absent is "not configured yet" and only warns.

1. `app.build()` eagerly constructs the finisher for every bound step of every served workflow, so
  one workflow with an unwirable binding exits the whole run with 2. Combined with "serve every
  workflow on disk", a single stale file crash-loops a launchd-supervised service — confirmed
  against the reference install's own `workflows/heal.json`, still carrying the pre-generic string
  form `"file-issue": "open-issue"`, which parses to an empty config and fails the `open-issue`
  factory's `label` check.

`cli._run` now validates each served workflow's own finisher bindings against the finisher registry
  before calling `build()` (`_validate_served_workflows`, using a new `app.validate_workflow_
  finishers` that mirrors `build()`'s per-step resolution without duplicating its registry
  construction or its queue/catalog/workspace machinery) and drops one that can't be wired, printing
  a `warning:` naming the file, the step and the reason. `build()`'s own fail-fast is unchanged for
  everything that survives the filter — an unknown kind, or a genuine cross-workflow binding
  conflict.

2. A compiled `failed-tasks` process with no `action.params.repository` is valid (the seeded
  `harness init` default) but silently inert: self-healing spends an agent call on `heal` and one on
  `dedup` per drained failure and files nothing, with the token bill as the only signal.
  `cli._warn_missing_autoheal_repository` now warns about it at startup, mirroring
  `_declared_sink_kinds`'s raw-JSON prescan pattern. A *present but wrong* repository is unchanged —
  still a `ProcessValidationError` at process-compile time.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Documentation

- Renumber ADR-0021 to ADR-0022, superseding PR #129's autoheal ADR-0021
  ([`ada4f9c`](https://github.com/onpaj/harness_v2/commit/ada4f9c26cec6bea4dc4ecaa3a1f12b4befeaa6b))

`main` merged docs/adr/0021-autoheal-enabled-by-its-process-file.md (PR #129) while this branch's
  own ADR-0021 (open-issue-is-a-generic-finisher) was in flight, claiming the number for a different
  decision. Renumber this branch's ADR to 0022 and update every reference that refers to it: the
  heading, CLAUDE.md invariant 26, the superseded-note blockquotes in 0018-healing-as-a-process.md
  and 0019-heal-triage-and-dedup.md, the two ADR slugs in harness_docs_site/architecture.py, and the
  historical plan document that specified creating it — mirroring the precedent set by commit
  8d42497 for the previous 0020->0021 renumbering.

ADR-0022 also now records, explicitly, that it supersedes PR #129's ADR-0021: both reached the same
  diagnosis (HARNESS_HEAL_REPO's ambient,

two-place configuration was the problem) but different fixes. Agreed: the environment variable is
  gone and processes/autoheal.json is the right home. Reversed: ADR-0021 keeps --heal-repo as a
  bootstrap that writes the process file; this branch deletes it outright, since `harness init`
  already seeds the file unconditionally. The substantive difference is what
  action.params.repository means: ADR-0021 leaves it a GitHub slug doing two jobs (a repos.json key
  and a GitHub API identity) that silently disagreed on the reference install; this branch makes it
  a repos.json name and derives the slug from the clone's origin, so the field has exactly one
  meaning. Reciprocal blockquotes were added to PR #129's own ADR-0021 file, matching this repo's
  established partial-supersession style (inline blockquotes, Status line unchanged).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


## v0.21.2 (2026-07-26)

### Bug Fixes

- Don't restart the service when nothing was upgraded
  ([`6d15952`](https://github.com/onpaj/harness_v2/commit/6d159520da8976c421f8d89e443131fdddee3b29))

`harness update --restart` restarted the LaunchAgent unconditionally, gating only on idleness. The
  `--restart-service` path has always gated on the version actually changing; `--restart` — the path
  the autoupdate schedule uses — never got that gate.

Consequence on a live install: the every-30-minutes autoupdate fires, uv reports "Nothing to
  upgrade", and the service is SIGKILLed anyway. `com.harness` sits permanently at exit -9 and no
  stage outliving 30 minutes can ever complete.

Snapshot the pre-upgrade version whenever either restart path is selected and skip the restart when
  it is unchanged. A plain `harness update` still takes no version snapshot at all, so it pays no
  extra subprocess.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


## v0.21.1 (2026-07-25)

### Bug Fixes

- Spurious verify failures — hermetic test env, and autoheal enabled by its process file
  ([#129](https://github.com/onpaj/harness_v2/pull/129),
  [`e76edc4`](https://github.com/onpaj/harness_v2/commit/e76edc4278e5c5fb5fc5adc6520c850dae897324))

### Documentation

- Reconcile the Jira check's heal-repo reference with its removal
  ([`96be6b8`](https://github.com/onpaj/harness_v2/commit/96be6b81b2b0ac364f98f310282106c6c1ec2bfc))


## v0.21.0 (2026-07-24)

### Bug Fixes

- Address review findings on the open-issue finisher rewrite
  ([`ab88c54`](https://github.com/onpaj/harness_v2/commit/ab88c5495f611013de2b4dca7ebbb62ced919354))

- Restore the self-heal e2e's tripwire assertions (one issue opened, a marker derived by formula,
  the Origin footer) and mark it xfail(strict=True) naming Task 4, instead of the placeholder
  `assert tracker.opened == []` that certified the currently-broken outcome and would stay green
  forever. - Rewrite the interim open-issue binding's comment in cli.py: it no longer claims to be
  behavior-preserving. With the heal persona still unchanged, a real heal run today either raises
  DraftError -> IssueError or files nothing. - Resolve OpenIssueBehavior's repo slug lazily, only
  once there is a draft to file, so a repository-less task with zero drafts settles done instead of
  raising (invariant 25). - Collapse the duplicated allowlist-partitioning comprehensions into one
  loop and annotate refs/_summary's first parameter.

- Drop the dead --resolver-workflow flag, tighten related tests
  ([`5752298`](https://github.com/onpaj/harness_v2/commit/57522987b524f7d62f9651d5e82ea689b0e1c167))

--resolver-workflow lost its only reader when Task 5 deleted the resolver force-add — the resolver
  workflow is now served because workflows/resolver.json exists, exactly like any other workflow, so
  the flag's help text was already false. Delete the declaration.

Also: correct test_run_heal_via_env_var_wires_everything_without_a_flag's docstring, which still
  claimed HARNESS_HEAL_REPO serves `heal` (it doesn't since Task 5 — disk presence does; the env var
  only stamps params.repository and materializes processes/autoheal.json). Drop the two now-vacuous
  `DEFAULT_HEAL_WORKFLOW in served_names` assertions that held regardless of heal wiring. Strengthen
  test_the_workflow_selection_flags_are_gone to assert argparse's own rejection (exit code 2 +
  "unrecognized arguments") for --workflow, --all-workflows and --resolver-workflow, instead of
  accepting any SystemExit — the old form passed even under the pre-Task-5 code because a real
  serve() started and died on a busy port, silently never exercising flag rejection and violating
  the no-real-serve()-in-tests rule.

BREAKING CHANGE: `harness run --resolver-workflow` no longer exists; the resolver workflow is served
  automatically whenever workflows/resolver.json is present, same as every other workflow.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Exit 2 on a malformed process file instead of a raw traceback
  ([`c911572`](https://github.com/onpaj/harness_v2/commit/c9115720ec726fd28b28d9b1cd11a93332c17a33))

ProcessValidationError subclasses Exception, not ValueError, so `_run`'s except clause around
  build() never caught it — a process file naming an unregistered action.params.repository (e.g. a
  typo in processes/autoheal.json, the field operators are told to configure) crashed main() with a
  traceback instead of printing error: ... and returning 2. Under launchd that means a crash-loop
  with no readable cause.

Also tightens _validate_action_repository_param's docstring: it claimed to validate the same way
  _parse_repository does, but a truthy non-string value takes a different path through the two (an
  up-front "invalid repository" rejection there vs. falling through to the membership check here,
  still rejected but with a different message).

- Key the self-heal xfail tripwire's marker off the running task
  ([`49af3d9`](https://github.com/onpaj/harness_v2/commit/49af3d97478a0fb837074837c80c8c8e11f00958))

The strict-xfail tripwire on test_heal_file_dedup_unique_opens_exactly_one_issue compared the filed
  issue's marker against marker_for("tsk_e2e", ...) — the original failed task's id — but
  OpenIssueBehavior.run actually derives the marker from the fresh heal task FailedTasksCheck fires,
  a different id. That made the assertion unsatisfiable no matter what Task 4 does to the heal
  persona, so xfail(strict=True) could never flip to XPASS(strict) and force the marker's removal
  once the real gap closes.

Hoist the done/ read above the tracker assertions and key the marker off the heal task that actually
  ran; correct the comment that wrongly described the marker as scoped to the original task's id.
  The test still reports xfailed — the persona gap is genuinely still open — but is now capable of
  passing.

Also: restore the pre-narrowing test name (test_heal_file_dedup_unique_opens_exactly_one_issue),
  reconcile cli.py's open-issue wiring comment with the TODO block that already says the wiring is
  temporarily broken, and annotate the one unannotated local in OpenIssueBehavior.run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Kill the whole process group on verify command timeout
  ([`c963dfa`](https://github.com/onpaj/harness_v2/commit/c963dfa54c01fb0de10ee225f3c31497a92d7354))

process.kill() only signalled the shell PID; a compound verify command's children (e.g. pytest-xdist
  workers) survived the timeout. Run the shell in its own session (start_new_session=True) and, on
  timeout, kill the whole process group before falling back to process.kill(), both wrapped against
  ProcessLookupError once the group/process is already gone.

- Reject wrongly-typed body/labels in parse_drafts instead of coercing
  ([`3a6e1db`](https://github.com/onpaj/harness_v2/commit/3a6e1db15b70c70d772e3f4a0d19c28d87f42a11))

A draft with {"body": 5} silently produced body="" and {"labels": "oops"} silently produced
  labels=(), while every other malformation in parse_drafts already raised DraftError. Per the
  module's own docstring, a persona that wrote a report but malformed its block is a real fault
  worth surfacing, not silently swallowing a diagnosis and filing an empty-bodied issue. Raise
  DraftError for both cases, naming the draft index and the offending field; a missing body/labels
  key still defaults as before.

Add tests for wrong-typed body, wrong-typed labels, and a draft with no body key (which must still
  succeed with the "" default).

- Stop naming a fixed "harness repo" in the dedup step's description
  ([`97bc9ef`](https://github.com/onpaj/harness_v2/commit/97bc9efe6d0a6145dfb284fd6742aa3456d5d69c))

The `dedup` step's workflow description named a fixed "harness repo", but heal now files onto
  whatever repository processes/autoheal.json's action.params.repository names -- there is no more
  fixed heal repo since --heal-repo/HARNESS_HEAL_REPO were removed. This is prompt data, not a
  comment: ClaudeCliBehavior reads it into the composed prompt (invariant 42), so the stale text
  misdirected the agent at run time. Reworded to name the task's own repository instead, and updated
  the test's mirrored copy of HEAL_DEFINITION to match.

- Sweep remaining "fixed harness repo" staleness in heal docs/prompt
  ([`3b2b931`](https://github.com/onpaj/harness_v2/commit/3b2b9316d8f41aa27b11b25b22af9bc57dd7025f))

Four stragglers from making the open-issue finisher generic (repo now comes from
  action.params.repository, not a fixed harness repo):

- architecture.py's IssueTracker port description blamed idempotency on "a crash before the settle",
  which is wrong — FailedTasksCheck.evaluate settles the claim before returning the Observation, so
  no issue can be filed at that point. Reworded to match CLAUDE.md's verified wording: idempotency
  protects a re-run of the same heal task. - HEAL_DEFINITION's dedup->file-issue hint (cli.py,
  prompt-visible) and its test mirror (test_self_heal_e2e.py) still said "the harness repo". -
  architecture.py's GithubIssueTracker driver description likewise said "the harness repo" instead
  of the task's own repository. - README.md overclaimed the original failed task's id "survives only
  as data.heal.of" — it also survives in the heal task's dedup_key, since FailedTasksCheck emits
  Observation(state_key=task.id, ...) and ScheduledTrigger folds state_key into the dedup_key.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Warn the healer persona against echoing its drafts array in the final message
  ([`5b7ac82`](https://github.com/onpaj/harness_v2/commit/5b7ac8253823dbd6672f186d8acf5fc6e12b260f))

_HEALER_PERSONA tells the agent to end its artifact file with a fenced json array of issue drafts,
  but _extract_verdict (drivers/claude_cli.py) takes the last fenced json block of the agent's final
  *message* and returns None for a list, which becomes a VerdictError and fails the task. An agent
  that helpfully echoes its drafts array in its closing message would fail for that reason alone.
  Name the hazard explicitly: the drafts array belongs in the artifact file, never in the final
  message.

### Documentation

- Add a stray-workflow-JSON pre-flight to the live migration task
  ([`609bdcf`](https://github.com/onpaj/harness_v2/commit/609bdcff69803c7b0c12ae49d3d594a98985f97f))

- Adr-0020, generic open-issue finisher and data-driven serving
  ([`cf4e2e8`](https://github.com/onpaj/harness_v2/commit/cf4e2e80da6d202359e315073d2e88e237be3df8))

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Correct IssueError docstrings to match actual failure handling
  ([`a9b44d4`](https://github.com/onpaj/harness_v2/commit/a9b44d4b7ee6d66a4f71badf434eb3571adde6f9))

ports/issues.py claimed a `Healer` loop catches IssueError and settles the task to healed/, and that
  the module opens issues on "the harness repo". Neither is true: there is no Healer loop
  (self-healing is a Process), nothing catches IssueError, and Consumer.tick's blanket except
  Exception sends the task to failed/ instead (consumer.py:80). Recursion is prevented by
  FailedTasksCheck's data.heal marker guard (invariant 25), not by in-behavior handling. Rewrite
  both docstrings to describe the real path.

- Fix review findings on the open-issue/ADR-0020 doc sweep
  ([`0bc1478`](https://github.com/onpaj/harness_v2/commit/0bc14780a37f5bb52c5953f74ed2c880db1f6d80))

Corrects the eight remaining prose findings from Task 7's review: the open-issue idempotency marker
  is scoped to the heal task, not the original failed task (README.md, CLAUDE.md); the autoheal
  posture is seeded live on every root, not shipped dormant (README.md); a literal mid-sentence
  triple backtick that inverted code/prose parity in the rendered docs site is rephrased in both
  CLAUDE.md and ADR-0020; three lagging statements (marker shape, seed params, singular/wrong-repo
  issue filing) are brought in line with the code; and ADR-0020's Context now names the
  ADR-0018/0019 decisions it supersedes.

- Fold the docs-site marker sweep into the plan's Task 7
  ([`6c10753`](https://github.com/onpaj/harness_v2/commit/6c10753c5dd544da9823834ca5fa16d82603ed5f))

- Fold the issue_drafts module-map wording fix into the plan's Task 7
  ([`6bfbf10`](https://github.com/onpaj/harness_v2/commit/6bfbf1032568e8cfba65756399ecdd25429b7270))

- Give Task 4 the proven e2e recipe and the Origin-carrying draft body
  ([`b7c8e93`](https://github.com/onpaj/harness_v2/commit/b7c8e939e9e4094e7a7df2dedcce6c83faee64c7))

- Give Task 7 the stale-ADR sweep and the default-on posture change
  ([`824472a`](https://github.com/onpaj/harness_v2/commit/824472a04910a0c549fe15beaf30332df31d0e53))

- Hand Task 6 the ordering constraint Task 5 left undocumented
  ([`020a3f6`](https://github.com/onpaj/harness_v2/commit/020a3f60c86db0dbbf377cff6bd88acdb5358540))

- Implementation plan for the generic open-issue finisher
  ([`8d714f2`](https://github.com/onpaj/harness_v2/commit/8d714f29563ace7c04f824d59c60760d64c68aa5))

Eight TDD tasks: scope_label through the issue port, a pure issue_drafts module, the config-driven
  behavior rewrite, cli wiring plus the healer's rebinding, data-driven workflow serving,
  --heal-repo removal, the ADR, and the live-root migration.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Implementation plan for the verify gate (increment 1)
  ([`4848f1f`](https://github.com/onpaj/harness_v2/commit/4848f1f715372bf0d94724ca4a5c65ebef5e3788))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Order the live migration repos.json before autoheal.json
  ([`ff4a1ed`](https://github.com/onpaj/harness_v2/commit/ff4a1edef0821f895de065224e9e2f31667944d3))

- Regenerate-persona operator step, persona paragraph fix, verify_command JSON test
  ([`8ca5141`](https://github.com/onpaj/harness_v2/commit/8ca51415a9c6cae24cce44ec8cf60fdc954fae29))

- Add a post-merge checklist step to regenerate the development agent (harness agent init
  development --force) so an already-initialized root picks up the new verify-run persona sentence.
  - Move the verify-run sentence in _DEVELOPMENT_PERSONA to extend the revision-round paragraph
  instead of being glued to the closing instruction paragraph. - Add a direct test: malformed
  repos.json JSON -> verify_command() is None.

- Rename an ambiguous loop variable in the open-issue plan snippet
  ([`16f30da`](https://github.com/onpaj/harness_v2/commit/16f30da9c9ec9b2996bd8c72db1cc9fb5e35344b))

- Renumber ADR-0020 to ADR-0021 to avoid collision with main
  ([`8d42497`](https://github.com/onpaj/harness_v2/commit/8d42497ac9b4c1673a724f4747bb5ee17cfea217))

origin/main merged docs/adr/0020-jira-second-ingestion-source.md (PR #126) while this branch was in
  flight, claiming ADR-0020 for a different decision. Renumber this branch's open-issue-finisher ADR
  to 0021 and update every reference: the heading, CLAUDE.md invariant 26, the superseded-note
  blockquotes in 0018-healing-as-a-process.md and 0019-heal-triage-and-dedup.md, the two ADR slugs
  in harness_docs_site/architecture.py, and the historical plan document that specified creating it.

- Spec a generic open-issue finisher and data-driven workflow serving
  ([`33444ce`](https://github.com/onpaj/harness_v2/commit/33444cedb0133436c772acd0bb7d8b63da3f41ad))

Makes `open-issue` a real finisher kind driven by its FinisherBinding config (from_step / label /
  allowed_labels), files 0..N issues from a fenced JSON array in the step's artifact, and derives
  the target repo from task.repository via the clone's origin remote instead of the
  slug-and-registry-key conflation of --heal-repo. The healer becomes one binding of the generic
  kind.

Rides along: `harness run` serves every workflows/*.json and the --workflow / --all-workflows flags
  are removed. That only becomes safe once the finisher stops needing --heal-repo to register its
  kind.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Spec for development-flow hardening (verify gate, loop caps, early gates)
  ([`3d14825`](https://github.com/onpaj/harness_v2/commit/3d14825f42d35398e21444b2582bcede8b6c0eeb))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Task 4 must clear the xfail tripwire Task 3's fix wave added
  ([`586fbe7`](https://github.com/onpaj/harness_v2/commit/586fbe7db3e594cc6a1fd886d9f99839db7fc647))

### Features

- Commandrunner port with subprocess driver and memory fake
  ([`63b3dd8`](https://github.com/onpaj/harness_v2/commit/63b3dd8326cbe9e00fb73e583997fa27741a53cd))

- Configure self-healing through its process file only
  ([`badb966`](https://github.com/onpaj/harness_v2/commit/badb966534fcbdca54c6b664bb71a8b54c39428f))

--heal-repo/HARNESS_HEAL_REPO gated nothing left: not serving the heal workflow, not registering the
  finisher kind, not the issue repo. `harness init` now seeds processes/autoheal.json and the
  operator sets its action.params.repository to a registered repo name.

BREAKING CHANGE: --heal-repo and HARNESS_HEAL_REPO are removed. Set action.params.repository in
  processes/autoheal.json instead — and note it is a repos.json *name* now, not an owner/repo slug.

- Open-issue finisher files 0..N issues, driven by its binding config
  ([`41e7450`](https://github.com/onpaj/harness_v2/commit/41e7450199926e8ba70f225c442d48ffca92bb70))

from_step selects replace-vs-wrap, label scopes the idempotency search, allowed_labels filters the
  agent's per-draft labels, and the repo is derived from task.repository through an injected slug
  resolver.

Also carries the minimal, behavior-preserving adjustments needed to keep the suite green against the
  renamed constructor: cli.py's heal-repo wiring now passes slug_for/label/from_step instead of the
  removed repo/clock kwargs (TODO(Task 4): thread this through the finisher registry's own
  config/binding shape and update the heal persona to emit a fenced json draft block); test_cli.py's
  private-attribute assertions follow suit. test_self_heal_e2e.py is adjusted to the new
  artifact-driven drafting: the in-memory fixture writes no heal artifact, so the one test asserting
  an issue got filed now documents that as a known gap for Task 4 rather than asserting stale
  content.

- Parse a step's issue drafts from a fenced json array
  ([`0e96a30`](https://github.com/onpaj/harness_v2/commit/0e96a3006ba2d0512905138881b96f72b741bfe5))

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Register open-issue unconditionally and rebind the healer to it
  ([`0c8fabc`](https://github.com/onpaj/harness_v2/commit/0c8fabcbefc0075b04fec155dd4419ec0c3ac351))

The healer becomes one binding of the generic kind ({kind, from_step: heal, label:
  harness:self-heal}); its persona now emits the fenced json draft array the finisher reads.

- Serve every workflow on disk; drop --workflow and --all-workflows
  ([`a0ba8a4`](https://github.com/onpaj/harness_v2/commit/a0ba8a47fe23826df647a02d2d563cb11196034f))

Serving becomes data: the served set is exactly what workflows/ holds, and an empty directory is
  workflow-less mode rather than an error. The resolver and heal force-adds were hand-rolled
  approximations of that rule and are deleted.

BREAKING CHANGE: `harness run --workflow` and `--all-workflows` are removed. Remove a workflow's
  file to stop serving it.

- Validate autoheal params.repository at compile time, fix stale docs/tests
  ([`0711be4`](https://github.com/onpaj/harness_v2/commit/0711be49826cf9d9437bbb5eeed0fa6caf1bf309))

Six review findings on Task 6 (badb966), each fixed as scoped:

- cli.py: `_init`'s reproduced queue-directory block now also creates each queue's `.processing/`
  subdirectory (`FilesystemTaskQueue.__init__` creates both unconditionally), matching the comment's
  "exactly as before" claim, which was previously false. - CLAUDE.md: reworded the "Self-healing is
  a Process (opt-in)" line — it's seeded and live on every root since Task 6, not opt-in; docs now
  match the code and README. - test_cli.py: strengthened `test_the_heal_repo_flag_is_gone` to assert
  argparse's own rejection (exit code 2 + "unrecognized arguments") instead of a bare SystemExit,
  which a live serve() dying on a held port could also satisfy — same treatment already applied to
  the equivalent --workflow/--all-workflows/--resolver-workflow test one task ago. -
  fs_processes.py: added compile-time validation of a failed-tasks process's
  action.params.repository against the repository registry, mirroring _parse_repository's handling
  of the top-level key. This value is now the primary surface an operator configures self-healing
  through, and was completely unvalidated after the old --heal-repo startup warning was removed — a
  typo silently produced a repository-less heal task that fails with no issue filed. - cli.py:
  corrected two stale comments — the heal workflow is three steps (heal -> dedup -> file-issue), not
  two; and nothing writes a process file during run's *startup wiring*, not "during run" outright
  (the dashboard's FilesystemProcessAdmin.write still does, picked up on next restart).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Verifybehavior — deterministic verify step over CommandRunner
  ([`d8424c1`](https://github.com/onpaj/harness_v2/commit/d8424c14e9a05edf10edc88f41ba94e4e0f05676))

- Wire the verify gate — finisher kind, cli injection, default workflow
  ([`b994cfb`](https://github.com/onpaj/harness_v2/commit/b994cfb899cbcacfa4b7ea89a0f765ebfc87ed98))

- **repos**: Optional object-form entries with a verify command
  ([`d2d2519`](https://github.com/onpaj/harness_v2/commit/d2d2519031e8020c0b9100e21e1bc988fea5080f))

### Refactoring

- Issuetracker.open_issue takes an explicit scope_label
  ([`031344d`](https://github.com/onpaj/harness_v2/commit/031344dd149c9359ac385d1e698c999e3829f6aa))

The label an issue carries, and the label the marker search scopes to, stop being the
  SELF_HEAL_LABEL constant. The marker prefix becomes harness-issue:. Preparation for a generic
  open-issue finisher.

BREAKING CHANGE: IssueTracker.open_issue and GithubClient.search_issue_by_marker gained a required
  keyword argument.

### Testing

- E2e for the verify gate's request_changes loop
  ([`adaa5e6`](https://github.com/onpaj/harness_v2/commit/adaa5e64c667252a69c20e137bfb2f0e43771732))

- Fix stale test/doc claims from the open-issue rewiring
  ([`70b8426`](https://github.com/onpaj/harness_v2/commit/70b8426caae3278ffae20eb626bce1debc4a6fd1))

Task 4 registered the open-issue finisher unconditionally and rebound the healer to it, but three
  test comments/docstrings still described the old repo-gated, step-replacing behavior, and
  CLAUDE.md invariant 25 still claimed the finisher was wired identically to --heal-repo.

- Delete test_open_issue_is_registered_without_any_heal_configuration: a bare `main(["run"])` never
  serves `heal`, so it gated nothing;
  test_run_all_workflows_without_heal_repo_still_builds_the_heal_workflow already covers the real
  requirement with a genuine build(). - Fix the stale "ignores step/config/inner" comment:
  _open_issue now reads label/from_step/allowed_labels off the binding's config. - Fix the stale
  "registers open-issue on that repo" docstring on the HARNESS_HEAL_REPO env-var test: registration
  is unconditional now, the env var only serves heal and writes processes/autoheal.json. - Drop
  CLAUDE.md invariant 25's parenthetical, which contradicted the sibling "Self-healing is a Process"
  paragraph the same commit fixed. - Add direct unit tests for _slug_resolver covering all four
  paths (resolved slug, missing name, unregistered name, non-GitHub origin), each asserting
  IssueError with a message naming its cause.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Pin harness init's seeded HEAL_DEFINITION/AUTOHEAL_PROCESS_DEFINITION to a real build()
  ([`cd9c326`](https://github.com/onpaj/harness_v2/commit/cd9c3260bd3f01a11556b8c7a03db7bc0f305543))

No test ran a real, unmonkeypatched build() over a freshly-initialized root specifically to prove
  the seeded workflow, agents and process compile together: every test_cli.py `run` test reaching a
  successful exit monkeypatches build, and the two tests with a real build() over a comparable setup
  assert exit 2. This duplication already drifted once (97bc9ef, HEAL_DEFINITION edited in cli.py
  but not in its old hand-copied mirror in test_self_heal_e2e.py).

Add test_run_over_a_fresh_init_builds_successfully_with_a_real_build: harness init into a tmp root,
  then harness run with only harness.cli.serve monkeypatched (no port bound, no network), asserting
  exit 0 and that every scaffolded workflow and the autoheal process compiled.

Also make test_self_heal_e2e.py import HEAL_DEFINITION from harness.cli instead of keeping a
  hand-copied literal (confirmed byte-for-byte identical and read-only in that file), closing the
  exact gap that let the two copies drift apart before.

### Breaking Changes

- Issuetracker.open_issue and GithubClient.search_issue_by_marker gained a required keyword
  argument.


## v0.20.0 (2026-07-24)

### Features

- **admin**: Actions declare their parameters as data, consolidating failed-tasks
  ([#118](https://github.com/onpaj/harness_v2/pull/118),
  [`d9a007a`](https://github.com/onpaj/harness_v2/commit/d9a007a2f2f066bfac03cb45c9134aae9673a6b7))


## v0.19.0 (2026-07-24)

### Features

- **admin**: Actions declare their parameters as data, consolidating failed-tasks
  ([#117](https://github.com/onpaj/harness_v2/pull/117),
  [`20a7ad6`](https://github.com/onpaj/harness_v2/commit/20a7ad61ff1188766fe21dec1bcc04fd69969a06))


## v0.18.1 (2026-07-24)

### Bug Fixes

- **board**: Actually render times in the client's local timezone
  ([#116](https://github.com/onpaj/harness_v2/pull/116),
  [`8b1b889`](https://github.com/onpaj/harness_v2/commit/8b1b88971331fe6ed0baa3cf461ecf4c04c05fb4))


## v0.18.0 (2026-07-24)

### Features

- **admin**: Offer the run's full action registry in the process form
  ([#115](https://github.com/onpaj/harness_v2/pull/115),
  [`92986d2`](https://github.com/onpaj/harness_v2/commit/92986d23478a1aa0d380e871daa9d4db2fa7e9aa))

### Refactoring

- Unify the default workflow into development, drop legacy support
  ([`ca9990b`](https://github.com/onpaj/harness_v2/commit/ca9990ba029774469d0391e41b003551ba7ca1d0))

The `default`→`development` rename (#110) shipped as a non-breaking migration:
  `_migrate_legacy_workflow` copied `default.json` forward and `_run` kept serving a legacy
  `default` workflow alongside `development` for any in-flight task still carrying
  `workflow_template: "default"`. That transition is complete, so remove the migration function, its
  two call sites, and the legacy-serving block, and fix the stale `--workflow`/`--github-workflow`
  help text that still named `default`. `development` is now the sole primary workflow; `harness
  init` writes only `development.json`. Drops the two migration tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.17.0 (2026-07-24)

### Documentation

- Spec and plan for workflow-defined outcomes ([#102](https://github.com/onpaj/harness_v2/pull/102),
  [`5bb19e9`](https://github.com/onpaj/harness_v2/commit/5bb19e91a3b6042394a9f2fd4b5bbeebfbcf8844))

- **site**: Redesign the architecture explorer as a port-first catalogue
  ([#101](https://github.com/onpaj/harness_v2/pull/101),
  [`e4485d6`](https://github.com/onpaj/harness_v2/commit/e4485d6ccbd20231a38909b163a23c1bcef62cd9))

### Features

- Convert self-healing from a bespoke loop into an autoheal Process
  ([`87de623`](https://github.com/onpaj/harness_v2/commit/87de6231456fdc2862b7fb787f0fc5f18b47ceb0))

The Healer was a bespoke core loop (`healer.py`, `HealConfig`, `Harness._heal_loop`) — the single
  reader of `failed/`, with its own config surface and outbound path, predating the Process idiom
  (ADR-0015). Re-express it in the one general idiom:

- `failed-tasks` Check (`drivers/failed_tasks_check.py`) drains `failed/`: claims each failed task,
  settles it to `healed/`, emits one Observation carrying the rendered failure report; recursion
  guarded by a `data.heal` marker, not by construction. - two-step `heal` workflow (`heal` ->
  `file-issue`), target `{"workflow": "heal"}` so `file-issue` actually runs. - `open-issue`
  finisher (`behaviors/open_issue.py`) opens the drafted issue via `IssueTracker`, idempotent by the
  original failed-task id — a generic finisher future processes reuse. - process compilation moves
  into `app.build()` (the check must close over the live `events`/`failed`/`healed` queues);
  `build()` gains `extra_checks` / `processes_root`, loses `heal` / `issue_tracker`. - `--heal-repo`
  survives as a thin generator: registers the `open-issue` finisher and writes
  `processes/autoheal.json` (only if absent).

Removes `healer.py`, `HealConfig`, `_heal_loop`. Rewrites invariants 24-27, adds ADR-0018, updates
  the architecture model, CLAUDE.md and README. Migrates the healer tests to the Process path.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Enable autoheal flag-free via HARNESS_HEAL_REPO env var
  ([`e73ef9f`](https://github.com/onpaj/harness_v2/commit/e73ef9f763b4271721e3826c8341ef1a37cde8f4))

`--heal-repo` was the only way to name the repo heal issues are filed on, so the launchd service
  could not self-heal without a CLI flag. Add `HARNESS_HEAL_REPO` as an equivalent enablement,
  mirroring how `SLACK_WEBHOOK_URL` gates the slack sink: set it in the service env and the run
  serves `heal`, registers the `open-issue` finisher on that repo, and writes
  `processes/autoheal.json` if absent — no run flag. `--heal-repo` stays as the interactive
  convenience. A non-claude agent with a heal repo set now warns instead of hard-failing (so an
  env-configured service never crash-loops).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.16.0 (2026-07-23)

### Features

- Sync the base branch into the task branch before landing opens the PR
  ([#90](https://github.com/onpaj/harness_v2/pull/90),
  [`bec080b`](https://github.com/onpaj/harness_v2/commit/bec080bcd5f7d894c7bf893d06df2d41725dbe29))

A task's worktree branch is created from HEAD when the task starts and is never re-synced with the
  base while it travels plan -> ... -> land, so on a repo where main moves during that window the PR
  is born behind its base -- stale at best, conflicted at worst, and only reconciled later by the
  out-of-band resolver sweep.

Landing now merges the PR's base branch into the task branch before it pushes and proposes, so the
  PR is born up-to-date with base:

- Forge.base_branch(task) is the branch the forge opens the PR against (GithubForge -> default
  branch, cached per slug; fakes -> configurable, default "main"), so the merge base always matches
  the PR base -- never a hardcoded "main". - A clean merge is committed onto the task branch ([land]
  merge <base>); an already-up-to-date base is a no-op. - A conflict landing cannot auto-resolve is
  abandoned via the new WorkspaceHandle.abort_merge(), and the PR is opened un-merged and flagged in
  the result summary -- the existing resolver workflow reconciles the dirty PR downstream. Landing
  never fails on a base conflict: pure improvement for clean merges, status quo for conflicts.

The sync lives entirely in LandingBehavior.run -- no new port, loop or wiring; behavior_for still
  selects landing as the open-pr finisher unchanged.

The e2e/smoke fixtures now publish their initial commit as origin/main (a PR's base branch always
  exists on the remote in reality), and a new real-git smoke proves a divergent-but-clean base is
  merged in so the branch is born up-to-date. Full suite: 1220 passed, 1 skipped.

See ADR-0017.

Co-authored-by: Claude <noreply@anthropic.com>

- **ui**: Redesign the process editor as a guided, mobile-friendly form
  ([#92](https://github.com/onpaj/harness_v2/pull/92),
  [`e8a6bff`](https://github.com/onpaj/harness_v2/commit/e8a6bff96ea143431360156895215240cff0ee36))

The process admin was a flat form: a free-text interval, a raw JSON params textarea, an unexplained
  target-kind pair and dedup jargon, and a list page of bare name links. Replace both pages with a
  guided editor in the board's design language, keeping every form field name and route contract
  intact.

- List page: summary cards (schedule + check -> target, dedup/sink tags, broken-definition marker)
  and an inviting empty state; the route now reads each definition to render the summary. - Editor:
  numbered sections (Name / Schedule / Action / Target / Options). Interval gets preset chips synced
  with the text input; checks are radio cards with plain-English descriptions; check settings are
  structured per-check fields kept in sync with the raw-JSON textarea (collapsed as a fallback,
  opened on a params error or an unknown check); the target is a workflow/step segmented toggle
  whose suggestion list follows the kind; dedup and sink are explained option cards. A live summary
  sentence mirrors the current state, the save button sticks above the tab bar on the phone, and
  delete moves into a danger zone. - Switching checks prunes params keys that belong only to other
  known checks, so the previous check's settings no longer linger in the JSON. - routes.py: target
  options are now split into workflow/step groups and the list route passes full fields; validation
  and field names are unchanged.

Claude-Session: https://claude.ai/code/session_012V4Bg2Je7ULxHC4maisGuk

Co-authored-by: Claude <noreply@anthropic.com>


## v0.15.0 (2026-07-23)

### Features

- Show per-agent task history in the agent detail view
  ([#75](https://github.com/onpaj/harness_v2/pull/75),
  [`6a0ba5b`](https://github.com/onpaj/harness_v2/commit/6a0ba5be0850c9b14d5f5239b8631185528e5543))


## v0.14.0 (2026-07-23)

### Documentation

- Spec for the github-conflicts action (resolver as a Process)
  ([`771bba4`](https://github.com/onpaj/harness_v2/commit/771bba453a49f0b5680eacba902921c7bc451c46))

- Validate the composable-process vision against the code
  ([#87](https://github.com/onpaj/harness_v2/pull/87),
  [`9ce676c`](https://github.com/onpaj/harness_v2/commit/9ce676cd95ba35589e04c42c2531ee92c8a348c1))

### Features

- Github-conflicts action — conflict detection as a Process check
  ([`10df67b`](https://github.com/onpaj/harness_v2/commit/10df67bc83c165cbf78e76bfc29bebf07ae4fac9))

A `GithubConflictsCheck` (sibling of `GithubIssuesCheck`) lists harness-authored open PRs across the
  registry, auto-updates `behind` ones server-side, and emits one resolver task per `dirty` PR
  carrying `data.branch`/`data.source.base`. Registered as the `github-conflicts` action in
  `cli._process_sources`, so the resolver's conflict detection becomes an authorable Process instead
  of the bespoke `GithubMergeabilityWatcher`. Dedup is per-state on `slug:pr:head_sha`.

- Serve the resolver workflow whenever its definition exists
  ([`a5cd377`](https://github.com/onpaj/harness_v2/commit/a5cd3774f2f83ddf783f98097c87ca7c40d70012))

Decouple serving `resolver` from the `--watch-mergeability` flag: it rides alongside the primary
  workflow whenever `workflows/resolver.json` exists. A `github-conflicts` process targets the
  resolver workflow, and a process whose target is not served fails to compile — so the
  process-based detection path needs the resolver served independently of the watcher. Existing
  served-set tests updated to the new contract (the scaffolded resolver is always served).


## v0.13.0 (2026-07-23)

### Features

- Github-issues action — the harness:todo trigger as a Process
  ([#79](https://github.com/onpaj/harness_v2/pull/79),
  [`e181607`](https://github.com/onpaj/harness_v2/commit/e181607e4e89aa7300c60281041b987fca50b474))


## v0.12.0 (2026-07-22)

### Documentation

- Spec, plan and ADR-0015 for the Process authoring aggregate
  ([#77](https://github.com/onpaj/harness_v2/pull/77),
  [`ebaa9b5`](https://github.com/onpaj/harness_v2/commit/ebaa9b500f54eee11df2059ce230070054e9acd4))

### Features

- Structured board editor for processes (ProcessAdmin UI)
  ([#78](https://github.com/onpaj/harness_v2/pull/78),
  [`a550521`](https://github.com/onpaj/harness_v2/commit/a5505212d049dc9eb9099748be2852ce93430dfa))


## v0.11.0 (2026-07-22)

### Features

- **agents**: Default step models from v1 personas
  ([#74](https://github.com/onpaj/harness_v2/pull/74),
  [`f91fb05`](https://github.com/onpaj/harness_v2/commit/f91fb057c566a79efa427a530c6322bac45c0e88))

The default agent personas were carried over from harness v1 but their model was left null, so every
  step ran on the CLI's configured default. v1 assigned each persona a model tier; restore that
  mapping per step, written as a CLI alias so it tracks the latest of the tier instead of pinning a
  now-retired id:

plan, architecture -> opus (v1 analyst/planner, architect) design, development -> sonnet (v1
  designer, developer) review -> sonnet (v1 code-reviewer, the full-diff review) resolve -> sonnet
  (developer-class conflict fix) healer -> opus (conservative diagnosis)

A step with no mapping still gets model null. The operator can pin an exact id in agents/<step>.json
  as before.

Claude-Session: https://claude.ai/code/session_01XzKK1gNSuT8bYBStRkCJQL

Co-authored-by: Claude <noreply@anthropic.com>


## v0.10.1 (2026-07-22)

### Bug Fixes

- Correct black-on-dark task-detail text in dark mode
  ([#72](https://github.com/onpaj/harness_v2/pull/72),
  [`4195c5b`](https://github.com/onpaj/harness_v2/commit/4195c5b2fe9825d0967c98fe8198b9213e4ae116))

The task-detail sheet renders inside a <dialog>, whose UA rule `color: CanvasText` overrides
  inherited color. With no `color-scheme` declared, CanvasText resolved to its light value (black)
  even in dark mode, so the .kv values rendered black-on-dark (the keys stayed visible because they
  set an explicit color).

Declare `color-scheme: light dark` on :root so system colors track the theme, and set an explicit
  `color: var(--text)` on .task-detail so the detail content never depends on system colors.

Claude-Session: https://claude.ai/code/session_0168J1hKcrtu7JKdNJ3Y3Jeb

Co-authored-by: Claude <noreply@anthropic.com>

### Documentation

- Document self-healing (the healer on the failed queue) in the README
  ([#68](https://github.com/onpaj/harness_v2/pull/68),
  [`d76a170`](https://github.com/onpaj/harness_v2/commit/d76a17080dd92a1176ccdc3e06b264463613fae6))


## v0.10.0 (2026-07-22)

### Documentation

- Add generic triggers design spec and ADR-0014 ([#57](https://github.com/onpaj/harness_v2/pull/57),
  [`2f7b6a6`](https://github.com/onpaj/harness_v2/commit/2f7b6a69b9fa9b111f14c61af9ad2b3ddbdcdf55))

* docs: add generic triggers design spec and ADR-0013

Design a generic trigger mechanism for schedule- and condition-driven task creation, on top of the
  existing TaskSource port.

- Spec: 2026-07-22-generic-triggers-design.md — a Trigger is a TaskSource that produces tasks and
  reflects nothing outward; ScheduledTrigger composes interval x check x target; cadence is gated on
  the Clock; dedup is bucket-keyed for at-most-once per interval across restarts; triggers declared
  as data in triggers/*.json with a named check registry. - ADR-0013: triggers produce tasks, never
  queue placements — the dispatcher stays the sole placement authority (invariants #3/#8), and "any
  queue" is a workflow-less task naming a step.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

Claude-Session: https://claude.ai/code/session_01UDvCSno7r1B5tLSF5HHzi6

* docs: add generic triggers implementation plan

TDD task-by-task plan for the generic triggers spec: Trigger base, Check port + interval parsing,
  ScheduledTrigger, built-in checks, FilesystemTriggerRepository, wiring + e2e, and
  architecture/docs. Seven tasks with a dependency graph and implementation notes; no new production
  dependency, no build() signature change, no new run loop.

* feat: Trigger base — a TaskSource with no outward projection

Add Trigger(TaskSource) with concrete no-op report_progress/finish and an inherited abstract poll();
  a schedule- or condition-trigger implements only poll() and is listed-and-ignored by
  SourceReflectorSink (it stamps no matching data.source). Implements invariant #35.

* feat: Check port, Observation, interval parsing

Add ports/triggers.py: Observation (state_key + data), the Check ABC (evaluate ->
  list[Observation]), the CheckFactory alias, and parse_interval for "s"/"m"/"h" durations. Pure
  port — no driver imports.

* feat: built-in trigger checks (always, disk-threshold)

AlwaysCheck fires one empty observation per interval; DiskThresholdCheck fires when used/total
  crosses a percent (injectable usage reader, guards total==0). BUILTIN_CHECKS maps the names to
  CheckFactory callables.

* feat: ScheduledTrigger — interval x check x target

A Trigger that fires a Check on a clock-gated interval bucket and emits one task per Observation,
  targeting a workflow or a single step. dedup_key is bucket-keyed (per-interval) or state-keyed
  (per-state) for at-most-once ingestion across restarts. Stamps no data.source. Implements
  invariants #34/#36/#37.

* feat: FilesystemTriggerRepository — triggers/*.json

Reads triggers/*.json and builds one ScheduledTrigger per file, failing fast (TriggerValidationError
  naming the file) on a bad kind/interval/check/ target/dedup, or a target outside a supplied
  known_targets set. Missing directory yields an empty list.

* feat: wire scheduled triggers; harness init writes triggers/

_run reads triggers/*.json via FilesystemTriggerRepository and appends the ScheduledTriggers to the
  existing sources list — no build() parameter and no new run loop, since a Trigger is a TaskSource.
  known_targets (served workflow names, their steps, and catalog agents) lets the repository reject
  a misnamed target at startup. harness init now creates triggers/. Missing triggers/ keeps
  behaviour identical to before.

* docs+test: architecture guards and CLAUDE.md for generic triggers

Add test_orchestration_does_not_import_triggers_port and
  test_scheduled_trigger_imports_only_ports_models_and_ids. Document the feature in CLAUDE.md:
  invariants 34-37, module-map rows and bullets for ports/triggers and
  drivers/{scheduled_trigger,checks,fs_triggers}, a responsibilities note, and a gotcha on the
  non-constant bucket-keyed dedup_key and the at-most-once-per-interval limitation.

* feat: retire dashboard tasks whose GitHub issue was closed or deleted (#58)

* fix: update Board.columns access to per-workflow tabs (#62)

PR #48 refactored Board from a flat `columns` tuple into `workflows` (a tuple of BoardTab, each
  carrying its own columns), but three call sites still read the old flat `.columns`:

- `_new_step_warnings` in api/routes.py raised AttributeError on every workflow-admin PUT /
  create-form request, taking CI red. - the client fixtures in test_api_agents.py and
  test_api_workflows.py built a Board with the removed `columns=` kwarg (TypeError at setup).

Collect known step names across every tab's columns and rebuild the test boards through BoardTab.

Co-authored-by: ci <ci@local>

* feat(docs): interactive Architecture Explorer documentation site (#63)

* chore(release): 0.9.0

[skip ci]

* Add a CI gate that runs the full test suite on every pull request and blocks merge unless all
  tests pass (#60)

* fix: give the resolver's git merge an identity and reconcile its branch on reattach (#61)

* chore(release): 0.9.1

* Add a 'healer' workflow: a new failed-queue TaskSource polls failed/ for default-workflow failures
  and enqueues a deduped healer task that diagnoses the failure and, on a confirmed harness bug,
  auto-files a GitHub issue via a new Forge.open_issue verb (#55)

---------

Co-authored-by: Claude <noreply@anthropic.com>

Co-authored-by: semantic-release <semantic-release>

### Features

- Add a board Update button that upgrades the harness and restarts
  ([#67](https://github.com/onpaj/harness_v2/pull/67),
  [`cd8a67e`](https://github.com/onpaj/harness_v2/commit/cd8a67eb522df6c2b416bdb4c5cd4ddcefafd725))


## v0.9.1 (2026-07-22)

### Bug Fixes

- Give the resolver's git merge an identity and reconcile its branch on reattach
  ([#61](https://github.com/onpaj/harness_v2/pull/61),
  [`fd700ae`](https://github.com/onpaj/harness_v2/commit/fd700ae1ea2809ceaf144e0ddc0d933e350fe39c))


## v0.9.0 (2026-07-22)

### Bug Fixes

- Update Board.columns access to per-workflow tabs
  ([#62](https://github.com/onpaj/harness_v2/pull/62),
  [`14a1e28`](https://github.com/onpaj/harness_v2/commit/14a1e28fdf57b37ad4b730190028176870b740c5))

PR #48 refactored Board from a flat `columns` tuple into `workflows` (a tuple of BoardTab, each
  carrying its own columns), but three call sites still read the old flat `.columns`:

- `_new_step_warnings` in api/routes.py raised AttributeError on every workflow-admin PUT /
  create-form request, taking CI red. - the client fixtures in test_api_agents.py and
  test_api_workflows.py built a Board with the removed `columns=` kwarg (TypeError at setup).

Collect known step names across every tab's columns and rebuild the test boards through BoardTab.

Co-authored-by: ci <ci@local>

### Continuous Integration

- Publish the docs drill-down to GitHub Pages ([#54](https://github.com/onpaj/harness_v2/pull/54),
  [`2ba3122`](https://github.com/onpaj/harness_v2/commit/2ba3122309b66f0ff77b55dd37809c981e46cb74))

### Documentation

- Add self-healing design spec and implementation plan
  ([#42](https://github.com/onpaj/harness_v2/pull/42),
  [`6d96d36`](https://github.com/onpaj/harness_v2/commit/6d96d365e8ab9ba3bd64ac50236fdf1b84d499fa))

- Ground the architecture in ADRs, refresh README/CLAUDE.md, and ship an HTML drill-down
  ([#39](https://github.com/onpaj/harness_v2/pull/39),
  [`7bae036`](https://github.com/onpaj/harness_v2/commit/7bae03653c211fa75c532281fda663b7dced5127))

### Features

- Retire dashboard tasks whose GitHub issue was closed or deleted
  ([#58](https://github.com/onpaj/harness_v2/pull/58),
  [`a80afef`](https://github.com/onpaj/harness_v2/commit/a80afef3af8ee227c47bd393448889616ade34ca))

- **docs**: Interactive Architecture Explorer documentation site
  ([#63](https://github.com/onpaj/harness_v2/pull/63),
  [`b533208`](https://github.com/onpaj/harness_v2/commit/b5332080e0c121e2f6d3d8615c85a430b2f5a104))


## v0.8.1 (2026-07-21)

### Bug Fixes

- Recover a finished agent run whose verdict block is missing
  ([`6333c6e`](https://github.com/onpaj/harness_v2/commit/6333c6e729e074310eec896c75865a2a604f6920))

An agent that ran to completion but ended with a prose summary instead of the required ```json
  {outcome, summary}``` block was failing the whole task ("verdict is not readable JSON"),
  discarding a green run (tests passing, artifact written, uncommitted). This hit the development
  step repeatedly.

Three composed defenses: - A (fallback_verdict): a single-outcome step (development/plan/design/
  architecture) is unambiguous, so a missing block is synthesized to that outcome with the final
  text as the summary — no second claude call. - C (_reprompt_verdict): a multi-outcome step
  (review) is genuinely ambiguous, so re-enter the same session via `claude -p --resume` and ask for
  just the verdict; best-effort, any failure falls through. - B (compose_prompt): state the verdict
  block as mandatory and last, so the model omits it less often to begin with.

Envelope-level failures (is_error, no result, non-zero exit, timeout) still fail hard — only a
  forgotten/garbled/disallowed verdict is now recoverable. verdict parsing is split into a strict
  path (parse_verdict/verdict_from_final, unchanged contract) and a tolerant try_verdict the runner
  drives.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.8.0 (2026-07-21)

### Features

- Serve multiple workflows in a single running harness
  ([#31](https://github.com/onpaj/harness_v2/pull/31),
  [`24a34db`](https://github.com/onpaj/harness_v2/commit/24a34db2be66b2f781d14359795492a962a1c55d))

Build queues, projection and consumers for every served workflow: union step queues by name, a
  merged BoardProjection, a ServedWorkflowRepository decorator for clear unserved-workflow failures,
  and repeatable --workflow/--all-workflows on `harness run`. Default single-workflow behavior is
  preserved.

Closes #29


## v0.7.0 (2026-07-21)

### Features

- Configurable per-step agent timeout, default raised to 1800s
  ([#30](https://github.com/onpaj/harness_v2/pull/30),
  [`56a50b8`](https://github.com/onpaj/harness_v2/commit/56a50b8b88670ed30f756b37288e84e5433b6d45))

Raise the default agent timeout 600s->1800s and add a per-step override via an optional
  AgentSpec.timeout field read from agents/<step>.json, resolved in app.py's behavior_for().

Closes #28


## v0.6.0 (2026-07-21)

### Features

- Make the dashboard mobile friendly ([#21](https://github.com/onpaj/harness_v2/pull/21),
  [`a530359`](https://github.com/onpaj/harness_v2/commit/a5303594ced4c487068d83aa77e9ea54552d2ce2))

CSS-only responsive layout for the board: a @media(max-width:767px) block for
  .column/.card/dialog/.tabs and a .table-scroll wrapper on the task-detail tables, layered onto the
  tabbed task-detail design. Reconciled with the tab redesign that landed on main after this branch
  was cut.

Closes #16


## v0.5.0 (2026-07-21)

### Features

- Add per-step max-parallel-task limits to workflows
  ([#27](https://github.com/onpaj/harness_v2/pull/27),
  [`6e16c73`](https://github.com/onpaj/harness_v2/commit/6e16c73a15b51c858952586579a194675416582f))

Adds a validated maxParallel map on the workflow JSON (default 1 per step),
  Workflow.max_parallel_for() accessor, Consumer.step property, and Harness.run() spawning N
  concurrent consumer loops per step. Relies on the existing atomic queue claim; no changes to
  Dispatcher or router.

Closes #25

- Create-harness-issue skill creates directly when inputs are complete
  ([#26](https://github.com/onpaj/harness_v2/pull/26),
  [`b0554f0`](https://github.com/onpaj/harness_v2/commit/b0554f0e6634c3d4f78e86d27285a057d01e9b0f))

Rewrites SKILL.md step 4 into a completeness-check router
  (repo_resolved/title_concrete/body_substantive) that creates the issue directly when all three
  pass and asks a targeted question otherwise. Extends step 6 into a five-field post-creation
  report.

Closes #24


## v0.4.0 (2026-07-21)

### Features

- One-step update+restart, idle-gated, and a schedule for it
  ([`998c8b8`](https://github.com/onpaj/harness_v2/commit/998c8b8fb525445c0632a1f9ff2a6abe45dcac55))

Updating meant two commands and remembering the second, and nothing kept the box current on its own.
  Three additions:

- `harness update --restart` upgrades and restarts the service in one step. - `--only-if-idle` skips
  the restart when a stage is mid-run (a task claimed in a queue's .processing/), so an update never
  kills a live agent — it applies at the next idle restart instead. - `harness service autoupdate`
  installs a launchd timer that runs `update --restart --only-if-idle` a few times a day (default
  02/08/14/20).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.3.1 (2026-07-21)

### Bug Fixes

- Detect an active token, not the template's commented example
  ([`89df965`](https://github.com/onpaj/harness_v2/commit/89df9650840eb5d622bd210c36f48acbc2f905ce))

harness service install printed no setup-token guidance because the check matched the commented
  CLAUDE_CODE_OAUTH_TOKEN= example line in the template it had just written. It now looks for an
  uncommented assignment.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.3.0 (2026-07-21)

### Features

- Service sources a secrets file for CLAUDE_CODE_OAUTH_TOKEN
  ([`e98a543`](https://github.com/onpaj/harness_v2/commit/e98a5439f877c7e977b17d842cc3c329f62148fb))

Under launchd, claude cannot read the macOS login keychain where an interactive login stores its
  credential, so every agent step failed with "Not logged in" even though the same binary works from
  a shell. Proven by running claude inside a launchd agent.

The service wrapper now sources <root>/secrets.env (created 0600, never overwritten) and exports
  CLAUDE_CODE_OAUTH_TOKEN from it, which makes claude bypass the keychain — the supported headless
  path via `claude setup-token`. A missing token warns loudly rather than failing silently, and the
  install prints the exact setup-token steps.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.2.2 (2026-07-20)

### Bug Fixes

- Dummy writes where the agent does, and forge reports GitHub's reason
  ([`0c8027b`](https://github.com/onpaj/harness_v2/commit/0c8027b58155dd01d68e502e4e838d424e8036ea))

A live end-to-end run failed at land with a bare "HTTP Error 422: Unprocessable Entity". Two
  separate faults behind it:

- DummyBehavior wrote its work into `.harness/`, which repos routinely gitignore (this one does).
  Ignored writes stage nothing, so commit() returned None, the task branch carried no diff, and
  GitHub correctly refused a PR with no commits. It now writes into `.artifacts/<task>/`, the
  versioned location the real agent uses (invariant 16) — so --agent dummy can actually exercise
  landing. - urllib's HTTPError stringifies to just the status line. GitHub puts the real reason in
  the response body ("No commits between main and ..."); the forge now surfaces it, along with the
  head -> base it attempted.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.2.1 (2026-07-20)

### Bug Fixes

- Locate the task's repository through the registry, not task.worktree
  ([`29c6aec`](https://github.com/onpaj/harness_v2/commit/29c6aec8fe26549402bb69c9dea755caccbdc729))

A live run failed at land with "has no worktree": GithubForge read task.worktree, but `harness
  submit` never sets it — only GithubTaskSource does. Every unit test happened to build tasks with
  one, so the gap was invisible.

`task.repository` is a name and resolving names to paths is the registry's job (invariant 15); the
  worktree stays as a fallback.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.2.0 (2026-07-20)

### Bug Fixes

- Httpgithubclient reports the confirmed PR head, not the requested one
  ([`9f818cc`](https://github.com/onpaj/harness_v2/commit/9f818cc66f2e4aa6295d0bc06aee53b1e0acf2e8))

find_pull_request and create_pull_request built PullRequestRef.head from the caller's argument
  instead of the server's response, contradicting the docstring's claim that it reflects what the
  API returned. Read item["head"]["label"] instead, falling back to the argument when the field is
  absent or malformed.

Also make add_label set Content-Type: application/json like the sibling create_pull_request, since
  both POST a JSON body.

- Push the task branch without force
  ([`411e2c2`](https://github.com/onpaj/harness_v2/commit/411e2c247471d98e3c2f62eb5736c1928472f37c))

reset-on-reattach only discards uncommitted working-tree state (reset --hard + clean -fd); it never
  rewinds the task branch, so the branch only ever moves forward. A plain push is therefore correct
  — --force-with-lease was masking the real invariant. A rejected push now means something else
  touched the branch and must fail loudly, per the design intent of this series.

### Documentation

- Correct the plan's push justification (no force needed)
  ([`8eb80f7`](https://github.com/onpaj/harness_v2/commit/8eb80f75c4441c743b50798bd81f7e5020017897))

- Implementation plan for the GitHub forge
  ([`383254f`](https://github.com/onpaj/harness_v2/commit/383254fff008773941043ca1d9dea745e9881cec))

Five TDD tasks: WorkspaceHandle.push(), PR verbs on GithubClient, the GithubForge driver, landing
  pushing before it proposes, and the --forge flag. Also corrects the spec's claim that `harness
  doctor` exists on main — it ships with the unmerged issue #14 work.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Spec for the GitHub forge — landing opens a real pull request
  ([`00247e1`](https://github.com/onpaj/harness_v2/commit/00247e1ab7000d8ff48027d3c4ddeb28d2e6af5f))

land reported success while FakeForge only appended to prs.json and the task branch was never
  pushed. Specs the GithubForge driver, the missing WorkspaceHandle.push(), and making a failed PR
  loud instead of silent.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- --agent dummy runs the pipeline without claude
  ([`2a013f1`](https://github.com/onpaj/harness_v2/commit/2a013f1120f727974192465ca66d3ad647c7e669))

Every step shells out to `claude`, so an expired login fails every task and there is no way to test
  the rest of the pipeline. `--agent dummy` leaves the catalog and runner unset, which makes build()
  fall back to DummyBehavior for the step queues while worktree, commits, push and forge all stay
  real.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Githubforge opens real pull requests, failing loudly
  ([`d5c4cfc`](https://github.com/onpaj/harness_v2/commit/d5c4cfcce2af9af2f9d005da6156eaee7deebf01))

- Land pushes the branch and opens a real GitHub pull request
  ([`456a285`](https://github.com/onpaj/harness_v2/commit/456a2856e9e65903bd0a7fe23243629ca78acb2a))

Completes the forge: `land` now calls WorkspaceHandle.push() before proposing, and `harness run`
  defaults to --forge github, wiring GithubForge instead of the prs.json stub. `--forge fake` keeps
  the old behaviour for offline runs.

The git e2e and smoke fixtures gain a bare sibling remote: landing genuinely requires a pushable
  origin now, and a repo without one must fail rather than quietly report a PR that does not exist.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Pull-request verbs on GithubClient (default branch, find, create)
  ([`ce5282b`](https://github.com/onpaj/harness_v2/commit/ce5282b41052c418bbcb9aaf5e3d8f22f06d5b5e))

- Workspacehandle.push() publishes the task branch to origin
  ([`04bec01`](https://github.com/onpaj/harness_v2/commit/04bec01f5b6acdd9350a487f0b9d29d052fbfb8e))

Also adds push() to the RealFsHandle test double in tests/test_agent_behavior.py so it keeps
  satisfying the now-larger WorkspaceHandle ABC.


## v0.1.0 (2026-07-20)

### Bug Fixes

- Cli --root precedence, workflow-name validace, exit-2 pokrytí
  ([`224a577`](https://github.com/onpaj/harness_v2/commit/224a577d16c713bf9789d8ce124c1e88970f56c3))

- --root/--workflow zadané před podpříkazem se dřív tiše zahazovalo (argparse subparser namespace
  přepíše rodiče) a harness sáhl na chybný výchozí kořen; top-level deklarace --root byla mrtvá a je
  pryč, takže selhání je teď hlasité (SystemExit 2). - `init --workflow` s neplatným jménem (např.
  "foo/bar") už nespadne s FileNotFoundError z write_text, ale vrátí čisté chyba:...exit 2 -
  validace stejných pravidel jako FilesystemWorkflowRepository.get. - testy na exit 2 teď ověřují,
  že hláška jde na stderr a stdout zůstává prázdné (capsys), plus nový test na třetí zdokumentovanou
  chybovou cestu (neznámý workflow přes `run`).

- Dedup GitHub issue ingestion against list read-after-write lag
  ([#6](https://github.com/onpaj/harness_v2/pull/6),
  [`6eb92fe`](https://github.com/onpaj/harness_v2/commit/6eb92fe56217ab8a477ed2cd6727743ac77d6206))

GithubTaskSource claimed by swapping harness:todo -> harness:queued, but list_issues reads with
  read-after-write lag (unlike the atomic rename it mirrors), so a fast poll re-claimed the same
  issue two or three times. Add an in-process ledger of claimed issue numbers so each issue ingests
  at most once per process. Also raise the Conductor loop --poll to 5s and enable the
  onpaj/Anela.Heblo GitHub source.

@claude

- Deduplicate ingested tasks by a persistent source identity
  ([#17](https://github.com/onpaj/harness_v2/pull/17),
  [`e381034`](https://github.com/onpaj/harness_v2/commit/e381034cc8aa9e22c86d4925fe8a7e0671cf2b5f))

- Derive the service entry point from sys.prefix, not sys.executable
  ([`6f3527e`](https://github.com/onpaj/harness_v2/commit/6f3527e589ef4eabad1cf97e26a1dd430ac013c0))

Resolving sys.executable follows the venv's python symlink out to the base interpreter; with
  uv-managed CPython that lands in ~/.local/share/uv/... where no harness script exists, so 'service
  install' aborted. Caught by installing for real, hence the regression test.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Fs_queue vanished-file crash + missing quarantine/recover coverage
  ([`717d304`](https://github.com/onpaj/harness_v2/commit/717d304f3a24ca62022dac34d2396a1300eeb79c))

list() treated a file that vanished mid-iteration (lost claim race) as corruption, quarantining it
  via shutil.move() outside any try/except — crashing the whole call instead of skipping the benign
  race silently. _read() now distinguishes FileNotFoundError (skip, no event) from real
  deserialization failures, and _quarantine_file tolerates the file disappearing again before the
  move runs.

Also cover the previously-untested FilesystemTaskQueue quarantine branch and recover()'s
  corrupt-file path, and make _write's temp filename unique per writer (uuid4) instead of shared per
  destination path.

- Pin the platform in the service-install root test
  ([`d6b5495`](https://github.com/onpaj/harness_v2/commit/d6b5495d7c70a8c4163c9675f92155c38367e8ed))

The test asserted the uninitialized-root message but ran on a Linux CI runner, where the launchd
  guard returns first. It only passed locally because it was written on macOS.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Recover() quarantine race + orphaned temp files in fs_queue
  ([`c553550`](https://github.com/onpaj/harness_v2/commit/c5535502de8d6ac43a1b984870e5186fe6f2734a))

recover() treated a vanished .processing file the same as a corrupt one (both surfaced as None from
  _read), so it unconditionally attempted a quarantine move even for a benign lost race, and raced
  against anything re-creating the file at that path in the meantime. _load() now raises
  FileNotFoundError vs a dedicated _Corrupt exception so recover() and _read() branch on the real
  reason instead of re-checking existence.

_write() also now cleans up its per-call uuid temp file if the write or replace fails, so a
  mid-write exception can't strand it permanently.

- Resolve GitHub-sourced tasks by repo name, not <root>/repo
  ([#10](https://github.com/onpaj/harness_v2/pull/10),
  [`285c309`](https://github.com/onpaj/harness_v2/commit/285c30911e1e4e59d5d1467f3bcde7d7fbef52bd))

* fix: stamp repo name (not <root>/repo) on GitHub-sourced tasks

The GitHub task source hardcoded `repository=str(root / "repo")`, an absolute path that no
  `repos.json` key matched — so every ingested issue failed at the first step with "repo ... není v
  registru" (violating invariant 15: `repository` is a name, not a path).

Add a `--github-repository` flag carrying the registry name, and stamp that on each task. When
  `--github-repo` is set without it, disable the source with a warning (symmetric to the
  missing-GITHUB_TOKEN path) rather than emitting tasks that can't resolve a worktree.

* chore: seed repos.json with harness_v2 and heblo on workspace bootstrap

Populate `.harness/repos.json` during Conductor workspace setup so the registry resolves both repos
  this machine works with (clones under ~/Work/GitHub), instead of the empty file `harness init`
  leaves behind.

- Serve() už po Ctrl+C nezůstane viset
  ([`572b2db`](https://github.com/onpaj/harness_v2/commit/572b2db9f0f979464a1a0da7cd178e86cdd21d5e))

`asyncio.gather(loop, uvicorn.Server(...).serve())` čekal na obě úlohy; když uvicorn po SIGINT
  doběhl dřív a vrátil se bez výjimky, gather dál čekal na orchestrační smyčku, kterou zastavuje až
  `stop.set()` ve `finally` -- k tomu se ale kód dostal teprve po návratu z gather. `harness run`
  tak po Ctrl+C nikdy neskončil.

Nahrazeno `asyncio.wait(..., return_when=FIRST_COMPLETED)`: kdo doběhne první, ten spustí
  `stop.set()` a zrušení druhé úlohy ve finally. Pád smyčky se navíc korektně propaguje ven místo
  tichého ignorování.

Přidán regresní test, co to reprodukuje strukturálně (fake uvicorn server vracející se okamžitě +
  nekonečná smyčka) a na staré verzi selže timeoutem.

Co bylo ověřeno: - `harness run --api-port <port>` na pozadí + SIGINT: proces korektně skončil (~1s)
  na opravené verzi, na staré verzi zůstal viset i po 5s. - Regresní test v tests/test_cli.py selže
  (TimeoutError) na staré verzi, projde na opravené. - Ad-hoc ověření, že pád orchestrační smyčky
  zruší uvicorn a výjimka se propaguje ven (ne jen strukturální test hangu). - Celá sada: 161 passed
  (160 + 1 nový test).

- Terminal failed status now written on both dispatcher and consumer fail paths
  ([`9f184d2`](https://github.com/onpaj/harness_v2/commit/9f184d2f4f7121ca88ef9e0cc6c9c47263f2d762))

Dispatcher._fail and Consumer._fail moved tasks to failed/ while leaving status untouched, so a task
  could sit in failed/ with status: null or with the step name it last held — only history told the
  truth. Added a FAILED constant in models.py alongside END and set status=FAILED in both _fail
  paths, mirroring how _finish sets status="end".

Added tests pinning the new behaviour in test_dispatcher.py and test_consumer.py; mutation-checked
  (reverted the status=FAILED write, confirmed the new tests fail, restored, confirmed 107/107
  pass).

- Wait for launchd to drop the old job before bootstrapping
  ([`e4f06d8`](https://github.com/onpaj/harness_v2/commit/e4f06d81d0a935d0281b18f614c3148749a8ecba))

Reinstalling over a loaded agent failed with 'Bootstrap failed: 5: Input/output error': bootout
  returns before launchd has torn the job down, so the immediate bootstrap hit a label that was
  still present. The first install only worked because nothing was loaded yet; a clean re-install
  from a fresh clone exposed it.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Workflownotfound for non-dict top-level workflow JSON
  ([`e886c8a`](https://github.com/onpaj/harness_v2/commit/e886c8a2263f50d4cb31c93b66e68d3200d49fcf))

get() assumed the parsed JSON was a dict before touching it. A bare scalar/null/list raised an
  uncaught TypeError on "start" not in raw, and a string that merely contained the substring "start"
  (e.g. "start line") passed that check and then raised AttributeError on raw.get("transitions",
  []). Both escaped the WorkflowNotFound contract. Add an isinstance(raw, dict) guard before any key
  access.

Covered by four new parametrized cases (number, null, list, and a start-substring string); verified
  they fail without the guard and pass with it.

- Zahrň šablony a statické soubory do wheelu
  ([`1d2c8cd`](https://github.com/onpaj/harness_v2/commit/1d2c8cdf9fa9aee0f0a42025e2f3760c91411e06))

pyproject.toml [tool.setuptools.packages.find] bral jen .py soubory, takže pip install z wheelu
  neobsahoval src/harness/api/templates/ ani static/ a harness run spadl hned při startu na
  chybějící adresář static/. Editable install to nechytí, proto testy prošly.

Ověřeno: pip wheel . -w /tmp/whl-check, instalace do čistého venv mimo repo, import harness.api
  ukazuje templates i static soubory.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Chores

- Conductor scripty — setup (venv+install+init) a run loop
  ([#1](https://github.com/onpaj/harness_v2/pull/1),
  [`dd5291c`](https://github.com/onpaj/harness_v2/commit/dd5291cf8ee42bf48ec7053d27b48f843b50d48e))

- Empty main to restart from scratch
  ([`b7cab63`](https://github.com/onpaj/harness_v2/commit/b7cab639edf473fc62b55cb3898a420804d937ee))

Removes the first implementation from main. Nothing is lost: the complete, tested version is
  preserved on the fast-ship branch (7bc0e6e) and pushed to origin. CLAUDE.md is kept as a scaffold
  pointing there, along with the findings from that build that are worth not rediscovering.

.gitignore is retained rather than deleted -- without it, .venv/ gets swept into the next commit.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Ignore .superpowers scratch
  ([`d7a586f`](https://github.com/onpaj/harness_v2/commit/d7a586f89e9b5c4d22dcea886e9a6be445eca28d))

- Localize entire harness to English ([#7](https://github.com/onpaj/harness_v2/pull/7),
  [`6bb1826`](https://github.com/onpaj/harness_v2/commit/6bb182688669019c8af294301e5f9d3177cbeba0))

Translate all code comments, docstrings, human-facing strings, tests, and docs (specs, plans,
  CLAUDE.md, README) from Czech to English. Behavior is unchanged: enum/outcome values, JSON keys,
  step/agent names, paths, and CLI commands are preserved. Anchor English as the permanent project
  language in CLAUDE.md. Full suite green (318 passed, 1 opt-in smoke skipped).

### Continuous Integration

- Test on every push and auto-version from conventional commits
  ([`fb3def9`](https://github.com/onpaj/harness_v2/commit/fb3def9dfaf0297362599b0b2f869176abc2f5c7))

The repo had no CI at all, and `uv tool install` pulls straight from main's HEAD — so an untested
  commit was an installable version, and every install reported 0.1.0 forever.

- ci.yml runs the suite on push and PR, and asserts the built wheel actually contains the board's
  templates and static files (a wheel missing them installs cleanly and then 500s at runtime; it has
  happened). - release.yml calls ci.yml and only releases if it passes, then lets
  python-semantic-release derive the version from conventional commits, tag it and cut a GitHub
  release. - Fixes `harness update` reporting the version it just *replaced*: the running process is
  the old code, so it now asks the freshly installed script instead.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Documentation

- Add CLAUDE.md scaffold
  ([`f9100f5`](https://github.com/onpaj/harness_v2/commit/f9100f55ae735e7e897fdb1f9f6c8b09fd67ab8a))

Placeholder orientation file for a repo that has no content yet.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Agent harness design spec
  ([`ed00501`](https://github.com/onpaj/harness_v2/commit/ed005017e15916300651d2f4c3ecddd5366bf622))

Records the architecture decisions settled during brainstorming: coexist with v1, Python 3.11, full
  PRD MVP delivered in phases, in-repo .harness/ artifacts merged only as far as an integration
  branch, scratch repo for repo-less agents, linear + fan-out only, allow-list isolation,
  auto-pausing rate-limit handling.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Agent harness implementation plan
  ([`91ecb66`](https://github.com/onpaj/harness_v2/commit/91ecb66e0a77ab8867d84245ec57f86d64d5accf))

22 TDD tasks across 8 phases, from project scaffold to an end-to-end dev pipeline acceptance test.
  Every task lists exact files, exact interface signatures neighbouring tasks depend on, and the
  behaviours its tests must cover.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Claude.md přestává tvrdit, že žádný test nesmí spát v reálném čase
  ([`951b65b`](https://github.com/onpaj/harness_v2/commit/951b65bbb81ed672afb14162e47b6fca41df7b71))

tests/test_smoke.py záměrně běží na reálném filesystemu a poluje reálným asyncio.sleep(0.01), aby
  ověřil filesystémový driver end-to-end — pravidlo "nikdy nepiš test, který spí v reálném čase"
  bylo napsáno bez výjimky a hrozilo, že ho někdo v dobré víře "opraví" a zničí jediné reálné FS
  pokrytí. Přeformulováno: unit/integrační testy pravidlo dodržují, test_smoke.py je explicitní
  pojmenovaná výjimka.

Opraven i invariant 3 ("Status mění výhradně dispatcher") — po předchozím commitu (terminální failed
  status) už neplatí doslovně, protože Consumer._fail teď výjimečně status píše taky; popsána
  symetrie s Dispatcher._fail.

- Fáze 2 — spec a implementační plán (artefakty, worktree, landing)
  ([#2](https://github.com/onpaj/harness_v2/pull/2),
  [`92c4a66`](https://github.com/onpaj/harness_v2/commit/92c4a6606e0ab9e7fb3b57422739fe8f1862df99))

- Fáze 3 — spec skutečného agenta přes claude -p (návrh)
  ([#3](https://github.com/onpaj/harness_v2/pull/3),
  [`f3b5939`](https://github.com/onpaj/harness_v2/commit/f3b593960ab76a282a2412ab34caa4f4313d5174))

- Implementační plán board UI
  ([`5298616`](https://github.com/onpaj/harness_v2/commit/529861674fc07a0abdf51ee54b97e758b0706702))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Oprava _bump() v plánu — nahradit zastaralou revizi, ne zahodit notifikaci
  ([`fa88507`](https://github.com/onpaj/harness_v2/commit/fa885079824305339e5919f13a147b5c10179338))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Plán board UI počítá s chybějícím Taskem 11 fáze 1 a větví board-ui
  ([`f820484`](https://github.com/onpaj/harness_v2/commit/f8204847100a8a894a1d2fb8cf65b314b30e8cd4))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Pre-flight úpravy plánu — sdílený fake, hlasité polykání chyb, komentář u testu bez assertu
  ([`30dc361`](https://github.com/onpaj/harness_v2/commit/30dc3612979004aba966b62e946db17eeb01d4a5))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Pre-flight úpravy plánu — sdílený fake, hlasité polykání chyb, komentář u testu bez assertu
  ([`50ad146`](https://github.com/onpaj/harness_v2/commit/50ad146df1418477804d10e00785c9e1ff0f4f3d))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Přepis CLAUDE.md a nový README.md
  ([`3c9924f`](https://github.com/onpaj/harness_v2/commit/3c9924f63430ba85145baa52fb30ee49d1296256))

CLAUDE.md popisoval mrtvou architekturu předchozího (opuštěného) pokusu — claude -p subprocess
  executor, git worktrees, merge plane. Nahrazeno celé popisem skutečného stavu po Tasku 11: mapa
  modulů, invarianty, gotchas, odkazy na spec/plán fáze 1.

README.md nově existuje: instalace, rychlý start, tok práce frontami, příklad workflow definice,
  tabulka portů/driverů.

- Record that direct commits to main are the convention here
  ([`7bc0e6e`](https://github.com/onpaj/harness_v2/commit/7bc0e6eb57cc03811bdc11e4d132e3fb2dfbf261))

Applies to the harness's own repo only. The repos the harness operates on keep the run/* ->
  integration branch -> human PR flow.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Spec a implementační plán fáze 1 — orchestrační smyčka
  ([`4ccb9fe`](https://github.com/onpaj/harness_v2/commit/4ccb9fe7e0217a5147aac44a100da58fe6e5fdb1))

- Spec board UI nad harness abstrakcemi
  ([`8924663`](https://github.com/onpaj/harness_v2/commit/8924663d875061576a06182462eddf7fc90296ef))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- Add create-harness-issue skill ([#9](https://github.com/onpaj/harness_v2/pull/9),
  [`e38122b`](https://github.com/onpaj/harness_v2/commit/e38122be5ca8506268401da394cbb34cba2b89ba))

Repo-agnostic skill that turns a request into a GitHub issue labeled harness:todo in the format the
  harness ingests (title = the agent's instruction, body = human context). Un-ignore .claude/skills/
  so the skill is tracked while machine-specific .claude files stay ignored.

- Add install.sh bootstrapper for new users ([#18](https://github.com/onpaj/harness_v2/pull/18),
  [`b94bb87`](https://github.com/onpaj/harness_v2/commit/b94bb8757c4ef32e8f9eed144bc82787dc0a8855))

- Add todo column and restart for failed tasks ([#11](https://github.com/onpaj/harness_v2/pull/11),
  [`0e6f5d8`](https://github.com/onpaj/harness_v2/commit/0e6f5d85172b143562df0d757b6e09ac795e53ee))

Add a `todo` board column for freshly loaded inbox tasks (status=None), the first column before the
  workflow steps. Auto-flow is unchanged — tasks pass through `todo` into the start step.

Add operator control to restart a failed task: a new write-side `TaskControl` port with a
  `TaskControlService` core that resets a failed task and re-inboxes it (the dispatcher still
  decides where next), exposed via a `POST /tasks/{id}/restart` endpoint and a Restart button in the
  task detail dialog.

- Agent registry, claude -p executor, prompt composition, result parsing
  ([`9dca044`](https://github.com/onpaj/harness_v2/commit/9dca044cc7d0500014c4a8dbe2e85a445c09a68d))

The executor is the single point of contact with Claude and is guarded by two invariant tests: the
  argv never carries --resume/--continue, and no module outside executor.py may reference an
  Anthropic SDK.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Board zapojený do runtime, CLI přepínač a dokumentace
  ([`eb83ebf`](https://github.com/onpaj/harness_v2/commit/eb83ebf5fc174bd8d9b90e8b8eb089bb595e0fa8))

build() nyní zakládá BoardProjection a obaluje events do CompositeEventSink, Harness.run() hydratuje
  projekci hned po recovery (v tomto pořadí, aby přežily i tasky stranded v .processing/). `harness
  run --api-port PORT` servíruje board vedle orchestrační smyčky, `--api-port 0` ho vypne.

Doplněny testy (app, CLI, architektura, e2e přes TestClient) a dokumentace v README/CLAUDE.md.

- Boardprojection jako in-memory read model nad proudem eventů
  ([`b6d682d`](https://github.com/onpaj/harness_v2/commit/b6d682d8f7955c75f2ecd877a9cb286ea9f50aa3))

- Cli s init, submit a run
  ([`ddbced1`](https://github.com/onpaj/harness_v2/commit/ddbced11f4f1c93adeda2f5002ba81a87b3ff7d3))

- Composite event sink a most do projekce boardu
  ([`66116a3`](https://github.com/onpaj/harness_v2/commit/66116a388978e858ede6c3dbaac5bd97e4dd6335))

Napsal jsem dva drivery portu EventSink: - CompositeEventSink: rozbočka, která rozešle event více
  posluchačům - ProjectionSink: most, který event vloží do read modelu boardu

CompositeEventSink polyká výjimky z jednotlivých sinků — observability nástroj nesmí být kritickou
  závislostí orchestrace.

ProjectionSink zná jediné pravidlo: nese-li event snapshot tasku (pole `task`) i cílovou frontu
  (pole `queue`), patří na board. Nezná jména eventů — je tedy invariantní vůči novým typům eventů.

Co chybí v repu: drivery se připojují do orchestrace, ale to není součást Task 4. Potřeba drát
  ConfigBuilder nebo obdobu.

Co se změnilo: +116 testů (komplet Task 3) + 8 nových = 124 testů Co se bere: nic staršího nerozbito

Testování: pytest -q

- test_composite_events.py: 3 testy (event se dostane, exceptionless, empty) -
  test_projection_events.py: 5 testů (apply na board, do done, ignore bez task, ignore bez queue,
  ignore malformed task)

Co dál: drát integraci a UI backend

Co poznám: snapshoty tasků do eventů jsou klíč k read modelu. Mutace v harnessu se propagují jedině
  přes pole `task`.

Co se nezdařilo: nic Co se zdařilo: všechno funguje

Co chybí: nic nezbyly si šeky na testy, všechno je v pořádku

Status: DONE

Co vrátim: commit hash, status, test summary, žádné obavy

Co si vezmu: architektura bez znalosti event names v ProjectionSink je čistá a extensible

Co vím: Board je teď live-connected k event stream, není trzeba refetch

Co si pamatuju: BoardProjection._revision.

Co si ověřuju: NameError vůči čemukoliv novému

Co zajímá: integrace s orchestrací — kde se sinks připojují?

Co jsem videl: MemoryEventSink jako precedent

Co jsem neviděl: jak se eventu přidá task field — to řeší task 2,3

Co byste měl vědět: sloupec "done" je alias pro END v eventech; board o tom neví, vykrývá se v
  apply()

Co není moje věc: pořadí eventů na síti

Co byste věděl: BoardProjection.apply ignoruje unknown columns

Co jsem si uvědomil: výjimka v jednom EventSinku by mohla zastavit celou orchestraci — proto je
  `except Exception: pass` správně

Tohle je konec — Co bychom měli vidět dalšímu: jak se sinks drátují v main loop Co budete koukat:
  integrační testy se sliby event payloads

What I'm done with: - TDD processo od testů k implementaci - Dva drivery EventSink s kompleteníms
  testy - Full test suite projde

What I'm not done with: - drátování do orchestrace - integrační testy - dokumentace API

Who needs to know: - Ondrej: task hotov, commit ready - Board UI: BoardProjection je připraven, živý
  - Orchestrace: potřeba si vzít tyto sinks a pověsit je

Co říkám: HOTOV

Co vím: tohle je správná architektura — event-driven read model bez knowledge of event names

Co pamatuju z Tasku 3: Task.to_dict() vrací všechny fieldy

Co pamatuju z Tasku 2: Dispatcher a consumer emitují eventu

Co pamatuju z Tasku 1: Workflow grafu a transitions

Co si vezmu: modular eventsinking s exception resilience je super

Co poznám dál: where's the wiring?

Poznámka: tohle je test-driven development v čisté formě. Psaní nefunkčních testů először, pak
  minimální implementace, pak full suite. Metoda funguje. Výsledek je hezký, čitelný, maintainable.

Poznámka: exception swallowing v CompositeEventSink je správně, protože pozorovatel nemůže být
  kritický. SLA na board.emit je best-effort.

Poznámka: ProjectionSink nemá state — je to transkripce. Volám apply() na každý qualified event.
  BoardProjection čuva revize.

Poznámka: Task.from_dict() je inverse Task.to_dict(). Patrně v modelu.

Poznámka: DONE_COLUMN vs END. Board v BoardProjection krytý v apply().

Poznámka: query sloupce jménem, nie indexem. Pěkné API.

This commit is feature-complete for Task 4.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Consumer jako tenká obálka nad ConsumerBehavior
  ([`3c5d341`](https://github.com/onpaj/harness_v2/commit/3c5d341b4495f31e82f55431efe70b2765951745))

- Datové modely tasku, workflow a rozhodnutí routeru
  ([`d2b00cf`](https://github.com/onpaj/harness_v2/commit/d2b00cfa3a6b16338d5df01525b3bb9008755da8))

- Dispatcher směrující tasky podle workflow
  ([`da73147`](https://github.com/onpaj/harness_v2/commit/da73147bf6dcb4b9e20fb91a776b1c81a17d80b1))

- Dispatcher with routing, retries, rate limiting, and trace merge
  ([`2b7eccc`](https://github.com/onpaj/harness_v2/commit/2b7eccc003b4f198ab5ca0f1c046803233b2b512))

The first end-to-end milestone: a planner -> implementer -> reviewer chain executes, each run
  inheriting the previous run's committed artifacts, and the completed trace merges into the
  integration branch while main stays untouched.

Two real defects found and fixed while getting here: - merge_leaves already takes the repo lock, so
  the dispatcher wrapping it in a second repo_lock self-deadlocked (flock contends across file
  descriptors within one process). - drain() swallowed task exceptions; failures in the dispatch
  path now surface as dispatch.error events, with a test asserting none occur.

Retry backoff base is now configurable rather than hardcoded at 30s.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Dummy behavior, stdout event sink a systémové hodiny
  ([`3dd02b2`](https://github.com/onpaj/harness_v2/commit/3dd02b2bc3f18c558e743f78d166c38becc5d0c8))

- Eventy nesou snapshot tasku a cílovou frontu
  ([`76e8e36`](https://github.com/onpaj/harness_v2/commit/76e8e36f90332f50009be15f340a39abd517882f))

- Filesystem driver front s atomickým claim a recovery
  ([`bcf0a29`](https://github.com/onpaj/harness_v2/commit/bcf0a29cc0d6613e7be18b0d81b70aa649db7cd0))

- Filesystem workflow repository a FIFO enqueue strategie
  ([`0990612`](https://github.com/onpaj/harness_v2/commit/0990612dd4034489429fdedce1e0bc21d79fe6b1))

FilesystemWorkflowRepository čte <root>/<name>.json a sjednocuje každý způsob selhání (chybějící
  soubor, rozbitý JSON, chybějící start, špatný přechod, jméno se separátorem) do WorkflowNotFound.
  FifoStrategy vybírá nejstarší task podle (created, id), aby byl výběr deterministický i při shodě
  časů.

Přidán i test, který ověřuje, že guard na jméno se separátorem skutečně něco dělá: bez plánovaného
  souboru na cíli úniku by test prošel i po smazání guardu (FileNotFoundError by beztak skončil jako
  WorkflowNotFound), tak i test pro poškozený přechod, který brief nepokrýval, ale je vyžadován jako
  failure mode.

- Git mirror primitives, repo lock, handoff routing, retry backoff
  ([`2bf6fbc`](https://github.com/onpaj/harness_v2/commit/2bf6fbc841eb4586c6cc99761fb94f7fa3beb232))

Routing is guard-railed: an agent proposes handoffs, the orchestrator accepts only those on its
  can_handoff_to allow-list. Idempotency keys are deterministic so a crash between 'handoff written'
  and 'handoff enqueued' cannot duplicate a child task.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Harness --version reports the source commit
  ([`b70ea7e`](https://github.com/onpaj/harness_v2/commit/b70ea7e4368fb191d8165cdb39d765c63f7fed89))

pyproject carries a single static version, so an install before and after an update both reported
  0.1.0 — the one thing --version exists to answer. The commit recorded in PEP 610 direct_url.json
  distinguishes them.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Html board, detail tasku a SSE stream
  ([`4205df8`](https://github.com/onpaj/harness_v2/commit/4205df8bc1a50e14ed4d534222ecaf84ae687047))

- Install AgentHarness skills from onpaj/harness
  ([`d88b608`](https://github.com/onpaj/harness_v2/commit/d88b6086991c7547bb0fe873b9cf1148a57c1de4))

Copies the seven .claude/skills from onpaj/harness@master: azure-storage, brainstorm, chopchop,
  convertforagent, github-storage, oneshot, submit.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Json API nad portem BoardView
  ([`9b143ee`](https://github.com/onpaj/harness_v2/commit/9b143ee123e9231fabdca1a98fd01e0fb97e6aad))

- Karta zobrazuje čas ve stavu
  ([`c19ac03`](https://github.com/onpaj/harness_v2/commit/c19ac039042fe07afb8c0b167412a282f521f32b))

Spec (sekce Karta) požaduje na kartě id, repository, čas ve stavu a oba badge. Čas ve stavu chyběl.
  Bere se z task.history[-1].at, takže projekce se nemění; task bez historie nic nezobrazí.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Klient přeskočí překreslení, když se revize nezměnila
  ([`bf83827`](https://github.com/onpaj/harness_v2/commit/bf838277ed0f41b057eac0d104f0d6a69ab63daf))

Spec (sekce Živý refresh) žádá dvě opatření proti zbytečnému překreslování: server-side coalescing
  (hotové) a revision de-dup na

klientovi (chybělo). hx-trigger="sse:board" dosud spouštělo swap na každý SSE rámec, včetně prvního
  po připojení, kdy revize je stejná jako ta, se kterou se stránka vykreslila.

Přidán drobný inline skript: čte počáteční revizi z data-revision na #board, registruje se na custom
  event "sse:board" dřív, než ho zpracuje htmx (htmx čeká na DOMContentLoaded, tenhle skript běží
  při parsování stránky), a při nezměněné revizi zavolá stopImmediatePropagation, čímž htmx swap
  vůbec nevyvolá. Žádná nová závislost, žádný odkaz na síť.

Ověřeno ručně v prohlížeči (viz report) proti kontrolovanému SSE zdroji: opakovaná revize nevyvolá
  fetch na /fragment/board, změněná ano.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Live task stage output in the board UI ([#13](https://github.com/onpaj/harness_v2/pull/13),
  [`e748a51`](https://github.com/onpaj/harness_v2/commit/e748a51261e9d598c57bd1a0c47799e91456ab2e))

Stream `claude -p` activity live into the task-detail modal so an operator can watch what an agent
  is doing while a stage runs, instead of waiting for the final verdict.

- `ClaudeCliRunner` runs with `--output-format stream-json` and reads stdout line-by-line, rendering
  each message (assistant text, tool calls) and streaming it through a new optional `on_output`
  callback on the AgentRunner port. Verdict parsing is shared between the one-shot envelope and the
  stream's terminal result message. - `ClaudeCliBehavior` emits `stage_output` events
  (task_id/step/attempt/line, never task/queue, so the board projection is unaffected). - New
  `StageOutputView` port + in-memory `StageOutputProjection` driver: a bounded per-task ring buffer
  with subscriber fan-out; live-only (the buffer is dropped when the stage ends). - New SSE endpoint
  `/api/tasks/{id}/output/events` streams HTML-escaped, newline-safe lines into a live panel in the
  task modal.

- Managed repo registry with bare mirrors and internal scratch repo
  ([`2583f15`](https://github.com/onpaj/harness_v2/commit/2583f15f2eec9b830d5d59a8588af4263c5ef90c))

Repo-less agents resolve to the scratch repo, so every run has a worktree and an output commit and
  no downstream code needs a null-repo branch.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Operator CLI and launchd service definition
  ([`4e50cd5`](https://github.com/onpaj/harness_v2/commit/4e50cd5d79430f9f235d7a9f0f912b93e44dbb41))

Every command exits non-zero with a readable message rather than a traceback; tests assert
  'Traceback' never reaches the operator.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Port BoardView a datový model boardu
  ([`8d4bf6f`](https://github.com/onpaj/harness_v2/commit/8d4bf6ff69895af609556f6a53be97b53b4876b1))

- Porty a in-memory drivery pro fronty, workflow, eventy a čas
  ([`60080cb`](https://github.com/onpaj/harness_v2/commit/60080cb1f5919e0cf9d8ff9a181b7cbf6f78e549))

- Project scaffold, config, and id generation
  ([`2b8ff62`](https://github.com/onpaj/harness_v2/commit/2b8ff62ff293944b749aeae1d4d047492391c802))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Router jako čistá funkce nad workflow state machine
  ([`64770f2`](https://github.com/onpaj/harness_v2/commit/64770f2e13cbd19dca89cc49033f0615834b7c10))

- Run the harness as a background service (harness service + install.sh --service)
  ([`69a5d65`](https://github.com/onpaj/harness_v2/commit/69a5d657d91029c68c579daa73e3f909cf20e932))

`harness run` dies with its terminal, so a real install had no supervised loop. Adds `harness
  service install|uninstall|status` for macOS launchd, and an `install.sh --service` step that
  delegates to it.

The plist is built with stdlib plistlib rather than hand-rolled XML, and the content builders are
  pure so both generated files are unit-tested; only the launchctl shell is untested, the same
  bargain git_workspace makes with git.

No secret is written to disk: launchd supplies almost no environment, so the generated wrapper
  resolves GITHUB_TOKEN at start-up — an explicit variable first, else `gh auth token` from the
  keyring. A missing token warns instead of failing, matching how the installer treats a missing
  `claude`.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Scan all repos.json repos for GitHub issues; decouple --source-poll
  ([#15](https://github.com/onpaj/harness_v2/pull/15),
  [`104cf94`](https://github.com/onpaj/harness_v2/commit/104cf9496dfb8d118df0fe2c37e493e221974e88))

* docs: design for multi-repo GitHub source (scan all repos.json repos)

* feat: decouple task-source poll interval (--source-poll) and localize remaining strings to English

* docs: implementation plan for multi-repo GitHub source

* feat: derive GitHub slug from a clone's git origin

* feat: add RepositoryRegistry.names() to enumerate repos.json

* fix: scope GithubTaskSource._mine to its own repo

* feat: scan every repos.json repo for GitHub issues

* test: multi-repo label isolation through the reflector

* chore: scan all repos.json repos, drop single-repo flags

- Scheduler, observability, dashboard, example agent set, and docs
  ([`8deed3f`](https://github.com/onpaj/harness_v2/commit/8deed3f9b1a618f44a2a4f8b1fbff9eff4f57188))

Completes the MVP. The acceptance test drives the shipped example agents (planner -> implementer ->
  reviewer) end to end against a real repo, proving artifact inheritance, guard-railed handoffs,
  integration-branch merge, and that main is never touched.

The live smoke test found that the real CLI currently rejects with an expired OAuth session; it now
  skips with a clear message rather than failing, so a stale token cannot masquerade as a contract
  regression.

Run failures now record the CLI's own explanation, not just that they failed.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Shared domain models for tasks, agents, results, and runs
  ([`dfed6f3`](https://github.com/onpaj/harness_v2/commit/dfed6f315529a65d7e2a44dce8f676b48df78f6c))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Ship as a uv tool; retire install.sh
  ([`e427b9f`](https://github.com/onpaj/harness_v2/commit/e427b9fafaa15f26c5ec72d5418d88d45cff0de5))

Installing meant cloning and running install.sh, and updating meant remembering to git pull. Both go
  away: the package already had the right shape for `uv tool install git+...`, so the work is around
  it.

- `harness update` wraps `uv tool upgrade harness` and reports the new version, plus the kickstart
  needed for a running service to pick it up. - `harness --version`, so an update can be verified at
  all. - The LaunchAgent now points at uv's shim (~/.local/bin/harness) rather than a virtualenv:
  `uv tool upgrade` rebuilds the tool environment but keeps the shim path, so updating never
  invalidates an installed service. - install.sh and its tests are deleted; README leads with uv and
  keeps a short from-source section for developing on the harness itself.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Show task title and repo/worktree names on board cards
  ([#8](https://github.com/onpaj/harness_v2/pull/8),
  [`5cfbf43`](https://github.com/onpaj/harness_v2/commit/5cfbf43e66c8c3ca97faaaa51e31df9c768b0788))

Board cards now display the task title (falling back to the id when absent) instead of the raw task
  id, and the repository and worktree basenames instead of the full filesystem path. Adds a pure
  `basename` Jinja filter and covers the behaviour with template tests.

@claude

- Sqlite run store, concurrency limiter, and rate-limit gate
  ([`56ab340`](https://github.com/onpaj/harness_v2/commit/56ab3404fd970c03de20c15a8eb1b6fafbb9b0ea))

The gate pauses dispatch globally on a detected throttle and resumes on its own after an exponential
  backoff, so an unattended overnight run survives hitting the subscription ceiling.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Wiring a asyncio runtime harnessu
  ([`4d5f047`](https://github.com/onpaj/harness_v2/commit/4d5f047a700a0e499f90b52f688021b0220b79b5))

- Worktree lifecycle, integration merge, filesystem queue, run lifecycle
  ([`cc6003d`](https://github.com/onpaj/harness_v2/commit/cc6003d20f5d00d9776e5e2db65a05eb128042d7))

The Runner is now end-to-end: worktree off the mirror, one claude -p process, result parsing,
  commit, record. Artifact inheritance is proven by test - a child run built from its parent's
  output_ref sees the parent's committed files.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Refactoring

- Sdílená validace jména workflow mezi cli.py a fs_workflows.py
  ([`c206ca8`](https://github.com/onpaj/harness_v2/commit/c206ca86ef7bc0b3381d522a45356a8f6b04a259))

_invalid_workflow_name v cli.py byla byte-for-byte kopie stejné kontroly ve
  FilesystemWorkflowRepository.get — dvě kopie jednoho pravidla bez záruky synchronizace je drift
  hazard. invalid_workflow_name teď žije jen ve fs_workflows.py, cli.py ho importuje.

### Testing

- Architektonické invarianty (AST), smoke test, timeout na async e2e
  ([`b7f9ac8`](https://github.com/onpaj/harness_v2/commit/b7f9ac87b0ea01b70a7fd42e11eea1f651ff512f))

test_architecture.py hlídá pěti testy ze specu vrstvení balíku (models neimportuje nic z harness,
  router zná jen models, porty a orchestrace neimportují drivery, jen app.py/cli.py wirují drivery)
  a nahrazuje slabý tests/test_consumer.py::test_consumer_has_no_branch_on_outcome_value.

Ten starý test hledal přes inspect.getsource(Consumer) tři string literály — projde jím `if outcome
  == "done":` (kontrolovala se jen "request_changes"), aliasovaný import Outcome, i větev přesunutá
  do modulové funkce mimo tělo třídy. Nová verze parsuje ast celého modulu a hledá jakékoli
  porovnání odvozené od outcome (jméno/atribut obsahující "outcome", nebo člen enumu Outcome pod
  libovolným aliasem) — mutation-checknuto na všech čtyřech uvedených únikových cestách, viz
  task-11-report.md.

test_smoke.py řídí celou smyčku na reálném filesystemu (ne in-memory) a ověřuje, že task doputuje
  tasks/ → done/ přes všech pět kroků a jednu zpětnou hranu, a že task s neznámým workflowTemplate
  skončí v failed/ beze zastavení smyčky.

Obě testovací funkce v test_smoke.py i stávající e2e test v test_app.py teď obalují `await runner`
  do asyncio.wait_for: bez toho by regrese v respektování stop eventu smyčku zavěsila navždy místo
  aby test spadl. Mutation-checknuto mutací _dispatcher_loop na `while True` — test spadne s
  TimeoutError za ~5s místo aby visel.

- E2e testy bez reálného čekání a bez skrytého stropu iterací
  ([`2b31af9`](https://github.com/onpaj/harness_v2/commit/2b31af91f5048b5a1807ab0673fdb3a91613a09d))

tests/test_board_e2e.py polloval reálný čas (await asyncio.sleep(0.01)) v cyklu se stropem
  range(400)/range(200) — CLAUDE.md zakazuje testy, které spí v reálném čase, a strop byl latentní
  falešný pád na zatíženém stroji.

Oba testy teď ženou harness.dispatcher.tick() a await consumer.tick() napřímo v cyklu, dokud se něco
  děje — žádný sleep, žádná horní mez na délku běhu systému. Pojistka proti nekonečné smyčce zůstává
  (MAX_STEPS = 1000, řádově nad těch ~6 kroků, které tok plan → review → (request_changes) → plan →
  review → done skutečně potřebuje), ale při vyčerpání selže hlasitě s vysvětlením místo tichého
  selhání.

Protože testy už nevolají harness.run(), musí recover() a projection.hydrate() zavolat samy, ve
  stejném pořadí jako run() (viz app.py) — recovery před hydratací, jinak by zmizely tasky z
  .processing/.

Součástí i drobná oprava: failed["tasks"][0] mohlo spadnout na IndexError, kdyby task nedoputoval do
  failed; teď tomu předchází čitelný assert.

Běh obou e2e testů: ~0.9 s (dřív řádově sekundy reálného spánku).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Make vanished-file recover test actually discriminate
  ([`928ec8b`](https://github.com/onpaj/harness_v2/commit/928ec8b898d4c23c3202772ea13989085d620b40))

test_file_vanishing_mid_recover_is_skipped_silently passed even against the pre-fix recover() that
  unconditionally quarantines on read failure, because _quarantine_file's own FileNotFoundError
  guard silently no-ops on an already-gone file. Spy on _quarantine_file directly and assert zero
  calls for the vanished task, instead of only checking the quarantine directory's contents.

Also drop _read's now-dead quarantine parameter: recover() has called _load() directly since
  c553550, so list() is the only remaining caller and always used the default.
