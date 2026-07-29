"""`PullRequestMerger` port — merging a pull request.

The fourth GitHub-touching port, deliberately distinct from the other three:

- `Forge` (phase 2) **opens** pull requests — it proposes a change.
- `MergeChecker` (`ports/merge.py`) **reads** whether a PR has merged.
- `IssueTracker` **creates** a fresh issue.
- `PullRequestMerger` **merges** an open PR — it disposes of a proposal.

This port is where the harness's founding posture — "the harness never touches
`main`; it only proposes, the merge strategy is a human's call" — is
deliberately relaxed, and having it as its own port is the point: the
relaxation is explicit, auditable and swappable, rather than smuggled in as a
fifth verb on `Forge` (whose own docstring promises the opposite) or as a bare
`GithubClient` call. A deployment that wants the old posture back simply never
wires a merger, and no workflow can merge anything.

Two safety properties belong to the *contract*, not to any one driver:

1. **The merge is pinned to a sha.** `expected_sha` is the head the reviewer
   actually read. A driver MUST refuse when the PR's head has moved past it —
   so it is structurally impossible to merge code no agent reviewed. This is
   the port's most important guarantee.
2. **A refusal is not a failure.** "The head moved", "the PR is no longer
   mergeable", "a required check went red since the scan" are all ordinary
   states of the world, raised as `MergeRefused` for the caller to settle
   benignly — the next scan re-reviews the new head. Only a genuine fault
   (no token, network, a 5xx) raises `MergeError`, which fails the task.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

MERGE_METHODS = ("merge", "squash", "rebase")
"""The merge methods GitHub accepts. Validated at wiring time by the
`merge-pr` finisher's factory, so a typo in a workflow binding fails at build
rather than at merge time."""


@dataclass(frozen=True)
class MergeRef:
    """A completed merge as the driver reports it."""

    sha: str
    """The resulting merge commit's sha."""
    url: str = ""


class MergeError(Exception):
    """The merge failed for a reason the operator must see.

    Nothing catches this in the `merge-pr` finisher: it propagates out of
    `ConsumerBehavior.run`, and `Consumer.tick`'s blanket `except Exception`
    sends the task to `failed/` like any other behavior failure — where, with
    an autoheal Process wired, the healer picks it up.
    """


class MergeRefused(MergeError):
    """The PR could not be merged *right now*, and that is not a fault.

    The head moved past `expected_sha`, a required check went red, a review
    was requested, branch protection said no. The caller settles the task
    benignly; the next scan sees the new head as a new state and re-reviews it
    from scratch. Deliberately a subclass of `MergeError`, so a driver author
    who forgets this distinction fails safe (nothing merges) rather than
    silently merging.
    """


class PullRequestMerger(ABC):
    @abstractmethod
    def merge(
        self,
        repo: str,
        number: int,
        *,
        method: str,
        expected_sha: str,
        title: str = "",
        body: str = "",
    ) -> MergeRef:
        """Merge PR `number` on `repo`, but only while its head is `expected_sha`.

        `method` is one of `MERGE_METHODS`. `title`/`body` become the merge
        commit's message where the method produces one (`merge`/`squash`);
        `rebase` ignores them, as GitHub does.

        Raises `MergeRefused` when the PR is not mergeable as-is — including,
        mandatorily, when its head has moved past `expected_sha`. Raises
        `MergeError` on a genuine fault (auth, network, a server error).

        **Idempotent in the direction that matters:** merging an already-merged
        PR must not raise — it returns the existing merge, the same way
        `Forge.open_pull_request` returns an existing PR rather than opening a
        second. A re-run after a crash between the API call and the task's
        delivery must not report a failure for work that already succeeded.
        """
