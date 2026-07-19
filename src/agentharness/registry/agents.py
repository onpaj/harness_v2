"""Load and validate agent definitions from a directory of YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from agentharness.models import AgentDef

_SUFFIXES = (".yaml", ".yml")


class AgentValidationError(Exception):
    """Raised when the agent definitions on disk are malformed or inconsistent."""


class AgentRegistry:
    """An immutable, validated view of the agents defined under `agents_dir`."""

    def __init__(self, agents: dict[str, AgentDef], agents_dir: Path) -> None:
        self._agents = agents
        self._agents_dir = Path(agents_dir)

    # -- construction --------------------------------------------------------

    @classmethod
    def load(
        cls,
        agents_dir: Path,
        known_repos: set[str] | None = None,
    ) -> AgentRegistry:
        agents_dir = Path(agents_dir)
        if not agents_dir.is_dir():
            raise AgentValidationError(f"agents directory not found: {agents_dir}")

        agents: dict[str, AgentDef] = {}
        sources: dict[str, Path] = {}

        for path in sorted(
            p for p in agents_dir.iterdir() if p.is_file() and p.suffix in _SUFFIXES
        ):
            agent = cls._parse(path)

            if agent.name != path.stem:
                raise AgentValidationError(
                    f"agent file {path.name}: filename stem {path.stem!r} does not "
                    f"match name field {agent.name!r}"
                )
            if agent.name in agents:
                raise AgentValidationError(
                    f"agent {agent.name!r}: duplicate definition in {path.name} "
                    f"(already defined in {sources[agent.name].name})"
                )

            agents[agent.name] = agent
            sources[agent.name] = path

        # Second pass: cross-references, so agents may refer to each other in
        # any order.
        for name, agent in agents.items():
            for target in agent.can_handoff_to:
                if target not in agents:
                    raise AgentValidationError(
                        f"agent {name!r}: can_handoff_to references unknown agent "
                        f"{target!r}"
                    )
            if known_repos is not None:
                for repo in agent.repos:
                    if repo not in known_repos:
                        raise AgentValidationError(
                            f"agent {name!r}: repos references unknown repo {repo!r}"
                        )
            if agent.system_prompt_file is not None:
                prompt_path = agents_dir / agent.system_prompt_file
                if not prompt_path.is_file():
                    raise AgentValidationError(
                        f"agent {name!r}: system_prompt_file "
                        f"{agent.system_prompt_file!r} does not exist "
                        f"(looked in {prompt_path})"
                    )

        return cls(agents, agents_dir)

    @staticmethod
    def _parse(path: Path) -> AgentDef:
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise AgentValidationError(
                f"agent file {path.name}: invalid YAML: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise AgentValidationError(
                f"agent file {path.name}: expected a YAML mapping, got "
                f"{type(raw).__name__}"
            )
        try:
            return AgentDef.model_validate(raw)
        except ValidationError as exc:
            label = raw.get("name") or path.stem
            raise AgentValidationError(
                f"agent {label!r} in {path.name}: {exc}"
            ) from exc

    # -- queries -------------------------------------------------------------

    def get(self, name: str) -> AgentDef:
        return self._agents[name]

    def names(self) -> list[str]:
        return sorted(self._agents)

    def can_handoff(self, src: str, dst: str) -> bool:
        agent = self._agents.get(src)
        if agent is None or dst not in self._agents:
            return False
        return dst in agent.can_handoff_to

    def system_prompt(self, name: str) -> str | None:
        agent = self.get(name)
        if agent.system_prompt_file is None:
            return None
        return (self._agents_dir / agent.system_prompt_file).read_text()

    def __contains__(self, name: object) -> bool:
        return name in self._agents

    def __len__(self) -> int:
        return len(self._agents)
