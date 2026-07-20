"""Registr rep z JSON configu `{"<jméno>": "<cesta>"}`."""

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
                f"config rep neexistuje ({self._config})"
            ) from None
        except json.JSONDecodeError as error:
            raise RepositoryNotFound(
                f"config rep má rozbitý JSON ({self._config}): {error}"
            ) from None

        if not isinstance(raw, dict):
            raise RepositoryNotFound(
                f"config rep má neplatný tvar: očekáván objekt, "
                f"nalezeno {type(raw).__name__}"
            )

        try:
            path = raw[name]
        except KeyError:
            raise RepositoryNotFound(
                f"repo {name!r} není v registru ({self._config})"
            ) from None

        return Path(path).expanduser()
