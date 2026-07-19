"""The only module in the harness that speaks to Claude.

Contact is exclusively `subprocess` spawning the `claude` CLI in headless mode.
No Anthropic SDK, no HTTP client, no API surface of any kind. Statelessness is
structural: `build_argv` never emits `--resume` or `--continue`, so every run is
a clean session and continuity comes only from committed artifacts.
"""

from __future__ import annotations

import json
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Flags that would give a run memory of a previous one. Asserted absent by tests.
FORBIDDEN_FLAGS = ("--resume", "--continue")


@dataclass
class ExecRequest:
    prompt: str
    cwd: Path
    system_prompt: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "acceptEdits"
    model: str | None = None
    max_turns: int = 25
    mcp_config: Path | None = None
    timeout_seconds: int = 900


@dataclass
class ExecResult:
    exit_code: int
    is_error: bool
    stdout: str = ""
    stderr: str = ""
    cli_json: dict | None = None
    session_id: str | None = None
    num_turns: int | None = None
    total_cost_usd: float | None = None
    result_text: str | None = None
    duration_ms: int = 0
    timed_out: bool = False


class Executor(ABC):
    @abstractmethod
    def run(self, req: ExecRequest) -> ExecResult:
        """Execute one agent invocation and return its outcome."""


class LocalExecutor(Executor):
    """Spawns the real `claude` binary."""

    def __init__(self, binary: str = "claude") -> None:
        self.binary = binary

    def build_argv(self, req: ExecRequest) -> list[str]:
        """Pure: builds the command line, performs no I/O."""
        argv = [self.binary, "-p", req.prompt, "--output-format", "json"]
        argv += ["--permission-mode", req.permission_mode]
        argv += ["--max-turns", str(req.max_turns)]
        argv += ["--add-dir", str(req.cwd)]
        if req.allowed_tools:
            argv += ["--allowedTools", ",".join(req.allowed_tools)]
        if req.disallowed_tools:
            argv += ["--disallowedTools", ",".join(req.disallowed_tools)]
        if req.model:
            argv += ["--model", req.model]
        if req.mcp_config:
            argv += ["--mcp-config", str(req.mcp_config)]
        if req.system_prompt:
            argv += ["--append-system-prompt", req.system_prompt]
        return argv

    def run(self, req: ExecRequest) -> ExecResult:
        argv = self.build_argv(req)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(req.cwd),
                capture_output=True,
                text=True,
                timeout=req.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                exit_code=-1,
                is_error=True,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        cli_json = _parse_json(proc.stdout)
        is_error = proc.returncode != 0
        if cli_json is not None and cli_json.get("is_error"):
            is_error = True

        return ExecResult(
            exit_code=proc.returncode,
            is_error=is_error,
            stdout=proc.stdout,
            stderr=proc.stderr,
            cli_json=cli_json,
            session_id=(cli_json or {}).get("session_id"),
            num_turns=(cli_json or {}).get("num_turns"),
            total_cost_usd=(cli_json or {}).get("total_cost_usd"),
            result_text=(cli_json or {}).get("result"),
            duration_ms=duration_ms,
        )


class FakeExecutor(Executor):
    """Test double. Records requests; the script may write files into req.cwd."""

    def __init__(self, script: Callable[[ExecRequest], ExecResult] | None = None) -> None:
        self.script = script
        self.requests: list[ExecRequest] = []

    def run(self, req: ExecRequest) -> ExecResult:
        self.requests.append(req)
        if self.script is None:
            return ExecResult(exit_code=0, is_error=False, result_text="fake", duration_ms=1)
        return self.script(req)


def fake_ok(
    result_payload: dict,
    artifact_dir: str,
    *,
    cost: float = 0.01,
    turns: int = 3,
) -> Callable[[ExecRequest], ExecResult]:
    """Script helper: writes result.json into the run's artifact dir, reports success."""

    def _script(req: ExecRequest) -> ExecResult:
        target = req.cwd / artifact_dir
        target.mkdir(parents=True, exist_ok=True)
        (target / "result.json").write_text(json.dumps(result_payload, indent=2))
        return ExecResult(
            exit_code=0,
            is_error=False,
            stdout=json.dumps({"result": "done"}),
            result_text="done",
            session_id="sess_fake",
            num_turns=turns,
            total_cost_usd=cost,
            duration_ms=5,
        )

    return _script


def _parse_json(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_text(value) -> str:
    if value is None:
        return ""
    return value.decode() if isinstance(value, bytes) else str(value)
