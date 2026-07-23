# Architecture — unify admin UI with the dashboard design system

## Alignment with existing patterns and integration points

Verified directly against the tree, not just the plan/design prose (this step
has `Bash`; plan and design do not, which is why both left the branch-sync
question open rather than resolved — see the decision below).

- **The worktree/branch-staleness blocker plan-01.md and design-01.md both
  flagged is real and still unresolved on disk.** `git log --oneline
  0c8027b..HEAD` shows this task's branch has exactly two commits
  (`plan-01.md`, `design-01.md`), no code changes; `git diff --stat
  0c8027b..origin/main` confirms `src/harness/api/static/app.css`,
  `_nav.html`, `board.html`, `_columns.html`, `_task.html`, and all four
  `admin/*.html` templates plus `admin/_update_result.html` genuinely do not
  exist in this worktree yet (`ls src/harness/api/static/` and `ls .../
  templates/admin/` come back empty of `app.css`/`admin/`). `origin/main` is
  46 commits ahead, and PR #65/#69 (the mobile-first redesign, merged and
  conflict-resolved) is the commit range that introduced every file this task
  touches. This is a hard blocker for `development`, not a style preference —
  none of the files FR-2/FR-3 name are addressable until the branch is synced.
- **The merge is clean — verified, not assumed.** `git merge-tree
  $(git merge-base HEAD origin/main) HEAD origin/main` produces zero
  `CONFLICT`/`<<<<<<<` markers. This checks out: the task branch's only two
  commits add two new files under `.artifacts/tsk_35fef287ee384ce0/` that
  `origin/main` never touches, while `origin/main`'s 46 commits touch
  `src/harness/api/**` and `docs/**` paths this task branch has never
  modified. There is no overlapping edit for git to conflict on.
