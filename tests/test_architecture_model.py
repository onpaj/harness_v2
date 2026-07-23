from pathlib import Path

import pytest

from harness_docs_site.architecture import (
    ArchitectureModel,
    Driver,
    Port,
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


def _driver(**kw) -> Driver:
    base = dict(
        id="d",
        name="D",
        tagline="t",
        description="d",
        sources=("src/thing.py",),
    )
    base.update(kw)
    return Driver(**base)


def _port(**kw) -> Port:
    base = dict(
        id="a",
        name="A",
        group="G",
        tagline="t",
        description="d",
        adrs=("0001-ports-and-adapters",),
        sources=("src/thing.py",),
        drivers=(_driver(),),
    )
    base.update(kw)
    return Port(**base)


def _model(*ports: Port, groups: tuple[str, ...] = ("G",)) -> ArchitectureModel:
    return ArchitectureModel(groups=groups, ports=tuple(ports))


def test_validate_passes_on_a_coherent_model(tmp_path: Path):
    _tree(tmp_path)
    validate(_model(_port(id="a"), _port(id="b")), tmp_path)  # must not raise


def test_validate_rejects_duplicate_port_ids(tmp_path: Path):
    _tree(tmp_path)
    with pytest.raises(ValueError, match="duplicate port"):
        validate(_model(_port(id="a"), _port(id="a")), tmp_path)


def test_validate_rejects_unknown_group(tmp_path: Path):
    _tree(tmp_path)
    with pytest.raises(ValueError, match="unknown group"):
        validate(_model(_port(group="ghost")), tmp_path)


def test_validate_rejects_group_with_no_port(tmp_path: Path):
    _tree(tmp_path)
    with pytest.raises(ValueError, match="no port"):
        validate(_model(_port(), groups=("G", "Empty")), tmp_path)


def test_validate_rejects_port_with_no_drivers(tmp_path: Path):
    _tree(tmp_path)
    with pytest.raises(ValueError, match="no drivers"):
        validate(_model(_port(drivers=())), tmp_path)


def test_validate_rejects_duplicate_driver_ids_within_a_port(tmp_path: Path):
    _tree(tmp_path)
    port = _port(drivers=(_driver(id="d"), _driver(id="d")))
    with pytest.raises(ValueError, match="duplicate driver"):
        validate(_model(port), tmp_path)


def test_validate_rejects_unknown_adr_slug(tmp_path: Path):
    _tree(tmp_path)
    with pytest.raises(ValueError, match="9999-nope"):
        validate(_model(_port(adrs=("9999-nope",))), tmp_path)


def test_validate_rejects_missing_source_path(tmp_path: Path):
    _tree(tmp_path)
    with pytest.raises(ValueError, match="src/gone.py"):
        validate(_model(_port(sources=("src/gone.py",))), tmp_path)


def test_validate_checks_driver_refs_too(tmp_path: Path):
    _tree(tmp_path)
    port = _port(drivers=(_driver(sources=("src/gone.py",)),))
    with pytest.raises(ValueError, match="src/gone.py"):
        validate(_model(port), tmp_path)
    port = _port(drivers=(_driver(adrs=("9999-nope",)),))
    with pytest.raises(ValueError, match="9999-nope"):
        validate(_model(port), tmp_path)


def test_model_to_dict_is_json_shaped():
    data = model_to_dict(_model(_port()))
    assert data["groups"] == ["G"]
    assert data["ports"][0]["id"] == "a"
    assert data["ports"][0]["adrs"] == ["0001-ports-and-adapters"]
    assert data["ports"][0]["drivers"][0]["id"] == "d"


def test_shipped_model_is_coherent_with_the_repo():
    from harness_docs_site.architecture import MODEL

    repo_root = Path(__file__).resolve().parents[1]
    validate(MODEL, repo_root)  # must not raise against the real tree


def test_shipped_model_covers_the_load_bearing_ports():
    from harness_docs_site.architecture import MODEL

    port_ids = {p.id for p in MODEL.ports}
    assert {"task-source", "task-queue", "behavior", "workspace", "forge"} <= port_ids


def test_shipped_model_real_and_test_drivers_coexist_behind_task_queue():
    from harness_docs_site.architecture import MODEL

    queue = next(p for p in MODEL.ports if p.id == "task-queue")
    names = {d.name for d in queue.drivers}
    assert {"FilesystemTaskQueue", "MemoryTaskQueue"} <= names
