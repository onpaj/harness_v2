# ADR-0024: A healed failure lands in `done`, a declined one stays in `failed`

Status: Accepted

Refines ADR-0018's "`healed/` is the never-consumed terminal" decision and the
half of ADR-0019 that settles a *declined* failure the same way, per ADR-0000's
additive convention — the rest of both ADRs (self-healing as a Process, the
one-hop limit, the three guards) is unchanged and still authoritative.

## Context

ADR-0018 gave the `failed-tasks` check its own terminal queue: it claims a task
out of `failed/` and settles it onto `healed/`, so `failed/` drains
monotonically and no failure can be healed twice. `healed/` got a board column
of its own, unconditional, next to `done` and `failed`.

Three things were then true of that column at once, and only the first was
intended:

1. It is where a failure the healer took over goes.
2. It is where a failure the healer explicitly **declined** to take over goes —
   `heal-failed` (the healer's own task failed) and `heal-declined` (the
   one-hop limit). ADR-0019 settled those there precisely to keep `failed/`
   draining, noting the reason in the history line.
3. Because both settle to the same place, the column answers no question an
   operator has. "Healed" reads as a success state, but it holds the one case
   where the automation gave up — CLAUDE.md's own gotcha has to spell out that
   `heal-declined` in `healed/` means "the automated fix didn't work, it's
   yours now". A terminal that needs that footnote is not a terminal, it is two
   states sharing a name.

The operator's report was the plain version of this: a healed task is finished,
so it belongs in `done` — the healer's involvement is a fact about *how* it got
there, which is what history is for.

## Decision

**The two outcomes land in the two columns that already mean what they mean.**

- **A claimed failure is retired into `done/`**, with `status = END` — the same
  terminal an ordinary completion reaches. Nobody has to do anything about it
  any more, which is exactly what the `done` column tells an operator. That the
  healer ended it, rather than the workflow, is recorded in the task's history:
  the `failed-tasks` actor moving it `failed → end`. One `Observation` per
  claim, exactly as before; the heal pipeline is untouched.
- **A declined failure stays in `failed/`.** Nothing is coming to fix it, so it
  must keep reading as a problem in the column an operator watches. It is
  claimed exactly once, to append one history line saying why the healer is
  leaving it alone, and written straight back. That line is also the marker:
  `_already_declined` recognises it and every later tick skips the candidate
  without claiming it.
- **The `healed` status, queue and column are retired.** `healed/` is still
  constructed and still hydrated — into the `done` column — so tasks a previous
  version left there stay on the board and gettable by id. Nothing writes it.

## Consequences

- **Invariant 24 changes shape.** `failed/` still has exactly one reader, but it
  no longer drains *unconditionally*: it drains of everything healable, and what
  remains is precisely the set the healer refused. That residue is bounded (it
  only grows by failures the harness declined) and inert (each is claimed once,
  ever), so the property that mattered — no loop, no repeated work, no failure
  healed twice — is preserved, while the property that misled — "the failed
  column empties itself" — is deliberately given up.
- **A decline is now visible where failures are visible.** The information that
  used to be a history line inside a terminal nobody opens is a history line on
  a card in the `failed` column. No new board mechanism, no new status.
- **`done` holds two kinds of ending.** Distinguishing them is a history read
  (`actor == "failed-tasks"`), not a column read. This is the trade the decision
  makes on purpose: the board answers "is anyone still on the hook for this?",
  and both kinds answer no.
- **Outward reflection is unchanged.** `SourceReflectorSink` keys on event
  *names*, and the healer's settle event was never one of them, so a healed
  task's GitHub issue keeps the label its failure earned. The work did fail; the
  healer only filed something about it.
- **The upgrade is not a migration.** An existing root's `healed/` directory is
  left exactly as it is and read at hydration; a fresh root simply never creates
  one. Deleting the directory by hand is safe once its contents no longer matter
  to the operator.
