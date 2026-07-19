"""Parsing the agent's result contract.

Prefer the committed `result.json`. When it is missing or unusable, fall back to
the CLI's own result text and mark the run degraded, so a malformed contract
never silently looks like a clean success.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agentharness.models import Result, Task
from agentharness.runner.executor import ExecResult


@dataclass
class ParsedResult:
    result: Result
    degraded: bool
    reason: str | None = None


def parse_result(worktree: Path, task: Task, exec_result: ExecResult) -> ParsedResult:
    path = worktree / task.artifact_dir / "result.json"

    if not path.exists():
        return _fallback(exec_result, "result.json was not written")

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return _fallback(exec_result, f"result.json is not valid JSON: {exc}")

    try:
        return ParsedResult(result=Result.model_validate(raw), degraded=False)
    except ValidationError as exc:
        return _fallback(exec_result, f"result.json failed schema validation: {exc.error_count()} error(s)")


def _fallback(exec_result: ExecResult, reason: str) -> ParsedResult:
    status = "failed" if exec_result.is_error else "ok"
    return ParsedResult(
        result=Result(status=status, summary=exec_result.result_text or ""),
        degraded=True,
        reason=reason,
    )
