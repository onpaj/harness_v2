"""`ClaudeCliBehavior` — práci kroku svěří agentovi za `AgentRunner`.

Nahrazuje `DummyBehavior`: připojí worktree, spočítá číslo pokusu v
`.artifacts/<id>/`, sestaví prompt persony, spustí agenta a jeho verdikt
namapuje 1:1 na `BehaviorResult`. Commit dělá tenhle worker, ne agent
(invariant 9). Behavior **nevětví na hodnotě outcome ani na jménu agenta**
(invarianty 2, 14) — rozdíl mezi personami je obsah `AgentSpec`u.

Výjimky z runneru (`AgentError` / `VerdictError` / timeout) nechává probublat —
consumer je zvládne přes `_fail` a task skončí v `failed/`.
"""

from __future__ import annotations

from harness.artifacts_layout import next_attempt
from harness.models import BehaviorResult, Task
from harness.ports.agent import AgentRunner, AgentSpec
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.workspace import Workspace


class ClaudeCliBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        clock: Clock,
        workspace: Workspace,
        runner: AgentRunner,
        spec: AgentSpec,
        timeout: float = 600.0,
    ) -> None:
        self._clock = clock
        self._workspace = workspace
        self._runner = runner
        self._spec = spec
        self._timeout = timeout

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or ""
        handle = self._workspace.attach(task)

        # Zbytek `ArtifactStore.begin()` z fáze 2: oskenuj `.artifacts/<id>/`
        # ve worktree a alokuj číslo dalšího pokusu tohoto kroku.
        _attempt, relpath = next_attempt(handle.path, task.id, step)

        prompt = compose_prompt(
            task, step=step, artifact_relpath=relpath, spec=self._spec
        )
        run = await self._runner.run(
            prompt=prompt, spec=self._spec, cwd=handle.path, timeout=self._timeout
        )

        # Agent artefakty i kód jen zapsal; commit spouští worker (invariant 9).
        handle.commit(run.summary)
        return BehaviorResult(run.outcome, run.summary)


def compose_prompt(
    task: Task, *, step: str, artifact_relpath: str, spec: AgentSpec
) -> str:
    """Sestav instrukci pro agenta kroku.

    Stručná, deterministická: co je úkol (z `task.data`), ať přečte předchozí
    artefakty ve svém cwd, kam zapsat výstup a jak skončit strojově čitelným
    verdiktem s outcome z povolené množiny.
    """
    request = _request_of(task)
    allowed = ", ".join(outcome.value for outcome in spec.allowed_outcomes)
    artifacts_dir = f".artifacts/{task.id}/"

    lines = [
        f"Jsi agent kroku '{step}' tasku {task.id}.",
        f"Úkol: {request}" if request else "Úkol tasku není blíže popsán.",
        "",
        f"Kontext předchozích kroků najdeš jako soubory v adresáři "
        f"{artifacts_dir} ve svém pracovním adresáři — přečti si je, než začneš.",
        f"Svůj výstup tohoto kroku zapiš do souboru {artifact_relpath}.",
        "",
        "Až budeš hotov, skonči přesně tímto strojově čitelným verdiktem "
        "(a ničím za ním):",
        "```json",
        '{"outcome": "<jeden z: ' + allowed + '>", "summary": "<krátké shrnutí>"}',
        "```",
    ]
    return "\n".join(lines)


def _request_of(task: Task) -> str:
    for key in ("request", "title", "summary"):
        value = task.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
