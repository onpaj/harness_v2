"""The suite must not read the harness's own configuration environment.

The `verify` gate (ADR-0009's "landing is a step" applied to verification)
runs a repo's test command through `SubprocessCommandRunner`, which inherits
the harness process's environment. Under the launchd service that environment
carries `GITHUB_TOKEN` and, with self-healing on, `HARNESS_HEAL_REPO`. So when
this repo's verify command is its own `pytest`, any test whose assertion
depends on one of those variables being *unset* flips red under the gate while
staying green in the operator's shell — a spurious `request_changes` bouncing
a perfectly good diff back to `development` over a defect that is not in it.

`conftest.py`'s autouse `hermetic_environment` fixture is the fix. These tests
are its guard: one reproduces the original failure end to end under a hostile
environment, one proves a test can still opt back in, and one pins the cleared
list to what `src/harness` really reads so a new variable cannot quietly
reopen the hole.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from tests.conftest import _HARNESS_ENVIRONMENT

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src" / "harness"

# `os.environ.get("NAME")` / `os.environ["NAME"]` / `os.getenv("NAME")`.
_ENV_READ = re.compile(
    r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]"""
)

# Read by the package but deliberately not cleared: `HARNESS_SMOKE_CLAUDE` is
# the opt-in gate for `test_smoke_claude.py`, read at collection time by a
# module-level skipif, so clearing it per-test would be both ineffective and
# wrong.
_NOT_HARNESS_CONFIGURATION = {"HARNESS_SMOKE_CLAUDE"}

# The exact tests that went red under the service environment. Cheap to run
# (they monkeypatch `build`/`serve`, so nothing is actually served) and they
# are the real reproduction, not a proxy for one.
_PREVIOUSLY_ENVIRONMENT_SENSITIVE = (
    "tests/test_cli.py::test_run_registers_label_issue_finisher_only_with_a_token",
    "tests/test_cli.py::test_run_without_heal_repo_wires_no_open_issue_finisher",
    "tests/test_cli.py::test_run_resolves_default_workflow_when_omitted",
    "tests/test_cli.py::test_run_with_no_workflow_harness_defaults_to_none",
)


def test_the_suite_is_green_under_the_service_environment():
    """Reproduce the verify gate's environment and require green.

    A subprocess, not `monkeypatch`, because the whole point is the value
    being present *before* pytest starts — which is how the launchd service
    delivers it and the only arrangement the autouse fixture has to survive.
    Without the fixture these four fail; with it they pass.
    """
    hostile = {
        **os.environ,
        "GITHUB_TOKEN": "tok",
        "HARNESS_HEAL_REPO": "onpaj/harness_v2",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.invalid/services/x",
        "JIRA_BASE_URL": "https://example.atlassian.net",
        "JIRA_EMAIL": "operator@example.com",
        "JIRA_API_TOKEN": "tok",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *_PREVIOUSLY_ENVIRONMENT_SENSITIVE],
        cwd=_ROOT,
        env=hostile,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        "the suite's verdict depends on the harness's own configuration "
        "environment — the verify gate would report these as real failures:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_a_test_can_still_opt_back_in(monkeypatch):
    """Clearing is a floor, not a ceiling.

    A test that genuinely exercises the token-present path sets it itself; the
    autouse fixture runs first, so the explicit `setenv` wins.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert os.environ["GITHUB_TOKEN"] == "tok"


def test_every_configuration_variable_the_package_reads_is_covered():
    """Pin the cleared set to what `src/harness` actually reads.

    A new `os.environ.get("HARNESS_...")` in `cli.py` that nobody adds to
    `_HARNESS_ENVIRONMENT` would silently reopen exactly the hole this file
    exists to close, so the list is derived from the source rather than
    trusted.
    """
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        for match in _ENV_READ.finditer(path.read_text(encoding="utf-8")):
            found.add(match.group(1) or match.group(2))

    uncovered = found - set(_HARNESS_ENVIRONMENT) - _NOT_HARNESS_CONFIGURATION
    assert not uncovered, (
        f"src/harness reads {sorted(uncovered)} as configuration, but "
        f"tests/conftest.py's _HARNESS_ENVIRONMENT does not clear them — an "
        f"ambient value would change what the suite asserts on, and the verify "
        f"gate inherits the service's environment. Add them there, or list "
        f"them in _NOT_HARNESS_CONFIGURATION with a reason."
    )
