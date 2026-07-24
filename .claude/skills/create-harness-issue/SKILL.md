---
name: create-harness-issue
description: Use when the user wants to queue work for the harness — turning an idea, request, plan, or bug into a GitHub issue the harness picks up. Triggers on "create a harness task", "queue this for the harness", "make a harness:todo issue", "new harness issue". Repo-agnostic; works in any repo.
---

# Create Harness Issue

## Overview

The harness ingests GitHub issues labeled **`harness:todo`**: its poller claims
each such issue, turns it into a task, and runs it through the workflow. This
skill creates one of those issues in the correct format, in whatever repo the
user names.

**The contract (do not guess it — it is fixed by the harness):**

| Issue field | Becomes | Notes |
|---|---|---|
| Label `harness:todo` | The claim signal | Without it the poller never sees the issue |
| Title | The task's **instruction** — what the agent acts on | Must be self-contained and imperative |
| Body | Task context, stored + shown to humans | Not injected into the agent prompt today; write it for humans and future use |

Because the **title drives the agent**, put the actionable request in the title,
not just a headline. "Add rate limiting to the login endpoint" — not "Rate limiting".

## Workflow

1. **Resolve the target repo** (repo-agnostic — never hardcode):
   - Explicit `owner/repo` from the user wins.
   - Else infer from the current directory: `gh repo view --json nameWithOwner -q .nameWithOwner`.
   - If neither resolves, ask which repo.

2. **Ensure the `harness:todo` label exists** (`gh issue create --label` fails on
   a missing label):
   ```sh
   gh label list --repo <repo> | grep -q '^harness:todo' \
     || gh label create "harness:todo" --repo <repo> \
          --color BFD4F2 --description "Queued for the harness"
   ```

3. **Draft title and body.**
   - **Title**: one imperative sentence, self-contained (see above).
   - **Body**: this template.
     ```markdown
     ## Context
     Why this task exists / background.

     ## Goal
     What "done" looks like.

     ## Acceptance criteria
     - [ ] ...
     - [ ] ...

     ## Notes
     Links, constraints, out-of-scope. Optional.
     ```

4. **Check completeness, then create or ask.** Decide whether the draft is
   ready to act on with no further input, using three checks:
   - `repo_resolved` — true iff an explicit `owner/repo` was given, or `gh repo
     view` unambiguously inferred one; false if neither resolved.
   - `title_concrete` — true iff the title is a self-contained imperative
     instruction an agent could act on with no further clarification (e.g.
     "Add rate limiting to the login endpoint"); false if it's a bare headline
     the skill would have to expand into an actual ask on its own (e.g. "Rate
     limiting", "Bug in parser").
   - `body_substantive` — true iff the user's request contains real content
     for Context/Goal/Acceptance beyond the template headers — i.e. the body
     is transcribing/organizing what the user said, not inventing it; false
     if filling the template would mean fabricating context/goal/acceptance
     criteria wholesale from a one-line ask.

   If all three are true, proceed straight to step 5 — no preview, no pause.
   If any is false, ask the user about that specific missing/ambiguous piece
   (not a generic "does this look right?"), then continue once answered.

   Do not add other labels unless asked; `harness:queued` / `harness:pr-open`
   / `harness:failed` are the harness's to set, not yours.

5. **Create it.** Use a temp file for the body so multi-line markdown survives:
   ```sh
   gh issue create --repo <repo> \
     --title "<imperative title>" \
     --body-file <tmpfile> \
     --label "harness:todo"
   ```

6. **Report what was created.** Alongside the URL that `gh` prints back, show
   the repo, the title, a short body summary (Context/Goal in a line or two —
   not a full re-print), and the label. This applies the same way whether the
   issue was created directly (step 4 all-true) or after an ask (step 4
   resolved by the user's answer) — it's what lets the user catch a mistake
   even though the direct path has no pre-create pause.

## Common mistakes

- **Vague title.** The title *is* the agent's instruction; a headline like
  "Bug in parser" leaves the agent with no task. Make it a full imperative.
- **Skipping the label existence check.** A missing `harness:todo` label makes
  `gh issue create` error out; step 2 prevents it.
- **Adding harness state labels** (`harness:queued`, etc.). The poller owns
  those; setting them by hand corrupts the state machine.
- **Hardcoding the repo.** Keep it repo-agnostic — resolve it per invocation.
- **Creating directly on an ambiguous request.** If the repo isn't resolved,
  the title is a bare headline, or the body would have to be invented from
  nothing, ask about that specific gap before creating — don't skip the ask.
- **Asking anyway when the request is already complete.** If the repo,
  title, and body substance are all clear, create the issue directly; don't
  hold it up with a confirmation the user didn't need.
