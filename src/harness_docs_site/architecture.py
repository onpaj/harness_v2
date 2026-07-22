"""The curated architecture model the Explorer draws — the single source of
truth for the diagram, its animated flow, and every part's drill-down.

Hand-authored (not derived from the live module graph): a small, legible graph
of the harness's parts, each grounded in the ADR(s) that decide it. `validate`
keeps this model honest against the real `docs/adr/` files and `src/` tree, and
runs in the test suite so a rename that breaks the mapping fails CI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

_KINDS = {"port", "driver", "core", "ui", "store"}


@dataclass(frozen=True)
class Part:
    id: str
    name: str
    kind: str  # one of _KINDS
    tagline: str
    description: str
    adrs: tuple[str, ...]  # ADR slugs: docs/adr/<slug>.md
    sources: tuple[str, ...]  # repo-relative source paths
    x: float  # 0..100 diagram coordinate
    y: float  # 0..100 diagram coordinate
    related_docs: tuple[str, ...] = ()  # optional extra doc slugs (specs/plans)


@dataclass(frozen=True)
class Stage:
    part_id: str
    caption: str


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str


@dataclass(frozen=True)
class ArchitectureModel:
    parts: tuple[Part, ...]
    flow: tuple[Stage, ...]
    edges: tuple[Edge, ...]


def validate(model: ArchitectureModel, repo_root: Path) -> None:
    """Raise ValueError if the model is internally inconsistent or has drifted
    from the real docs/source tree. Silence means coherent."""
    repo_root = Path(repo_root)
    ids = [p.id for p in model.parts]
    id_set = set(ids)

    if len(ids) != len(id_set):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate part id(s): {dupes}")

    for part in model.parts:
        if part.kind not in _KINDS:
            raise ValueError(f"part {part.id!r} has unknown kind {part.kind!r}")
        for slug in part.adrs:
            if not (repo_root / "docs" / "adr" / f"{slug}.md").is_file():
                raise ValueError(
                    f"part {part.id!r} cites missing ADR {slug!r}"
                )
        for src in part.sources:
            if not (repo_root / src).exists():
                raise ValueError(
                    f"part {part.id!r} cites missing source path {src!r}"
                )

    for stage in model.flow:
        if stage.part_id not in id_set:
            raise ValueError(f"flow stage references unknown part {stage.part_id!r}")

    referenced: set[str] = set()
    for edge in model.edges:
        for endpoint in (edge.src, edge.dst):
            if endpoint not in id_set:
                raise ValueError(f"edge references unknown part {endpoint!r}")
            referenced.add(endpoint)
    referenced.update(stage.part_id for stage in model.flow)

    orphans = sorted(id_set - referenced)
    if orphans:
        raise ValueError(f"orphan part(s) on no edge and not in flow: {orphans}")


def model_to_dict(model: ArchitectureModel) -> dict:
    """Plain, JSON-serializable dict for embedding in the page."""
    def _convert(obj):
        if isinstance(obj, tuple):
            return [_convert(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        else:
            return obj

    return _convert(asdict(model))
