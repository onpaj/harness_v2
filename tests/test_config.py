from pathlib import Path

from agentharness.config import harness_home, load_config


def test_harness_home_reads_env(home):
    assert harness_home() == home


def test_harness_home_defaults_to_dot_agentharness(monkeypatch):
    monkeypatch.delenv("AGENTHARNESS_HOME", raising=False)
    assert harness_home() == Path.home() / ".agentharness"


def test_load_config_defaults(home):
    cfg = load_config()
    assert cfg.home == home
    assert cfg.max_concurrency == 3
    assert cfg.branch_retention_days == 30
    assert cfg.default_integration_branch == "harness/integration"


def test_load_config_overrides_from_yaml(home):
    (home / "config.yaml").write_text("max_concurrency: 7\nclaude_binary: /usr/bin/claude\n")
    cfg = load_config()
    assert cfg.max_concurrency == 7
    assert cfg.claude_binary == "/usr/bin/claude"


def test_ensure_dirs_creates_layout(home):
    cfg = load_config()
    cfg.ensure_dirs()
    for name in ("agents", "repos", "queues", "worktrees", "locks", "logs"):
        assert (home / name).is_dir()
