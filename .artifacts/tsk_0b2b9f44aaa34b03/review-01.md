# Review: ground the architecture in ADRs, refresh README/CLAUDE.md, ship an HTML drill-down

## Verdict: done

## What I checked

Read plan-01.md, design-01.md, architecture-01.md, development-01.md, then verified
the implementation against the tree directly rather than trusting the development
summary.

**FR-1/FR-2 (ADRs).** `docs/adr/` has `0000-adr-process.md` plus twelve numbered
ADRs (`0001`–`0012`), one per FR-2 topic. `tests/test_adr_docs.py` enforces filename
pattern, title/status line, the three required sections (except the process doc),
and a minimum of twelve decision records — all pass. Spot-checked ADR-0000 and
cross-referenced ADR groundings (`tests/test_architecture.py` test names,
`ports/control.py`/`ports/logs.py` contracts) against the live source — accurate.

**FR-3 (`CLAUDE.md`).** Diffed `CLAUDE.md` against the architecture step's stated
delta: all seven missing modules added to their correct rows (`ports/{control,logs}`,
`task_control` in Orchestration, four drivers), plus a `UI` row that wasn't in FR-3's
literal list but is required by FR-3's actual acceptance criterion ("every `.py` file
... appears in the table") — a defensible, disclosed deviation, not scope creep.
`StageOutputView` bullet added. `See ADR-000N` cross-refs added to 16 invariants
without renumbering or rewording any of the 23. `tests/test_claude_md_module_map.py`
passes and would fail on a future undocumented module.

**FR-4 (`README.md`).** Verified the two corrections from architecture-01.md were
actually applied, not just acknowledged: no `--github-repo` flag exists in
`cli.py`, and the README's new "GitHub issue ingestion" section correctly describes
the real mechanism (per-`repos.json` auto-discovery via GitHub origin) and cites
`--github-label`/`--github-workflow`/`--source-poll`, all three confirmed present
in `cli.py`'s `run` subparser. The label lifecycle text
(`harness:todo → harness:queued → per-step → harness:pr-open|failed`) matches
`drivers/github_source.py` and `DEFAULT_STEP_LABELS` in `cli.py` verbatim, down to
the exact label strings. `restart` and "live output" each appear, `docs/adr/` is
linked. Intro paragraph no longer claims "Phase 1 is a POC."

**FR-5 (doc-site generator).** `src/harness_docs_site/{corpus,markdown,site}.py` +
`scripts/build_docs.py` exist as designed, with zero import of `harness`. Ran it for
real: `scripts/build_docs.py --out /tmp/site_review` produced 29 pages; every href in
the generated `index.html` resolves to a file that exists (checked programmatically,
0 missing links). Confirmed both bugs claimed as fixed during development actually
render correctly — `grep -c "<h1"` on a generated ADR page returns 1 (no duplicate
title), and a multi-line `CLAUDE.md` paragraph with wrapped continuation lines
renders as one clean `<p>`, not fragmented. Confirmed the `pyproject.toml`
`exclude = ["harness_docs_site*"]` actually works by building the wheel
(`python -m build --wheel`) and checking `harness_docs_site` is absent from it.
`.gitignore` has `site/`.

**Non-functional requirements.** No new production dependency (hand-rolled Markdown
converter, `harness_docs_site` excluded from the wheel). No changes to
`dispatcher.py`/`consumer.py`/`router.py`/driver runtime behavior — diff is
docs + a new, standalone, non-shipped package + tests. English throughout.
Full suite: `.venv/bin/pytest -q` → 486 passed, 1 skipped (the pre-existing opt-in
`HARNESS_SMOKE_CLAUDE` smoke), consistent with development-01.md's claim.

## Deviations noted in development-01.md — assessed

All four are reasonable, disclosed, and don't conflict with the architecture step's
guidance:
1. Module-map test uses bare stems, not full dotted paths — matches the table's
   existing brace-notation convention; the architecture step's own §5 anticipated
   exactly this call.
2. Added a `UI` row not named in FR-3's literal delta — required by FR-3's stated
   acceptance criterion, smallest fix, disclosed.
3/4. Two markdown-renderer bugs found and fixed during manual verification, with new
   unit tests added. Verified independently above — not just taking the claim on
   faith.

## Assessment

No functional requirement is unmet, nothing conflicts with the architecture, all
tests the plan called for exist and pass, and I found no correctness bugs beyond
the two the implementer already caught and fixed themselves (verified those fixes
hold). The one loose end — `repo_root` is an unused parameter in
`site.build_site()` — is a stylistic nit, not a defect, and not worth a
`request_changes` round.
