import argparse
import asyncio
import json

import pytest

from harness.cli import DEFAULT_WORKFLOW, _github_source, main, serve
from harness.drivers.memory import MemoryArtifactStore
from harness.models import END, Task, Transition, Workflow
from harness.projection import BoardProjection
from tests.fakes import FakeTaskControl

SERVE_TEST_WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
    ),
)


def test_init_creates_layout_and_default_workflow(tmp_path):
    assert main(["init", "--root", str(tmp_path)]) == 0

    definition = json.loads((tmp_path / "workflows" / "default.json").read_text())
    assert definition["start"] == "plan"
    assert {"from": "review", "on": "request_changes", "to": "development"} in definition["transitions"]
    assert (tmp_path / "tasks").is_dir()
    assert (tmp_path / "queues" / "development").is_dir()
    assert (tmp_path / "done").is_dir()
    assert (tmp_path / "failed").is_dir()


def test_init_is_idempotent_and_keeps_edits(tmp_path):
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "default.json").write_text(
        json.dumps({"name": "default", "start": "plan", "transitions": []})
    )

    assert main(["init", "--root", str(tmp_path)]) == 0

    definition = json.loads((tmp_path / "workflows" / "default.json").read_text())
    assert definition["transitions"] == []


def test_submit_writes_a_task(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])

    assert main(
        [
            "submit",
            "--root",
            str(tmp_path),
            "--repo",
            "app-backend",
            "--data",
            '{"request": "rate limiting"}',
        ]
    ) == 0

    # init() also prints to stdout and capsys hasn't been drained since, so the
    # buffer holds init's status lines followed by submit's id — take the last
    # line, not the whole (naively stripped) buffer.
    task_id = capsys.readouterr().out.strip().splitlines()[-1]
    raw = json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text())
    task = Task.from_dict(raw)
    assert task.repository == "app-backend"
    assert task.workflow_template == DEFAULT_WORKFLOW
    assert task.status is None
    assert task.data == {"request": "rate limiting"}


def test_submit_rejects_invalid_data(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    capsys.readouterr()  # discard init's messages; from here we only care about submit

    assert main(["submit", "--root", str(tmp_path), "--data", "{broken"]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "valid JSON" in err


def test_submit_without_init_fails_cleanly(tmp_path, capsys):
    assert main(["submit", "--root", str(tmp_path / "empty")]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "initialized" in err


def test_run_with_unknown_workflow_fails_cleanly(tmp_path, capsys):
    """The third documented error path: unknown workflow (via `run`)."""
    main(["init", "--root", str(tmp_path)])
    capsys.readouterr()

    assert main(["run", "--root", str(tmp_path), "--workflow", "nonexistent"]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "nonexistent" in err


def test_init_rejects_workflow_name_with_path_separator(tmp_path, capsys):
    assert main(["init", "--root", str(tmp_path), "--workflow", "foo/bar"]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "foo/bar" in err
    # confirm it failed before anything was written to disk
    assert not (tmp_path / "workflows").exists()


def test_root_before_subcommand_is_rejected_not_silently_applied(tmp_path, monkeypatch):
    """`--root` given BEFORE the subcommand used to be silently dropped, and the
    harness reached for the (wrong) default root. It must fail loudly, not write
    elsewhere. We also redirect HARNESS_HOME to tmp_path so that, even on a
    regression, the test never touches the real ~/.harness."""
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "should-not-be-used"))
    bogus_root = tmp_path / "bogus-root"

    with pytest.raises(SystemExit) as excinfo:
        main(["--root", str(bogus_root), "init"])

    assert excinfo.value.code == 2
    assert not bogus_root.exists()
    assert not (tmp_path / "should-not-be-used").exists()


def test_harness_home_used_only_when_root_absent(tmp_path, monkeypatch):
    """HARNESS_HOME is used only when --root is absent; otherwise --root takes
    precedence."""
    env_root = tmp_path / "from-env"
    flag_root = tmp_path / "from-flag"
    monkeypatch.setenv("HARNESS_HOME", str(env_root))

    assert main(["init"]) == 0
    assert env_root.is_dir()
    assert not flag_root.exists()

    assert main(["init", "--root", str(flag_root)]) == 0
    assert flag_root.is_dir()


def _github_args(**overrides):
    """Minimální namespace, jaký `run` parser předá do `_github_source`."""
    base = dict(
        github_repo="onpaj/Anela.Heblo",
        github_repository="heblo",
        github_workflow="default",
        github_label="harness:todo",
        worktree_root=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_github_source_stamps_repository_name_not_root_path(monkeypatch, tmp_path):
    """`task.repository` je jméno pro `repos.json` (invariant 15), ne cesta
    `<root>/repo`. Zdroj bere jméno z `--github-repository`."""
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")

    source = _github_source(_github_args(github_repository="heblo"), tmp_path)

    assert source is not None
    assert source._repository == "heblo"
    # a rozhodně ne stará napevno drátovaná cesta
    assert source._repository != str(tmp_path / "repo")


def test_github_source_disabled_without_repository_name(monkeypatch, tmp_path, capsys):
    """`--github-repo` bez `--github-repository` nemá jak resolvnout worktree —
    zdroj se vypne s hláškou, symetricky k chybějícímu tokenu."""
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")

    assert _github_source(_github_args(github_repository=None), tmp_path) is None

    assert "--github-repository" in capsys.readouterr().err


def test_run_accepts_api_port(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    captured = {}

    async def fake_serve(harness, port, poll_interval):
        captured["port"] = port

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path), "--api-port", "9123"]) == 0
    assert captured["port"] == 9123


async def test_serve_returns_when_uvicorn_stops_before_the_loop(monkeypatch):
    """Regression: `serve()` used to do `await asyncio.gather(loop, uvicorn.Server(...).serve())`
    and only called `stop.set()` in `finally`. When uvicorn (after Ctrl+C)
    finishes first and returns WITHOUT an exception, `gather` kept waiting on
    `loop` -- but that won't finish before `stop` is set, which the code only
    reaches AFTER `gather` returns. A closed loop: `serve()` would never finish.

    The reproduction is structural (the way the reviewer did it): a fake uvicorn
    server that returns immediately on its own (like uvicorn after Ctrl+C), plus
    a `loop` that runs forever until it gets `stop`. On the unfixed version this
    test hangs and fails on the `asyncio.wait_for` timeout; on the fixed one it
    finishes in a fraction of a second."""

    class FakeHarness:
        def __init__(self):
            self.projection = BoardProjection(SERVE_TEST_WORKFLOW)
            self.artifacts = MemoryArtifactStore()
            self.control = FakeTaskControl()
            self.stop_seen: asyncio.Event | None = None

        async def run(self, poll_interval, stop):
            self.stop_seen = stop
            while not stop.is_set():
                await asyncio.sleep(0.01)

    class FakeUvicornServer:
        def __init__(self, config):
            pass

        async def serve(self):
            return  # simulates uvicorn returning without an exception after Ctrl+C

    monkeypatch.setattr("harness.cli.uvicorn.Server", FakeUvicornServer)

    harness = FakeHarness()
    await asyncio.wait_for(serve(harness, 8000, 0.01), timeout=2.0)

    assert harness.stop_seen is not None
    assert harness.stop_seen.is_set()
