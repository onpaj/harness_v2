"""GithubIssueImportService: the `IssueImport` driver behind the Ahanas
board's manual "Add issue" button.

The manual mirror of `GithubTaskSource`/`GithubIssuesCheck` — same `Task`
shape, same `dedup_key`, same claim-label courtesy — but driven by a single
synchronous ref (`owner/repo#number` or a full issue URL) an operator pastes,
fetched by number regardless of label, rather than by scanning for
`harness:todo`. Not a `TaskSource`: it has no `poll()` loop.
"""

from __future__ import annotations

import re

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.ids import new_task_id
from harness.models import Task
from harness.ports.board import TODO_COLUMN
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.issue_import import IssueImport, IssueImportResult
from harness.ports.queue import TaskQueue
from harness.ports.repos import RepositoryNotFound, RepositoryRegistry
from harness.ports.source import dedup_key

_REF_RE = re.compile(r"^([^/\s#]+/[^/\s#]+)#(\d+)$")
_URL_RE = re.compile(
    r"^https?://github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)(?:[/?#].*)?$"
)


def _parse_ref(ref: str) -> tuple[str, int] | None:
    """`owner/repo#number` or a full issue URL -> `(slug, number)`. Neither
    shape -> None (a syntax error, not a crash)."""
    match = _REF_RE.match(ref) or _URL_RE.match(ref)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


class GithubIssueImportService(IssueImport):
    def __init__(
        self,
        *,
        client: GithubClient,
        registry: RepositoryRegistry,
        inbox: TaskQueue,
        step_queues: dict[str, TaskQueue],
        done: TaskQueue,
        failed: TaskQueue,
        healed: TaskQueue,
        archived: TaskQueue,
        events: EventSink,
        clock: Clock,
        workflow: str | None = "development",
        step: str | None = None,
        worktree_root: str,
        claimed_label: str = "harness:queued",
        slug_of=github_slug,
    ) -> None:
        self._client = client
        self._registry = registry
        self._inbox = inbox
        # A deliberate superset of the pollers'/reconcilers' usual sweep sets
        # (which stop at inbox/step_queues/done/failed): a pasted ref for an
        # issue that's already *resolved* — healed or archived — should still
        # read as "already queued", not spawn a confusing second task. Don't
        # narrow this back down to four queues.
        self._queues = [inbox, *step_queues.values(), done, failed, healed, archived]
        self._events = events
        self._clock = clock
        self._workflow = workflow
        self._step = step
        self._worktree_root = worktree_root
        self._claimed_label = claimed_label
        self._slug_of = slug_of

    def add(self, ref: str) -> IssueImportResult:
        parsed = _parse_ref(ref.strip())
        if parsed is None:
            return IssueImportResult(
                ref=ref,
                ok=False,
                error=f"not a valid owner/repo#number or issue URL: {ref!r}",
            )
        slug, number = parsed

        repository = self._resolve_repository(slug)
        if repository is None:
            return IssueImportResult(
                ref=ref,
                ok=False,
                error=f"repo {slug!r} is not registered (check repos.json)",
            )

        try:
            issue = self._client.get_issue(slug, number)
        except Exception as error:  # noqa: BLE001 - network/auth: never crash the request
            return IssueImportResult(ref=ref, ok=False, error=str(error))
        if issue is None:
            return IssueImportResult(
                ref=ref, ok=False, error=f"issue {slug}#{number} was not found"
            )

        key = dedup_key("github", slug, number)
        existing = self._find_by_dedup_key(key)
        if existing is not None:
            return IssueImportResult(
                ref=ref, ok=True, already_queued=True, task_id=existing.id
            )

        task_id = new_task_id()
        task = Task(
            id=task_id,
            workflow_template=self._workflow,
            step=self._step,
            created=self._clock.now(),
            repository=repository,
            worktree=f"{self._worktree_root}/{task_id}",
            dedup_key=key,
            data={
                "title": issue.title,
                "body": issue.body,
                "source": {
                    "kind": "github",
                    "repo": slug,
                    "issue": number,
                    "url": issue.url,
                },
            },
        )

        try:
            self._client.add_label(slug, number, self._claimed_label)
        except Exception:  # noqa: BLE001 - best-effort: the task is already real
            pass

        self._inbox.put(task)
        self._events.emit(
            "ingested", task_id=task.id, queue=TODO_COLUMN, task=task.to_dict()
        )
        return IssueImportResult(ref=ref, ok=True, task_id=task.id)

    def _resolve_repository(self, slug: str) -> str | None:
        for name in self._registry.names():
            try:
                path = self._registry.resolve(name)
            except RepositoryNotFound:
                continue
            if self._slug_of(path) == slug:
                return name
        return None

    def _find_by_dedup_key(self, key: str) -> Task | None:
        for queue in self._queues:
            for task in queue.list():
                if task.dedup_key == key:
                    return task
        return None
