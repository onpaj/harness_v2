# Design: skip confirmation in create-harness-issue when the request is already complete

## Scope note

This is a single markdown instruction file
(`.claude/skills/create-harness-issue/SKILL.md`), not application code. There
is no UI to wireframe — the "interface" is the skill's own workflow as
executed by an agent, and its "output" is the text it prints to the user. The
sections below therefore replace the usual UX/UI + component design +
schemas with their nearest equivalents for a prompt document: the decision
flow the agent follows, the responsibility of each workflow step, and the
concrete shape of the two pieces of structured content the skill produces
(the completeness check, the post-creation report).

## Component design — the workflow as a decision flow

Today step 4 is a single unconditional gate: draft → confirm → create. The
redesign splits it into a **decision point** (step 4) that routes to one of
two paths, replacing the single path with a branch. Steps 1–3 and 5 keep
their current responsibilities unchanged; only step 4's role changes (gate →
router) and step 6 gains a second responsibility (reporting after a direct
create, not just echoing the URL).

```
1. Resolve repo ─────────────────────────────────────────────────┐
2. Ensure label exists                                            │
3. Draft title + body                                             │
                                                                    ▼
4. Completeness check (NEW responsibility: router, not gate)
   ┌────────────────────────────┬─────────────────────────────────┐
   │ repo resolved AND           │ any check fails                │
   │ title concrete AND          │                                 │
   │ body has real substance     │                                 │
   └──────────────┬───────────────┴────────────────┬───────────────┘
                  │ complete                        │ incomplete/ambiguous
                  ▼                                  ▼
   4a. Direct-create path              4b. Ask path (existing safety net)
       no preview, no pause                ask the user for the specific
                                            missing/ambiguous piece(s);
                                            re-run step 3/4 once answered
                  │                                  │
                  ▼                                  ▼
5. Create (gh issue create) ◄─────────────────────────┘
                  │
                  ▼
6. Report (EXTENDED: repo/title/body summary + label + URL,
   always shown after creation — this is the direct path's only
   user-facing checkpoint, so it must be fuller than today's
   URL-only line)
```

Responsibility of each component (workflow step), stated as an interface
contract so the rewrite has a precise target:

| Step | Responsibility | Input | Output |
|---|---|---|---|
| 1. Resolve repo | Unchanged | user text / cwd | `repo` (`owner/name`) or an unresolved state that forces step 4 to the ask path |
| 2. Ensure label | Unchanged | `repo` | label exists (side effect only) |
| 3. Draft | Unchanged | user text, `repo` | `title`, `body` (draft) |
| 4. Completeness check (router) | Classify the draft as *complete* or *incomplete*, using the three criteria below, and route accordingly. This is the only new decision logic in the file. | `repo`, `title`, `body`, and how each was obtained (explicit vs. inferred vs. invented) | route: 4a or 4b |
| 4a. Direct-create path | No new output; simply the absence of a pause. Falls through to step 5. | — | — |
| 4b. Ask path | Ask the user only about the specific failing criterion/criteria (not a generic "confirm?"), then loop back to step 3/4 with the answer folded in. | failing criterion | updated `title`/`body`/`repo`, or explicit user go-ahead |
| 5. Create | Unchanged (`gh issue create` with temp-file body) | `repo`, `title`, `body` | created issue + URL |
| 6. Report | Extended: always show repo, title, label, and a short body summary alongside the URL — this is what makes the direct path safe to skip a pre-create pause, since the user still sees exactly what was made, just after instead of before | `repo`, `title`, `body`, `label`, `url` | printed report |

Key design decision: **step 4b is not a new invention** — it's today's
step-4 confirmation, narrowed and repurposed. Today it always fires and asks
a generic "does this look right?". After the redesign it fires only on
failure of the completeness check, and asks specifically about *what's*
missing or ambiguous (e.g. "which repo did you mean?", "what should the
title actually say — 'Bug in parser' doesn't tell me what to fix"). This
keeps FR-3's safety net intact while making it targeted rather than blanket.

## Data schema — the completeness check

This is the structured judgment the agent makes at step 4. It is not a data
structure that gets serialized anywhere (no code, no storage) — it's the
checklist the rewritten SKILL.md must spell out explicitly, so a future
agent reading it applies the same bar every time rather than drifting.

```
CompletenessCheck:
  repo_resolved: bool
    true  ⇔ explicit "owner/repo" given, OR unambiguous `gh repo view` inference
    false ⇔ neither resolves, OR the user named something gh can't confirm

  title_concrete: bool
    true  ⇔ title is a self-contained imperative instruction an agent could
             act on with no further clarification (e.g. "Add rate limiting
             to the login endpoint")
    false ⇔ title is a bare headline/topic the skill would have to expand
             into an actual ask on its own (e.g. "Rate limiting", "Bug in
             parser")

  body_substantive: bool
    true  ⇔ the user's request contains real content for Context/Goal/
             Acceptance beyond the section headers — i.e. the skill is
             transcribing/organizing what the user said, not inventing it
    false ⇔ filling the template would mean the skill fabricates
             context/goal/acceptance criteria wholesale from a one-line ask

  complete = repo_resolved AND title_concrete AND body_substantive
```

`complete == true` routes to 4a (direct create). Any `false` routes to 4b
(ask), and the question asked must name the specific failing field(s) — not
a generic "should I proceed?".

## Data schema — the post-creation report (step 6)

Replaces today's bare "report the issue URL" with a fixed set of fields, so
the direct-create path (which has no pre-create preview) still gives the
user everything they'd have seen in the old preview, just afterward:

```
Report:
  repo:  string        (owner/name actually used)
  title: string         (exact title passed to gh issue create)
  body_summary: string  (short — Context/Goal in a line or two, not a full
                          re-print of the body; the full body is on GitHub)
  label: "harness:todo"
  url:   string          (gh issue create's stdout)
```

This applies uniformly to both paths (4a direct-create and 4b after the
user's go-ahead) — step 6 doesn't need to know which path was taken, it just
always reports the same fields after `gh issue create` returns.

## Section-level content plan

The rewrite touches exactly three sections of SKILL.md, each replaced
in-place with content matching the schemas above:

- **Step 4** ("Preview and confirm" → "Check completeness, then create or
  ask"): states the three criteria from `CompletenessCheck`, the routing
  rule, and that the ask (4b) must be specific to the failing criterion —
  not a blanket confirmation prompt.
- **Step 6** ("Report the issue URL" → report per the `Report` schema
  above): lists the fields to surface, for both paths.
- **Common mistakes**: the "Creating without confirmation. Outward-facing;
  always preview first." bullet is replaced with two bullets — one
  reframing over-eager direct-create on a genuinely ambiguous request as the
  new mistake to avoid, one confirming that a fully-specified request should
  *not* be held up by an unnecessary confirmation (so the file doesn't just
  invert the old bug into a new one of always asking out of habit).

No other section (Overview/contract table, steps 1–3, step 5, frontmatter
`description`) changes — confirmed against the plan's non-functional
requirements (repo-resolution order, label policy, body template all stay
as-is).
