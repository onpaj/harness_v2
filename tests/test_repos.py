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
