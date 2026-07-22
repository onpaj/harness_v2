from pathlib import Path

import pytest

from harness_docs_site.architecture import (
    ArchitectureModel,
    Edge,
    Part,
    Stage,
    model_to_dict,
    validate,
)


def _tree(root: Path) -> Path:
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-ports-and-adapters.md").write_text(
        "# ADR-0001: Ports and adapters\n", encoding="utf-8"
    )
    (root / "src").mkdir()
    (root / "src" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _part(**kw) -> Part:
    base = dict(
        id="a",
        name="A",
        kind="core",
        tagline="t",
        description="d",
        adrs=("0001-ports-and-adapters",),
        sources=("src/thing.py",),
        x=50.0,
        y=50.0,
    )
    base.update(kw)
    return Part(**base)


def test_validate_passes_on_a_coherent_model(tmp_path: Path):
    _tree(tmp_path)
    b = _part(id="b", x=10.0, y=10.0)
    model = ArchitectureModel(
        parts=(_part(id="a"), b),
        flow=(Stage("a", "start"), Stage("b", "end")),
        edges=(Edge("a", "b"),),
    )
    validate(model, tmp_path)  # must not raise


def test_validate_rejects_unknown_adr_slug(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(adrs=("9999-nope",)),),
        flow=(Stage("a", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="9999-nope"):
        validate(model, tmp_path)


def test_validate_rejects_missing_source_path(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(sources=("src/gone.py",)),),
        flow=(Stage("a", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="src/gone.py"):
        validate(model, tmp_path)


def test_validate_rejects_dangling_flow_stage(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(id="a"),),
        flow=(Stage("ghost", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="ghost"):
        validate(model, tmp_path)


def test_validate_rejects_dangling_edge_endpoint(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(id="a"),),
        flow=(Stage("a", "only"),),
        edges=(Edge("a", "ghost"),),
    )
    with pytest.raises(ValueError, match="ghost"):
        validate(model, tmp_path)


def test_validate_rejects_orphan_part(tmp_path: Path):
    _tree(tmp_path)
    # 'b' is on no edge and not in the flow -> orphan.
    model = ArchitectureModel(
        parts=(_part(id="a"), _part(id="b", x=1.0, y=1.0)),
        flow=(Stage("a", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="orphan"):
        validate(model, tmp_path)


def test_model_to_dict_is_json_shaped(tmp_path: Path):
    part = _part()
    model = ArchitectureModel(parts=(part,), flow=(Stage("a", "s"),), edges=())
    data = model_to_dict(model)
    assert data["parts"][0]["id"] == "a"
    assert data["parts"][0]["adrs"] == ["0001-ports-and-adapters"]
    assert data["flow"][0] == {"part_id": "a", "caption": "s"}
    assert isinstance(data["parts"][0]["x"], float)


def test_shipped_model_is_coherent_with_the_repo():
    from harness_docs_site.architecture import MODEL

    repo_root = Path(__file__).resolve().parents[1]
    validate(MODEL, repo_root)  # must not raise against the real tree


def test_shipped_model_flow_starts_at_the_task_source():
    from harness_docs_site.architecture import MODEL

    assert MODEL.flow[0].part_id == "task-source"
    part_ids = {p.id for p in MODEL.parts}
    assert {"router", "agent-runner", "landing", "board"} <= part_ids


def test_shipped_model_flow_legs_all_have_a_backing_edge():
    from harness_docs_site.architecture import MODEL

    edge_pairs = set()
    for e in MODEL.edges:
        edge_pairs.add((e.src, e.dst))
        edge_pairs.add((e.dst, e.src))
    for a, b in zip(MODEL.flow, MODEL.flow[1:]):
        assert (a.part_id, b.part_id) in edge_pairs, (
            f"flow leg {a.part_id} -> {b.part_id} has no backing edge"
        )
