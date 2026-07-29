# ADR-0021: Autoheal is enabled by its process file, not by an environment variable

Status: Accepted

Refines ADR-0018's `--heal-repo` decision bullet and supersedes ADR-0019's
"the check's `repository` param must equal `HARNESS_HEAL_REPO`" consequence,
per ADR-0000's additive convention — the rest of both ADRs is unchanged and
still authoritative.

## Context

ADR-0018 kept `--heal-repo` as a thin generator: the flag serves `heal`,
registers the `open-issue` finisher, and writes `processes/autoheal.json` if
absent. That left the launchd service unable to self-heal at all, because
`launchd.wrapper_script` ends in a fixed line with no seam for extra flags:

```
exec "{harness}" run --root "{root}" --api-port {api_port}
```

`HARNESS_HEAL_REPO` was added as an equivalent enablement, justified as
mirroring how `SLACK_WEBHOOK_URL` gates the slack sink. That parallel does not
hold. `SLACK_WEBHOOK_URL` is a **secret** — invariant #40 says explicitly it
"is a secret and never enters a JSON file", so the environment is the only
correct home for it. `HARNESS_HEAL_REPO` is a public repo slug
(`onpaj/harness_v2`). Nothing about it needs the environment.

Worse, the slug was already persisted: `_ensure_autoheal_process` writes it
into `processes/autoheal.json` as `action.params.repository`. Configuration
lived in two places, and the copy that actually drove wiring was the invisible
one. Autoheal was the only automation in the harness enabled by an environment
variable rather than by a file in `processes/`.

That ambience had a concrete cost. `SubprocessCommandRunner` hands the verify
gate the harness's whole environment, so running this repo's own `pytest` as
its verify command saw `HARNESS_HEAL_REPO` set and turned eight `test_cli.py`
tests red — tests asserting on the *absence* of a heal repo. The gate reported
a failure that did not exist in the diff, and bounced it back to `development`
in a loop no development agent could close.

## Decision

**`processes/autoheal.json` is the single source of truth.** Its existence
enables self-healing; its `action.params.repository` configures it.

- `cli._autoheal_repo(layout)` reads the slug back out of that file. A
  missing, malformed, or `repository`-less file returns `None` — "nothing to
  wire" — rather than raising: the process still has to compile in `build()`
  moments later, and that is where a malformed one earns its error message.

  > **Superseded 2026-07-26 (ADR-0022).** `cli._autoheal_repo` does not exist
  > in the shipped harness — `action.params.repository` is never read at
  > startup as a slug at all. `OpenIssueBehavior` resolves the repo
  > per-task, at consume time, from `task.repository` (which the field is
  > stamped onto) through `RepositoryRegistry` and the clone's own `origin`,
  > the same way every other GitHub-touching driver does. See ADR-0022's
  > Context for why: this bullet's "the slug" presumes `action.params.
  > repository` *is* a GitHub slug, an assumption ADR-0022 replaces.
- `--heal-repo <owner/repo>` becomes a **bootstrap**: it writes the file
  (still only if absent — a hand-edited process is never clobbered) and
  enables healing for that run. Every run afterwards needs no flag.

  > **Superseded 2026-07-26 (ADR-0022).** `--heal-repo` is removed outright,
  > not kept as a bootstrap: `harness init` seeds `processes/autoheal.json`
  > unconditionally (with an empty `action.params.repository`), so nothing
  > is left for a flag to bootstrap. See ADR-0022.
- `HARNESS_HEAL_REPO` is removed. The service needs neither a flag nor a
  variable; it finds the process file in the root it was already pointed at.
- The `--agent claude` requirement splits by how the misconfiguration was
  reached. Via the flag it stays a hard error (exit 2) — the operator is at a
  prompt to read it. Via the process file it is a warning, because an
  unattended service must not crash-loop on config it cannot be told to fix.

  > **Superseded 2026-07-26 (ADR-0022).** There is no flag path any more, so
  > this split has nothing left to split on — `harness run` never accepted
  > `--heal-repo`/`--agent claude` as a paired requirement in the shipped
  > code. The underlying principle — an unattended service warns rather than
  > crash-loops on config it cannot be told to fix — survives, restated over
  > this ADR's own two `action.params.repository` cases: a *present but
  > wrong* value still fails loud at process-compile time
  > (`ProcessValidationError`, unchanged from this ADR's Decision above), and
  > an *absent* one — silent before ADR-0022 — now warns at startup instead.

## Consequences

- **The drift ADR-0019 documented is eliminated, not merely mitigated.** That
  ADR recorded a hand-edited `autoheal.json` as an operator-authored risk: the
  check's `repository` param and the finisher's file-to repo could point at
  different repos, so a heal task would attach a worktree in one and file its
  issue against another. There is now one value, read once, feeding both. A
  hand-edited file is no longer a drift vector — it is the configuration.
- **Enablement is uniform.** Every automation in the harness is now enabled by
  a file in `processes/`. `harness init` still ships `workflows/heal.json` and
  the personas unconditionally and `autoheal.json` never, so a bare init is
  unchanged: `failed/` stays a dead-end terminal until a process file exists.
- **Turning healing off is deleting a file**, not unsetting a variable in an
  environment the operator has to remember the service reads.
- **One fewer ambient input to the verify gate.** `SubprocessCommandRunner`
  still does not sanitize the environment — deliberately, since a verify
  command legitimately needs `PATH`, `HOME` and often a token — so
  `GITHUB_TOKEN` remains inherited and remains a real secret that must. The
  suite's defence is `tests/conftest.py`'s autouse `hermetic_environment`
  fixture, guarded both ways by `tests/test_hermetic_environment.py`: a config
  variable the package reads but the fixture misses fails, and so does a stale
  entry the package stopped reading.
- **No migration for an operator already using the variable**, as long as
  `processes/autoheal.json` exists — which it does, because the same code path
  that read the variable also wrote the file. An operator who deleted the file
  while keeping the variable set loses healing silently on the next restart;
  the fix is one `--heal-repo` run to regenerate it.
