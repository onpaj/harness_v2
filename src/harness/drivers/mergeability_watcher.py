"""`GithubMergeabilityWatcher`: watches harness-authored open PRs and either
auto-updates a stale ("behind") one or queues a conflicted ("dirty") one as a
resolver task.

This is a deliberate, documented widening of `TaskSource.poll()`'s contract
(see `ports/source.py`): an implementation may perform an idempotent,
side-effecting action per polled item that produces no task, alongside
returning new tasks. Precedent: `GithubTaskSource.poll()` already swaps a
label as part of claiming an issue.

Per-PR isolation lives *inside* `poll()` (the try/except around
`update_branch`), not only at `SourcePoller.tick()`'s level — one
persistently misbehaving PR must not block every PR that sorts after it in
the same tick. A failure in `list_pull_requests` itself still propagates
uncaught, exactly like `GithubTaskSource` — caught once by `SourcePoller.tick`.
"""

from __future__ import annotations

from harness.drivers.github_client import GithubClient
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.clock import Clock
from harness.ports.source import FinishResult, Progress, TaskSource, dedup_key


class GithubMergeabilityWatcher(TaskSource):
    kind = "mergeability"

    def __init__(
        self,
        *,
        client: GithubClient,
        clock: Clock,
        repo: str,
        repository: str,
        worktree_root: str,
        resolver_workflow: str = "resolver",
        head_prefix: str = "harness/",
        resolving_label: str = "harness:resolving",
    ) -> None:
        self._client = client
        self._clock = clock
        self._repo = repo
        self._repository = repository
        self._worktree_root = worktree_root
        self._resolver_workflow = resolver_workflow
        self._head_prefix = head_prefix
        self._resolving_label = resolving_label

    def poll(self) -> list[Task]:
        tasks: list[Task] = []
        for pr in self._client.list_pull_requests(self._repo, head_prefix=self._head_prefix):
            if pr.mergeable_state == "behind":
                try:
                    self._client.update_branch(self._repo, pr.number)
                except Exception:  # noqa: BLE001 - one bad PR must not block the rest of this tick
                    continue
                continue
            if pr.mergeable_state != "dirty":
                continue  # clean/blocked/unstable/unknown → leave alone (v1 scope)
            task_id = new_task_id()
            tasks.append(
                Task(
                    id=task_id,
                    workflow_template=self._resolver_workflow,
                    created=self._clock.now(),
                    repository=self._repository,
                    worktree=f"{self._worktree_root}/{task_id}",
                    dedup_key=dedup_key(self.kind, self._repo, pr.number, pr.head_sha),
                    data={
                        "branch": pr.head_branch,
                        "title": f"resolve merge conflict on PR #{pr.number}",
                        "source": {
                            "kind": self.kind,
                            "repo": self._repo,
                            "pr": pr.number,
                            "url": pr.url,
                            "base": pr.base_branch,
                        },
                    },
                )
            )
        return tasks

    def report_progress(self, task: Task, progress: Progress) -> None:
        if not self._mine(task):
            return
        if progress.step in ("resolve", "land"):
            self._client.add_label(self._repo, self._pr(task), self._resolving_label)

    def finish(self, task: Task, result: FinishResult) -> None:
        if not self._mine(task):
            return
        self._client.remove_label(self._repo, self._pr(task), self._resolving_label)

    def _mine(self, task: Task) -> bool:
        src = task.data.get("source", {})
        return src.get("kind") == self.kind and src.get("repo") == self._repo

    def _pr(self, task: Task) -> int:
        return task.data["source"]["pr"]
