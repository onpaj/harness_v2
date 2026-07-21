"""The launchd service driver — pure builders, no launchctl and no disk.

`wrapper_script` and `plist_bytes` are deliberately pure so the *content* of
the two generated files is covered here; only the thin `launchctl` shell is
left untested, the same bargain `git_workspace` makes with the system `git`.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

from harness.drivers.launchd import (
    DEFAULT_LABEL,
    agents_dir,
    plist_bytes,
    plist_path,
    wrapper_script,
)


def build_wrapper(**overrides) -> str:
    kwargs = {
        "harness": Path("/opt/app/.venv/bin/harness"),
        "root": Path("/home/rem/harness-root"),
        "api_port": 8420,
        "path_entries": ["/opt/app/.venv/bin", "/usr/bin"],
        "env_file": Path("/home/rem/harness-root/secrets.env"),
    }
    kwargs.update(overrides)
    return wrapper_script(**kwargs)


# --- wrapper script --------------------------------------------------------


def test_wrapper_is_a_strict_bash_script():
    text = build_wrapper()

    assert text.startswith("#!/usr/bin/env bash")
    # Without strict mode a failing token lookup would be invisible.
    assert "set -euo pipefail" in text


def test_wrapper_execs_harness_run_with_the_root_and_port():
    text = build_wrapper()

    assert (
        'exec "/opt/app/.venv/bin/harness" run --root "/home/rem/harness-root" '
        "--api-port 8420" in text
    )


def test_wrapper_exports_the_supplied_path():
    text = build_wrapper(path_entries=["/a/bin", "/b/bin"])

    assert 'export PATH="/a/bin:/b/bin"' in text


def test_wrapper_borrows_the_token_from_gh_rather_than_storing_one():
    text = build_wrapper()

    assert "gh auth token" in text
    # The whole point: no secret is ever written into the generated files.
    assert "ghp_" not in text and "gho_" not in text


def test_wrapper_prefers_an_explicit_token_over_the_keyring():
    text = build_wrapper()

    # The gh lookup must be guarded by "is GITHUB_TOKEN already set".
    assert 'if [ -z "${GITHUB_TOKEN:-}" ] && command -v gh' in text


def test_wrapper_warns_but_does_not_die_without_a_token():
    text = build_wrapper()

    assert "warning: no GITHUB_TOKEN" in text
    # A missing token disables ingestion; it must not abort the service.
    assert "exit 1" not in text


def test_wrapper_honours_a_disabled_board():
    text = build_wrapper(api_port=0)

    assert "--api-port 0" in text


def test_wrapper_sources_the_secrets_file_with_export():
    text = build_wrapper(env_file=Path("/home/rem/harness-root/secrets.env"))

    assert 'ENV_FILE="/home/rem/harness-root/secrets.env"' in text
    # `set -a` around the source is what exports CLAUDE_CODE_OAUTH_TOKEN into the
    # child `claude` process; without it the var is set but not inherited.
    assert "set -a" in text and "set +a" in text
    assert '. "$ENV_FILE"' in text


def test_wrapper_warns_when_the_claude_token_is_missing():
    text = build_wrapper()

    # The load-bearing failure mode: no keychain under launchd, so no token
    # means every agent step dies. It must be called out, not silent.
    assert 'if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]' in text
    assert "claude setup-token" in text
    # ...but a missing token warns, it does not abort the service.
    assert "exit 1" not in text


def test_wrapper_never_embeds_a_token():
    text = build_wrapper()

    for marker in ("sk-ant-", "ghp_", "gho_"):
        assert marker not in text


# --- plist -----------------------------------------------------------------


def build_plist(**overrides) -> dict:
    kwargs = {
        "label": DEFAULT_LABEL,
        "wrapper": Path("/home/rem/harness-root/harness-run.sh"),
        "working_dir": Path("/home/rem/harness-root"),
        "log_dir": Path("/home/rem/harness-root/logs"),
        "home": Path("/home/rem"),
    }
    kwargs.update(overrides)
    return plistlib.loads(plist_bytes(**kwargs))


def test_plist_is_valid_and_runs_the_wrapper():
    definition = build_plist()

    assert definition["Label"] == "com.harness"
    assert definition["ProgramArguments"] == [
        "/bin/bash",
        "/home/rem/harness-root/harness-run.sh",
    ]


def test_plist_survives_login_and_crashes():
    definition = build_plist()

    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] is True


def test_plist_sends_both_streams_to_the_log_dir():
    definition = build_plist()

    assert definition["StandardOutPath"] == "/home/rem/harness-root/logs/harness.log"
    assert (
        definition["StandardErrorPath"]
        == "/home/rem/harness-root/logs/harness.error.log"
    )


def test_plist_carries_no_secret():
    """The reason the wrapper exists — a plist is a readable file in the home dir."""
    definition = build_plist()

    assert "GITHUB_TOKEN" not in definition["EnvironmentVariables"]
    assert "GITHUB_TOKEN" not in plist_bytes(
        label="com.harness",
        wrapper=Path("/w.sh"),
        working_dir=Path("/w"),
        log_dir=Path("/w/logs"),
        home=Path("/home/rem"),
    ).decode("utf-8")


def test_plist_label_is_configurable():
    definition = build_plist(label="com.harness.staging")

    assert definition["Label"] == "com.harness.staging"


# --- paths -----------------------------------------------------------------


def test_plist_path_follows_the_launchagents_convention():
    assert agents_dir(Path("/home/rem")) == Path("/home/rem/Library/LaunchAgents")
    assert plist_path(Path("/home/rem"), "com.harness") == Path(
        "/home/rem/Library/LaunchAgents/com.harness.plist"
    )


# --- the bootout/bootstrap race -------------------------------------------


def test_load_waits_for_the_old_copy_to_disappear(monkeypatch):
    """`bootout` returns before launchd has torn the job down; bootstrapping
    into a still-loaded label fails with "Bootstrap failed: 5"."""
    from harness.drivers import launchd

    calls: list[list[str]] = []
    # Loaded for the first two polls, gone on the third.
    remaining = [object(), object(), None]

    def fake_launchctl(args, *, check=True):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(launchd, "_launchctl", fake_launchctl)
    monkeypatch.setattr(launchd, "status", lambda uid, label: remaining.pop(0))
    monkeypatch.setattr(launchd.time, "sleep", lambda seconds: None)

    launchd.load(501, Path("/tmp/x.plist"), "com.harness")

    verbs = [args[0] for args in calls]
    assert verbs == ["bootout", "bootstrap", "kickstart"]


def test_load_refuses_to_bootstrap_over_a_stuck_job(monkeypatch):
    from harness.drivers import launchd

    import pytest

    monkeypatch.setattr(
        launchd, "_launchctl", lambda args, check=True: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    )
    monkeypatch.setattr(launchd, "status", lambda uid, label: "still here")
    monkeypatch.setattr(launchd.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(launchd.time, "monotonic", iter(range(100)).__next__)

    with pytest.raises(launchd.ServiceError, match="still loaded"):
        launchd.load(501, Path("/tmp/x.plist"), "com.harness")
