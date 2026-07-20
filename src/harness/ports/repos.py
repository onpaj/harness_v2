from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class RepositoryNotFound(Exception):
    """Repo daného jména není v registru."""


class InvalidRepositoryDefinition(RepositoryNotFound):
    """Definice repa v configu má neplatný tvar (chybí `path`, špatný typ…).

    Podtřída `RepositoryNotFound`: kdo chytá „repo nejde získat", chytí i tohle;
    kdo chce rozlišit „chybí" od „je rozbité", má na to zvlášť typ."""


@dataclass(frozen=True)
class RepositoryDefinition:
    """Co o repu víme na tomhle stroji.

    Dvě věci, které si uživatel nastaví ke každému repu:

    - **`path`** — lokální složka (kořen repa na tomhle stroji). Z ní se
      odvozují worktree tasků. Povinná.
    - **`source`** — *nepovinný* původ repa venku, typicky GitHub URL
      (`https://github.com/owner/name`) nebo slug `owner/name`. Odkud tečou
      issue a kam míří PR. Je to **neprůhledný řetězec**: co znamená „GitHub",
      ví až driver, který ho interpretuje (viz `github_source`). `None` = repo
      je jen lokální, bez napojení na vnější forge.

    Task nese pouze logické jméno repa, nikdy cestu ani URL — ty žijí tady,
    machine-specific."""

    name: str
    path: Path
    source: str | None = None


class RepositoryRegistry(ABC):
    """Mapa logické jméno repa → jeho definice na tomhle stroji.

    Mapa je machine-specific: kde repo na tomhle stroji leží a odkud pochází, ví
    jen tenhle registr. Task nese pouze logické jméno (`"harness_v2"`), nikdy
    cestu ani URL — tím zůstává přenositelný mezi stroji a layout konkrétního
    disku (ani napojení na konkrétní forge) do něj neprosakuje."""

    @abstractmethod
    def get(self, name: str) -> RepositoryDefinition:
        """Vrať definici repa pro jméno. Neznámé jméno → `RepositoryNotFound`,
        rozbitá definice → `InvalidRepositoryDefinition`."""

    def resolve(self, name: str) -> Path:
        """Kořen repa pro jméno. Zkratka nad `get(name).path` — zachována kvůli
        volajícím (`GitWorkspace`), kterým stačí lokální cesta."""
        return self.get(name).path
