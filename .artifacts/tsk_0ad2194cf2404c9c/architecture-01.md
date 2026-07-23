# Architecture: CI gate that blocks merge on failing tests

## Verdict up front

This task ships **no application code**. Re-verified live against this
worktree and the GitHub API (matches plan-01.md and design-01.md exactly):

- `.github/workflows/ci.yml` already triggers on `pull_request` (unfiltered,
  so it covers PRs into `main`), `push: [main]`, and `workflow_call`; job
  `test` installs Python 3.11 via `uv`, runs `uv pip install -e ".[dev]"`,
  then `uv run pytest -q`. No masking of failures.
- `release.yml` already reuses `ci.yml` via `workflow_call` and gates
  `release` on `needs: test`. Untouched by this task, as instructed.
- `GET /repos/onpaj/harness_v2/branches/main/protection` → still `404 Branch
  not protected`, confirmed just now. `main` has zero protection today.

So FR-1/2/3/5 are satisfied by code that already exists. The only functional
gap is FR-4 — a **branch-protection setting**, applied through the GitHub
API/UI, not through a diff. The dev step's job is to apply that setting
(with explicit confirmation, per the risk notes below) and leave a doc trail,
not to write a workflow file.

## Alignment with existing patterns and integration points

This repo's architecture (`ports/`, `dispatcher.py`, `consumer.py`,
`behaviors/`, `drivers/`) governs the harness's own runtime. This task is
orthogonal to all of it:

- It doesn't touch `models`, `router`, `dispatcher`, `consumer`,
  `behaviors/`, or any `drivers/` module. None of the 23 invariants in
  `CLAUDE.md` are implicated — there's no dispatcher branch, no port, no
  driver swap here.
- `tests/test_architecture.py` (the guard for those invariants) is
  unaffected: it inspects this repo's own source, not `.github/workflows/`.
- The only "integration point" is GitHub itself: the `test` job name in
  `ci.yml` is the string that the branch-protection API call must reference
  byte-for-byte. That coupling is implicit (a string match, not an import),
  so it's worth stating plainly rather than leaving it to be rediscovered:
  **renaming the `test` job in `ci.yml` in the future silently breaks the
  branch-protection rule** (GitHub keeps requiring a check that will never
  report again, and the merge button locks forever). Anyone touching
  `ci.yml`'s job name later needs to know to update protection too — this is
  the one thing worth writing down for future readers, since git diffs to
  `ci.yml` won't reveal it.

## Proposed architecture

There are two artifacts, and only one is a "component" in the code sense:

### 1. `.github/workflows/ci.yml` — no change

