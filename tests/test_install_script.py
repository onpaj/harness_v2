"""Guards on the installer (`install.sh`).

The installer is a plain-shell bootstrapper — it runs *before* the package is
installed, so it can't be a Python entry point and isn't exercised by the
in-memory suite. These are cheap, filesystem-only checks that keep it from
silently drifting from the setup documented in `CLAUDE.md`/`README.md`:
the interpreter floor it enforces must match `requires-python`, and the three
commands that make up "install" must still be the ones it runs. They neither
create a venv nor run the script (that would need real network + disk); they
read the sources instead.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


def _script() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_installer_exists_and_is_executable() -> None:
    assert INSTALL_SH.is_file(), "install.sh is missing"
    assert os.access(INSTALL_SH, os.X_OK), "install.sh must be executable"


def test_installer_is_a_bash_script_with_strict_mode() -> None:
    text = _script()
    assert text.startswith("#!/usr/bin/env bash"), "expected a bash shebang"
    # Strict mode is what makes a failing prereq/install actually abort.
    assert "set -euo pipefail" in text


def test_installer_runs_the_three_install_commands() -> None:
    text = _script()
    # venv creation, the editable install with dev extras, and init.
    assert "-m venv" in text
    assert re.search(r'pip install [^\n]*-e [^\n]*\[dev\]', text), (
        "installer must do the editable install with the dev extras"
    )
    assert "harness" in text and "init" in text


def test_installer_python_floor_matches_requires_python() -> None:
    """The `MIN_PY_MINOR` the script enforces must track pyproject."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    requires = pyproject["project"]["requires-python"]
    match = re.search(r">=\s*3\.(\d+)", requires)
    assert match, f"unexpected requires-python: {requires!r}"
    wanted_minor = match.group(1)

    floor = re.search(r"MIN_PY_MINOR=(\d+)", _script())
    assert floor, "install.sh no longer declares MIN_PY_MINOR"
    assert floor.group(1) == wanted_minor, (
        f"install.sh enforces 3.{floor.group(1)} but pyproject wants "
        f"3.{wanted_minor}"
    )


def test_readme_documents_the_installer() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "install.sh" in readme, "README should point users at install.sh"


def test_installer_offers_the_service_flag() -> None:
    text = _script()
    assert "--service" in text, "installer must expose the --service step"


def test_installer_delegates_the_service_to_the_cli() -> None:
    """Generating a launchd plist in shell means hand-rolling XML.

    `harness service install` builds it with plistlib and is unit-tested, so the
    installer must call it rather than write the plist itself.
    """
    text = _script()
    assert re.search(r'service\s+install', text), (
        "installer must delegate to `harness service install`"
    )
    assert "<plist" not in text, "the installer must not hand-roll plist XML"
