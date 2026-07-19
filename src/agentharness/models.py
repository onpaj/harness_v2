"""Every type shared across the harness. This module imports nothing from agentharness."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCRATCH_REPO_ID = "_scratch"

PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions"]
BackoffKind = Literal["exponential", "linear", "fixed"]
ResultStatus = Literal["ok", "failed", "needs_input"]
RunStatus = Literal["ok", "failed", "timeout"]
TaskStatus = Literal["pending", "leased", "running", "done", "failed", "dead", "blocked"]


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 3
    backoff: BackoffKind = "exponential"


class AgentDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    model: str | None = None
    permission_mode: PermissionMode = "acceptEdits"
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    mcp_config: str | None = None
    system_prompt_file: str | None = None
    max_turns: int = 25
    timeout_seconds: int = 900
    concurrency: int = 1
    retries: RetryPolicy = Field(default_factory=RetryPolicy)
    repos: list[str] = Field(default_factory=list)
    can_handoff_to: list[str] = Field(default_factory=list)


class TaskArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_ref: str | None = None
    inputs: list[str] = Field(default_factory=list)


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    parent_task_id: str | None = None
    agent: str
    repo: str | None = None
    intent: str
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: TaskArtifacts = Field(default_factory=TaskArtifacts)
    idempotency_key: str
    priority: int = 5
    attempt: int = 1
    created_at: datetime
    schedule_id: str | None = None

    @property
    def artifact_dir(self) -> str:
        return f".harness/runs/{self.trace_id}/{self.task_id}"


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    intent: str
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: TaskArtifacts = Field(default_factory=TaskArtifacts)


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    summary: str = ""
    outputs: list[str] = Field(default_factory=list)
    handoffs: list[Handoff] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str
    trace_id: str
    agent: str
    attempt: int
    status: RunStatus
    exit_code: int | None = None
    is_error: bool = False
    degraded: bool = False
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    claude_session_id: str | None = None
    num_turns: int | None = None
    total_cost_usd: float | None = None
    workspace_path: str | None = None
    output_ref: str | None = None
    branch: str | None = None
    stdout_log: str | None = None
    stderr_log: str | None = None


class RepoDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    url: str
    integration_branch: str = "harness/integration"
    base_branch: str = "main"


class ScheduleDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: str
    cron: str
    agent: str
    intent: str
    repo: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
