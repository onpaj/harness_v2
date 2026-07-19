"""The run lifecycle: one task in, one commit out.

A run is a pure function of its immutable inputs. It builds a fresh worktree
from `base_ref`, spawns exactly one `claude -p` process, commits whatever that
process produced, and records the result. Nothing survives in memory between
runs; continuity is entirely carried by the commit.

`execute` never raises. A run that blows up is still a recorded run.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentharness.config import Config
from agentharness.git import mirror as g
from agentharness.git import worktree as wt
from agentharness.git.lock import repo_lock
from agentharness.ids import new_run_id
from agentharness.models import Result, RunRecord, Task
from agentharness.registry.agents import AgentRegistry
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.executor import ExecRequest, Executor, ExecResult
from agentharness.runner.prompt import compose_prompt
from agentharness.runner.result import parse_result
from agentharness.store.runs import RunStore


@dataclass
class RunOutcome:
    run: RunRecord
    result: Result | None = None
    error: str | None = None
    # Carried so the dispatcher can ask the rate-limit gate about the raw CLI
    # outcome rather than pattern-matching a stringified error.
    exec_result: ExecResult | None = None


class Runner:
    def __init__(
        self,
        cfg: Config,
        agents: AgentRegistry,
        repos: RepoRegistry,
        store: RunStore,
        executor: Executor,
    ) -> None:
        self.cfg = cfg
        self.agents = agents
        self.repos = repos
        self.store = store
        self.executor = executor

    def execute(self, task: Task) -> RunOutcome:
        run_id = new_run_id()
        started = datetime.now(timezone.utc)
        branch = f"run/{task.task_id}"
        worktree = self.cfg.worktrees_dir / task.trace_id / task.task_id

        # Upsert the task first: a run row references it, and a Runner driven
        # directly (replay, tests) must not depend on the dispatcher having
        # recorded it earlier.
        self.store.record_task(task, status="running")
        self.store.event(
            "run.started", task_id=task.task_id, trace_id=task.trace_id, run_id=run_id, agent=task.agent
        )

        def record(
            status: str,
            *,
            exec_result: ExecResult | None = None,
            output_ref: str | None = None,
            degraded: bool = False,
            error: str | None = None,
            result: Result | None = None,
        ) -> RunOutcome:
            ended = datetime.now(timezone.utc)
            run = RunRecord(
                run_id=run_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                agent=task.agent,
                attempt=task.attempt,
                status=status,
                exit_code=exec_result.exit_code if exec_result else None,
                is_error=bool(exec_result and exec_result.is_error),
                degraded=degraded,
                started_at=started,
                ended_at=ended,
                duration_ms=int((ended - started).total_seconds() * 1000),
                claude_session_id=exec_result.session_id if exec_result else None,
                num_turns=exec_result.num_turns if exec_result else None,
                total_cost_usd=exec_result.total_cost_usd if exec_result else None,
                workspace_path=str(worktree),
                output_ref=output_ref,
                branch=branch if output_ref else None,
                stdout_log=f"{task.artifact_dir}/logs/stdout.log" if output_ref else None,
                stderr_log=f"{task.artifact_dir}/logs/stderr.log" if output_ref else None,
            )
            self.store.record_run(run)
            self.store.event(
                "run.finished",
                task_id=task.task_id,
                trace_id=task.trace_id,
                run_id=run_id,
                agent=task.agent,
                data={"status": status, "degraded": degraded, "error": error, "output_ref": output_ref},
            )
            return RunOutcome(run=run, result=result, error=error, exec_result=exec_result)

        try:
            agent = self.agents.get(task.agent)
            repo = self.repos.resolve(task.repo)
            mirror = self.repos.mirror_path(repo.repo_id)
            base_ref = task.artifacts.base_ref or g.resolve_ref(mirror, repo.base_branch)
        except Exception as exc:  # noqa: BLE001 -- a run must always be recorded
            return record("failed", error=f"preparation failed: {exc}")

        try:
            try:
                with repo_lock(self.cfg.home, repo.repo_id):
                    worktree.parent.mkdir(parents=True, exist_ok=True)
                    wt.add_worktree(mirror, worktree, branch, base_ref)

                wt.write_json(
                    worktree, f"{task.artifact_dir}/task.json", task.model_dump(mode="json")
                )

                exec_result = self.executor.run(
                    ExecRequest(
                        prompt=compose_prompt(task, agent),
                        cwd=worktree,
                        system_prompt=self.agents.system_prompt(agent.name),
                        allowed_tools=agent.allowed_tools,
                        disallowed_tools=agent.disallowed_tools,
                        permission_mode=agent.permission_mode,
                        model=agent.model,
                        max_turns=agent.max_turns,
                        mcp_config=Path(agent.mcp_config) if agent.mcp_config else None,
                        timeout_seconds=agent.timeout_seconds,
                    )
                )

                self._write_logs(worktree, task, exec_result)
                parsed = parse_result(worktree, task, exec_result)

                # Always commit a result.json, even a synthesised one, so the
                # record in git is complete for every run.
                result_path = worktree / task.artifact_dir / "result.json"
                if not result_path.exists():
                    result_path.parent.mkdir(parents=True, exist_ok=True)
                    result_path.write_text(parsed.result.model_dump_json(indent=2))

                with repo_lock(self.cfg.home, repo.repo_id):
                    output_ref = wt.commit_all(
                        worktree,
                        f"{task.agent}: {task.intent} ({task.task_id})",
                        self.cfg,
                    )

                if exec_result.timed_out:
                    return record(
                        "timeout",
                        exec_result=exec_result,
                        output_ref=output_ref,
                        degraded=parsed.degraded,
                        error="run exceeded timeout_seconds",
                        result=parsed.result,
                    )

                if exec_result.is_error or parsed.result.status == "failed":
                    return record(
                        "failed",
                        exec_result=exec_result,
                        output_ref=output_ref,
                        degraded=parsed.degraded,
                        error=parsed.reason or "agent reported failure",
                        result=parsed.result,
                    )

                return record(
                    "ok",
                    exec_result=exec_result,
                    output_ref=output_ref,
                    degraded=parsed.degraded,
                    error=parsed.reason,
                    result=parsed.result,
                )
            finally:
                self._cleanup(mirror, repo.repo_id, worktree)
        except Exception as exc:  # noqa: BLE001 -- a run must always be recorded
            return record("failed", error=f"{type(exc).__name__}: {exc}")

    def _write_logs(self, worktree: Path, task: Task, exec_result: ExecResult) -> None:
        logs = worktree / task.artifact_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "stdout.log").write_text(exec_result.stdout or "")
        (logs / "stderr.log").write_text(exec_result.stderr or "")
        if exec_result.cli_json is not None:
            (logs / "cli.json").write_text(json.dumps(exec_result.cli_json, indent=2))

    def _cleanup(self, mirror: Path, repo_id: str, worktree: Path) -> None:
        """The branch is the durable artifact; the directory is disposable."""
        try:
            with repo_lock(self.cfg.home, repo_id):
                wt.remove_worktree(mirror, worktree)
        except Exception:  # noqa: BLE001
            shutil.rmtree(worktree, ignore_errors=True)
