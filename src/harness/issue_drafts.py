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

Where the block *ends* is decided by JSON, not by the closing fence — see
`_decode_last_block`. `merge_verdict.py` and `drivers/claude_cli.py` still
close on the fence with a regex; they read a small verdict object rather than
an array of markdown bodies, so the nested-fence collision is far less likely
there, but it is the same convention and the same latent bug.

The module imports nothing from the `harness` package — like `models`,
`ids` and `artifacts_layout` — so the shared convention is deliberately
duplicated rather than imported from a driver.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha1

_OPENER = "```json"


def _decode_last_block(artifact: str) -> object:
    """The decoded value of the artifact's last fenced json block.

    Raises `DraftError` when there is no block, or when the last one that
    could be a block does not decode.

    The closing fence is deliberately *not* what ends the block — JSON itself
    is. A draft's `body` is markdown, and markdown quotes things in fenced
    blocks: the healer reporting a failure writes a ``` fence inside the very
    string it is filing. Those backticks sit inside a JSON string, but a regex
    closing on the first fence cannot know that, and cut the array mid-string.
    `raw_decode` reads exactly one JSON value and stops where that value ends,
    so everything after it — the closing fence, nested fences, trailing prose
    — is none of the parser's business.

    Openers are tried last-first and the first one that decodes at all wins,
    which keeps the documented "the last fenced block wins" rule while
    stepping back past an opener that is really part of an earlier draft's
    body — such an opener decodes to nothing, since it starts mid-string.
    """
    decoder = json.JSONDecoder()
    starts = [
        index + len(_OPENER)
        for index in range(len(artifact))
        if artifact.startswith(_OPENER, index)
    ]
    if not starts:
        raise DraftError("the artifact has no fenced json block of issue drafts")

    failure = None
    for start in reversed(starts):
        try:
            value, _ = decoder.raw_decode(artifact, _skip_space(artifact, start))
        except ValueError as error:
            # The last opener is the likeliest real one, so its message is the
            # one worth reporting even if an earlier candidate also fails.
            failure = failure or str(error)
            continue
        return value
    raise DraftError(f"the artifact's json block is not valid JSON: {failure}")


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


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

    raw = _decode_last_block(artifact)

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
        if not isinstance(body, str):
            raise DraftError(
                f"draft {index} has a non-string body: {type(body).__name__}"
            )
        labels = item.get("labels", [])
        if not isinstance(labels, list):
            raise DraftError(
                f"draft {index} has non-array labels: {type(labels).__name__}"
            )
        drafts.append(
            IssueDraft(
                title=title.strip(),
                body=body,
                labels=tuple(str(label) for label in labels),
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
