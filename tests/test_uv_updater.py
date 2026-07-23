"""Unit tests for the UvUpdater driver's decision logic.

The thin subprocess shell (`uv tool upgrade`, `harness --version`) is driven by
tiny scripts in a tmp dir and `_version_report` is overridden where the branch
under test is about the version comparison, not the shelling out — mirroring how
the launchd builders are unit-tested without touching real `launchctl`.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from harness.drivers import uv_updater as uv_mod
from harness.drivers.launchd import ServiceError
from harness.drivers.uv_updater import UvUpdater
from harness.ports.updater import UpdateError


def _script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _updater(tmp_path: Path, *, versions: list[str], uv_rc: int = 0, **kw) -> UvUpdater:
    uv = _script(tmp_path / "uv", f"#!/bin/sh\nexit {uv_rc}\n")
    updater = UvUpdater(
        package="harness",
        entry_point=tmp_path / "harness",  # unused: _version_report is overridden
        uid=501,
        label="com.harness",
        is_stage_active=kw.get("is_stage_active", lambda: []),
        uv_path=uv,
    )
    reports = iter(versions)
    updater._version_report = lambda: next(reports)  # type: ignore[method-assign]
    return updater


def test_no_version_change_does_not_restart(tmp_path, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(uv_mod, "kickstart", lambda uid, label: called.append(label))
    updater = _updater(tmp_path, versions=["harness 0.9.1", "harness 0.9.1"])

    result = updater.update()

    assert result.changed is False
    assert result.restarted is False
    assert "already up to date" in result.detail
    assert called == []


def test_version_change_while_idle_restarts(tmp_path, monkeypatch):
    called: list[tuple[int, str]] = []
    monkeypatch.setattr(uv_mod, "kickstart", lambda uid, label: called.append((uid, label)))
    updater = _updater(tmp_path, versions=["harness 0.9.1", "harness 0.9.2"])

    result = updater.update()

    assert result.changed is True
    assert result.restarted is True
    assert called == [(501, "com.harness")]
    assert "0.9.2" in result.detail


def test_version_change_defers_restart_while_a_stage_runs(tmp_path, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(uv_mod, "kickstart", lambda uid, label: called.append(label))
    updater = _updater(
        tmp_path,
        versions=["harness 0.9.1", "harness 0.9.2"],
        is_stage_active=lambda: ["tsk_7"],
    )

    result = updater.update()

    assert result.changed is True
    assert result.restarted is False
    assert called == []  # the running stage's agent is not killed mid-run
    assert "tsk_7" in result.detail


def test_restart_failure_is_reported_not_raised(tmp_path, monkeypatch):
    def boom(uid, label):
        raise ServiceError("service not loaded")

    monkeypatch.setattr(uv_mod, "kickstart", boom)
    updater = _updater(tmp_path, versions=["harness 0.9.1", "harness 0.9.2"])

    result = updater.update()

    # The new code is on disk — a failed restart is not an update failure.
    assert result.changed is True
    assert result.restarted is False
    assert "restart the service manually" in result.detail


def test_failed_upgrade_raises(tmp_path):
    updater = _updater(tmp_path, versions=["harness 0.9.1"], uv_rc=3)

    with pytest.raises(UpdateError):
        updater.update()


def test_missing_uv_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(uv_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(uv_mod.Path, "home", classmethod(lambda cls: tmp_path))
    updater = UvUpdater(
        package="harness",
        entry_point=tmp_path / "harness",
        uid=501,
        label="com.harness",
        is_stage_active=lambda: [],
    )

    with pytest.raises(UpdateError):
        updater.update()
