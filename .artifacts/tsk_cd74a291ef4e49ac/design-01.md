# Design — include the issue body in every agent's prompt

No UI is involved — `compose_prompt` is an internal, pure string builder
consumed only by `ClaudeCliBehavior.run()` before it hands the prompt to
`AgentRunner`. This design covers component design and data shapes only.

## Component design

### Affected component

`src/harness/behaviors/agent.py` — no new module, no new port, no new class.
The change is confined to two functions:

- `_request_of(task) -> str` — unchanged, kept as the single source for the
  short "Task:" line.
- `compose_prompt(task, *, step, artifact_relpath, spec) -> str` — grows one
  extra section; signature is unchanged (invariant: prompt stays a pure
  function of its four inputs).

### New helper: `_body_of`

```python
def _body_of(task: Task) -> str:
    value = task.data.get("body")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""
```

Mirrors `_request_of`'s null-safety (non-str, `None`, and whitespace-only all
collapse to `""`), but only reads the single `body` key — there is no
precedence list to walk, since `body` is populated by exactly one producer
(`GithubTaskSource`). Placed directly below `_request_of` in the same file;
same visibility (module-private, no export).

### `compose_prompt` — rendering rule

Responsibility split stays exactly as invariant #14 requires: no branching on
agent identity, only on the shape of `task.data`.

1. Compute `request = _request_of(task)` and `body = _body_of(task)` (both
   already `.strip()`-ed, `""` when absent).
2. If `body` is non-empty **and** `body != request` (guards FR-4's
   no-duplication case, e.g. a manually submitted task where `body` happens to
   equal `request`), append the body as its own paragraph directly under the
   `Task:` line, separated by one blank line — no extra "Body:" label (matches
   how a human would paste an issue's title + description). This mirrors how
   the existing block already separates the `Task:` line from the next
   section with a blank line, so no new separator convention is introduced.
3. If `body` is empty, or equals `request`, the output is byte-for-byte what
   `compose_prompt` produces today — this is the regression guard for FR-2/FR-3.
4. The "The task has no further description." fallback line is unchanged and
   still only triggers when `request` is empty; a task with a `body` but no
   `title`/`request`/`summary` falls back to that same "no further
   description" line for the short form, then still gets its body paragraph
   appended below it (body and request are independent — one can be present
   without the other).

Resulting prompt shape (both present, differing):

```
You are the agent for step '<step>' of task <id>.
Task: <request>

<body>

You'll find the context from previous steps as files in the .artifacts/<id>/ ...
```

Resulting prompt shape (no body, or body == request — today's shape, unchanged):

```
You are the agent for step '<step>' of task <id>.
Task: <request>

You'll find the context from previous steps as files in the .artifacts/<id>/ ...
```

### Sequencing (unchanged)

`ClaudeCliBehavior.run()` already calls `compose_prompt(task, step=..., artifact_relpath=..., spec=...)` once per attempt (agent.py:52-54); no caller change is needed — the new body text flows through automatically once `compose_prompt` itself is fixed.

## Data schemas

No schema change anywhere in the system. Confirmed shapes, unchanged by this
task:

**`task.data` for a GitHub-sourced task** (`GithubTaskSource.poll`,
`drivers/github_source.py:86-96`):

```json
{
  "title": "string",
  "body": "string",
  "source": {
    "kind": "github",
    "repo": "string",
    "issue": 0,
    "url": "string"
  }
}
```

**`task.data` for a manually submitted task** (`harness submit`, no `body`
key at all):

```json
{ "request": "string" }
```

or, in existing test fixtures, `{"summary": "string"}`. `_request_of`'s
existing precedence (`request` → `title` → `summary`) is untouched; `body` is
read independently by the new `_body_of` and is orthogonal to that
precedence list, not merged into it.

No request/response shapes or event payloads are touched — `compose_prompt`'s
output is a prompt string consumed in-process by `AgentRunner.run(prompt=...)`,
never serialized, logged as a payload, or exposed through `api/`.

## Test surface (for the development step, not prescribed here as tasks)

The existing `test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes`
uses `make_task()`, whose `data` has no `body` key — it must keep asserting
exactly the current strings and must NOT start asserting on a body-shaped
section, since none should appear (FR-2 regression guard). New coverage
needs: `body` present + `title` present and differing (FR-1/FR-4), `body`
absent (FR-2), `body` whitespace-only (FR-3), and `body` equal to `request`
(FR-4 no-duplication case).
