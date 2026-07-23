# Review: continuously reflect task state onto the source GitHub issue's labels

## Verdict: done

The implementation matches plan-01.md / design-01.md / architecture-01.md exactly,
with architecture's one correction (subclass `TaskSource`, not `Trigger`) applied
in the actual code. Verified against the diff and by running the suite directly,
not by trusting development-01.md's narrative.

## What I checked

**Conformance to spec / acceptance criteria** — all four met, verified in code:

- **AC1 (labels track state for Process-sourced tasks).**
  `GithubLabelReflector` (`src/harness/drivers/github_source.py:24`) implements
  `report_progress`/`finish` with the same `_set_state` shape `GithubTaskSource`
  used before, and is registered per GitHub-origin repo in `cli.py._run` exactly
  when `--no-github-source` is set (`src/harness/cli.py:1425-1428`). Confirmed
  end-to-end by `tests/test_processes_e2e.py::test_github_issues_process_reflects_task_state_onto_issue_labels`,
  which drives a `github-issues` Process through `plan → development → review →
  land → end` against `FakeGithubClient` and asserts the issue's final label is
  `harness:pr-open`.
- **AC2 (idempotent, non-blocking, invariant #21).** `_set_state` recomputes the
  full target label set from scratch every call (remove all managed-but-target,
  add target) — no persisted history, so a repeated call is a no-op by
  construction. Verified both by reading `add_label`/`remove_label` on
  `FakeGithubClient`/`HttpGithubClient` (genuinely idempotent — add-if-absent,
  remove-if-present, 404-on-delete swallowed) and by
  `test_reflector_double_report_progress_is_idempotent` /
  `test_reflector_double_finish_is_idempotent`.
- **AC3 (foreign tasks silently ignored).** `_mine()` gates on
  `source.kind == "github" and source.repo == self._repo`; covered by
  `test_reflector_ignores_task_without_source`,
  `test_reflector_ignores_task_from_another_repo`,
  `test_reflector_ignores_task_from_foreign_kind`.
- **AC4 (no new coupling into dispatcher/consumer).** The new class only touches
  `ports.source`, wired exclusively in `cli.py`. `tests/test_architecture.py`
  (26 tests) passes unmodified — reran it directly, green.

**Adherence to architecture** — the one correction architecture-01.md made to
the design (base class `TaskSource`, not `Trigger`, since `Trigger` names the
inbound-only shape and this class is the exact inverse) is present in the
actual code (`class GithubLabelReflector(TaskSource):`), not just described in
the development note. `GithubTaskSource` composes a `GithubLabelReflector`
internally and delegates `report_progress`/`finish` to it — single
implementation of the state→label mapping, per FR-5. Mutual exclusion between
`GithubTaskSource` and the standalone reflector is structural (`cli.py`'s
`github`/`reflectors` both gated on the same `args.no_github_source`), backed
by `test_run_gates_github_sources_and_reflectors_mutually_exclusively`, which
spies on `_run`'s actual call pattern rather than just reading the two-line
diff.

**Correctness** — no logic errors found. `_managed` set construction, `_issue`
extraction, and the delegation in `GithubTaskSource.__init__` are all
straightforward and covered by tests. No schema, port, or dispatcher/consumer
change, matching invariants #18-#20 and the plan's explicit scope boundary
(`_ACCEPTED_SINK_KINDS` untouched, `DEFAULT_STEP_LABELS` not widened).

**Independent verification performed:**
- `.venv/bin/pytest -q` → 1115 passed, 1 skipped (matches development-01.md's
  claim exactly).
- `.venv/bin/pytest -q tests/test_architecture.py tests/test_claude_md_module_map.py`
  → 27 passed.
- Read `src/harness/app.py`'s `build()` to confirm the same `sources` list feeds
  both `SourcePoller` and `SourceReflectorSink` (`app.py:442`, `:571`) — the
  precondition the whole design leans on.
- Read `github_client.py`'s `add_label`/`remove_label` on both `FakeGithubClient`
  and `HttpGithubClient` to independently confirm the idempotency claim rather
  than taking FR-2's assertion at face value.

## Non-blocking observations (not requesting changes)

- `SourceReflectorSink.emit()`'s per-source exception isolation remains
  unaddressed, as scoped out by architecture-01.md's fourth decision. Confirmed
  this doesn't worsen anything: the number of registered GitHub-kind reflecting
  sources per repo is unchanged (exactly one, either shape) in both
  configurations.
- `DEFAULT_STEP_LABELS` coverage (only `development`/`review`/`land` get a
  label) is unchanged from pre-existing `GithubTaskSource` behavior — consistent
  with the acceptance criteria's "e.g." phrasing, not a gap in this change.

```json
{"outcome": "done", "summary": "Implementation matches plan/design/architecture exactly: GithubLabelReflector (TaskSource, not Trigger per architecture's correction) extracted in drivers/github_source.py, composed by GithubTaskSource, wired via _github_reflectors in cli.py gated on --no-github-source. All 4 acceptance criteria verified against code and tests, not just the development note. Full suite reran green (1115 passed, 1 skipped), test_architecture.py's 26 invariant checks pass unmodified. No issues found."}
```
