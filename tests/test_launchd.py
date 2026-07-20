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
