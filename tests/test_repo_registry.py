import pytest

from agentharness.config import load_config
from agentharness.git.mirror import resolve_ref
from agentharness.models import SCRATCH_REPO_ID
from agentharness.registry.repos import RepoRegistry


@pytest.fixture()
def registry(home):
    cfg = load_config()
    cfg.ensure_dirs()
    return RepoRegistry(cfg)


def test_add_creates_a_bare_mirror(registry, origin_repo):
    registry.add("app", str(origin_repo))
    assert registry.mirror_path("app").is_dir()
    assert resolve_ref(registry.mirror_path("app"), "main")


def test_add_persists_across_instances(registry, origin_repo, home):
    registry.add("app", str(origin_repo), integration_branch="harness/int")

    fresh = RepoRegistry(load_config())
    repo = fresh.get("app")
    assert repo.url == str(origin_repo)
    assert repo.integration_branch == "harness/int"
    assert repo.base_branch == "main"


def test_add_applies_configured_defaults(registry, origin_repo):
    repo = registry.add("app", str(origin_repo))
    assert repo.integration_branch == "harness/integration"


def test_get_unknown_repo_raises(registry):
    with pytest.raises(KeyError):
        registry.get("nope")


def test_mirror_path_shape(registry, home):
    assert registry.mirror_path("app") == home / "repos" / "app.git"


def test_list_returns_every_registered_repo(registry, origin_repo):
    registry.add("app", str(origin_repo))
    registry.add("docs", str(origin_repo))
    assert sorted(r.repo_id for r in registry.list()) == ["app", "docs"]


def test_ensure_scratch_creates_a_usable_repo(registry):
    scratch = registry.ensure_scratch()

    assert scratch.repo_id == SCRATCH_REPO_ID
    assert registry.mirror_path(SCRATCH_REPO_ID).is_dir()
    assert len(resolve_ref(registry.mirror_path(SCRATCH_REPO_ID), "main")) == 40


def test_ensure_scratch_is_idempotent(registry):
    first = registry.ensure_scratch()
    sha = resolve_ref(registry.mirror_path(SCRATCH_REPO_ID), "main")
    second = registry.ensure_scratch()

    assert first == second
    assert resolve_ref(registry.mirror_path(SCRATCH_REPO_ID), "main") == sha


def test_resolve_none_yields_the_scratch_repo(registry):
    """Repo-less agents are not a special case in any downstream code path."""
    assert registry.resolve(None).repo_id == SCRATCH_REPO_ID


def test_resolve_named_repo(registry, origin_repo):
    registry.add("app", str(origin_repo))
    assert registry.resolve("app").repo_id == "app"


def test_sync_fetches_new_commits(registry, origin_repo):
    import subprocess

    registry.add("app", str(origin_repo))
    before = resolve_ref(registry.mirror_path("app"), "main")

    (origin_repo / "new.txt").write_text("more\n")
    subprocess.run(["git", "add", "-A"], cwd=origin_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "second"], cwd=origin_repo, check=True, capture_output=True
    )

    registry.sync("app")
    assert resolve_ref(registry.mirror_path("app"), "main") != before
