"""Global harness configuration and the on-disk home layout."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "usage limit",
    "too many requests",
    "429",
]

SUBDIRS = ("agents", "repos", "queues", "worktrees", "locks", "logs")


def harness_home() -> Path:
    env = os.environ.get("AGENTHARNESS_HOME")
    return Path(env).expanduser() if env else Path.home() / ".agentharness"


class Config(BaseModel):
    home: Path
    max_concurrency: int = 3
    lease_timeout_seconds: int = 1800
    poll_interval_seconds: float = 1.0
    branch_retention_days: int = 30
    claude_binary: str = "claude"
    default_integration_branch: str = "harness/integration"
    default_base_branch: str = "main"
    rate_limit_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_RATE_LIMIT_PATTERNS)
    )
    retry_base_seconds: float = 30.0
    retry_max_backoff_seconds: float = 3600.0
    rate_limit_initial_backoff_seconds: int = 60
    rate_limit_max_backoff_seconds: int = 3600
    commit_author_name: str = "agentharness"
    commit_author_email: str = "agentharness@localhost"

    @property
    def db_path(self) -> Path:
        return self.home / "runs.db"

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    @property
    def repos_dir(self) -> Path:
        return self.home / "repos"

    @property
    def queues_dir(self) -> Path:
        return self.home / "queues"

    @property
    def worktrees_dir(self) -> Path:
        return self.home / "worktrees"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRS:
            (self.home / name).mkdir(exist_ok=True)


def load_config(home: Path | None = None) -> Config:
    root = home or harness_home()
    data: dict = {}
    cfg_file = root / "config.yaml"
    if cfg_file.exists():
        data = yaml.safe_load(cfg_file.read_text()) or {}
    data["home"] = root
    return Config(**data)
