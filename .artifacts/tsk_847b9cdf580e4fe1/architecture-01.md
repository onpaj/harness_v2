# Architecture assessment — retire `GithubMergeabilityWatcher`

## Verdict

**Approved, with one required addition and three ratified decisions.**
`plan-01.md` and `design-01.md` are unusually well-grounded — I independently
re-verified every quoted line, filename, test name, and behavior against
`origin/main` (fetched fresh: `e4485d6`, 75 commits ahead of this worktree's
checkout) rather than trusting the documents, and every non-trivial claim in
both held up exactly as written: the watcher's shape, the check's shape, the
`cli.py` wiring sites and line contents, all six doc/comment sites naming the
watcher, the CLAUDE.md invariant #31 text, the README's current silence on
`processes/`, `test_architecture.py`'s clean glob (zero watcher hits), and the
`_process_sources`/`FilesystemProcessRepository.build(checks=...)` machinery
the three new e2e tests need already exists and is callable exactly as
described. This is a low-risk deletion: no orchestration-core file changes,
no invariant violated, no data-shape migration. Development can proceed
directly from `design-01.md`'s exact diffs. This document exists to close the
one coverage gap the plan/design missed, ratify the three decisions they left
"open," and give explicit sequencing so the deletion never leaves a window
with weaker coverage than today.

## Alignment with existing patterns

This task is architecturally inert in the sense that matters most: it deletes
one driver and its `cli.py` wiring, and the thing that replaces it
(`GithubConflictsCheck` + `ScheduledTrigger` + `FilesystemProcessRepository`)
**already exists, already ships, already has unit tests, and is already wired
into `_process_sources`**. There is no new port, no new ABC, no new core
module. The precedent for "detection logic lives behind `Check`, not
`TaskSource`, once a process-shaped alternative exists" is `github_issues.py`
→ `github_issues_check.py`, which went through the identical retirement
already (per `docs/adr/0015`, not in scope here but confirms the pattern is
established, not novel). Nothing about this task asks for a new abstraction —
it asks for the removal of a now-redundant one. That is exactly the shape a
"clear the legacy path" task should have.

Checked against the invariants this task's blast radius could plausibly
touch:

- **#1/#17/#20** (driver isolation, ports-only imports in core) — untouched.
  `dispatcher.py`/`consumer.py`/`source_poller.py` never imported the watcher;
  `git grep` confirms zero hits outside `cli.py`, the driver file itself, and
  its two test files. Removing it is architecturally a no-op for these
  invariants, not a fix to a violation.
- **#31** (the branch-override hard-reset) — this is the one invariant whose
  *prose* the task rewrites. The *behavior* it describes (the reset in
  `GitWorkspace.attach`) is explicitly out of scope and untouched — confirmed
  by reading the invariant text and `git_workspace.py` directly: the
  causal fact ("something advances a shared branch server-side with no local
  git touch") is still true, just now caused by `GithubConflictsCheck` instead
  of the watcher. Renaming the actor in five files' prose is correct and
  sufficient; there is no code change the reset itself needs.
- **#39/#40** (processes ride the existing `sources` list, no new `build()`
  parameter; slack sink is the outbound half) — `_process_sources` isn't
  touched by this task and already satisfies both.

No invariant is being relaxed, tightened, or reinterpreted beyond invariant
#31's prose update, which the issue explicitly calls for.

## Proposed architecture

Confirmed as designed — I re-derive it here in terms of the four independent
surfaces that change, because that framing is what makes the risk easy to
reason about (each surface fails independently and has an independent
regression check):

1. **Driver deletion** — `drivers/mergeability_watcher.py` and its two test
   files (`test_mergeability_watcher.py`, `test_mergeability_e2e.py`) go away
   whole. No replacement class; `GithubConflictsCheck` is the replacement and
   is not modified.
2. **`cli.py` wiring collapse** — four independent edits inside `_run` and
   argparse setup: delete `_mergeability_sources` + its import, collapse the
   `sources = github + reflectors + mergeability` line, reword (don't touch
   the logic of) the `resolver_defined` block's comment, delete
   `--watch-mergeability` while keeping `--resolver-workflow` with reworded
   help text. `DEFAULT_RESOLVER_WORKFLOW`/`RESOLVER_DEFINITION` (the `_init`
   scaffolding) are untouched — verified: they're read at `_init`-time, not by
   the watcher.
3. **Prose sweep** — six sites across `CLAUDE.md` (invariant #31, module map,
   two "what is responsible for what" bullets), `git_workspace.py` (module
   docstring + 2 inline comments), `test_git_workspace.py` (docstring + 1
   comment), `ports/source.py` (1 docstring line) — all confirmed to contain
   exactly the text `design-01.md` quotes, so the replacements can be applied
   as close-to-verbatim find/replace. `docs/adr/0014-*` is correctly left
   untouched (point-in-time record).
