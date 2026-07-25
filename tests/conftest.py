"""Session-wide test setup.

`harness_docs_site` is deliberately excluded from the installed package (see
`pyproject.toml`'s `tool.setuptools.packages.find.exclude`), so unlike
`harness` it isn't reachable through the editable install. This inserts `src/`
onto `sys.path` so `tests/test_docs_site.py` can import it directly.

It also makes the suite **hermetic against the harness's own configuration
environment** — see `hermetic_environment` below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# Every environment variable `src/harness` reads as configuration. The suite
# asserts on what `cli._run()` wires, and `_run()` reads exactly these — so an
# ambient value silently changes the wiring under test.
#
# This is not hypothetical tidiness. The `verify` gate runs a repo's test
# command through `SubprocessCommandRunner`, which inherits the harness
# process's environment; under the launchd service that environment carries
# `GITHUB_TOKEN` (the wrapper resolves it from `gh auth token`) and, with
# self-healing enabled, `HARNESS_HEAL_REPO`. Running this repo's own suite as
# its verify command therefore turned eight `test_cli.py` tests red — tests
# asserting "*without* a token" / "*without* a heal repo" while both were set.
# A green suite in the operator's shell, a red one under the gate, and a task
# bounced back to `development` over a defect that does not exist in the diff.
#
# `HARNESS_SMOKE_CLAUDE` is deliberately absent: it is the opt-in gate for
# `test_smoke_claude.py`, read at collection time by a module-level skipif, so
# clearing it here would be both ineffective and wrong.
_HARNESS_ENVIRONMENT = (
    "GITHUB_TOKEN",
    "HARNESS_HEAL_REPO",
    "HARNESS_HOME",
    "JIRA_API_TOKEN",
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "SLACK_WEBHOOK_URL",
)


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    """Unset the harness's configuration environment for every test.

    Autouse and unconditional: a test that *wants* one of these set does so
    itself with `monkeypatch.setenv`, which runs after this fixture and wins.
    The inverse — a test that needs one *unset* — must not have to say so,
    because the whole failure mode is nobody thinking to.
    """
    for name in _HARNESS_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