- **This repo already has a precedent for exactly this situation** — commit
  `e255cc1` ("Have the reviewer agent merge main into the working branch and
  return request_changes when the merge conflicts (#51)") gave the `review`
  persona (`cli.py:213-239`) a first-class "sync with base branch" procedure:
  `git fetch origin` → `git merge origin/<base>` → on conflict, `git merge
  --abort` and report `request_changes`; on success, fall through unchanged.
  That precedent establishes two things this task should reuse rather than
  re-derive: (1) **merge, not rebase**, is this project's convention for
  bringing a stale task branch up to date with `main` — rebasing would rewrite
  this task's own two commits and isn't what the only prior instance of this
  problem did; (2) the sync happens **on the current branch**, no new branch,
  no worktree — which is exactly what `_DEVELOPMENT_PERSONA` (`cli.py:191-211`)
  already forbids doing anyway ("DO NOT create a git worktree, and DO NOT
  create or switch branches"). A `git merge origin/main` while staying on
  `harness/tsk_35fef287ee384ce0` violates neither rule.
- **`development`'s allowed outcome is only `done`** (`DEFAULT_DEFINITION`,
  `cli.py:59-71`: `development --done--> review` is the only edge out of
  `development`). Unlike `review`, `development` has no `request_changes`
  escape hatch for itself and, more importantly, *does* have `Write`/`Edit`
  tools (`cli.py:247-250`) that `review` lacks. That asymmetry is the reason
  the merge belongs in `development`, not here: if this step performed the
  merge and something unexpected fell out of the 46-commit gap (unlikely,
  verified conflict-free, but this step cannot fix code even if something did),
  there is no clean way for `architecture` to hand off a partially-merged
  worktree — whereas `development` can merge, notice a problem, and fix it in
  the same turn.
- **Design's `.panel`-over-`.card` override is verified correct, not just
  plausible.** Read `app.css` (`origin/main`) directly: `.card`
  (lines 180-201) has `cursor: pointer` (implied by `.card:active { transform:
  scale(.985) }` at 187) and a status-colored 4px `::before` left edge driven
  by `.is-working`/`.is-done`/`.is-changes`/`.is-failed` modifiers
  (192-195) — board-task-specific state that a static admin form has no
  equivalent for. Design's new `.panel` (static container, no press effect, no
  status bar) is the right call; confirmed, not merely trusted.
- **Design's exact markup for all four templates was checked against the real
  `origin/main` file contents** (`git show origin/main:<path>`), not just
  design's paraphrase of them: `agent_form.html`'s field names, POST targets,
  and Jinja context variables (`errors`, `checked_outcomes`, `is_new`,
  `saved`, `name`, `prompt`, `model`, `fallback_model`, `allowed_tools`) match
  design-01.md §3.4 exactly, field for field. Same confirmed for `_nav.html`
  (already the shared appbar/tabbar, unchanged, `active` state is
  client-side JS matching `location.pathname` — no server-side "current
  section" variable to preserve) and `board.html`'s shell
  (`_nav.html` include → `<main class="page">` → `.page-header` — identical
  structure design proposes reusing for the admin pages).
- **No existing test locks in the old inline styles or would need updating for
  a class-name change.** Checked `tests/test_api_agents.py` and
  `tests/test_api_workflows.py`'s HTML-admin tests (`origin/main`) — they
  assert on visible text (`"No agents defined yet."`, a saved name, a form
  value) and route reachability, never on a CSS class or the presence of a
  `<style>` tag. FR-3's "no functional change" constraint is safe to execute
  literally: swap markup/classes freely, touch no field name or POST target.
- **`docs/design/ui-guide.md` will not be swept into the generated docs
  site.** `discover_docs` (`src/harness_docs_site/corpus.py:85-96`) only scans
  four fixed locations (`docs/adr/*.md`, `docs/superpowers/specs/*.md`,
  `docs/superpowers/plans/*.md`, `README.md`/`CLAUDE.md`) — a new
  `docs/design/` directory is invisible to it. No change to `corpus.py` is in
  scope or needed; `test_docs_site.py`'s assertions run against a synthetic
  fixture tree in `tmp_path`, not this repo's real `docs/`, so they are
  unaffected either way.
- **`admin/_update_result.html`'s inclusion in scope is correct.** Read the
  real file: three inline hex colors (`#bf2600`/`#006644`/`#5e6c84`) exactly as
  design-01.md §3.6 describes, and it is included by `_nav.html` on *every*
  page (board included via the `#update-result` target), so it is a live,
  visible inconsistency today, not a hypothetical one. Folding it in costs one
  template line and directly serves the task's own goal ("no visual regression
  on the board" cuts both ways — this is a pre-existing regression on the
  board that this task is well-positioned to fix in passing).

## Proposed architecture

**Decision: this is a template/CSS consolidation plus one git-hygiene
prerequisite — no new port, driver, route, or model field.** Design-01.md's
CSS additions and template markup are sound as specified; nothing here revises
them. The one substantive architectural decision this step adds is closing
plan/design's open "how does the branch get synced" question with a concrete,
verified answer, because that question blocks `development` from writing a
single line otherwise.

Options considered for the sync:

1. **Architecture performs the merge now**, since this step has `Bash` and
   plan/design don't. Rejected: verified conflict-free, but this step has no
   `Write`/`Edit` to react if the merge surfaces something the dry-run can't
   see (e.g. a `pip install -e ".[dev]"` needing a re-run for a new dependency
   introduced somewhere in 46 commits — unrelated to this task's files but
   part of the same merge). `development` is the step equipped to merge *and*
   fix fallout in the same turn; splitting "merge" and "fix" across two steps
   with only `architecture`→`development` (`done`-only, no back-edge) between
   them would leave no clean way to signal a problem back.
2. **Rebase the task branch onto `origin/main`.** Rejected: rewrites this
   task's own `plan-01.md`/`design-01.md` commits, and isn't what the one
   prior instance of "task branch behind main" (`review`'s merge procedure,
   commit `e255cc1`) does. Consistency with that precedent is worth more here
   than the marginally cleaner history a rebase would give a two-commit
   branch.
3. **Merge `origin/main` into the task branch as `development`'s first
   action, on the current branch, no new branch/worktree** (chosen). Matches
   the `review`-step precedent's mechanism, respects
   `_DEVELOPMENT_PERSONA`'s existing "don't create/switch branches" rule
   (merging isn't switching), and puts the action in the hands of the step
   that can both perform it and immediately act on whatever it reveals.

Rationale: the actual "decision" here — sync, then implement — is not a
routing decision the dispatcher/router need ever see (`development`'s only
edge stays `done → review`, unchanged), it is purely a sequencing instruction
inside one step's own work, exactly like the `review` persona's existing
sync-then-review sequencing. No workflow-definition change, no new outcome
value, no new field on `AgentSpec`.

## Implementation guidance

**Step 0 of `development`, before any file is touched — sync the branch:**

```sh
git fetch origin
git merge origin/main
```

(No `--no-edit` flag mandated; either is fine since this repo's git identity
for automated commits is set via `_IDENTITY` env in `GitWorkspaceHandle` for
the harness's own commit step — the merge commit here is created directly by
the agent's shell, same as the existing `review` precedent, and doesn't need
to match that identity.) Contract for this step, stated precisely so
`development` doesn't have to re-derive it:

- Verified conflict-free in advance (see above) — this is not expected to
  produce any `<<<<<<<` markers. If it unexpectedly does anyway (state
  drifted between this assessment and the actual run), resolve it directly
  (favor `origin/main`'s version for every file this task doesn't intend to
  change; keep both sides' additions where they don't overlap) — `development`
  has `Write`/`Edit` for exactly this contingency, unlike `review`'s
  abort-only path.
- After the merge, confirm the expected files now exist:
  `src/harness/api/static/app.css`, `src/harness/api/templates/_nav.html`,
  `src/harness/api/templates/admin/{agent_form,workflow_editor,agents_list,
  workflows_list,_update_result}.html`. If any are still missing, the merge
  didn't do what this assessment expects — stop and report via the artifact
  rather than improvising a different sync mechanism.
- Do not push mid-step, do not open a PR, do not amend history beyond this one
  merge commit — landing remains the dedicated step's job (invariant 12).

**Everything after the sync follows design-01.md as written — no
deviation:**

- Add the new `app.css` sections from design-01.md §3.2 (`.panel`/`.panel.wide`,
  `.field`/`.field-error`, input/textarea rules incl. `textarea.editor`,
  `.outcomes`, `.actions`, `.banner` + 3 variants, `.btn.danger`,
  `.page-header__action`, `.update-result-ok`/`.update-result-error`),
  appended after the file's existing `Desktop`/`reduced-motion` blocks
  (verified: those are the last two sections in the current 371-line file, so
  "append at end of file" is the same instruction as design's "after Desktop/
  reduced-motion"). Purely additive — no existing selector's declaration
  block may change (this is what makes FR-4's "board unchanged" true by
  construction, not by manual comparison alone).
- Replace the four templates' markup verbatim per design-01.md §3.3-§3.5,
  and `admin/_update_result.html` per §3.6. Field names, `id`/`name`
  attributes, POST `action` targets, and Jinja variables referenced must be
  byte-identical to what's in the templates today (confirmed above) — only
  the surrounding markup/classes/`<style>` blocks change.
- Write `docs/design/ui-guide.md` from design-01.md's outline (its final
  section, "outline for the dev step to author"), sourcing token values from
  the actual `app.css` `:root`/dark-mode blocks (`app.css:12-66` post-merge)
  rather than retyping design-01.md's already-accurate table — the doc should
  cite the real file, not the design artifact.

**Verification `development` should run before finishing** (mirrors plan-01.md
step 6/7, made concrete):

- `grep -n "<style" src/harness/api/templates/admin/*.html` → no output.
- `grep -nE "#[0-9a-fA-F]{3,6}" src/harness/api/templates/admin/*.html` → no
  output (confirms no hardcoded hex survives).
- `git diff origin/main -- src/harness/api/static/app.css` shows insertions
  only in the post-merge diff (no line inside an existing selector's braces is
  modified) — this is the mechanical check for FR-4.
- Run the existing suite (`.venv/bin/pytest -q`); `tests/test_api_agents.py`
  and `tests/test_api_workflows.py`'s HTML-admin tests are expected to pass
  unmodified (verified above they assert on text/routes, not markup shape).

## Risks and mitigations

- **Risk: the merge is not actually conflict-free by the time `development`
  runs**, if `origin/main` moves further between this assessment and that
  step. Mitigation: `development` has `Write`/`Edit` and is explicitly told
  above to resolve rather than abort — unlike the `review` precedent, there is
  no downstream step here that re-attempts on `request_changes`, so silently
  giving up is not an option; the persona's own "read the review in full"
  fallback doesn't apply on a first pass anyway.
- **Risk: the 46-commit gap includes unrelated dependency/tooling changes**
  (new pip deps, CI config, etc.) that this task doesn't need but that the
  merge pulls in regardless. Mitigation: none needed at the architecture
  level — a task branch merging forward to current `main` is expected to pick
  up unrelated upstream progress; that's the point of syncing rather than
  cherry-picking only the admin-UI-relevant commits (cherry-picking would
  diverge from how every other multi-commit-behind branch in this project is
  expected to reconcile, per the `review`-step precedent, which also does a
  full merge, not a partial one).
- **Risk: someone re-reads `docs/design/ui-guide.md` as a place to add new
  tokens later and treats it as the source of truth instead of `app.css`.**
  Mitigation: the guide must state explicitly (already planned in design's
  outline, item 7) that `app.css` is authoritative and the guide is a reading
  aid — restated here so `development` doesn't drop that line while writing
  the doc.
- **Risk: `.banner.error`'s `white-space: pre-wrap` (design-01.md §3.2)
  combined with the workflow editor's multi-banner case (error + success +
  N warnings, §3.5) produces a visually cluttered stack.** Mitigation:
  none required pre-implementation — this is a real but minor visual
  polish concern, not a correctness one, and the plan/design's non-functional
  scope explicitly excludes new JS/behavior; if it reads poorly once rendered,
  a follow-up spacing tweak (`.banner + .banner { margin-top: 8px }`, additive)
  is a one-line fix within FR-2's own additive-only constraint, not a
  redesign.

## Prerequisites before implementation begins

**One, load-bearing: `development` must run `git fetch origin && git merge
origin/main` on the current branch as its first action**, before touching any
file the plan/design reference — verified conflict-free above, with resolution
guidance if that verification turns out to be stale. Nothing else blocks
`development`: `Write`/`Edit`/`Bash` access is already granted for this step
(`cli.py:247-250`), design-01.md's CSS and markup are verified accurate against
the real (post-merge) file contents, and no existing test needs updating for
the restyle itself.
