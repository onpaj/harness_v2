"""GithubTaskSource: issue → task, stav tasku → label na issue.

Jediné místo se znalostí GitHubu na straně zdroje. Přehození labelu v `poll()`
je dvojče atomického `rename` ve `fs_queue.claim()` — další poll issue s claim
labelem nevrátí, což dává ingesci „nanejvýš jednou" přes restarty procesu.

Na rozdíl od `rename` ale `list_issues` čte s read-after-write lagem: po
`remove_label` může další (rychlý) tick issue pořád vrátit pod select labelem a
claimnout ho podruhé. Proti tomu drží `poll()` in-process ledger už claimnutých
čísel (`_claimed`) — v rámci procesu ingestuje každé issue právě jednou.
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
        # Množina labelů, které tenhle zdroj spravuje. Jen z ní `_set_state`
        # odebírá — cizí labely (bug, priority) zůstanou nedotčené.
        self._managed = {
            claimed_label,
            pr_label,
            failed_label,
            *self._step_labels.values(),
        }
        # In-process ledger už claimnutých issue. Swap labelu (todo→queued) dává
        # at-most-once přes restarty, ale `list_issues` čte s read-after-write
        # lagem — po `remove_label` může další tick issue pořád vrátit pod select
        # labelem a claimnout ho podruhé. Tenhle set to utne v rámci procesu.
        self._claimed: set[int] = set()

    def poll(self) -> list[Task]:
        tasks: list[Task] = []
        for issue in self._client.list_issues(self._repo, label=self._select_label):
            if issue.number in self._claimed:
                continue  # už claimnuto tímhle procesem, list jen dojíždí lag
            self._claimed.add(issue.number)
            # Claim: přehoď label dřív, než task odejde do inboxu.
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
        if label:  # neznámý krok → bez labelu (coarse default, míň šumu)
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
        return task.data.get("source", {}).get("kind") == self.kind

    def _issue(self, task: Task) -> int:
        return task.data["source"]["issue"]
