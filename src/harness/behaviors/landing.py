"""Landing: folds the artifacts into the worktree and opens a PR.

The last step before `end`. It's a normal behavior — it can fail and drop into
`failed/` like any other step. `end` stays a clean terminal.
"""

from __future__ import annotations

from harness.models import DONE, BehaviorResult, Task
from harness.ports.artifacts import ArtifactView
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.forge import Forge
from harness.ports.workspace import Workspace


class LandingBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        clock: Clock,
        workspace: Workspace,
        artifacts: ArtifactView,
        forge: Forge,
        dest: str = "docs/tasks",
        copy_artifacts: bool = True,
    ) -> None:
        self._clock = clock
        self._workspace = workspace
        self._artifacts = artifacts
        self._forge = forge
        self._dest = dest
        self._copy_artifacts = copy_artifacts

    async def run(self, task: Task) -> BehaviorResult:
        handle = self._workspace.attach(task)

        # A branch-override task (invariant 28 — the `unblock-pr` and
        # `automerge` workflows) is landing onto a branch that may belong to a
        # human, so the agent's write-up must not be staged onto their pull
        # request; the step that produced it excludes `.artifacts` for the same
        # reason. On an ordinary, harness-owned branch the artifacts are
        # committed exactly as before — invariant 16 / ADR-0006 has them ride
        # along with the code they document, in the same commit and the same
        # PR. Stated here rather than left to `GitWorkspace`'s reattach
        # `clean -fd` deleting the untracked file first: that is an
        # implementation detail of the workspace driver, and a refactor of it
        # must not silently re-open this.
        exclude = (".artifacts",) if task.data.get("branch") else ()

        # Phase 3: the artifacts are already versioned in the worktree (the
        # agent wrote them straight into `.artifacts/`), so there's nowhere to
        # copy them — just open the PR. Phase 2 (a separate artifact store)
        # still copies and commits them.
        if self._copy_artifacts:
            for ref in self._artifacts.list(task.id):
                content = self._artifacts.read(task.id, ref.step, ref.attempt, ref.name)
                if content is None:
                    continue
                relpath = f"{self._dest}/{task.id}/{ref.step}/{ref.attempt}/{ref.name}"
                handle.write(relpath, content)
            handle.commit("[land] task artifacts", exclude=exclude)

        # Pre-landing sync: merge the PR's base branch into the task branch so
        # the PR is born up-to-date with base — mergeable, no stale-base
        # resolver round-trip. The base is the very branch the forge opens the
        # PR against, so the merge base always matches the PR base. A clean
        # merge is committed onto the branch; a real conflict (landing has no
        # agent to resolve it) is abandoned and the PR opened on the un-merged
        # branch anyway — the resolver workflow reconciles the dirty PR
        # downstream, exactly as it does for a conflict that appears after the
        # PR is open. Either way the PR still opens; the conflict is only ever
        # flagged, never fatal to landing.
        base = self._forge.base_branch(task)
        conflicted = handle.merge(base)
        if conflicted:
            handle.abort_merge()
        else:
            handle.commit(f"[land] merge {base}", exclude=exclude)

        # The forge cannot open a PR for a ref the remote has never seen. A
        # failure here raises, and the consumer writes the task into `failed/`.
        handle.push()

        pull = self._forge.open_pull_request(
            task,
            branch=handle.branch,
            title=self._title(task),
            body=self._body(task),
        )
        summary = f"opened PR {pull.url}"
        if conflicted:
            summary += (
                f" — conflicts with {base}, opened un-merged for the resolver "
                "to reconcile"
            )
        return BehaviorResult(
            DONE,
            summary,
            data={
                "pr": {
                    "repo": pull.repo,
                    "number": pull.number,
                    "url": pull.url,
                    "branch": pull.branch,
                }
            },
        )

    @staticmethod
    def _title(task: Task) -> str:
        for key in ("title", "request", "summary"):
            value = task.data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return f"harness task {task.id}"

    @staticmethod
    def _body(task: Task) -> str:
        """PR body aggregated from the summaries of consumer history entries."""
        lines = ["## What the task did", ""]
        for entry in task.history:
            if entry.actor.startswith("consumer:") and entry.summary:
                lines.append(f"- **{entry.from_step}** — {entry.summary}")
        return "\n".join(lines) + "\n"
