"""SlackWebhookSink: a task's progress/outcome as messages to a Slack webhook.

The first real Process sink — the outbound-only mirror of
`GithubLabelReflector`, both routed through the same `effective_sink_kind`
helper (`ports/source.py`): `_mine` matches on `data.sink.kind` when a process
declares one (e.g. `{"kind": "slack"}`), falling back to `data.source.kind`
when it doesn't — so a Process can ingest from one medium and reflect into
another (invariant #40), while a Slack-origin task with no explicit sink still
routes here by the same default-to-source rule `github` relies on. `poll()` is
always `[]`; like the label reflector it subclasses `TaskSource` directly, not
`Trigger` (which names the exact inverse shape: real `poll()`, no-op
reflection).

Stateless by choice: every report posts a fresh webhook message. The stateful
create-then-update sink (Slack Web API, one message edited as progress
advances, via a persistable handle) remains the spec's future refinement —
this driver deliberately posts new lines instead of holding a message id.

An in-process ledger of `(task_id, step)` pairs makes a repeated report a
no-op within a run (invariant #21's "report_progress twice is a no-op").
Cross-restart duplicates are accepted: the ledger is empty after a restart, so
a replayed event may post the same line again — the same convergent posture as
the label reflector, where re-applying a label is harmless.

The HTTP POST runs on stdlib `urllib` (same posture as `HttpGithubClient` — no
third-party deps) and is injectable for tests. The webhook URL is a secret: it
arrives from the environment (`SLACK_WEBHOOK_URL`, wired in `cli._run`) and
never enters a JSON file — the service holds no secret.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

from harness.models import Task
from harness.ports.source import FinishResult, Progress, TaskSource, effective_sink_kind


def post_json(url: str, payload: dict) -> None:
    """POST `payload` as a JSON body — the default webhook transport."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request)


class SlackWebhookSink(TaskSource):
    kind = "slack"

    def __init__(
        self,
        *,
        webhook_url: str,
        post: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._post = post or post_json
        # In-process ledger of already-posted (task id, step) pairs — a repeat
        # of the same report within a run posts nothing. The terminal report
        # rides the same ledger under the reserved pseudo-step "finished".
        self._posted: set[tuple[str, str]] = set()

    def poll(self) -> list[Task]:
        return []

    def report_progress(self, task: Task, progress: Progress) -> None:
        if not self._mine(task):
            return
        if (task.id, progress.step) in self._posted:
            return
        self._posted.add((task.id, progress.step))
        self._post(
            self._webhook_url,
            {"text": f"{self._name(task)} moved to {progress.step}"},
        )

    def finish(self, task: Task, result: FinishResult) -> None:
        if not self._mine(task):
            return
        if (task.id, "finished") in self._posted:
            return
        self._posted.add((task.id, "finished"))
        text = f"{self._name(task)} {'finished ok' if result.ok else 'failed'}"
        if result.summary:
            text = f"{text}: {result.summary}"
        self._post(self._webhook_url, {"text": text})

    def _mine(self, task: Task) -> bool:
        # Effective sink kind: `data.sink.kind` if present, else falling back
        # to `data.source.kind` — a task with no sink and no matching source
        # is foreign.
        return effective_sink_kind(task) == self.kind

    def _name(self, task: Task) -> str:
        title = task.data.get("title")
        return f"{task.id} ({title})" if title else task.id
