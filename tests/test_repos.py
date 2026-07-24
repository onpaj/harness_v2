import json
from pathlib import Path

import pytest

from harness.drivers.fs_repos import FilesystemRepositoryRegistry
from harness.drivers.memory import MemoryRepositoryRegistry
from harness.ports.repos import RepositoryNotFound


def test_memory_resolves_known_name():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})

    assert registry.resolve("harness_v2") == Path("/repos/harness_v2")


def test_memory_unknown_name_raises():
    registry = MemoryRepositoryRegistry({})

    with pytest.raises(RepositoryNotFound, match="unknown"):
        registry.resolve("unknown")


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

    with pytest.raises(RepositoryNotFound, match="unknown"):
        registry.resolve("unknown")


def test_fs_missing_config_raises(tmp_path):
    registry = FilesystemRepositoryRegistry(tmp_path / "missing.json")

    with pytest.raises(RepositoryNotFound):
        registry.resolve("harness_v2")


def test_fs_malformed_json_raises(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text("{this is not json")
    registry = FilesystemRepositoryRegistry(config)

    with pytest.raises(RepositoryNotFound):
        registry.resolve("harness_v2")


def test_memory_names_lists_keys():
    registry = MemoryRepositoryRegistry(
        {"harness_v2": Path("/repos/harness_v2"), "heblo": Path("/repos/heblo")}
    )

    assert sorted(registry.names()) == ["harness_v2", "heblo"]


def test_fs_names_lists_keys(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"harness_v2": "/a", "heblo": "/b"}))
    registry = FilesystemRepositoryRegistry(config)

    assert sorted(registry.names()) == ["harness_v2", "heblo"]


def test_fs_names_missing_config_is_empty(tmp_path):
    registry = FilesystemRepositoryRegistry(tmp_path / "missing.json")

    assert registry.names() == []


def test_fs_names_broken_json_is_empty(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text("{not json")
    registry = FilesystemRepositoryRegistry(config)

    assert registry.names() == []


def test_object_form_entry_resolves_path(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": {"path": "/srv/app", "verify": "pytest -q"}}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.resolve("app") == Path("/srv/app")
    assert registry.names() == ["app"]


def test_verify_command_object_form(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": {"path": "/srv/app", "verify": "pytest -q"}}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.verify_command("app") == "pytest -q"


def test_verify_command_string_form_is_none(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": "/srv/app"}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.verify_command("app") is None


def test_verify_command_unknown_or_missing_is_none(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": "/srv/app"}))
    registry = FilesystemRepositoryRegistry(config)
    assert registry.verify_command("ghost") is None
    assert FilesystemRepositoryRegistry(tmp_path / "absent.json").verify_command("app") is None


def test_object_form_without_path_is_not_found(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"app": {"verify": "pytest -q"}}))
    registry = FilesystemRepositoryRegistry(config)
    with pytest.raises(RepositoryNotFound):
        registry.resolve("app")


def test_memory_registry_verify_command(tmp_path):
    registry = MemoryRepositoryRegistry({"app": tmp_path}, verify={"app": "make test"})
    assert registry.verify_command("app") == "make test"
    assert registry.verify_command("other") is None
    assert MemoryRepositoryRegistry({"app": tmp_path}).verify_command("app") is None
