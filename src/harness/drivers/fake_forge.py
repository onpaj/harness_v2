"""Forge pro filesystémový běh — zapisuje PR do souboru.

Otevřené PR se ukládají jako JSON list do `<root>/prs.json`. Idempotence podle
branch: existuje-li už PR pro danou branch, vrátí se místo založení dalšího.
Skutečný forge (GitHub apod.) se doplní později; tohle stačí pro e2e a smoke.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.models import Task
from harness.ports.forge import Forge, PullRequest


class FakeForge(Forge):
    """Zaznamenává PR do `<root>/prs.json`. Idempotentní podle branch."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._file = self._root / "prs.json"

    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        records = self._load()
        for record in records:
            if record["branch"] == branch:
                return self._to_pr(record)
        number = len(records) + 1
        record = {
            "number": number,
            "url": f"file://{self._root}/prs.json#{number}",
            "branch": branch,
            "title": title,
            "body": body,
        }
        records.append(record)
        self._store(records)
        return self._to_pr(record)

    def _load(self) -> list[dict]:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []

    def _store(self, records: list[dict]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _to_pr(record: dict) -> PullRequest:
        return PullRequest(
            number=record["number"],
            url=record["url"],
            branch=record["branch"],
            title=record["title"],
        )