Confirmed by direct read (above) to already satisfy every acceptance
criterion that can be satisfied in code (FR-1, FR-2, FR-3, FR-5). The dev
step should do a final read-through immediately before finishing (in case
this worktree drifts from what's described here) but should not edit the
file absent a genuine discrepancy. If a discrepancy *is* found, the fix
belongs in `ci.yml` directly — no new workflow file, no duplicate job. A
second workflow that also runs `pytest -q` on `pull_request` would produce a
second, differently-named check and fragment the "one gate" story; don't
create one.

### 2. Branch protection on `main` — the actual deliverable

Options considered:

- **(a) Add a `branches: [main]` filter to the existing `pull_request:`
  trigger.** Rejected — this doesn't gate merging at all; a status check
  existing is not the same as GitHub *requiring* it before allowing merge.
  Branch protection is the only mechanism that blocks the merge button.
  Filtering the trigger would also needlessly narrow coverage (PRs against
  other branches, if any ever exist, would stop getting tested).
- **(b) A second workflow file dedicated to "the gate."** Rejected — `ci.yml`
  already *is* the gate's test run; a second file duplicates CI minutes and
  invites the two checks to drift apart. One workflow, one job, one required
  context.
- **(c) Branch-protection API call requiring the existing `test` check.**
  Chosen. It's the only GitHub-native mechanism that turns "a check exists"
  into "merge is blocked until it's green," and it requires zero code
  changes since the check already exists and already behaves correctly.

Chosen shape, exactly as verified in design-01.md (context name confirmed
against a real PR's check-runs API, not assumed):

```
gh api repos/onpaj/harness_v2/branches/main/protection \
  -X PUT \
  -H "Accept: application/vnd.github+json" \
  -f required_status_checks[strict]=true \
  -f 'required_status_checks[contexts][]=test' \
  -F enforce_admins=true \
  -f required_pull_request_reviews='' \
  -f restrictions=''
```

- `contexts: ["test"]` — verified live: a PR's check run for the
  directly-triggered `pull_request` event is named plainly `test`, not
  `test / test` (that composite name is specific to `main`'s
  `workflow_call`-reused run under `release.yml` and never appears on a PR).
  Using the wrong string here is the single biggest failure mode: GitHub
  would require a check that never posts, and every PR would show merge
  blocked forever. This must be verified against a live PR before applying,
  not assumed from workflow YAML alone.
- `strict: true` — require the PR branch to be up to date with `main` before
  merge. This is a genuine design choice (not free): it means a green PR
  can go stale and require a re-run after `main` moves. Recommended because
  it matches "all tests must be valid and passing" against the code that
  will actually land, but it wasn't asked for explicitly — the dev step
  should call it out rather than silently bake it in.
- `enforce_admins: true` — recommended, matches "cannot be merged unless...
  passes" with no stated admin exception, but this is the highest-blast-radius
  choice in this task (it removes the admin bypass on a shared repo) and
  must get an explicit human go-ahead before the mutation runs, not just a
  mention in an artifact.
- `required_pull_request_reviews=''` / `restrictions=''` — required by the
  GitHub API's replace-whole-object semantics on `PUT`; clearing them is not
  a request to add a review-count gate, only a mechanical consequence of
  using `PUT` instead of a narrower PATCH-like endpoint.

## Implementation guidance

Where the "code" (such as it is) belongs:

- **No new source files, no new tests, no new ports/drivers.** This task
  produces zero lines under `harness/`.
- **The mutation itself is the deliverable, run from the shell, not
  committed.** It is not idempotent-by-git — nothing in `git log` will show
  it happened. Treat it like the "executing actions with care" guidance in
  `CLAUDE.md` requires: surface the exact command, get an explicit go-ahead
  (particularly for `enforce_admins`), then run it — don't fold it silently
  into an unrelated commit or run it without confirmation because it's
  "just a setting."
- **Documentation is the one piece of actual content to write**, and it
  belongs in `CLAUDE.md` (this repo's existing single orientation doc — no
  precedent here for a separate `docs/` note on a repo setting; `docs/` in
  this repo is reserved for phase specs/plans, not operational notes). Add a
  short paragraph near the CI/release section stating: `main` requires the
  `test` status check via branch protection (a repo setting, not workflow
  code); the check is produced by `.github/workflows/ci.yml`; renaming that
  job breaks the protection rule silently and must be paired with a
  protection update. This is the one artifact `git diff` and code review can
  actually see and confirm.
- **Verification is manual/API-driven, not a pytest addition.** There is no
  meaningful unit test for "GitHub blocks a merge button." The verification
  loop is: apply → `GET .../protection` returns `200` with `contexts:
  ["test"]` → (optionally) confirm on a real PR that a red `test` check
  shows "Required statuses must pass" and the merge button is disabled.

## Data flow

Trivial, stated for completeness since the task asks for it:

1. Contributor opens/pushes a PR targeting `main`.
2. GitHub fires `ci.yml`'s `pull_request` trigger → job `test` runs → posts a
   check run named `test` on the PR's head SHA.
3. Branch protection on `main` (once applied) consults that check's
   conclusion before allowing the merge button to activate — success unlocks
   it, anything else (failure, pending, missing) blocks it.
4. Separately, on push to `main`, `release.yml` reuses the same `ci.yml` job
   (as `test / test`) and gates `release` on it — unaffected by this task,
   shown only to make clear the two paths don't collide over the same
   context name (PR path posts `test`; the `main`-push/release path posts a
   differently-named `test / test`, so requiring `test` on branch protection
   never accidentally waits on the release path).

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Wrong required-check context string (e.g. requiring `test / test` or `CI / test` instead of `test`) permanently blocks all merges on `main` | Verified live against a real PR's check-runs API in design-01.md and re-confirmed here conceptually; dev step should do one more live check-run read immediately before the `PUT`, since GitHub's naming is sensitive to exact trigger/caller shape |
| `enforce_admins: true` removes the admin bypass repo-wide, a hard-to-reverse behavioral change | Do not apply silently — surface the exact payload and get explicit confirmation before running the mutation, per `CLAUDE.md`'s risk-handling guidance |
| Branch protection is invisible in `git diff`, so a future contributor renaming the `test` job breaks the gate without any visible signal | Add the `CLAUDE.md` note described above; it's the only durable record once this artifact folder is gone |
| A second, competing CI workflow gets added later to "fix" a perceived gap, duplicating `test` | None needed beyond this document — `ci.yml` is explicitly the single source of the gate; no structural safeguard prevents duplication, but nothing in this task creates that risk today |

## Prerequisites before implementation (dev step)

1. Repo-admin-capable GitHub credential for the `PUT` call (already
   confirmed available: the acting identity has `ADMIN` on
   `onpaj/harness_v2`).
2. Explicit human confirmation of `enforce_admins` and `strict` before
   running the mutation — do not default silently.
3. One live re-check of the required-check context name against a current
   PR's check-runs, since this is the single point where a wrong string has
   an outsized, hard-to-notice blast radius.

No other prerequisites — no dependency bump, no test scaffolding, no new
port/driver wiring.

```json
{"outcome": "done", "summary": "No new code/components needed: ci.yml already satisfies FR-1/2/3/5 (verified live). The only deliverable is a branch-protection PUT requiring the 'test' status check on main (FR-4), to be applied with explicit human confirmation on enforce_admins/strict, plus a CLAUDE.md note documenting the setting since it's invisible in git diff."}
```
