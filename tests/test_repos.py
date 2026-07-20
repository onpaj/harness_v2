import json
from pathlib import Path

import pytest

from harness.drivers.fs_repos import FilesystemRepositoryRegistry
from harness.drivers.memory import MemoryRepositoryRegistry
from harness.ports.repos import (
    InvalidRepositoryDefinition,
    RepositoryDefinition,
    RepositoryNotFound,
)


def test_memory_resolves_known_name():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})

    assert registry.resolve("harness_v2") == Path("/repos/harness_v2")


def test_memory_get_wraps_bare_path_without_source():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})

    definition = registry.get("harness_v2")
    assert definition == RepositoryDefinition(
        name="harness_v2", path=Path("/repos/harness_v2"), source=None
    )


def test_memory_get_returns_definition_with_source():
    registry = MemoryRepositoryRegistry(
        {
            "harness_v2": RepositoryDefinition(
                name="harness_v2",
                path=Path("/repos/harness_v2"),
                source="https://github.com/onpaj/harness_v2",
            )
        }
    )

    definition = registry.get("harness_v2")
    assert definition.source == "https://github.com/onpaj/harness_v2"
    assert registry.resolve("harness_v2") == Path("/repos/harness_v2")


def test_memory_unknown_name_raises():
    registry = MemoryRepositoryRegistry({})

    with pytest.raises(RepositoryNotFound, match="neznamy"):
        registry.resolve("neznamy")


def test_fs_resolves_known_name(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"harness_v2": "/repos/harness_v2"}))
    registry = FilesystemRepositoryRegistry(config)

    assert registry.resolve("harness_v2") == Path("/repos/harness_v2")


def test_fs_expands_user_home(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"harness_v2": "~/code/harness_v2"}))
    registry = FilesystemRepositoryRegistry(config)

    assert registry.resolve("harness_v2") == Path.home() / "code" / "harness_v2"


def test_fs_unknown_name_raises(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"harness_v2": "/repos/harness_v2"}))
    registry = FilesystemRepositoryRegistry(config)

    with pytest.raises(RepositoryNotFound, match="neznamy"):
        registry.resolve("neznamy")


def test_fs_missing_config_raises(tmp_path):
    registry = FilesystemRepositoryRegistry(tmp_path / "chybi.json")

    with pytest.raises(RepositoryNotFound):
        registry.resolve("harness_v2")


def test_fs_malformed_json_raises(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text("{tohle neni json")
    registry = FilesystemRepositoryRegistry(config)

    with pytest.raises(RepositoryNotFound):
        registry.resolve("harness_v2")


# --- Definice: lokální složka + zdroj (GitHub URL) -------------------------


def _registry_with(tmp_path, mapping) -> FilesystemRepositoryRegistry:
    config = tmp_path / "repos.json"
    config.write_text(json.dumps(mapping))
    return FilesystemRepositoryRegistry(config)


def test_fs_shorthand_string_is_path_without_source(tmp_path):
    registry = _registry_with(tmp_path, {"harness_v2": "/repos/harness_v2"})

    definition = registry.get("harness_v2")
    assert definition == RepositoryDefinition(
        name="harness_v2", path=Path("/repos/harness_v2"), source=None
    )


def test_fs_object_form_carries_path_and_source(tmp_path):
    registry = _registry_with(
        tmp_path,
        {
            "harness_v2": {
                "path": "~/code/harness_v2",
                "source": "https://github.com/onpaj/harness_v2",
            }
        },
    )

    definition = registry.get("harness_v2")
    assert definition.path == Path.home() / "code" / "harness_v2"
    assert definition.source == "https://github.com/onpaj/harness_v2"
    # resolve() je pořád zkratka na cestu
    assert registry.resolve("harness_v2") == Path.home() / "code" / "harness_v2"


def test_fs_object_form_source_is_optional(tmp_path):
    registry = _registry_with(tmp_path, {"harness_v2": {"path": "/repos/harness_v2"}})

    assert registry.get("harness_v2").source is None


def test_fs_object_without_path_raises_invalid(tmp_path):
    registry = _registry_with(
        tmp_path, {"harness_v2": {"source": "onpaj/harness_v2"}}
    )

    with pytest.raises(InvalidRepositoryDefinition, match="path"):
        registry.get("harness_v2")


def test_fs_path_wrong_type_raises_invalid(tmp_path):
    registry = _registry_with(tmp_path, {"harness_v2": {"path": 42}})

    with pytest.raises(InvalidRepositoryDefinition, match="path"):
        registry.get("harness_v2")


def test_fs_source_wrong_type_raises_invalid(tmp_path):
    registry = _registry_with(
        tmp_path, {"harness_v2": {"path": "/repos/harness_v2", "source": ["x"]}}
    )

    with pytest.raises(InvalidRepositoryDefinition, match="source"):
        registry.get("harness_v2")


def test_fs_empty_path_raises_invalid(tmp_path):
    registry = _registry_with(tmp_path, {"harness_v2": {"path": "   "}})

    with pytest.raises(InvalidRepositoryDefinition, match="prázdné"):
        registry.get("harness_v2")


def test_fs_definition_of_wrong_type_raises_invalid(tmp_path):
    registry = _registry_with(tmp_path, {"harness_v2": 123})

    with pytest.raises(InvalidRepositoryDefinition):
        registry.get("harness_v2")


def test_invalid_definition_is_a_repository_not_found(tmp_path):
    """Podtřída: kdo chytá RepositoryNotFound, chytí i rozbitou definici."""
    registry = _registry_with(tmp_path, {"harness_v2": {"source": "x"}})

    with pytest.raises(RepositoryNotFound):
        registry.get("harness_v2")
