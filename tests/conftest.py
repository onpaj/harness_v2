import subprocess

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "harness"
    h.mkdir()
    monkeypatch.setenv("AGENTHARNESS_HOME", str(h))
    return h


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def origin_repo(tmp_path):
    """A non-bare repo with one commit on `main`, usable as a clone URL."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    _git("config", "user.email", "test@localhost", cwd=repo)
    (repo / "README.md").write_text("# origin\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    return repo
