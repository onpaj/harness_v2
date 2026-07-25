"""Repository registry from a JSON config `{"<name>": "<path>"}`."""

from __future__ import annotations

import json
from pathlib import Path

from harness.ports.repos import RepositoryNotFound, RepositoryRegistry


class FilesystemRepositoryRegistry(RepositoryRegistry):
    def __init__(self, config: Path) -> None:
        self._config = Path(config)

    def _load(self) -> dict:
        try:
            raw = json.loads(self._config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RepositoryNotFound(
                f"repos config does not exist ({self._config})"
            ) from None
        except json.JSONDecodeError as error:
            raise RepositoryNotFound(
                f"repos config has broken JSON ({self._config}): {error}"
            ) from None
        if not isinstance(raw, dict):
            raise RepositoryNotFound(
                f"repos config has an invalid shape: expected object, "
                f"got {type(raw).__name__}"
            )
        return raw

    def resolve(self, name: str) -> Path:
        raw = self._load()
        try:
            entry = raw[name]
        except KeyError:
            raise RepositoryNotFound(
                f"repo {name!r} is not in the registry ({self._config})"
            ) from None
        # String form: the value is the path. Object form: {"path": ..., "verify": ...}.
        if isinstance(entry, dict):
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                raise RepositoryNotFound(
                    f"repo {name!r} has an object entry without a 'path' "
                    f"({self._config})"
                )
            return Path(path).expanduser()
        return Path(entry).expanduser()

    def verify_command(self, name: str) -> str | None:
        try:
            raw = self._load()
        except RepositoryNotFound:
            return None
        entry = raw.get(name)
        if isinstance(entry, dict):
            verify = entry.get("verify")
            if isinstance(verify, str) and verify.strip():
                return verify
        return None

    def names(self) -> list[str]:
        try:
            raw = json.loads(self._config.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(raw, dict):
            return []
        return list(raw)