4. **Test suite reshaping** — delete 2 files, add 3 new e2e tests to
   `tests/test_processes_e2e.py`, trim `tests/test_cli.py` (delete
   `_mergeability_sources`-related tests, rename and simplify one surviving
   test). Confirmed: the deletion candidates' unit-level coverage is already
   reproduced by `test_github_conflicts_check.py` for 7 of the watcher's 11
   unit tests (the 3 `report_progress`/`finish` tests are a deliberate,
   acknowledged capability drop — a `Check` has no `report_progress`/`finish`
   at all, and the check's own design doc already deferred outbound
   reflection to a future sink). See "Coverage gap" below for the 3 tests the
   plan/design's mapping table doesn't name.

No component in this list needs new abstractions, new ports, or new test
infrastructure (`test_processes_e2e.py` already has `build_harness`,
`write_process`, `drive_until_quiet`, and `FilesystemProcessRepository.build`
already accepts the `checks=` override the new tests need — verified by
reading `fs_processes.py` directly, line 228-246).

### Key decision — ratifying the plan's three "open questions"

`plan-01.md` flagged three decisions as open and asked architecture to
confirm them. All three are correct as proposed; none needs revisiting:

- **Filename `processes/autoresolver.json`** (not `resolve-conflicts.json`,
  the pre-existing `test_cli.py` fixture's name). Confirmed cosmetic — a
  process's `name` defaults to its file stem and nothing downstream hardcodes
  either string (verified: `dedup_key` uses `f"scheduled:{name}"` where `name`
  comes from the compiled process, not a literal). **Ratified: use
  `autoresolver.json`**, matching the issue text. The existing test fixture in
  `test_cli.py` keeps its own filename (`resolve-conflicts.json`) since it's
  an isolated fixture, not the shipped template — no reason to rename it too.
- **`sink: {"kind": "none"}`** in the shipped template. Correct — no outbound
  reflection exists for `github-conflicts` today (the watcher's
  `report_progress`/`finish` label swap is being dropped, not migrated to a
  sink), so `"none"` is the only value that doesn't misrepresent a capability
  that isn't there. **Ratified.**
- **`BREAKING CHANGE:` footer, no deprecation shim.** Correct call. The
  counter-argument (a shim that prints a redirect and exits non-zero) would
  reintroduce exactly the kind of dead flag-parsing machinery this repo's own
  conventions argue against, for a single-operator audience where the
  README's new section is the actual discovery mechanism people will read
  before running `harness run` after an upgrade, not a CLI error message.
  **Ratified — no shim.**

## Implementation guidance

**Sequencing is the one place development must not deviate from the plan's
order**, because the deletion is only safe once its replacement coverage
exists:

1. `FR-0` — merge `origin/main`. This is not optional groundwork, it's the
   actual prerequisite: none of the files this task touches exist in this
   worktree's checkout today. Confirm full suite green *before* any edit, so
   any later red is attributable to this task.
2. **Add the three new `test_processes_e2e.py` tests first, against the
   already-shipped `GithubConflictsCheck`/process machinery — zero production
   code change required for this step.** Get them green before deleting
   anything. This is the load-bearing ordering choice: it guarantees there is
   never a commit where conflict-resolution parity has *less* proof than
   before the task started.
3. Add the missing unit test closing the coverage gap below, to
   `test_github_conflicts_check.py`, before deleting
   `test_mergeability_watcher.py`.
4. Delete the watcher driver, its two test files, `_mergeability_sources`,
   the flag, and the `test_cli.py` tests that exercised them; rename/simplify
   the one surviving `resolver`-served test per `design-01.md`.
5. Sweep the six prose sites; grep `[Mm]ergeab` across the tree afterward and
   confirm the only remaining hit is the deliberately-untouched ADR-0014 line.
6. Add the README section.
7. Full suite green, `test_architecture.py` explicitly, plus a manual check
   that every token-less `main(["run", ...])` test in `test_cli.py` still
   returns `0` (this is the regression the "no init-seeding" decision exists
   to prevent — worth eyeballing once, not just trusting the reasoning).
8. Commit with the `BREAKING CHANGE:` footer.

Interfaces and data flow are unchanged from `design-01.md` — I re-verified
the task shape byte-for-byte against `github_conflicts_check.py` on
`origin/main` and confirm it is identical to what
`ResolveConflictBehavior`/`GitWorkspace.attach` already consume:
`data.branch`, `data.source = {kind: "mergeability", repo, pr, url, base}`.
The only shape-level fact worth restating plainly (design already does this,
correctly): `Task.dedup_key`'s *namespace* changes from
`dedup_key("mergeability", repo, pr, head_sha)` to
`ScheduledTrigger`'s `dedup_key(f"scheduled:{name}", f"wf:{workflow}",
"{slug}:{number}:{head_sha}")` — this is a fact for the PR body, not a
compatibility concern, since dedup is compared only against tasks already on
disk in a given install, never across the cutover.

## Coverage gap (required addition, not in plan/design)

`design-01.md`'s "Removed" section names exactly 7 of the watcher's 11 unit
tests as covered by `test_github_conflicts_check.py`, and separately names 3
of the remaining 4 (`report_progress`/`finish`) as a deliberate, unmigrated
capability drop. That accounts for 10 of 11. The 11th
(`test_report_progress_unmanaged_step_leaves_label_untouched`) is the same
kind of drop and clearly falls in the same bucket by inspection — not a real
gap, just an unnamed one.

But three *other* watcher unit tests are not accounted for by name in either
document, and I checked each individually against the check's actual test
file rather than assuming they're covered:

- `test_dirty_pr_dedup_key_embeds_repo_pr_and_head_sha` and
  `test_reconflicted_pr_after_new_head_sha_gets_a_fresh_dedup_key` — **not a
  real gap**. The check's `test_emits_one_observation_per_dirty_pr_with_provenance`
  already asserts `o.state_key == "onpaj/harness_v2:85:3035f7d"` and
  `test_a_new_head_re_emits_after_the_first_was_seen` already asserts two
  different `state_key`s for two head SHAs of the same PR — the same facts,
  proven under different test names. No action needed.
- `test_non_harness_pr_is_never_touched` — **a real, if narrow, gap.** This
  test proves the watcher only acts on PRs whose head branch matches
  `head_prefix`. The check threads `head_prefix` through to
  `list_pull_requests` identically (`client.list_pull_requests(slug,
  head_prefix=self._head_prefix)`, verified by reading
  `github_conflicts_check.py:60`), and the filtering itself is independently
  tested at the `GithubClient` level
  (`test_github_client.py::test_fake_list_pull_requests_filters_by_head_prefix`)
  — so the *underlying* filter is proven correct. What is **not** proven
  anywhere in `test_github_conflicts_check.py` is that `GithubConflictsCheck`
  actually threads its own `head_prefix` constructor argument through rather
  than, say, silently dropping it or hardcoding `"harness/"` — every existing
  fixture PR is built with a `head="harness/tsk_1"`-shaped default and no test
  constructs the check with a non-default `head_prefix` or a PR outside it.

  **Required addition**: one new test in `test_github_conflicts_check.py`,
  e.g. `test_skips_a_pr_outside_head_prefix`, mirroring the watcher's deleted
  test — add a `dirty` PR whose head doesn't start with the check's configured
  `head_prefix` and assert `evaluate()` returns `[]`. This closes the parity
  claim the issue's acceptance criteria actually ask for ("tests proving
  parity where a watcher test existed before") rather than leaving it
  asserted-but-not-verified. Low risk either way (the plumbing is a one-line
  pass-through identical in shape to already-tested code), but it's a
  five-line test and the acceptance criterion is explicit — add it before
  deleting `test_mergeability_watcher.py`, per the same "coverage never dips"
  ordering the plan already established for the other three parity tests.

## Risks and mitigations

- **Risk: the merge (FR-0) surfaces drift** between what `plan-01.md`/
  `design-01.md` quote and what's actually on `origin/main` by the time
  development runs it. **Mitigation**: both documents already flag this and
  instruct re-verification rather than blind trust; I independently
  re-verified everything as of `origin/main@e4485d6` (this session) and found
  zero drift from either document's quoted content — but development should
  still re-run the `git grep -in mergeab` sweep after merging, since more
  commits may land on `main` between now and then.
- **Risk: deleting `test_mergeability_e2e.py` before its replacement tests
  are green** would leave a window with weaker resolver-path coverage than
  today. **Mitigation**: sequencing above (and already mandated by
  `plan-01.md`'s FR-4) puts "add the three new e2e tests" strictly before
  "delete the old ones." Development must not reorder this for convenience.
- **Risk: `harness init` seeding `processes/autoresolver.json`** would look
  like a natural "finish the job" addition but would break every existing
  token-less `main(["run", ...])` test in `test_cli.py`, since
  `github_conflicts_factory` raises `ProcessValidationError` fast when no
  client is configured. **Mitigation**: already decided against in the plan;
  restated here as a hard constraint, not a style preference — do not seed
  the file via `_init` under any framing.
- **Risk: renaming the watcher out of invariant #31's prose accidentally
  changes the described *behavior*.** **Mitigation**: verified directly — the
  reset logic in `GitWorkspace.attach` (both the unconditional create-path
  reset and the ancestry-aware reattach-path reconciliation) contains no
  reference to the watcher's class name in code, only in comments. The prose
  rewrite is purely nominal; a diff of `git_workspace.py`'s actual `attach()`
  logic before/after this task should be empty.

## Prerequisites before implementation begins

1. `git merge origin/main` (FR-0), full suite green before any further edit.
2. Confirm (or re-confirm, if time has passed since this assessment) that
   `origin/main` still contains the exact quoted text this document and
   `design-01.md` verified — a fast `git grep -in mergeab` gives a cheap
   sanity check before starting.

No other prerequisite blocks starting; the replacement machinery
(`GithubConflictsCheck`, `_process_sources`, `FilesystemProcessRepository`)
is already shipped and requires no changes from this task.
