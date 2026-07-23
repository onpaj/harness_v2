# Architecture assessment — include the issue body in every agent's prompt

## Verdict

Approve the plan and design as written, with no structural changes. This is a
single-function, single-file bug fix that needs no new component, port, or
schema. I confirmed both artifacts against the live source:

- `src/harness/behaviors/agent.py:93,115-120` — `compose_prompt` calls only
  `_request_of(task)`, which walks `request` → `title` → `summary` and never
  touches `task.data["body"]`.
- `src/harness/drivers/github_source.py:86-96` — `GithubTaskSource.poll()`
  already stamps `data={"title": issue.title, "body": issue.body, "source": {...}}`
  onto every ingested task.

So the diagnosis is exactly right: the data is captured on ingestion and
silently dropped at prompt-composition time. Nothing else in the call chain
touches `task.data["body"]` — I checked `ClaudeCliBehavior.run` (agent.py:43-80)
and there is exactly one caller of `compose_prompt`, with no intermediate
transform that could also need fixing.

## Alignment with existing patterns and integration points

- **Layering.** `compose_prompt` is a free function in `behaviors/agent.py`,
  already downstream of `models` and `ports/agent` only. Adding a second
  `task.data` reader alongside `_request_of` introduces no new import and
  keeps the module's existing shape (two small helpers feeding one string
  builder). No layer boundary in the module map is crossed.
- **Invariant #14 (persona is data).** The fix reads `task.data`, not
  `spec.name` or any per-agent branch — `compose_prompt` stays a pure
  function of `(task, step, artifact_relpath, spec)` for every persona
  alike. Confirmed nothing in the plan/design proposes branching on agent
  identity.
- **Invariant #15/#19 boundary.** `body` is plain `task.data` content, not
  `task.data.source` — reading it in `compose_prompt` does not encroach on
  the `source` sub-object that only the outward projection is supposed to
  read. The design correctly keeps `body` orthogonal to `source`.
- **Determinism.** `compose_prompt` remains side-effect-free; the only
  change is which keys of an already-in-memory dict it reads. No I/O, no
  clock, no new dependency — consistent with every other prompt-composition
  path in this file.
- **Test convention.** The existing regression test
  (`test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes`) uses
  `make_task()` with `data={"request": "add rate limiting"}` and no `body`
  key — this is exactly the "no body" case the fix must leave byte-identical,
  which the plan already designates as the FR-2 regression guard.

## Proposed architecture

No new components. One function grows a helper and one extra conditional
branch in its output-building list:

```
compose_prompt(task, step, artifact_relpath, spec)
 ├─ request = _request_of(task)          # unchanged
 ├─ body    = _body_of(task)             # new, same file, same visibility
 └─ lines[] = [...]                      # one more conditional block
```

**Decision: a new sibling helper `_body_of`, not a merge into `_request_of`'s
precedence list.** Considered alternative: extend `_request_of` to also
fall through to `body`. Rejected — `request`/`title`/`summary` model the
same concept at decreasing specificity (manual submit vs. GitHub title vs.
generic fallback), whereas `body` is a materially different thing (a long
free-form description, not a short label) that must appear *in addition to*
the short line, not instead of it. Collapsing them into one precedence chain
would make it impossible to render both, which is FR-4's explicit
requirement. Two small pure functions, each single-purpose, is the right
granularity here — no premature abstraction, no missing one either.

**Decision: no-duplication guard (`body != request`), not an unconditional
append.** A body block that always renders would duplicate the title
verbatim in the (rare, but not schema-excluded) case where a manually
submitted task sets both `request` and `body` to the same string. Guarding
on inequality costs one extra comparison and keeps output non-redundant
without adding any new precedence or schema rule.

**Decision: no explicit "Body:" label, blank-line-separated paragraph
instead.** Matches the file's own existing separator convention (the blank
line between `Task:` and the "You'll find the context..." block is already
how this function delimits sections). Introducing a label would be a
second, inconsistent way of marking structure in the same string. If a
future prompt-engineering pass finds the unlabeled paragraph ambiguous to
the agent, that is a one-line change local to this function — not a reason
to block this fix.

## Implementation guidance

All changes confined to two files, matching the plan's stated scope:

1. **`src/harness/behaviors/agent.py`**
   - Add `_body_of(task: Task) -> str` directly below `_request_of`,
     identical null-safety shape:
     ```python
     def _body_of(task: Task) -> str:
         value = task.data.get("body")
         if isinstance(value, str) and value.strip():
             return value.strip()
         return ""
     ```
   - In `compose_prompt`, after computing `request = _request_of(task)`, add
     `body = _body_of(task)`. Build the `lines` list conditionally: keep the
     existing `Task:`/fallback line and its trailing `""` unchanged, then
     insert `body` (and one more `""`) only when `body and body != request`,
     immediately before the existing `"You'll find the context..."` line.
   - No signature change. No change to `ClaudeCliBehavior.run` — it already
     forwards `task` unchanged into `compose_prompt` (agent.py:52-54).

2. **`tests/test_agent_behavior.py`**
   - Extend or add cases around `make_task()` (line 63) — that fixture's
     `data={"request": "add rate limiting"}` has no `body` key today, so it
     stays the FR-2/FR-3 regression anchor unmodified.
   - New cases (can share the existing `spec`/call shape at line 193-210):
     `body` present and differing from `request` (FR-1/FR-4 — assert body
     text appears); `body` absent (already covered by the existing test, no
     change needed); `body` whitespace-only (FR-3 — assert no extra blank
     section, i.e. output equals the no-body case); `body` equal to
     `request` (FR-4 — assert body text isn't duplicated in the output).
   - No fixture in this file needs a new helper — `Task(..., data={...})`
     literals are constructed inline elsewhere in the file (e.g. around line
     193), so a `body`-bearing task can be built the same way without
     touching `make_task()`'s default (which several other tests depend on
     staying body-less).

**Data flow (unchanged shape, one more field read):**

```
GithubTaskSource.poll()
  → Task.data = {"title": ..., "body": ..., "source": {...}}
  → fs_queue → dispatcher → consumer → ClaudeCliBehavior.run(task)
      → compose_prompt(task, ...)     # now reads both title/request AND body
      → AgentRunner.run(prompt=...)
```

## Risks and mitigations

- **Risk: prompt-length growth for large issue bodies.** GitHub issue bodies
  can run to tens of KB. Mitigation: none needed for this task — the plan
  already scoped truncation out (no evidence of a problem, `claude -p`
  handles long prompts), and adding an arbitrary cap without a concrete
  failure to justify it would violate this repo's "don't add validation for
  scenarios that can't happen" convention. If a real length problem shows up
  later, it's a separate, evidence-driven change.
- **Risk: regressing the no-body prompt shape.** The existing test
  (`test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes`) only
  asserts substrings today, so it wouldn't itself catch an accidental stray
  blank line. Mitigation: the new FR-2/FR-3 test cases should assert the
  *exact* output string equals today's output when body is absent/blank
  (not just substring checks), so a formatting regression is caught
  directly rather than relying on the pre-existing test's looser
  assertions.
- **Risk: none at the port/driver level.** No port signature, no driver, no
  schema changes — nothing here can break `test_architecture.py`'s import
  or branching checks (invariants #1, #2, #14 all untouched by construction).

## Prerequisites before implementation

None. No open design questions block starting — the plan's two "open
questions" (precedence when both `request`/`summary` and `body` are present;
whether to label the body block) both already have defaults chosen and
justified above, and neither has a correctness consequence significant
enough to need a decision from anyone else before writing code.
