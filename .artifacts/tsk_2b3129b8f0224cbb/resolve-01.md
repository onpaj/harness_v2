# Resolve merge conflict — PR #128

Merged `origin/main` into this branch (token-usage-capture feature) and resolved
three conflicting files:

## `CLAUDE.md`
Doc-only conflict in the module map's bullet list. Both sides added a new
bullet next to `ports/repos.py`: HEAD's token-usage description of
`behaviors/agent.py`, and origin/main's new `ports/command.py` bullet (the
`CommandRunner` port) plus origin/main's own unmodified `behaviors/agent.py`
bullet. Kept both bullets — origin/main's `ports/command.py` entry, followed
by HEAD's richer `behaviors/agent.py` entry (which already includes the
`ports/command.py`-independent token-usage description) — so no information
from either side was lost.

## `src/harness/app.py`
Conflict in `build()`'s `checks` dict literal for the `"failed-tasks"` entry.
HEAD had it as a bare `lambda params: FailedTasksCheck(...)`; origin/main
wrapped the same factory in a `CheckDefinition(spec=FAILED_TASKS_SPEC,
factory=...)` so the process-admin UI treats it as a fully declared action
instead of an unknown one. Took origin/main's `CheckDefinition` wrapper
in full (both `CheckDefinition` and `FAILED_TASKS_SPEC` were already
imported at the top of the file), preserving the exact same `FailedTasksCheck`
construction arguments from both sides (they were identical). Verified the
resulting nesting closes correctly: `CheckDefinition(...)`'s outer paren is
closed by the pre-existing trailing `),` line that both sides shared outside
the conflict markers.

## `tests/test_cli.py`
Two identical small conflicts: a local `fake_serve` shim's signature was
missing the `registry=None` parameter on HEAD but present on origin/main
(matching `harness.cli.serve`'s current signature, already used elsewhere in
the same file, e.g. `test_run_starts_the_heal_process_with_the_shorthand_flag`
at line 253). Took origin/main's signature (with `registry=None`) in both
spots so the fakes match the real `serve()` signature.

## Verification
- No conflict markers remain (`grep` for `<<<<<<<`/`=======`/`>>>>>>>` across
  the three files returns nothing).
- Both edited Python files parse cleanly (`ast.parse`).
- Built a fresh `.venv` (Python 3.11) and ran `pip install -e ".[dev]"`, then
  the full suite: `.venv/bin/pytest -q` → **1500 passed, 1 skipped** (0
  failures), confirming the merged tree is coherent and no invariant broke.
- Staged the three resolved files (`git add`) so `git status` no longer shows
  them as unmerged; did not commit — that's the harness's job.

```json
{"outcome": "done", "summary": "Resolved 3-file merge conflict (CLAUDE.md doc bullets, app.py's failed-tasks CheckDefinition wiring, test_cli.py's fake_serve signature) merging origin/main into the token-usage-capture branch; full suite passes 1500/1500 (1 skipped) after a fresh venv install."}
```
