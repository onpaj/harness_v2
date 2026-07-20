"""GithubTaskSource: issue → task, stav tasku → label na issue.

Jediné místo se znalostí GitHubu na straně zdroje. Přehození labelu v `poll()`
je dvojče atomického `rename` ve `fs_queue.claim()` — další poll issue s claim
labelem nevrátí, což dává ingesci „nanejvýš jednou" bez vedlejšího ledgeru.
"""

from __future__ import annotations

from harness.drivers.github_client import GithubClient
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.clock import Clock
from harness.ports.source import FinishResult, Progress, TaskSource


def slug_from_source(source: str) -> str:
    """Vytáhni GitHub slug `owner/name` z hodnoty `RepositoryDefinition.source`.

    Zdroj je neprůhledný řetězec — teprve tady, v GitHub driveru, ho čteme jako
    GitHub. Přijme běžné tvary a všechny srovná na `owner/name`:

    - `owner/name` (už slug) → beze změny,
    - `https://github.com/owner/name` (i s `.git`, i s koncovým `/`),
    - `git@github.com:owner/name.git` (SSH remote).

    Jiný tvar (prázdno, jediný segment) → `ValueError` — ať se chyba configu
    ozve nahlas při wiringu, ne němým 404 z API."""
    value = source.strip()
    if value.endswith(".git"):
        value = value[: -len(".git")]
    value = value.rstrip("/")

    if value.startswith("git@"):  # git@github.com:owner/name
        value = value.partition(":")[2]
    elif "://" in value:  # https://github.com/owner/name → shoď schéma i host
        value = value.split("://", 1)[1]

    # `owner/name` z ocasu: u URL to odřízne host (`github.com`), u holého slugu
    # vezme obě části.
    parts = [segment for segment in value.split("/") if segment]
    if len(parts) < 2:
        raise ValueError(f"z {source!r} nejde odvodit GitHub slug 'owner/name'")
    return "/".join(parts[-2:])


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

    def poll(self) -> list[Task]:
        tasks: list[Task] = []
        for issue in self._client.list_issues(self._repo, label=self._select_label):
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
