"""Reálný `AgentRunner` kolem `claude -p` (headless CLI).

Driver se skládá ze dvou čistých funkcí a tenké subprocess slupky:

- `build_argv` složí argv pro `claude -p` z persony a `AgentSpec`u. Čistá
  funkce — testovatelná bez subprocessu.
- `parse_verdict` vytáhne z JSON obálky `claude -p` finální text agenta a z něj
  strojově čitelný verdikt `{outcome, summary}`. Čistá funkce.
- `ClaudeCliRunner` je spojí: složí argv, spustí `claude` v `cwd`, ohlídá
  timeout a exit kód, a vrátí `parse_verdict` výsledku.

Volá systémový `claude` přes `asyncio.create_subprocess_exec` — žádná nová
produkční závislost. Reálný `claude` v test sadě NEBĚŽÍ; čisté funkce se testují
napřímo, subprocess slupku pokrývá opt-in smoke.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from harness.models import Outcome
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec

# Poslední fenced ```json ... ``` blok ve finálním textu agenta.
_FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)


class AgentError(Exception):
    """`claude` proces selhal — nenulový exit, pád, nebo timeout."""


class VerdictError(Exception):
    """Výstup `claude -p` nese nečitelný, chybějící nebo nepovolený verdikt."""


def build_argv(
    *, prompt: str, spec: AgentSpec, output_format: str = "json"
) -> list[str]:
    """Složí argv pro `claude -p`. Čistá funkce, žádné I/O.

    Base flagy jsou vždy; `--model`, `--fallback-model` a `--allowedTools` se
    přidají jen když je spec nese. Persona jde přes `--append-system-prompt`.
    """
    argv = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        output_format,
        "--permission-mode",
        "bypassPermissions",
        "--setting-sources",
        "project",
        "--append-system-prompt",
        spec.prompt,
    ]
    if spec.model is not None:
        argv += ["--model", spec.model]
    if spec.fallback_model is not None:
        argv += ["--fallback-model", spec.fallback_model]
    if spec.allowed_tools:
        argv += ["--allowedTools", ",".join(spec.allowed_tools)]
    return argv


def _extract_verdict(result: str) -> dict:
    """Z finálního textu agenta vytáhni `{outcome, summary}`.

    Bere poslední fenced ```json``` blok; není-li žádný, zkusí naparsovat celý
    text jako JSON. Nečitelný → `VerdictError`.
    """
    blocks = _FENCED_JSON.findall(result)
    candidate = blocks[-1] if blocks else result
    try:
        verdict = json.loads(candidate)
    except (json.JSONDecodeError, ValueError) as error:
        raise VerdictError(
            f"verdikt není čitelný JSON: {candidate!r}"
        ) from error
    if not isinstance(verdict, dict):
        raise VerdictError(f"verdikt není objekt: {verdict!r}")
    return verdict


def parse_verdict(stdout: str, *, allowed: tuple[Outcome, ...]) -> AgentRun:
    """Z JSON obálky `claude -p` vytáhni verdikt a namapuj ho na `AgentRun`.

    Vnější JSON nese `result` (finální text agenta) a `is_error`. Chybí-li
    `result`, je-li `is_error`, nebo je-li obálka/verdikt nečitelný či outcome
    mimo `allowed` → `VerdictError`. `raw` nese celé `stdout`.
    """
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as error:
        raise VerdictError(
            f"výstup claude není čitelný JSON: {stdout!r}"
        ) from error
    if not isinstance(envelope, dict):
        raise VerdictError(f"obálka claude není objekt: {envelope!r}")
    if envelope.get("is_error"):
        raise VerdictError(f"claude ohlásil chybu: {stdout!r}")
    if "result" not in envelope:
        raise VerdictError(f"obálka claude nemá pole 'result': {stdout!r}")

    verdict = _extract_verdict(envelope["result"])
    if "outcome" not in verdict:
        raise VerdictError(f"verdikt nemá pole 'outcome': {verdict!r}")
    try:
        outcome = Outcome(verdict["outcome"])
    except ValueError as error:
        raise VerdictError(
            f"neznámý outcome {verdict['outcome']!r}"
        ) from error
    if outcome not in allowed:
        raise VerdictError(
            f"outcome {outcome.value!r} není v allowed {allowed!r}"
        )
    return AgentRun(outcome, summary=verdict.get("summary", ""), raw=stdout)


class ClaudeCliRunner(AgentRunner):
    """Spustí `claude -p` v `cwd` a vrátí verdikt.

    Timeout vede na `AgentError` (ne `VerdictError`): vypršelý čas je selhání
    procesu, ne vada jeho výstupu — symetricky s nenulovým exit kódem. Verdikt
    ještě neexistuje, takže o něm nemá co tvrdit. Obojí končí přes `_fail`
    v `failed/`, jen s jasnějším typem chyby.
    """

    async def run(
        self, *, prompt: str, spec: AgentSpec, cwd: Path, timeout: float
    ) -> AgentRun:
        argv = build_argv(prompt=prompt, spec=spec)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout
            )
        except asyncio.TimeoutError as error:
            proc.kill()
            await proc.wait()
            raise AgentError(f"claude timeout po {timeout}s") from error
        if proc.returncode != 0:
            raise AgentError(
                f"claude skončil s exit {proc.returncode}: "
                f"{stderr.decode().strip()}"
            )
        return parse_verdict(stdout.decode(), allowed=spec.allowed_outcomes)
