import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "harness"
    h.mkdir()
    monkeypatch.setenv("AGENTHARNESS_HOME", str(h))
    return h
