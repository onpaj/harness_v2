import plistlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
INSTALL = ROOT / "deploy" / "install.sh"


def render(**env) -> str:
    result = subprocess.run(
        ["/bin/sh", str(INSTALL), "--print"],
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": "/Users/example", "PATH": "/usr/bin:/bin", **env},
    )
    return result.stdout


def test_render_leaves_no_placeholders():
    assert not re.search(r"__[A-Z_]+__", render())


def test_render_produces_a_valid_plist():
    parsed = plistlib.loads(render().encode())
    assert parsed["Label"] == "com.agentharness"


def test_service_restarts_itself():
    parsed = plistlib.loads(render().encode())
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True


def test_logs_are_routed_into_the_harness_home():
    parsed = plistlib.loads(render(AGENTHARNESS_HOME="/tmp/ah").encode())
    assert parsed["StandardOutPath"] == "/tmp/ah/logs/serve.out.log"
    assert parsed["StandardErrorPath"] == "/tmp/ah/logs/serve.err.log"


def test_harness_home_is_passed_to_the_process():
    parsed = plistlib.loads(render(AGENTHARNESS_HOME="/tmp/ah").encode())
    assert parsed["EnvironmentVariables"]["AGENTHARNESS_HOME"] == "/tmp/ah"


def test_the_service_runs_serve():
    parsed = plistlib.loads(render().encode())
    assert parsed["ProgramArguments"][-1] == "serve"


def test_print_mode_installs_nothing(tmp_path):
    """--print must be safe to run on any machine."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    render(HOME=str(fake_home))
    assert not (fake_home / "Library").exists()
