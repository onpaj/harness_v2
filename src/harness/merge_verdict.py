"""The merge verdict a review step's artifact carries — parsed. A pure domain utility.

The twin of `issue_drafts.py`, for the other "an agent drafts, the harness
acts" pair (invariants 9/26): the `merge-review` persona writes an ordinary
markdown review and ends it with a fenced ```json block holding a **single
object** — its confidence in merging, why, and the risks it saw. This module
turns that text into a `MergeVerdict`; the `merge-pr` finisher compares its
`confidence` against the operator's threshold and decides. The agent never
merges, and it never sets the bar it is judged against.

Three deliberate differences from `parse_drafts`:

- **A single object, not an array.** One PR under review yields one verdict.
- **An empty artifact is an error, not a benign zero.** `parse_drafts` treats
  "the step wrote no file" as the healer's legitimate `skip` path; here there
  is no such path — the step only reaches the finisher when the review already
  routed to merge, so a missing verdict means the persona is broken, and
  saying so loudly beats silently never merging.
- **Confidence is clamped, never rejected, for being out of range.** A persona
  that writes `1.5` meant "very confident", not "bypass the threshold"; it
  clamps to `1.0`. A *non-numeric* confidence is still an error.

The **last** fenced block wins, mirroring `_extract_verdict`'s rule for the
agent's final message and `parse_drafts`' for issue drafts, so an example
earlier in the review cannot be mistaken for the verdict.

The module imports nothing from the `harness` package — like `models`, `ids`,
`artifacts_layout` and `issue_drafts`. That is also why `_FENCED_JSON` is a
local copy of the regex `drivers/claude_cli.py` uses: the convention is shared
by design, but this module must stay driver-free.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class MergeVerdict:
    """One reviewed pull request's verdict.

    `confidence` is the only field the finisher gates on; `reasoning` and
    `risks` exist to be read by a human on the board and in the merge commit
    body, so a merge the harness performed is always traceable to why.
    """

    confidence: float
    reasoning: str = ""
    risks: tuple[str, ...] = ()


class VerdictError(ValueError):
    """The artifact does not carry a readable merge verdict."""


def parse_verdict(artifact: str) -> MergeVerdict:
    """The verdict in `artifact`'s last fenced json block.

    Raises `VerdictError` when the artifact is blank, carries no fenced json
    block, or that block is not an object with a numeric `confidence`. Every
    failure here is fail-safe by construction: the caller refuses to merge.
    """
    if not artifact.strip():
        raise VerdictError(
            "the review step wrote no artifact, so no merge confidence could be read"
        )

    blocks = _FENCED_JSON.findall(artifact)
    if not blocks:
        raise VerdictError("the artifact has no fenced json block carrying a verdict")

    try:
        raw = json.loads(blocks[-1])
    except (json.JSONDecodeError, ValueError) as error:
        raise VerdictError(
            f"the artifact's json block is not valid JSON: {error}"
        ) from None

    if not isinstance(raw, dict):
        raise VerdictError(
            f"the artifact's json block must be a JSON object, got "
            f"{type(raw).__name__}"
        )

    confidence = raw.get("confidence")
    # bool is an int subclass — `true` is a plausible thing for a persona to
    # write, and reading it as 1.0 would silently mean "maximum confidence".
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise VerdictError(
            f"the verdict has no numeric 'confidence': got "
            f"{type(confidence).__name__}"
        )

    reasoning = raw.get("reasoning", "")
    if not isinstance(reasoning, str):
        raise VerdictError(
            f"the verdict has a non-string 'reasoning': {type(reasoning).__name__}"
        )

    risks = raw.get("risks", [])
    if not isinstance(risks, list):
        raise VerdictError(
            f"the verdict has non-array 'risks': {type(risks).__name__}"
        )

    return MergeVerdict(
        confidence=min(1.0, max(0.0, float(confidence))),
        reasoning=reasoning.strip(),
        risks=tuple(str(risk) for risk in risks),
    )
