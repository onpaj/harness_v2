# Resolve merge conflict on PR #145

## Conflict

Merging `origin/main` into this branch produced exactly one textual conflict,
in `tests/test_cli.py` around a `--heal-repo` era test that this branch's
history never touched:

- **HEAD** (this branch, `harness/tsk_a3233efcf2c443ac`) still had
  `test_run_heal_repo_needs_claude_agent` (asserting `--heal-repo` +
  `--agent dummy` is rejected with exit code 2) followed by
  `test_run_defaults_agent_timeout_to_5400` (this branch's own change,
  bumping the shared per-agent timeout default from 1800.0s to 5400.0s).
- **origin/main** had already removed the `--heal-repo` CLI flag entirely,
  replacing opt-in self-healing with the always-on
  `processes/autoheal.json` mechanism (ADR-0021). Its version of this
  region only had `test_run_defaults_agent_timeout_to_1800` (the pre-bump
  value, since main never saw this branch's timeout change).

## Resolution

Git's diff3 pulled both differing test-function bodies into one conflict
hunk because they sit adjacent in the file. Investigation showed the
`--heal-repo` flag is fully gone from `src/harness/cli.py` on this merged
tree (superseded by `_ensure_autoheal_process`/`_warn_missing_autoheal_repository`
wired unconditionally in `run`), and origin/main already carries a dedicated
replacement test, `test_the_heal_repo_flag_is_gone` (asserting argparse
itself rejects the now-unknown `--heal-repo` argument with exit code 2 and
"unrecognized arguments" on stderr), elsewhere in the same file.

So the correct merge of *intent*, not just text, is:

- **Drop** `test_run_heal_repo_needs_claude_agent` — it tests behavior
  (`cli.py` validating `--heal-repo` needs `--agent claude`) that no longer
  exists; keeping it would test a flag origin/main removed, and it was
  failing (`SystemExit: 2` propagating out of `argparse` instead of a
  returned code, since the flag is now genuinely unrecognized).
- **Keep** `test_run_defaults_agent_timeout_to_5400` — this branch's own,
  intentional change; its assertion (`captured["agent_timeout"] == 5400.0`)
  was already common code below the conflict markers on both sides, so
  keeping the `_5400`-named function is the only version consistent with
  the rest of the file.

No other files had conflict markers (`git status` showed only
`tests/test_cli.py` as `UU`).

## Verification

Ran the full suite after resolving and staging:

```
.venv/bin/pytest -q
```

Result: `1672 passed, 1 skipped, 1 warning` (the skip is the opt-in
`HARNESS_SMOKE_CLAUDE` real-`claude` smoke test, unaffected). No conflict
markers remain anywhere in the tree (`git grep` for
`^<<<<<<<|^=======|^>>>>>>>` only matches unrelated pytest output logs
under `.artifacts/` and a comment separator in `sse.js`).

`tests/test_cli.py` is staged (`git add`); the merge is otherwise clean.
