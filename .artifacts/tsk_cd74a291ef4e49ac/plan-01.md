# Plan — include the issue body in every agent's prompt

## Summary

`GithubTaskSource.poll()` stamps both `title` and `body` onto `task.data`
(`src/harness/drivers/github_source.py:88-90`), but `compose_prompt`'s helper
`_request_of` (`src/harness/behaviors/agent.py:115-120`) only ever reads one of
`request` / `title` / `summary` and returns early — it never looks at `body`. The
issue body a maintainer wrote (repro steps, acceptance criteria, links) is
captured on ingestion and then silently dropped before it reaches any of the
step agents (`plan`, `design`, `architect`, `develop`, `review`, …). Every agent
sees only the one-line title.

## Context

Phase 4 (`GithubTaskSource`) added `body` as part of ingesting an issue into
`task.data`, but the phase-3 prompt builder was never updated to match — the two
were written in different phases and nobody wired them together. This is a pure
bug fix: no new capability, just closing the gap so the data the source already
collects is actually used. It directly affects prompt quality/correctness for
every GitHub-originated task, which is presumably the primary path going
forward.

## Functional requirements

**FR-1 — `compose_prompt` includes the issue body when present.**
When `task.data` contains a non-empty `body` string, the composed prompt
contains that body text, in addition to the title/request line.
- Acceptance: `compose_prompt(task, ...)` where
  `task.data = {"title": "add rate limiting", "body": "Repro: ...\nAC: ..."}`
  produces a string containing both `"add rate limiting"` and
  `"Repro: ..."`/`"AC: ..."`.

**FR-2 — Backward compatible when there is no body.**
Tasks submitted via `harness submit` (manual `data={"request": "..."}`, no
`body` key) or any task whose `body` is absent/blank produce a prompt
unchanged from today's shape — no empty "Body:" section, no stray blank block.
- Acceptance: existing test
  `test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes` (built
  from `make_task()`, which sets `data={"request": "add rate limiting"}`, no
  `body`) keeps passing unmodified.

**FR-3 — Blank/whitespace-only body is treated as absent.**
GitHub issues can have an empty body (`issue.body` may be `""` or `None` at the
client layer — `Issue.body: str`, but ingestion doesn't guarantee non-empty).
A body that is empty or whitespace-only must not produce an empty section in
the prompt.
- Acceptance: `task.data = {"title": "x", "body": "   "}` (or `body` missing
  entirely) yields the same prompt shape as no body at all.

**FR-4 — Title and body are both surfaced, distinguishably.**
When both are present, the agent must be able to tell "task, in one line" from
"the rest of the description" — a wall of unlabeled text is worse than
today's title-only line for agents that key on the short form (e.g. for commit
message hints downstream). Prefer a two-part rendering: the existing
`Task: <request>` line stays as-is, followed by a body block only when the
body's content isn't already exactly the request line (avoid duplicating the
title verbatim when `request`/`title` and `body` happen to coincide — not
expected in practice but keeps the output non-redundant).

## Non-functional requirements

- **No new dependency, no I/O change.** `compose_prompt` is a pure string
  builder; this is a formatting-only change confined to
  `src/harness/behaviors/agent.py`.
- **Determinism preserved.** Output must remain a pure function of `task`,
  `step`, `artifact_relpath`, `spec` — required by the existing test style and
  by invariant #14 (persona is data, no branching on agent identity).
- **No prompt-injection concession needed beyond current baseline.** The body
  is operator-authored (GitHub issue text within the repos this harness is
  configured for) and is already trusted equivalently to the title today —
  same trust boundary, just more of the same trusted text reaching the agent.
- **Length**: no truncation requirement in scope — GitHub issue bodies are
  bounded by GitHub's own limits (~65KB) and `claude -p` handles arbitrarily
  long prompts; don't add a length cap that isn't asked for.

## Data model

No schema change. `task.data` already carries (from
`src/harness/drivers/github_source.py:88-96`):

```
{
  "title": str,
  "body": str,
  "source": {"kind": "github", "repo": str, "issue": int, "url": str},
}
```

and manually-submitted tasks carry an ad hoc `{"request": ...}` or
`{"summary": ...}` (see `_request_of`'s existing precedence list and
`tests/test_agent_behavior.py:70`). The fix only needs to additionally read
`task.data["body"]`; no new field is introduced anywhere.

## Interfaces

None — this is entirely internal to `compose_prompt`'s generated prompt text.
No CLI flag, no port signature, no artifact format changes.

## Dependencies and scope

**In scope:**
- `src/harness/behaviors/agent.py` — `compose_prompt` / `_request_of` (or a new
  sibling helper, e.g. `_body_of`).
- `tests/test_agent_behavior.py` — extend/add unit coverage for the new
  behavior (FR-1 through FR-4).

**Out of scope:**
- `GithubTaskSource` / `github_client.py` — already correctly capture and
  store the body; untouched.
- Any other prompt content (artifact instructions, verdict block) — unchanged.
- Truncating, summarizing, or sanitizing the body — not requested, no evidence
  of a problem to justify it.
- `harness submit` CLI — could optionally grow a `--body` flag/`request` vs
  `body` distinction, but no request for that; manual submissions keep using
  `request`/`title`/`summary` as today.

## Rough plan

1. In `src/harness/behaviors/agent.py`, add a small helper (e.g. `_body_of(task)`)
   that reads `task.data.get("body")`, returns `""` for `None`/non-str/blank
   after `.strip()` — mirroring `_request_of`'s existing null-safety.
2. In `compose_prompt`, after the existing `Task: {request}` line, append the
   body (only if non-empty and not identical to `request`) as its own labeled
   block, e.g.:
   ```
   Task: <title>

   <body>
   ```
   Keep the "no further description" fallback line exactly as today when there
   is neither a request nor a body.
3. Update `tests/test_agent_behavior.py`:
   - Extend `test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes`
     or add a new test asserting the body text shows up in the prompt when
     `task.data` has both `title` and `body`.
   - Add a regression test asserting no body-shaped section appears when
     `body` is absent, `None`, or whitespace-only (covers FR-2/FR-3).
   - Add a test with `body` equal to the title/request to confirm no
     duplication (FR-4), if this precedence detail is worth locking down.
4. Run `.venv/bin/pytest -q tests/test_agent_behavior.py` and the full suite
   (`.venv/bin/pytest -q`) to confirm no regressions, in particular
   `tests/test_smoke_git.py`/`tests/test_phase3_e2e.py`, which exercise
   `compose_prompt` indirectly through the real behavior.
5. Land as a `fix:` commit (this is a bug fix — data captured but never used —
   which per this repo's semantic-release convention bumps the patch version).

## Open questions

- **Precedence when `request`/`summary` (manual submit keys) coexist with
  `body`** (shouldn't happen in practice — `body` is GitHub-only, `request` is
  manual-submit-only — but not enforced by any schema): default chosen above
  is to always show both if both are non-empty and differ, since there's no
  signal that they're meant to be mutually exclusive.
- **Should the body get its own explicit label ("Body:") vs. just a blank-line
  separated block under `Task:`?** Default chosen: no extra label, just a
  blank-line-separated paragraph directly under the `Task:` line — mirrors how
  a human would paste an issue title + description into a prompt. If the
  downstream `develop`/`review` agents.md instructions later show this reads
  ambiguously, a "Description:" label can be added without changing FR
  behavior.
