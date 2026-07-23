"""`LabelIssueBehavior` — a finisher that wraps a step's own behavior and, after
it returns, applies an outcome -> label mapping to the task's source GitHub
issue.

This is the first `ConsumerBehavior` implementation that lives under
`drivers/` rather than `behaviors/`: it depends on `GithubClient`, a driver
with no dedicated port (the task's own notes judge that a new port would be
overkill for one verb, `add_label`), and `test_behaviors_import_only_ports_
not_drivers` forbids anything under `behaviors/` from importing a driver.
`drivers/` importing `drivers/` is already established precedent
(`github_issues_check.py` imports `GithubClient` the same way) — this is a
directory placement, not a new abstraction. It is still a plain
`ConsumerBehavior` (a port), constructed only by wiring (`cli.py`) and handed
to `app.build(finishers={...})` exactly like any other finisher factory.

Unlike `open-pr` (which fully replaces the `land` step's behavior — no agent
runs there), `label-issue` *wraps* the step's own agent behavior: the
persona runs first and returns its verdict, and only then does this class act
on it. The LLM never touches GitHub — invariants #9/#26's shape, extended from
"the worker commits"/"the worker opens the issue" to "the worker applies the
label."

Seam note: a natural companion is posting an issue comment with the persona's
reasoning (e.g. "needs: acceptance criteria"). `GithubClient` has no comment
verb today — deliberately out of scope here; adding one would be a new
`GithubClient` method plus one more line in `run()`, nothing else would need
to change.
"""

from __future__ import annotations

from dataclasses import replace as replace_dataclass

from harness.drivers.github_client import GithubClient
from harness.models import BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior


class LabelIssueBehavior(ConsumerBehavior):
    """`inner` is the step's own behavior (typically `ClaudeCliBehavior`,
    built from the step's `AgentSpec`) — it runs exactly as it would unbound.
    This class never touches the agent, the prompt, or the verdict parsing;
    it only reads the outcome the inner behavior already produced."""

    def __init__(
        self,
        *,
        inner: ConsumerBehavior,
        client: GithubClient,
        labels: dict[str, str],
    ) -> None:
        self._inner = inner
        self._client = client
        self._labels = labels

    async def run(self, task: Task) -> BehaviorResult:
        result = await self._inner.run(task)

        source = task.data.get("source")
        if not source:
            return replace_dataclass(
                result,
                summary=f"{result.summary} (no data.source — label not applied)",
            )

        label = self._labels.get(result.outcome.value)
        if label is None:
            return replace_dataclass(
                result,
                summary=(
                    f"{result.summary} (outcome {result.outcome.value!r} has no "
                    "mapped label — label not applied)"
                ),
            )

        self._client.add_label(source["repo"], source["issue"], label)
        return result
