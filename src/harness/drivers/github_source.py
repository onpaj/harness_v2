"""GithubTaskSource: issue → task, task status → label on the issue.

The single place with GitHub knowledge on the source side. Swapping the label in
`poll()` is the twin of the atomic `rename` in `fs_queue.claim()` — a later poll
won't return an issue with the claim label, which gives "at most once" ingestion
across process restarts.

Unlike `rename`, however, `list_issues` reads with read-after-write lag: after
`remove_label`, another (fast) tick may still return the issue under the select
label and claim it a second time. To guard against that, `poll()` keeps an
in-process ledger of already-claimed numbers (`_claimed`) — within a process it
ingests each issue exactly once.
"""

from __future__ import annotations

from harness.drivers.github_client import GithubClient
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.clock import Clock
from harness.ports.source import FinishResult, Progress, TaskSource


class GithubTaskSource(TaskSource):
    kind = "github"

    def __init__(
        self,
        *,
        client: GithubClient,
        clock: Clock,
        repo: str,
        workflow: str = "default",
        repository: str,
        worktree_root: str,
        select_label: str = "harness:todo",
        claimed_label: str = "harness:queued",
        pr_label: str = "harness:pr-open",
        failed_label: str = "harness:failed",
        step_labels: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._clock = clock
        self._repo = repo
        self._workflow = workflow
        self._repository = repository
        self._worktree_root = worktree_root
        self._select_label = select_label
        self._claimed_label = claimed_label
        self._pr_label = pr_label
        self._failed_label = failed_label
        self._step_labels = step_labels or {}
        # The set of labels this source manages. `_set_state` only removes from
        # it — foreign labels (bug, priority) stay untouched.
        self._managed = {
            claimed_label,
            pr_label,
            failed_label,
            *self._step_labels.values(),
        }
        # In-process ledger of already-claimed issues. Swapping the label
        # (todo→queued) gives at-most-once across restarts, but `list_issues`
        # reads with read-after-write lag — after `remove_label` another tick
        # may still return the issue under the select label and claim it a
        # second time. This set cuts that off within the process.
        self._claimed: set[int] = set()

    def poll(self) -> list[Task]:
        tasks: list[Task] = []
        for issue in self._client.list_issues(self._repo, label=self._select_label):
            if issue.number in self._claimed:
                continue  # already claimed by this process, the list is just catching up on lag
            self._claimed.add(issue.number)
            # Claim: swap the label before the task heads to the inbox.
            self._client.remove_label(self._repo, issue.number, self._select_label)
            self._client.add_label(self._repo, issue.number, self._claimed_label)
            task_id = new_task_id()
            tasks.append(
                Task(
                    id=task_id,
                    workflow_template=self._workflow,
                    created=self._clock.now(),
                    repository=self._repository,
                    worktree=f"{self._worktree_root}/{task_id}",
                    data={
                        "title": issue.title,
                        "body": issue.body,
                        "source": {
                            "kind": self.kind,
                            "repo": self._repo,
                            "issue": issue.number,
                            "url": issue.url,
                        },
                    },
                )
            )
        return tasks

    def report_progress(self, task: Task, progress: Progress) -> None:
        if not self._mine(task):
            return
        label = self._step_labels.get(progress.step)
        if label:  # unknown step → no label (coarse default, less noise)
            self._set_state(self._issue(task), label)

    def finish(self, task: Task, result: FinishResult) -> None:
        if not self._mine(task):
            return
        target = self._pr_label if result.ok else self._failed_label
        self._set_state(self._issue(task), target)

    def _set_state(self, number: int, target: str) -> None:
        for label in self._managed - {target}:
            self._client.remove_label(self._repo, number, label)
        self._client.add_label(self._repo, number, target)

    def _mine(self, task: Task) -> bool:
        src = task.data.get("source", {})
        return src.get("kind") == self.kind and src.get("repo") == self._repo

    def _issue(self, task: Task) -> int:
        return task.data["source"]["issue"]
