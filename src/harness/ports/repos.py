from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class RepositoryNotFound(Exception):
    """Repo daného jména není v registru."""


class RepositoryRegistry(ABC):
    """Mapa logické jméno repa → jeho kořen na disku.

    Mapa je machine-specific: kde repo na tomhle stroji leží, ví jen tenhle
    registr. Task nese pouze logické jméno (`"harness_v2"`), nikdy cestu — tím
    zůstává přenositelný mezi stroji a layout konkrétního disku do něj
    neprosakuje."""

    @abstractmethod
    def resolve(self, name: str) -> Path:
        """Vrať kořen repa pro jméno. Neznámé jméno → RepositoryNotFound."""
