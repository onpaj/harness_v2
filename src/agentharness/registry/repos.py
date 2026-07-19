"""Managed repositories.

The harness works *above* a repo, never inside it. For each registered repo it
keeps a bare mirror and adds worktrees off that mirror, so the user's own
checkout is never touched no matter how many runs execute concurrently.

Repo-less agents are not a special case: they resolve to an internal scratch
repo, so every run always has a worktree and an output commit.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentharness.config import Config
from agentharness.git import mirror as g
from agentharness.git.lock import repo_lock
from agentharness.models import SCRATCH_REPO_ID, RepoDef


class RepoRegistry:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._file = config.home / "repos.yaml"

    # -- persistence ----------------------------------------------------

    def _read(self) -> dict[str, RepoDef]:
        if not self._file.exists():
            return {}
        raw = yaml.safe_load(self._file.read_text()) or {}
        return {k: RepoDef(**v) for k, v in raw.items()}

    def _write(self, repos: dict[str, RepoDef]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            yaml.safe_dump({k: v.model_dump() for k, v in sorted(repos.items())})
        )

    # -- lookup ---------------------------------------------------------

    def mirror_path(self, repo_id: str) -> Path:
        return self.config.repos_dir / f"{repo_id}.git"

    def get(self, repo_id: str) -> RepoDef:
        repos = self._read()
        if repo_id not in repos:
            raise KeyError(f"no managed repo {repo_id!r}")
        return repos[repo_id]

    def list(self) -> list[RepoDef]:
        return list(self._read().values())

    def resolve(self, repo_id: str | None) -> RepoDef:
        """`None` means a repo-less agent, which runs against the scratch repo."""
        if repo_id is None:
            return self.ensure_scratch()
        return self.get(repo_id)

    # -- mutation -------------------------------------------------------

    def add(
        self,
        repo_id: str,
        url: str,
        integration_branch: str | None = None,
        base_branch: str | None = None,
    ) -> RepoDef:
        repo = RepoDef(
            repo_id=repo_id,
            url=url,
            integration_branch=integration_branch or self.config.default_integration_branch,
            base_branch=base_branch or self.config.default_base_branch,
        )
        dest = self.mirror_path(repo_id)
        self.config.repos_dir.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            g.clone_mirror(url, dest)

        repos = self._read()
        repos[repo_id] = repo
        self._write(repos)
        return repo

    def sync(self, repo_id: str) -> None:
        with repo_lock(self.config.home, repo_id):
            g.fetch(self.mirror_path(repo_id))

    def ensure_scratch(self) -> RepoDef:
        """Idempotently create the internal repo backing repo-less agents."""
        repos = self._read()
        dest = self.mirror_path(SCRATCH_REPO_ID)
        base = self.config.default_base_branch

        if not dest.exists():
            self.config.repos_dir.mkdir(parents=True, exist_ok=True)
            g.init_bare(dest)
            g.empty_root_commit(dest, branch=base)

        if SCRATCH_REPO_ID not in repos:
            repos[SCRATCH_REPO_ID] = RepoDef(
                repo_id=SCRATCH_REPO_ID,
                url="",
                integration_branch=self.config.default_integration_branch,
                base_branch=base,
            )
            self._write(repos)

        return repos.get(SCRATCH_REPO_ID) or self._read()[SCRATCH_REPO_ID]
