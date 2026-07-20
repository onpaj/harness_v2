"""Repository registry from a JSON config `{"<name>": "<path>"}`."""

from __future__ import annotations

import json
from pathlib import Path

from harness.ports.repos import RepositoryNotFound, RepositoryRegistry


class FilesystemRepositoryRegistry(RepositoryRegistry):
    def __init__(self, config: Path) -> None:
        self._config = Path(config)

    def resolve(self, name: str) -> Path:
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

        try:
            path = raw[name]
        except KeyError:
            raise RepositoryNotFound(
                f"repo {name!r} is not in the registry ({self._config})"
            ) from None

        return Path(path).expanduser()
