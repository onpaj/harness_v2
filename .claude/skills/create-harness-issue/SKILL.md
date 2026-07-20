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

4. **Preview and confirm.** Creating an issue is outward-facing — show the user
   the resolved repo, the title, the rendered body, and the label, and get an
   explicit go-ahead before creating. Do not add other labels unless asked;
   `harness:queued` / `harness:pr-open` / `harness:failed` are the harness's to
   set, not yours.

5. **Create it.** Use a temp file for the body so multi-line markdown survives:
   ```sh
   gh issue create --repo <repo> \
     --title "<imperative title>" \
     --body-file <tmpfile> \
     --label "harness:todo"
   ```

6. **Report the issue URL** that `gh` prints back.

## Common mistakes

- **Vague title.** The title *is* the agent's instruction; a headline like
  "Bug in parser" leaves the agent with no task. Make it a full imperative.
- **Skipping the label existence check.** A missing `harness:todo` label makes
  `gh issue create` error out; step 2 prevents it.
- **Adding harness state labels** (`harness:queued`, etc.). The poller owns
  those; setting them by hand corrupts the state machine.
- **Hardcoding the repo.** Keep it repo-agnostic — resolve it per invocation.
- **Creating without confirmation.** Outward-facing; always preview first.
