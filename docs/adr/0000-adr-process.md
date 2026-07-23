# ADR-0000: ADR process and numbering

Status: Accepted

## Context

`CLAUDE.md`'s "Invariants — do not break" section is, in effect, an undated,
unnumbered decision log: 23 rules that are load-bearing for the architecture but
carry no individual history, no "why," and no way to supersede one without
editing a shared wall of text. The phase specs and plans under
`docs/superpowers/` record *what was built when*, but reconstructing *why a
specific rule exists* still means cross-referencing dated files. This ADR set
gives the durable subset of those invariants a proper record: one decision per
file, each citing the code and tests that enforce it today.

## Decision

Architecture Decision Records live under `docs/adr/`, one file per decision,
named `NNNN-<slug>.md` with a four-digit, zero-padded, sequential number and a
lowercase hyphenated slug derived from the title. Numbers are never reused and
never renumbered. Every file follows the same template, in this order:

```markdown
# ADR-NNNN: <Title>

Status: Accepted

## Context

...

## Decision

...

## Consequences

...
```

`Status` is a single line: `Accepted`, `Proposed`, or `Superseded by ADR-NNNN`.
A decision that changes its mind gets a *new* ADR with the next number; the old
file's `Status` line is edited to point at the replacement, and the new file's
`Context` says what it supersedes and why — the old file is never deleted or
renumbered, so the history stays intact.

This file is itself `0000` rather than a bare `docs/adr/README.md`, so the
static doc-site generator's `docs/adr/*.md` glob picks it up like any other
entry with no special case for "the one file without a number." It documents
the process, not a design decision about the harness — but it still carries a
title line and a `Status` line so it titles consistently in the generated
index.

## Consequences

- Twelve ADRs (`0001`–`0012`) ship alongside this one, each grounding a rule
  already stated in `CLAUDE.md`'s invariants; the invariants themselves are not
  rewritten or deleted — the ADRs add the *why* around wording that stays
  canonical in `CLAUDE.md`.
- Adding a thirteenth ADR later is a one-file addition, not an edit to this
  process document or to the numbering of any existing file.
- A reviewer can disagree with one decision (open a new ADR that supersedes it)
  without touching the eleven others — the granularity is deliberately narrow,
  one topic per file.
