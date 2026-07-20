"""Landing: přiklopí artefakty do worktree a otevře PR.

Poslední krok před `end`. Je to normální behavior — může selhat a spadnout do
`failed/` stejně jako kterýkoli jiný krok. `end` zůstává čistý terminál.
"""

from __future__ import annotations

from harness.models import BehaviorResult, Outcome, Task
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
    ) -> None:
        self._clock = clock
        self._workspace = workspace
        self._artifacts = artifacts
        self._forge = forge
        self._dest = dest

    async def run(self, task: Task) -> BehaviorResult:
        handle = self._workspace.attach(task)

        for ref in self._artifacts.list(task.id):
            content = self._artifacts.read(task.id, ref.step, ref.attempt, ref.name)
            if content is None:
                continue
            relpath = f"{self._dest}/{task.id}/{ref.step}/{ref.attempt}/{ref.name}"
            handle.write(relpath, content)
        handle.commit("[land] artefakty tasku")

        pull = self._forge.open_pull_request(
            task,
            branch=handle.branch,
            title=self._title(task),
            body=self._body(task),
        )
        return BehaviorResult(Outcome.DONE, f"otevřen PR {pull.url}")

    @staticmethod
    def _title(task: Task) -> str:
        for key in ("title", "request", "summary"):
            value = task.data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return f"harness task {task.id}"

    @staticmethod
    def _body(task: Task) -> str:
        """Tělo PR z agregovaných summary consumer řádků historie."""
        lines = ["## Co task udělal", ""]
        for entry in task.history:
            if entry.actor.startswith("consumer:") and entry.summary:
                lines.append(f"- **{entry.from_step}** — {entry.summary}")
        return "\n".join(lines) + "\n"
