# Handoff — reimplement the weekly "coverage gap" routine as a harness Process

**Audience:** an agent running on the **host machine** (Ondrej's Mac), with the
harness installed and `~/.harness` populated, `gh` authenticated, and a clone of
`onpaj/harness_v2` available for the one code change.

**Goal:** replace the standalone "Coverage Gap Routine" prompt with a harness
Process that fires weekly, turns each low-coverage source file into a task, has
an agent qualify it, and files a GitHub issue on `onpaj/Anela.Heblo` for the
gaps worth closing.

Read this whole document before touching anything. Work in the order given —
Part A is a code change in the harness repo and everything after it depends on
it landing.

---

## 0. Preconditions — verify before starting

```sh
harness --help                      # harness is installed
echo "${HARNESS_HOME:-$HOME/.harness}"
ls "${HARNESS_HOME:-$HOME/.harness}"/{workflows,agents,processes,repos.json}
gh auth status                      # gh is authenticated
gh run list --workflow=ci-main-branch.yml --branch=main --status=success --limit 1 \
  --repo onpaj/Anela.Heblo          # the CI workflow name is real
jq . "${HARNESS_HOME:-$HOME/.harness}/repos.json"
```

`repos.json` **must** contain an entry whose key is `onpaj/Anela.Heblo` pointing
at a local clone. `compile_process` validates the process file's `repository`
against this registry and fails the whole `harness run` startup if it is
missing (`_parse_repository`, `field="repository"`). If it is absent, add it and
clone the repo before continuing.

Throughout this document `$H` means `${HARNESS_HOME:-$HOME/.harness}`.

---

## Part A — generalize the `open-issue` finisher (code change, harness repo)

### Why

`open-issue` is documented as generic ("any future process whose target's last
step drafts an issue can bind to this same kind") but it is hardcoded to the
self-heal workflow. Without this change the coverage process cannot file
anything. In `src/harness/behaviors/open_issue.py`:

| Line of code | Problem |
|---|---|
| `marker = task.data["heal"]["of"]` | plain `KeyError` on a non-heal task → straight to `failed/` |
| `_latest_draft()` filters `ref.step == "heal"` | a `cov-qualify` step's artifact is invisible |
| `_body()` reads `task.data['heal']['of']`, appends `_Filed by the harness healer..._` | wrong provenance footer |
| `repo` / `labels` fixed at construction | would file to the heal repo with `harness:self-heal` |
| `cli.py:1802` lambda discards `config` | the finisher registry already passes it (invariant #41) |
| the kind is registered only when `--heal-repo`/`HARNESS_HEAL_REPO` is set | unavailable otherwise |

The finisher contract was designed for exactly this: `build()`'s registry calls
`factory(step, binding.config, inner)`. The binding's `config` is everything but
`"kind"` in the workflow's `finishers` entry. We just need to read it.

### What to change

**`src/harness/behaviors/open_issue.py`**

1. Add constructor params, all defaulting to today's heal behavior so the
   autoheal process is byte-for-byte unaffected:
   - `draft_step: str = "heal"` — which step's artifact is the draft
   - `marker_path: tuple[str, ...] = ("heal", "of")` — where in `task.data` the
     idempotency marker lives
   - `footer: str | None = None` — overrides the "filed by the harness healer"
     line
   - keep `repo` and `labels` as constructor params (they already are)
2. Replace `task.data["heal"]["of"]` with a helper that walks `marker_path`
   defensively and **falls back to `task.id`** when the path is absent. A
   coverage task has no `data.heal`; its own task id is a perfectly good
   idempotency marker.
3. `_latest_draft` filters on `self._draft_step`, not the literal `"heal"`.
4. `_body` uses the resolved marker and `self._footer` (default: today's heal
   sentence, so heal output is unchanged).
5. `_title` / `_heal_verdict_summary`: `_heal_verdict_summary` must look for
   `entry.from_step == self._draft_step`, not `"heal"`.

**`src/harness/cli.py`**

6. Register the `"open-issue"` kind **unconditionally**, not only under
   `if heal_repo:`, and make the lambda read `config`:

   ```python
   finishers["open-issue"] = lambda step, config, inner: OpenIssueBehavior(
       tracker=issue_tracker,
       repo=config.get("repo") or heal_repo,
       artifacts=artifact_view,
       clock=clock,
       labels=tuple(config.get("labels", ("harness:self-heal",))),
       draft_step=config.get("draft_step", "heal"),
       marker_path=tuple(config.get("marker_path", ("heal", "of"))),
       footer=config.get("footer"),
   )
   ```

   The `issue_tracker` currently only exists inside the `if heal_repo:` block.
   Hoist its construction so the kind is always available; when there is no
   `GITHUB_TOKEN`, keep the existing in-memory-fake fallback so nothing crashes
   at build. A binding with neither `config["repo"]` nor a `heal_repo` must
   fail loudly at build time, not at consume time — raise a `ValueError` from
   the factory naming the step.

### Tests to add

Follow the existing style in `tests/` (in-memory drivers, `FakeClock`, no
sleeping):

- `open-issue` with `config={"repo": "onpaj/Anela.Heblo", "labels": [...],
  "draft_step": "cov-qualify"}` on a task with **no** `data.heal` opens an issue
  on the configured repo with the configured labels, using `task.id` as the
  marker.
- the existing heal path still produces an identical issue (title, body, labels,
  marker) — a regression guard for the defaults.
- a binding with no `repo` and no heal repo fails at `build()`.

### Commit

Project convention is **commit straight into `main`** for the harness repo, and
conventional commits are load-bearing for the release workflow:

```
feat: make the open-issue finisher configurable per binding

The kind was hardcoded to the heal workflow (data.heal marker, "heal" draft
step, wiring-time repo/labels) despite being documented as generic. Read
repo/labels/draft_step/marker_path/footer from the finisher binding's config,
defaulting to today's heal values, and register the kind unconditionally.
```

Run `.venv/bin/pytest -q` before committing. Do not open a PR.

---

## Part B — the candidate scanner

This is Steps 1–4 of the original routine, collapsed into one script whose
stdout is one line per candidate. The harness's `command` check turns each
non-empty stdout line into one task.

Write it to `$H/bin/coverage-gap-scan.py` and `chmod +x` it.

### Contract the script MUST honour

- **Always exit 0.** `CommandCheck.evaluate()` returns `[]` on a non-zero exit,
  *silently* — a crashed script is indistinguishable from "no gaps". Log
  diagnostics to **stderr** and exit 0 regardless.
- **One candidate per stdout line**, no blank lines, no header.
- The line is used as **both** the task's `data.title` (what the agent sees in
  its prompt) and the `state_key` (the dedup identity). Design it accordingly.
- Cap output at `MAX_ISSUES_PER_RUN` lines (`head`-style), and report the number
  dropped on stderr.
- Finish fast enough for the check's timeout — see the timeout note in Part E.

### Line format

```
<isoweek> <side> <module> <path> cov=<pct> lines=<valid> run=<run_id> sha=<head_sha>
```

Example:

```
2026-W30 backend Catalog backend/src/Anela.Heblo.Application/Features/Catalog/GetProductHandler.cs cov=32.4 lines=181 run=17234567 sha=a1b2c3d
```

The leading ISO week is deliberate — it is what makes the state key
*week-fresh*. See "Known limits → dedup" below.

### Behaviour

1. `gh run list --workflow=ci-main-branch.yml --branch=main --status=success
   --limit 1 --json databaseId,headSha,createdAt --repo onpaj/Anela.Heblo`.
   No run, or `createdAt` older than 7 days → print nothing to stdout, print the
   reason to stderr, exit 0.
2. `gh run download <run> --name coverage-backend --dir <tmp>/be` and
   `--name coverage-frontend --dir <tmp>/fe`. A failed or empty download marks
   that side UNAVAILABLE (stderr note) and the other side is processed
   independently.
3. **Backend** — for each `coverage.cobertura.xml` under `<tmp>/be`, walk
   `<class>` nodes:
   - `filename` must start with `backend/src/`; otherwise warn on stderr and
     skip.
   - skip `lines-valid == 0`.
   - group by `filename`; per node `lines-covered = round(lines-valid ×
     line-rate)`; file coverage = `Σ covered / Σ valid × 100`.
   - module = the path segment right after `Features/`, else `Other`.
   - cross-layer dedup: the same `Features/<Module>/<basename>` seen in a second
     project directory reuses the first path.
   - emit if file coverage < `COVERAGE_THRESHOLD` (60).
4. **Frontend** — parse `<tmp>/fe/lcov.info` (`SF:` / `LF:` / `LH:` /
   `end_of_record`). Skip `LF == 0`. Module = the first subfolder under
   `frontend/src/`. Emit if `LH/LF × 100 < 60`.
5. Sort ascending by coverage (worst first) so the cap keeps the worst gaps,
   then truncate to 10.

Use Python's `xml.etree.ElementTree` for the cobertura parse — do not shell out
to `grep`/`sed` for XML.

Verify it standalone before wiring anything:

```sh
"$H/bin/coverage-gap-scan.py"; echo "exit=$?"
```

---

## Part C — the workflow

Write `$H/workflows/coverage-gap.json`:

```json
{
  "name": "coverage-gap",
  "start": "cov-qualify",
  "transitions": [
    {
      "from": "cov-qualify",
      "on": "file",
      "to": "cov-dedup",
      "hint": "the file holds real untested logic worth an issue — draft it"
    },
    {
      "from": "cov-qualify",
      "on": "skip",
      "to": "end",
      "hint": "controller/DTO/config/generated, or no meaningful untested logic — file nothing"
    },
    {
      "from": "cov-dedup",
      "on": "unique",
      "to": "cov-file-issue",
      "hint": "no open coverage-gap issue already covers this file"
    },
    {
      "from": "cov-dedup",
      "on": "duplicate",
      "to": "end",
      "hint": "an open coverage-gap issue already covers this file — settle silently"
    },
    { "from": "cov-file-issue", "on": "done", "to": "end" }
  ],
  "descriptions": {
    "cov-qualify": "read the source file named in the task and decide whether its untested logic warrants a GitHub issue",
    "cov-dedup": "search the repo's open coverage-gap issues; decide whether the drafted issue is new"
  },
  "finishers": {
    "cov-file-issue": {
      "kind": "open-issue",
      "repo": "onpaj/Anela.Heblo",
      "labels": ["coverage-gap", "tech-debt"],
      "draft_step": "cov-qualify",
      "footer": "_Filed by the weekly coverage-gap process._"
    }
  },
  "maxParallel": { "cov-qualify": 2 }
}
```

### Why the `cov-` prefix is not cosmetic

Step queues are **shared across every served workflow**. The heal workflow
already owns steps named `heal`, `dedup` and `file-issue`. Two served workflows
binding the same step name to different finisher bindings is a build-time
failure (`app.py:576-583`, "conflicting finisher bindings"), and a shared step
name would also share one persona file. Every step in this workflow must have a
name no other served workflow uses.

`end` is a reserved terminal node — do not define a persona or a transition
leaving it.

---

## Part D — the personas

Personas are data (`agents/<step>.json`). The schema is exactly:

```json
{
  "prompt": "...",
  "model": null,
  "fallback_model": null,
  "allowed_tools": [],
  "allowed_outcomes": ["done"],
  "timeout": null
}
```

> **Hard constraint — read this twice.** `fs_agents._parse_agent_spec` accepts
> **only** `"done"` and `"request_changes"` in `allowed_outcomes`. Writing
> `["file", "skip"]` produces a file the catalog cannot load, and `app.build()`
> resolves agents eagerly, so the **next `harness run` crashes at startup**.
> Leave `allowed_outcomes` as `["done"]` in both files. The real vocabulary
> comes from the workflow's outgoing edges — `Workflow.outcomes_for(step)` is
> the live authority whenever a workflow drives the step (invariant #42), and
> `allowed_outcomes` is only the workflow-less fallback.

Do **not** write the artifact/verdict-block boilerplate into the prompt —
`compose_prompt` in `behaviors/agent.py` supplies the artifact path, the outcome
list with its hints, the step description and the verdict-block instruction at
runtime. The prompt is the **persona only**: role, inputs, what to deliver.

### `$H/agents/cov-qualify.json`

`allowed_tools`: `["Read", "Grep", "Glob", "Bash"]`. Suggested `timeout`: `900`.

Prompt should convey:

- You are a test-coverage analyst. The task title is one scan line of the form
  `<week> <side> <module> <path> cov=<pct> lines=<n> run=<id> sha=<sha>`. Parse
  it; the file lives at `<path>` relative to the worktree root.
- Read the full source file. If it does not exist in the checkout, return
  `skip` with the rationale "source not found in checkout".
- Optionally use Grep/Glob to locate the file's existing tests for context.
- **Return `skip`** for: MVC controllers whose actions only call
  `_mediator.Send()`/`Publish()`; DTO/request/response classes that are only
  properties; `Program.cs`, `Startup.cs`, `*Module.cs`,
  `ServiceCollectionExtensions.cs` and other DI/config/startup files;
  files of only auto-properties, auto-mapped fields or trivial passthroughs;
  generated code (`<auto-generated>` header or a known generated path).
- **Return `file`** for: a MediatR handler with 2+ conditional branches covering
  validation or business rules; domain logic with state transitions, status
  flows or invariants; business calculations (financial totals, margins, stock
  quantities, pricing, discounts); error/exception paths whose failure shape no
  existing test asserts; cross-module service contracts whose integration
  surface is untested.
- **The raw coverage number is only a candidate filter — your judgment after
  reading the source is the gate.**
- When returning `file`, write the artifact as the issue itself, in exactly this
  shape (the finisher takes the title from the first `# ` heading and the whole
  file as the body):

  ```markdown
  # [coverage-gap] <Module>/<File>: <specific untested logic>

  ## Module / File
  <path>

  ## Coverage
  Line coverage: <pct>% (filter threshold: 60%)

  ## What's not tested
  <uncovered branches, conditions, error paths — in plain language>

  ## Why it matters
  <what could silently break if this logic regresses>

  ## Suggested approach
  <unit or integration test, which scenario, rough effort>

  ---
  _Based on CI run #<run> (<sha>)._
  ```

- **Never paste source code into the artifact.** Describe the untested
  behaviour in plain language.

### `$H/agents/cov-dedup.json`

`allowed_tools`: `["Read", "Bash"]`. Suggested `timeout`: `300`.

Prompt should convey:

- Read the drafted issue from the previous step's artifact.
- Search the repo's open issues for one already covering the same file:

  ```sh
  gh issue list --repo onpaj/Anela.Heblo --label coverage-gap --state open \
    --search "<Module>/<basename> in:title" --limit 20
  ```

  Also list open `coverage-gap` issues generally and judge by content, not only
  by the title token — a differently-worded issue about the same file is still a
  duplicate.
- Return `duplicate` if one exists (nothing is filed, the task settles silently),
  `unique` otherwise.
- Re-emit the drafted issue unchanged as this step's artifact so the finisher's
  latest-artifact lookup still finds it if you adjust `draft_step` later; with
  `draft_step: "cov-qualify"` as configured above this is belt-and-braces.

`cov-file-issue` needs **no** persona file — it is fully replaced by the
`open-issue` finisher, the same way `land` is replaced by `open-pr`.

---

## Part E — the process

Write `$H/processes/coverage-gap.json`:

```json
{
  "trigger": { "cron": "0 6 * * 1" },
  "action": {
    "check": "command",
    "params": {
      "command": "$HOME/.harness/bin/coverage-gap-scan.py",
      "timeout": 600
    }
  },
  "target": { "workflow": "coverage-gap" },
  "dedup": "per-state",
  "repository": "onpaj/Anela.Heblo",
  "sink": { "kind": "none" }
}
```

Expand `$HOME` to the literal absolute path when you write the file — the
command runs through a shell, but keeping the path literal avoids surprises
under launchd's minimal environment.

Notes:

- **`cron` is UTC-only.** `0 6 * * 1` is Monday 06:00 **UTC** — 07:00 or 08:00 in
  Prague depending on DST. There is no per-trigger timezone field.
- **`timeout: 600`.** The default is 30s, which two `gh run download` calls will
  blow through. But note `CommandCheck.evaluate()` runs **synchronously inside
  `SourcePoller.tick`** — for the duration of the scan, *all* ingestion (GitHub
  issue polling, every other process) is stalled. Keep the scanner as fast as
  you can and set the timeout to the smallest value that reliably completes.
- **`repository`** gives each fired task a worktree of the Anela.Heblo clone, so
  `cov-qualify` can `Read` the source file. Without it the agent has nothing to
  read.
- **`sink: none`** — fire-and-forget. Switch to `{"kind": "slack"}` only if
  `SLACK_WEBHOOK_URL` is set in the service environment; it produces one Slack
  line *per task*, not a run summary.

---

## Part F — wiring the run

The workflow must be **served**, otherwise the dispatcher rejects tasks
targeting it. Either add it explicitly (`--workflow` is repeatable):

```sh
harness run --workflow development --workflow coverage-gap
```

…or serve everything with `--all-workflows`. If the harness runs under launchd,
update the installed service arguments and reinstall:

```sh
harness service uninstall && harness service install ...   # with the new flags
harness service status
```

The service's `PATH` is built explicitly by `cli.service_path_entries` —
**confirm `gh` is reachable on it**, since the scanner shells out to `gh`. The
wrapper resolves `GITHUB_TOKEN` at start-up (explicit variable, else
`gh auth token`), which also covers `gh`'s own auth.

---

## Part G — validate before letting it fire on schedule

1. **Startup validation.** Every process file is compiled at `harness run`
   startup and a bad one fails the whole boot. Run the harness in the foreground
   once and read the output:

   ```sh
   harness run --workflow development --workflow coverage-gap
   ```

   A `ProcessValidationError` names the file and the offending field
   (`trigger` / `cron` / `check` / `params` / `target` / `dedup` / `sink` /
   `repository`). Fix and re-run until it boots clean.

2. **Dry run the end-to-end path without waiting a week.** Temporarily copy the
   process to a second file with `{"trigger": {"interval": "2m"}}` and
   `"dedup": "per-state"`, boot the harness, and watch the board. Delete the
   temp file afterwards. Alternatively submit one task by hand:

   ```sh
   harness submit --workflow coverage-gap --repo onpaj/Anela.Heblo \
     --data '{"title":"2026-W30 backend Catalog backend/src/.../GetProductHandler.cs cov=32.4 lines=181 run=1 sha=abc"}'
   ```

3. **Dry-run the filing.** Before the first real fire, point the finisher at a
   scratch repo, or temporarily change the `cov-dedup` → `cov-file-issue` edge
   to `"to": "end"` so the whole chain runs and you can inspect the drafted
   artifacts without opening real issues. Revert once satisfied.

4. Check `$H/failed/` after the first run. A task there means the chain broke;
   the board (`harness serve`) shows the reason and history.

---

## Known limits — accept these, do not try to engineer around them

**These are real gaps between the original routine and what the harness can
express. Tell Ondrej which ones bit, don't silently paper over them.**

1. **`MAX_ISSUES_PER_RUN` is approximate.** Nothing in the harness coordinates
   across a fan-out; each candidate is an independent task and there is no
   cross-task counter. The cap lives in the scanner and caps **candidates**, not
   **filed issues** — 10 candidates may yield 3 issues after qualification and
   dedup. If a hard "≤10 filed" is required, there is no mechanism for it.

2. **No aggregate run summary (original Step 7).** The harness reports per task
   — a board column, or one Slack line per report. Nothing joins a cadence's
   fan-out back into one document. The nearest substitute is having the scanner
   write its own summary to stderr / a log file, which is *not* the same thing.

3. **Dedup semantics.** `SourcePoller._seen` means *ever ingested*, and it is
   seeded from `inbox` / step queues / `done` / `failed` — **not** from
   `archived` or `healed` (`app.py:212-218`). So a bare `per-state` key on the
   file path fires once ever, then re-fires unpredictably once the task is
   archived and the harness restarts. That is why the scan line starts with the
   ISO week: it makes each week a fresh state, so the process re-scans weekly as
   intended. The *real* duplicate suppression is the `cov-dedup` step's search
   against open GitHub issues, exactly as the original routine did it.

4. **Worktree HEAD ≠ the CI run's `HEAD_SHA`.** The `cov-qualify` step reads
   source from a worktree branched off the registered clone's HEAD, not the SHA
   the coverage was measured at, so numbers may not match the code being read.
   The persona's "source not found in checkout → skip" path covers the worst
   case. There is no supported way to check out a specific SHA: `data.branch` is
   a *branch* override and its reuse path hard-resets to `origin/<branch>`
   (invariant #31), which breaks on a bare SHA.

5. **Worktrees accumulate forever.** Invariant #30: nothing under `src/harness`
   ever removes a worktree. Up to 10 new worktrees of the Anela.Heblo clone per
   week, permanently. Budget the disk, or plan an out-of-band cleanup that
   respects invariant #31 before deleting anything.

6. **`DRY_RUN` has no equivalent.** Use the Part G step 3 workaround (re-point
   the edge to `end`) instead.

---

## Appendix — exact schemas

Sourced from `drivers/fs_processes.py`, `drivers/fs_workflows.py`,
`drivers/fs_agents.py`. Do not guess field names; these are validated
fail-fast at startup.

### `processes/<name>.json`

| Key | Required | Value |
|---|---|---|
| `trigger` | yes | object with **exactly one** of `interval` (duration string) or `cron` (5-field, UTC). Optional `kind`, only `"schedule"` |
| `action` | yes | `{"check": "<registered name>", "params": {...}}` |
| `target` | yes | **exactly one key**: `{"workflow": "..."}` or `{"step": "..."}` — must name a served workflow or known step |
| `dedup` | no | `"per-interval"` (default) or `"per-state"` |
| `sink` | no | `{"kind": "none" \| "slack" \| "github"}` |
| `repository` | no | a name present in `repos.json` |

Registered check names: `always`, `disk-threshold`, `fs-files`, `command`, plus
`failed-tasks`, and — when the relevant credentials are set — `github-issues`,
`github-conflicts`, `jira-issues`.

### `workflows/<name>.json`

| Key | Required | Value |
|---|---|---|
| `name` | no | defaults to the filename stem |
| `start` | yes | the first step |
| `transitions` | yes | list of `{"from", "on", "to"}` plus optional `"hint"` (prompt-only string) |
| `descriptions` | no | `{step: "free text"}`, prompt-only |
| `finishers` | no | `{step: "kind"}` or `{step: {"kind": "...", ...config}}` — everything but `kind` becomes that binding's `config` |
| `maxParallel` | no | `{step: N}`, absent defaults to 1 |

`end` is reserved. Registered finisher kinds: `open-pr`, `verify`, plus
`open-issue` (after Part A) and `label-issue` (when `GITHUB_TOKEN` is set).

### `agents/<step>.json`

| Key | Required | Value |
|---|---|---|
| `prompt` | yes | persona only — no artifact/verdict boilerplate |
| `model` / `fallback_model` | no | model alias or exact id, `null` for the default |
| `allowed_tools` | no | list of tool names, joined into `--allowedTools` |
| `allowed_outcomes` | no | **only `"done"` / `"request_changes"`** — workflow-less fallback, not the live vocabulary |
| `timeout` | no | positive number of seconds, `null` for the default |

---

## Deliverables checklist

- [ ] Part A committed to `main` of `onpaj/harness_v2`, `pytest -q` green
- [ ] `$H/bin/coverage-gap-scan.py` executable, exits 0, verified standalone
- [ ] `$H/workflows/coverage-gap.json`
- [ ] `$H/agents/cov-qualify.json`, `$H/agents/cov-dedup.json`
- [ ] `$H/processes/coverage-gap.json`
- [ ] `harness run` boots clean with the workflow served
- [ ] one task driven end-to-end without opening a junk issue
- [ ] service reinstalled with the new `--workflow` flags, if applicable
- [ ] report back which of the six known limits actually mattered in practice
