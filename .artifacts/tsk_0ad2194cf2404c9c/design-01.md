# Design: CI gate that blocks merge on failing tests

## Scope note

There is no user interface for this task. The only surfaces involved are a
GitHub Actions workflow (already implemented) and the GitHub branch-protection
API/UI (a repo setting). The UX/UI section is omitted accordingly.

## Verified state (as of this design step)

Re-checked the plan's claims directly against the repo and the GitHub API
rather than taking them on faith:

- `.github/workflows/ci.yml` triggers on `pull_request:` (no `branches:`
  filter → fires for PRs against any base, including `main`), `push:
  [main]`, and `workflow_call`. Job `test` sets up Python 3.11 via `uv`,
  installs with `uv pip install -e ".[dev]"`, configures a git identity, runs
  `uv run pytest -q`, then does a build/package-contents check.
- `release.yml` consumes `ci.yml` via `workflow_call` (`jobs.test.uses:
  ./.github/workflows/ci.yml`) and gates `release` on `needs: test`. Untouched
  by this task.
- `GET /repos/onpaj/harness_v2/branches/main/protection` → `404 Branch not
  protected`. Confirmed live: `main` currently has zero branch protection, so
  nothing blocks a merge regardless of check status.
- **Resolved the plan's open question about the check-run context name** by
  inspecting a real PR's check runs (`gh api
  repos/onpaj/harness_v2/pulls/58/commits` → head SHA → `.../check-runs`):
  for a PR, the check run is named plainly **`test`** (the job name under the
  directly-triggered `pull_request` event). The `test / test` composite name
  only appears on `main`'s push-triggered run, where `release.yml` reuses
  `ci.yml` via `workflow_call` and GitHub prefixes it with the caller job
  name. Since PRs never go through `release.yml`, `test / test` is
  irrelevant to branch protection — the context to require is **`test`**.

This confirms FR-1/2/3/5 need no code change. The only remaining work is
FR-4: registering `test` as a required status check on `main`.

## Component design

No new modules, classes, or ports. This task doesn't touch any layer in the
module map (`models`, `router`, `dispatcher`, `consumer`, `behaviors/`,
`drivers/`, `api/`) — it's a CI-workflow/repo-setting change, orthogonal to
the harness's own architecture. Two "components," neither of which is
application code:

### 1. `.github/workflows/ci.yml` (unchanged)

Already satisfies FR-1/2/3/5 as verified above. No diff planned. The dev step
should still re-read it once more before finishing, in case it drifts before
this design is acted on, but no edit is anticipated.

### 2. Branch-protection configuration on `main` (the actual deliverable)

A one-time `gh api` mutation (or an equivalent manual step in GitHub
Settings → Branches), not a code change:

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

Notes carried over from the plan, now with the context name confirmed:

- `required_status_checks.contexts = ["test"]` — the exact, now-verified
  check-run name for the PR-triggered job.
- `strict=true` ("require branches to be up to date before merging") is a
  reasonable default matching "all tests must be valid and passing" against
  the code actually being merged, but wasn't asked for explicitly — call it
  out at apply time rather than silently assume it if the dev step wants a
  narrower change.
- `enforce_admins` — the plan flagged this as an open question. Recommended
  `true` (matches "cannot be merged unless... passes," no stated exception
  for admins), but this is a repo-wide, hard-to-reverse behavioral change and
  should be confirmed explicitly with Ondrej before the dev step runs the
  mutation, not assumed silently.
- `required_pull_request_reviews=''` / `restrictions=''` clear those
  sub-resources rather than leaving them unset (the GitHub API requires the
  full protection object on `PUT`); this task does not intend to add a
  review-count requirement, only the status check.

Given the "executing actions with care" guidance (this mutates shared,
hard-to-reverse repo state visible to every future PR), the dev step should
either: (a) run this after an explicit go-ahead in the conversation, or (b)
hand the exact command to Ondrej to run himself. Either is acceptable; silent
execution is not.

### 3. Documentation update (small, in scope)

Branch protection is invisible in `git diff` — a future reader inspecting the
repo's history won't see why merges are blocked. Add a short paragraph to
`CLAUDE.md` (or a new `docs/` note, whichever the dev step judges more
discoverable) stating: `main` requires the `test` status check via branch
protection (not workflow code), and pointing at `ci.yml` as the source of
that check. This is documentation, not a "developer task" — it's a design
decision that the setting must be discoverable outside GitHub's UI.

## Data / interface shapes

No application data model changes. The only "schema" involved is the GitHub
REST payload for branch protection, already reflected in the `gh api` call
above. For completeness, the shape being read/written:

**Read (verification, already exercised above):**
```
GET /repos/onpaj/harness_v2/branches/main/protection
→ 404 { "message": "Branch not protected", ... }   (current state)
→ 200 { "required_status_checks": { "strict": bool, "contexts": [string] },
        "enforce_admins": { "enabled": bool }, ... }   (post-change state)
```

**Write:**
```
PUT /repos/onpaj/harness_v2/branches/main/protection
Body: {
  "required_status_checks": { "strict": true, "contexts": ["test"] },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
```

**Check-run context name** (the string that binds the workflow to the
protection rule): `test` — the job id in `ci.yml`, as surfaced on a
directly `pull_request`-triggered run. This is a GitHub-assigned string, not
something this repo's code defines; it changes only if the job in `ci.yml`
is renamed.

## Verification plan (for the dev/review steps, not implementation tasks)

1. Apply the branch-protection mutation.
2. `GET .../branches/main/protection` returns 200 with `contexts: ["test"]`.
3. Open a scratch PR with a deliberately failing test → PR UI shows the merge
   button blocked ("Required statuses must pass") while `test` is red.
4. Fix the test → check turns green → merge unblocks.
5. Confirm the CI log for that PR shows `tests/test_smoke_claude.py` as
   skipped, not passed/absent (FR-5 sanity check — no code change expected
   here, but worth a look since it's cheap).
