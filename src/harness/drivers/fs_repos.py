"""Registr rep z JSON configu (`repos.json`).

Každý záznam je jednou ze dvou forem:

- **zkratka** — `"<jméno>": "<cesta>"` — jen lokální složka, bez zdroje;
- **plný objekt** — `"<jméno>": {"path": "<cesta>", "source": "<github-url|slug>"}`
  — k lokální složce přidá i původ repa venku (GitHub URL nebo `owner/name`).

Obě formy se validují; rozbitý tvar (chybí `path`, špatný typ) →
`InvalidRepositoryDefinition`, aby si uživatel chybu v configu přečetl hned,
ne až o krok dál v gitu.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.ports.repos import (
    InvalidRepositoryDefinition,
    RepositoryDefinition,
    RepositoryNotFound,
    RepositoryRegistry,
)


class FilesystemRepositoryRegistry(RepositoryRegistry):
    def __init__(self, config: Path) -> None:
        self._config = Path(config)

    def get(self, name: str) -> RepositoryDefinition:
        raw = self._load()
        try:
            entry = raw[name]
        except KeyError:
            raise RepositoryNotFound(
                f"repo {name!r} není v registru ({self._config})"
            ) from None
        return self._definition(name, entry)

    def _load(self) -> dict:
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
        return raw

    def _definition(self, name: str, entry: object) -> RepositoryDefinition:
        # Zkratka: holý řetězec je cesta bez zdroje.
        if isinstance(entry, str):
            return RepositoryDefinition(name=name, path=self._path(name, entry))

        if isinstance(entry, dict):
            if "path" not in entry:
                raise InvalidRepositoryDefinition(
                    f"repo {name!r} nemá povinné 'path' ({self._config})"
                )
            path = entry["path"]
            if not isinstance(path, str):
                raise InvalidRepositoryDefinition(
                    f"repo {name!r}: 'path' musí být řetězec, "
                    f"ne {type(path).__name__} ({self._config})"
                )
            source = entry.get("source")
            if source is not None and not isinstance(source, str):
                raise InvalidRepositoryDefinition(
                    f"repo {name!r}: 'source' musí být řetězec nebo chybět, "
                    f"ne {type(source).__name__} ({self._config})"
                )
            return RepositoryDefinition(
                name=name, path=self._path(name, path), source=source
            )

        raise InvalidRepositoryDefinition(
            f"repo {name!r}: definice musí být cesta (řetězec) nebo objekt, "
            f"ne {type(entry).__name__} ({self._config})"
        )

    def _path(self, name: str, value: str) -> Path:
        if not value.strip():
            raise InvalidRepositoryDefinition(
                f"repo {name!r}: 'path' je prázdné ({self._config})"
            )
        return Path(value).expanduser()
