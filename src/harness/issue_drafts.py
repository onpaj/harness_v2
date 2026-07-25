"""The issue drafts a step's artifact carries — parsed. A pure domain utility.

A step writes an ordinary markdown report and ends it with a fenced ```json
block holding an **array** of issue drafts. This module turns that text into
`IssueDraft`s and derives each draft's idempotency marker.

Two deliberate asymmetries:

- **An empty artifact is zero drafts, not an error.** "The step wrote no file"
  is a legitimate report (the healer's `skip` path). A *non-empty* artifact
  with no readable block is an error — a persona that wrote a report but
  malformed its block is a real fault worth surfacing.
- **The last fenced block wins**, mirroring `_extract_verdict`'s rule for the
  agent's final message, so an example earlier in the report cannot be
  mistaken for the findings.

The module imports nothing from the `harness` package — like `models`,
`ids` and `artifacts_layout`. That is also why `_FENCED_JSON` is a local copy
of the regex `drivers/claude_cli.py` uses: the convention is shared by design,
but this module must stay driver-free.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha1

_FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class IssueDraft:
    """One issue a step proposes. `labels` are the agent's suggestions — the
    finisher filters them against its binding's allowlist before sending."""

    title: str
    body: str = ""
    labels: tuple[str, ...] = ()


class DraftError(ValueError):
    """The artifact does not carry a readable array of drafts."""


def parse_drafts(artifact: str) -> list[IssueDraft]:
    """The drafts in `artifact`'s last fenced json block.

    An empty/blank artifact yields `[]`. Anything else that cannot be read as
    an array of `{title, body?, labels?}` objects raises `DraftError`.
    """
    if not artifact.strip():
        return []

    blocks = _FENCED_JSON.findall(artifact)
    if not blocks:
        raise DraftError("the artifact has no fenced json block of issue drafts")

    try:
        raw = json.loads(blocks[-1])
    except (json.JSONDecodeError, ValueError) as error:
        raise DraftError(f"the artifact's json block is not valid JSON: {error}") from None

    if not isinstance(raw, list):
        raise DraftError(
            f"the artifact's json block must be a JSON array of drafts, got "
            f"{type(raw).__name__}"
        )

    drafts = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DraftError(f"draft {index} is not an object")
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise DraftError(f"draft {index} has no title")
        body = item.get("body", "")
        labels = item.get("labels", [])
        drafts.append(
            IssueDraft(
                title=title.strip(),
                body=body if isinstance(body, str) else "",
                labels=tuple(str(label) for label in labels)
                if isinstance(labels, list)
                else (),
            )
        )
    return drafts


def marker_for(task_id: str, title: str) -> str:
    """A draft's idempotency marker: `<task id>:<8 hex of sha1(title)>`.

    Task-scoped, so a re-run of the same task re-finds the issues it already
    opened. Content-scoped within the task, so reordered findings still match
    the right issue — which a positional `task:index` key would not.
    """
    digest = sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{task_id}:{digest}"
