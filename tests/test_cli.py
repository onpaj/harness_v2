import argparse
import asyncio
import json
import os
from pathlib import Path

import pytest

from harness.app import HarnessLayout
from harness.cli import (
    DEFAULT_WORKFLOW,
    _github_sources,
    _mergeability_sources,
    main,
    serve,
)
from harness.drivers.github_client import FakeGithubClient
from harness.drivers.memory import MemoryArtifactStore, MemoryRepositoryRegistry
from harness.drivers.stage_output import StageOutputProjection
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


def test_init_writes_default_agents_with_null_timeout(tmp_path):
    assert main(["init", "--root", str(tmp_path)]) == 0

    definition = json.loads((tmp_path / "agents" / "development.json").read_text())
    assert definition["timeout"] is None


def test_init_writes_healer_persona(tmp_path):
    from harness.drivers.fs_agents import FilesystemAgentCatalog
    from harness.models import Outcome

    assert main(["init", "--root", str(tmp_path)]) == 0

    path = tmp_path / "agents" / "healer.json"
    assert path.is_file()
    definition = json.loads(path.read_text())
    assert definition["allowed_outcomes"] == ["done", "request_changes"]

    # it parses to a valid AgentSpec with both outcomes
    spec = FilesystemAgentCatalog(tmp_path / "agents").get("healer")
    assert spec.allowed_outcomes == (Outcome.DONE, Outcome.REQUEST_CHANGES)
    assert spec.prompt


def test_run_heal_repo_passes_heal_config_and_tracker(monkeypatch, tmp_path):
    from harness.app import HealConfig
    from harness.drivers.memory import MemoryIssueTracker

    main(["init", "--root", str(tmp_path)])
    captured = {}

    def fake_build(*args, **kwargs):
        captured["heal"] = kwargs.get("heal")
        captured["issue_tracker"] = kwargs.get("issue_tracker")
        return object()

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        pass

    monkeypatch.setattr("harness.cli.build", fake_build)
    monkeypatch.setattr("harness.cli.serve", fake_serve)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert main(["run", "--root", str(tmp_path), "--heal-repo", "onpaj/harness_v2"]) == 0
    assert captured["heal"] == HealConfig(repository="onpaj/harness_v2")
    # offline (no token) → the in-memory tracker, so the loop still runs
    assert isinstance(captured["issue_tracker"], MemoryIssueTracker)


def test_run_heal_repo_uses_github_tracker_with_a_token(monkeypatch, tmp_path):
    from harness.drivers.github_issues import GithubIssueTracker

    main(["init", "--root", str(tmp_path)])
    captured = {}

    def fake_build(*args, **kwargs):
        captured["issue_tracker"] = kwargs.get("issue_tracker")
        return object()

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        pass

    monkeypatch.setattr("harness.cli.build", fake_build)
    monkeypatch.setattr("harness.cli.serve", fake_serve)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    assert main(["run", "--root", str(tmp_path), "--heal-repo", "onpaj/harness_v2"]) == 0
    assert isinstance(captured["issue_tracker"], GithubIssueTracker)


def test_run_without_heal_repo_wires_no_healer(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    captured = {}

    def fake_build(*args, **kwargs):
        captured["heal"] = kwargs.get("heal")
        return object()

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        pass

    monkeypatch.setattr("harness.cli.build", fake_build)
    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path)]) == 0
    assert captured["heal"] is None


def test_run_heal_repo_needs_claude_agent(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])

    def fake_build(*args, **kwargs):  # must not be reached
        raise AssertionError("build should not run when --heal-repo rejects the args")

    monkeypatch.setattr("harness.cli.build", fake_build)

    code = main(
        ["run", "--root", str(tmp_path), "--heal-repo", "o/r", "--agent", "dummy"]
    )
    assert code == 2


def test_run_defaults_agent_timeout_to_1800(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    captured = {}

    def fake_build(*args, **kwargs):
        captured["agent_timeout"] = kwargs["agent_timeout"]
        return object()

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        pass

    monkeypatch.setattr("harness.cli.build", fake_build)
    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path)]) == 0
    assert captured["agent_timeout"] == 1800.0


def test_run_accepts_explicit_agent_timeout(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    captured = {}

    def fake_build(*args, **kwargs):
        captured["agent_timeout"] = kwargs["agent_timeout"]
        return object()

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        pass

    monkeypatch.setattr("harness.cli.build", fake_build)
    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path), "--agent-timeout", "60"]) == 0
    assert captured["agent_timeout"] == 60.0


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


HOTFIX_DEFINITION = {
    "name": "hotfix",
    "start": "plan",
    "transitions": [{"from": "plan", "on": "done", "to": "end"}],
}


def test_run_serves_multiple_workflows_with_repeated_flag(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        captured["harness"] = harness

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(
        [
            "run",
            "--root",
            str(tmp_path),
            "--workflow",
            "default",
            "--workflow",
            "hotfix",
        ]
    ) == 0
    assert set(captured["harness"].workflows) == {"default", "hotfix"}


def test_run_with_no_workflow_flag_serves_only_default(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        captured["harness"] = harness

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path)]) == 0
    assert set(captured["harness"].workflows) == {"default"}


def test_run_all_workflows_serves_every_definition_found(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        captured["harness"] = harness

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path), "--all-workflows"]) == 0
    # `init` also scaffolds the resolver workflow, so `--all-workflows` serves it too.
    assert set(captured["harness"].workflows) == {"default", "hotfix", "resolver"}


def test_run_rejects_workflow_and_all_workflows_together(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    capsys.readouterr()

    assert main(
        ["run", "--root", str(tmp_path), "--workflow", "default", "--all-workflows"]
    ) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "mutually exclusive" in err


def test_run_all_workflows_with_no_definitions_is_a_startup_error(tmp_path, capsys):
    (tmp_path / "workflows").mkdir(parents=True)

    assert main(["run", "--root", str(tmp_path), "--all-workflows"]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "no workflow definitions found" in err


def test_run_rejects_github_workflow_not_in_served_set(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
    capsys.readouterr()

    assert main(
        [
            "run",
            "--root",
            str(tmp_path),
            "--workflow",
            "default",
            "--github-workflow",
            "hotfix",
        ]
    ) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "hotfix" in err
    assert "not served" in err


def test_run_single_custom_workflow_ignores_github_workflow_default(
    monkeypatch, tmp_path, capsys
):
    """Regression: `--github-workflow` used to default to `DEFAULT_WORKFLOW`
    ("default") and get checked against the served set unconditionally, so
    `run --workflow hotfix` with no GitHub flags at all (and no GITHUB_TOKEN)
    used to fail startup even though no GithubTaskSource is ever built in that
    case. FR-6 requires single-workflow runs to behave exactly as before."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        captured["harness"] = harness

    monkeypatch.setattr("harness.cli.serve", fake_serve)
    capsys.readouterr()

    assert main(["run", "--root", str(tmp_path), "--workflow", "hotfix"]) == 0

    out, err = capsys.readouterr()
    assert err == ""
    assert set(captured["harness"].workflows) == {"hotfix"}


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
    """The minimal namespace the `run` parser hands to `_github_sources`."""
    base = dict(
        github_workflow="default",
        github_label="harness:todo",
        worktree_root=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_github_sources_builds_one_per_github_repo(monkeypatch, tmp_path):
    """One source per repos.json repo that has a GitHub origin; the task carries
    the repo *name* (invariant 15), not a path."""
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "harness_v2": Path("/repos/harness_v2")}
    )
    slugs = {
        Path("/repos/heblo"): "onpaj/Anela.Heblo",
        Path("/repos/harness_v2"): "onpaj/harness_v2",
    }

    sources = _github_sources(
        _github_args(),
        tmp_path,
        registry,
        slug_of=slugs.get,
        client=FakeGithubClient(),
    )

    assert {s._repository for s in sources} == {"heblo", "harness_v2"}
    assert {s._repo for s in sources} == {"onpaj/Anela.Heblo", "onpaj/harness_v2"}


def test_github_sources_skips_repo_without_github_origin(monkeypatch, tmp_path, capsys):
    """A repo whose origin is not GitHub is skipped with a warning, others build."""
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "local": Path("/repos/local")}
    )
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo", Path("/repos/local"): None}

    sources = _github_sources(
        _github_args(), tmp_path, registry, slug_of=slugs.get, client=FakeGithubClient()
    )

    assert [s._repository for s in sources] == ["heblo"]
    assert "local has no GitHub origin" in capsys.readouterr().err


def test_github_sources_empty_without_token(monkeypatch, tmp_path):
    """No GITHUB_TOKEN → no sources (harness runs on `submit` alone), silently."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})

    assert _github_sources(_github_args(), tmp_path, registry) == []


def _mergeability_args(**overrides):
    base = dict(worktree_root=None, resolver_workflow="resolver")
    base.update(overrides)
    return argparse.Namespace(**base)


def test_mergeability_sources_builds_one_per_github_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "harness_v2": Path("/repos/harness_v2")}
    )
    slugs = {
        Path("/repos/heblo"): "onpaj/Anela.Heblo",
        Path("/repos/harness_v2"): "onpaj/harness_v2",
    }

    sources = _mergeability_sources(
        _mergeability_args(),
        tmp_path,
        registry,
        slug_of=slugs.get,
        client=FakeGithubClient(),
    )

    assert {s._repository for s in sources} == {"heblo", "harness_v2"}
    assert {s._repo for s in sources} == {"onpaj/Anela.Heblo", "onpaj/harness_v2"}
    assert all(s._resolver_workflow == "resolver" for s in sources)


def test_mergeability_sources_skips_repo_without_github_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "local": Path("/repos/local")}
    )
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo", Path("/repos/local"): None}

    sources = _mergeability_sources(
        _mergeability_args(), tmp_path, registry, slug_of=slugs.get, client=FakeGithubClient()
    )

    assert [s._repository for s in sources] == ["heblo"]


def test_mergeability_sources_empty_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})

    assert _mergeability_sources(_mergeability_args(), tmp_path, registry) == []


def test_init_also_writes_resolver_workflow_and_resolve_agent(tmp_path):
    assert main(["init", "--root", str(tmp_path)]) == 0

    resolver = json.loads((tmp_path / "workflows" / "resolver.json").read_text())
    assert resolver["start"] == "resolve"
    assert {"from": "resolve", "on": "done", "to": "land"} in resolver["transitions"]
    assert {"from": "land", "on": "done", "to": "end"} in resolver["transitions"]

    resolve_agent = json.loads((tmp_path / "agents" / "resolve.json").read_text())
    assert resolve_agent["allowed_outcomes"] == ["done"]
    assert "merge conflict" in resolve_agent["prompt"]
    # `land` is shared with the default workflow and already written there —
    # no separate agents/land.json (landing has no persona, invariant unchanged).
    assert not (tmp_path / "agents" / "land.json").exists()


def test_init_is_idempotent_for_resolver_workflow(tmp_path):
    main(["init", "--root", str(tmp_path)])
    (tmp_path / "workflows" / "resolver.json").write_text(
        json.dumps({"name": "resolver", "start": "resolve", "transitions": []})
    )

    assert main(["init", "--root", str(tmp_path)]) == 0

    resolver = json.loads((tmp_path / "workflows" / "resolver.json").read_text())
    assert resolver["transitions"] == []


def test_run_watch_mergeability_defaults_on_and_can_be_disabled(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        captured["harness"] = harness

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path), "--no-watch-mergeability"]) == 0
    # No token either way, so no observable source either way — this just
    # exercises that the flag parses and the run completes.
    assert "harness" in captured


def test_run_accepts_api_port(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        captured["port"] = port
        captured["source_interval"] = source_interval

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path), "--api-port", "9123"]) == 0
    assert captured["port"] == 9123
    assert captured["source_interval"] == 30.0


def test_run_forwards_source_poll(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    captured = {}

    async def fake_serve(harness, port, poll_interval, source_interval=30.0):
        captured["source_interval"] = source_interval

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path), "--source-poll", "5"]) == 0
    assert captured["source_interval"] == 5.0


async def test_serve_returns_when_uvicorn_stops_before_the_loop(monkeypatch, tmp_path):
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
            self.layout = HarnessLayout(tmp_path)
            self.projection = BoardProjection([SERVE_TEST_WORKFLOW])
            self.artifacts = MemoryArtifactStore()
            self.stage_output = StageOutputProjection()
            self.control = FakeTaskControl()
            self.stop_seen: asyncio.Event | None = None

        async def run(self, poll_interval, source_interval=30.0, stop=None):
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


# --- harness service -------------------------------------------------------


def test_service_install_refuses_an_uninitialized_root(tmp_path, monkeypatch, capsys):
    # Pin the platform: on Linux the launchd guard fires first and this would
    # assert the wrong message (as CI found).
    monkeypatch.setattr("harness.cli.sys.platform", "darwin")

    code = main(["service", "install", "--root", str(tmp_path / "nope")])

    assert code == 2
    assert "not initialized" in capsys.readouterr().err


def test_service_install_refuses_a_non_macos_host(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("harness.cli.sys.platform", "linux")

    code = main(["service", "install", "--root", str(tmp_path)])

    assert code == 2
    err = capsys.readouterr().err
    assert "launchd" in err and "linux" in err


def test_service_status_refuses_a_non_macos_host(monkeypatch, capsys):
    monkeypatch.setattr("harness.cli.sys.platform", "linux")

    assert main(["service", "status"]) == 2
    assert "launchd" in capsys.readouterr().err


def test_service_requires_an_action():
    import pytest

    with pytest.raises(SystemExit):
        main(["service"])


def test_service_path_entries_lead_with_the_venv_bin():
    from harness.cli import service_path_entries

    entries = service_path_entries(Path("/opt/app/.venv/bin/harness"))

    assert entries[0] == "/opt/app/.venv/bin"
    # git and gh live in these; without them the service cannot work at all.
    assert "/usr/local/bin" in entries
    assert "/usr/bin" in entries


def test_service_entry_point_is_a_real_script():
    """Regression: resolving sys.executable follows the venv symlink out to the
    base interpreter (uv-managed CPython), where no `harness` script exists —
    the service then failed to install. Whichever candidate wins (uv shim or
    this environment's own script), it must be a file that exists."""
    from harness.cli import service_entry_point

    entry = service_entry_point()

    assert entry.is_file(), f"{entry} is not an executable script"
    assert "share/uv/python" not in str(entry), (
        "pointed at the managed interpreter, not at a harness script"
    )


# --- uv install / update ---------------------------------------------------


def test_version_flag_reports_the_package_version(capsys):
    import pytest

    from harness.cli import version_string

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert version_string() in capsys.readouterr().out


def test_service_entry_point_prefers_the_uv_shim(tmp_path, monkeypatch):
    """`uv tool upgrade` rebuilds the tool env, but keeps the shim path stable,
    so an installed LaunchAgent must point at the shim, not the tool venv."""
    from harness import cli

    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    shim = home / ".local" / "bin" / "harness"
    shim.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))

    assert cli.service_entry_point() == shim


def test_service_entry_point_falls_back_to_this_environment(tmp_path, monkeypatch):
    import sys

    from harness import cli

    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path / "empty"))

    assert cli.service_entry_point() == Path(sys.prefix) / "bin" / "harness"


def test_uv_executable_falls_back_to_the_standard_location(tmp_path, monkeypatch):
    from harness import cli

    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    uv = home / ".local" / "bin" / "uv"
    uv.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))

    assert cli.uv_executable() == uv


def test_uv_executable_is_none_when_uv_is_absent(tmp_path, monkeypatch):
    from harness import cli

    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path / "empty"))

    assert cli.uv_executable() is None


def test_update_without_uv_explains_how_to_get_it(tmp_path, monkeypatch, capsys):
    from harness import cli

    monkeypatch.setattr(cli, "uv_executable", lambda: None)

    assert main(["update"]) == 2
    assert "astral.sh/uv/install.sh" in capsys.readouterr().err


def test_update_runs_uv_tool_upgrade(monkeypatch, capsys):
    from harness import cli

    calls = []

    class Result:
        returncode = 0
        stdout = "Updated harness\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(cli, "uv_executable", lambda: Path("/uv"))
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    monkeypatch.setattr(cli, "installed_version_report", lambda: "harness 0.2.0 (git abc1234)")

    assert main(["update"]) == 0
    assert calls == [["/uv", "tool", "upgrade", "harness"]]
    out = capsys.readouterr().out
    # A running service keeps the old code until it is bounced — say so.
    assert "kickstart" in out
    # ...and report the version we just installed, not the one being replaced.
    assert "harness 0.2.0 (git abc1234)" in out


def test_update_reports_a_failed_upgrade(monkeypatch, capsys):
    from harness import cli

    class Result:
        returncode = 2
        stdout = ""
        stderr = "no such tool\n"

    monkeypatch.setattr(cli, "uv_executable", lambda: Path("/uv"))
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: Result())

    assert main(["update"]) == 1
    assert "uv tool upgrade failed" in capsys.readouterr().err


def test_version_string_includes_the_source_commit(monkeypatch):
    """pyproject carries one static version, so two installs both say 0.1.0 —
    the commit from PEP 610 direct_url.json is what tells them apart."""
    from harness import cli

    class Dist:
        @staticmethod
        def read_text(name):
            assert name == "direct_url.json"
            return json.dumps(
                {
                    "url": "https://github.com/onpaj/harness_v2.git",
                    "vcs_info": {"vcs": "git", "commit_id": "e427b9fafaa15f26c5ec"},
                }
            )

    monkeypatch.setattr(cli.metadata, "version", lambda name: "0.1.0")
    monkeypatch.setattr(cli.metadata, "distribution", lambda name: Dist())

    assert cli.version_string() == "0.1.0 (git e427b9f)"


def test_version_string_without_vcs_info_is_just_the_version(monkeypatch):
    from harness import cli

    class Dist:
        @staticmethod
        def read_text(name):
            return json.dumps({"url": "file:///tmp/harness", "dir_info": {}})

    monkeypatch.setattr(cli.metadata, "version", lambda name: "0.1.0")
    monkeypatch.setattr(cli.metadata, "distribution", lambda name: Dist())

    assert cli.version_string() == "0.1.0"


def test_version_string_survives_a_missing_direct_url(monkeypatch):
    from harness import cli

    class Dist:
        @staticmethod
        def read_text(name):
            return None  # editable/source installs have no direct_url.json

    monkeypatch.setattr(cli.metadata, "version", lambda name: "0.1.0")
    monkeypatch.setattr(cli.metadata, "distribution", lambda name: Dist())

    assert cli.version_string() == "0.1.0"


def test_build_timestamp_is_the_install_locations_mtime(tmp_path, monkeypatch):
    from harness import cli

    install_dir = tmp_path / "harness-0.1.0.dist-info"
    install_dir.mkdir()
    # A known mtime, expressed in whole seconds (the function truncates to
    # second precision), so the assertion is exact rather than approximate.
    stamp = 1_784_629_920  # 2026-07-21T10:32:00Z
    os.utime(install_dir, (stamp, stamp))

    class Dist:
        @staticmethod
        def locate_file(name):
            assert name == ""
            return install_dir

    monkeypatch.setattr(cli.metadata, "distribution", lambda name: Dist())

    assert cli.build_timestamp() == "2026-07-21T10:32:00Z"


def test_build_timestamp_is_none_when_not_installed(monkeypatch):
    from harness import cli

    def raise_not_found(name):
        raise cli.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(cli.metadata, "distribution", raise_not_found)

    assert cli.build_timestamp() is None


def test_build_timestamp_is_none_on_an_unreadable_location(monkeypatch):
    from harness import cli

    class Dist:
        @staticmethod
        def locate_file(name):
            return "/no/such/path/at/all"

    monkeypatch.setattr(cli.metadata, "distribution", lambda name: Dist())

    assert cli.build_timestamp() is None


def test_installed_version_report_asks_the_new_script(tmp_path, monkeypatch):
    """After an upgrade this process is the OLD code, so reading our own
    metadata would report the version we just replaced."""
    from harness import cli

    script = tmp_path / "harness"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli, "service_entry_point", lambda: script)

    class Result:
        returncode = 0
        stdout = "harness 0.3.0 (git deadbee)\n"
        stderr = ""

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Result()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.installed_version_report() == "harness 0.3.0 (git deadbee)"
    assert seen["cmd"] == [str(script), "--version"]


def test_installed_version_report_degrades_when_the_script_fails(tmp_path, monkeypatch):
    from harness import cli

    script = tmp_path / "harness"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli, "service_entry_point", lambda: script)

    class Result:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: Result())

    # The upgrade itself succeeded; only the report failed. Don't imply otherwise.
    assert "installed" in cli.installed_version_report()


# --- forge selection -------------------------------------------------------


def test_run_rejects_an_unknown_forge():
    with pytest.raises(SystemExit):
        main(["run", "--forge", "bogus"])


def test_build_forge_returns_fake_when_asked(tmp_path):
    from harness.cli import _build_forge
    from harness.drivers.fake_forge import FakeForge

    assert isinstance(_build_forge("fake", tmp_path), FakeForge)


def test_build_forge_without_a_token_still_returns_a_github_forge(tmp_path, monkeypatch):
    """No token must fail at `land`, on the task — not refuse to start, which
    would make the harness unusable for `harness submit`."""
    from harness.cli import _build_forge
    from harness.drivers.github_forge import GithubForge

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    forge = _build_forge("github", tmp_path)

    assert isinstance(forge, GithubForge)
    assert forge._client is None


def test_build_forge_with_a_token_wires_the_http_client(tmp_path, monkeypatch):
    from harness.cli import _build_forge
    from harness.drivers.github_client import HttpGithubClient

    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    assert isinstance(_build_forge("github", tmp_path)._client, HttpGithubClient)


def test_run_agent_defaults_to_claude_and_accepts_dummy(tmp_path, monkeypatch):
    """`--agent dummy` runs the real pipeline (worktree, push, forge) with a stub
    step behavior — the only way to exercise landing where claude is unusable."""
    main(["init", "--root", str(tmp_path)])
    seen = {}

    def fake_build(*args, **kwargs):
        seen.update(kwargs)
        raise SystemExit(0)  # stop before the event loop

    monkeypatch.setattr("harness.cli.build", fake_build)

    with pytest.raises(SystemExit):
        main(["run", "--root", str(tmp_path), "--agent", "dummy", "--api-port", "0"])
    assert seen["catalog"] is None and seen["runner"] is None

    seen.clear()
    with pytest.raises(SystemExit):
        main(["run", "--root", str(tmp_path), "--api-port", "0"])
    assert seen["catalog"] is not None and seen["runner"] is not None


def test_service_install_prints_setup_token_steps_when_no_active_token(tmp_path, monkeypatch, capsys):
    """The commented example in the template must not be mistaken for a real
    token — otherwise the operator never sees the setup instructions."""
    monkeypatch.setattr("harness.cli.sys.platform", "darwin")
    monkeypatch.setattr("harness.cli.load", lambda *a, **k: None)
    monkeypatch.setattr("harness.cli.Path.home", staticmethod(lambda: tmp_path))
    main(["init", "--root", str(tmp_path / "root")])
    capsys.readouterr()

    main(["service", "install", "--root", str(tmp_path / "root")])

    out = capsys.readouterr().out
    assert "claude setup-token" in out
    assert (tmp_path / "root" / "secrets.env").stat().st_mode & 0o777 == 0o600


def test_service_install_stays_quiet_once_a_token_is_set(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("harness.cli.sys.platform", "darwin")
    monkeypatch.setattr("harness.cli.load", lambda *a, **k: None)
    monkeypatch.setattr("harness.cli.Path.home", staticmethod(lambda: tmp_path))
    root = tmp_path / "root"
    main(["init", "--root", str(root)])
    (root / "secrets.env").write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-real\n")
    capsys.readouterr()

    main(["service", "install", "--root", str(root)])

    out = capsys.readouterr().out
    assert "claude setup-token" not in out
    # An existing secrets file is never clobbered.
    assert "sk-ant-oat01-real" in (root / "secrets.env").read_text()


# --- idle detection & idle-gated restart -----------------------------------


def _claim(root: Path, step: str, task_id: str) -> None:
    """Simulate a stage actively working: a task in <queue>/.processing/."""
    proc = root / "queues" / step / ".processing"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / f"{task_id}.json").write_text("{}", encoding="utf-8")


def test_active_stages_empty_when_nothing_is_processing(tmp_path):
    from harness.cli import active_stages

    main(["init", "--root", str(tmp_path)])
    assert active_stages(tmp_path) == []


def test_active_stages_lists_claimed_tasks(tmp_path):
    from harness.cli import active_stages

    main(["init", "--root", str(tmp_path)])
    _claim(tmp_path, "development", "tsk_a")
    _claim(tmp_path, "review", "tsk_b")

    assert active_stages(tmp_path) == ["tsk_a", "tsk_b"]


def test_update_restart_only_if_idle_skips_when_busy(tmp_path, monkeypatch, capsys):
    from harness import cli

    main(["init", "--root", str(tmp_path)])
    _claim(tmp_path, "plan", "tsk_live")
    capsys.readouterr()

    class Ok:
        returncode = 0
        stdout = "upgraded\n"
        stderr = ""

    monkeypatch.setattr(cli, "uv_executable", lambda: Path("/uv"))
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **k: Ok())
    monkeypatch.setattr(cli, "installed_version_report", lambda: "harness 0.9.0")
    restarted = []
    monkeypatch.setattr(cli, "kickstart", lambda uid, label: restarted.append(label))

    assert main(["update", "--root", str(tmp_path), "--restart", "--only-if-idle"]) == 0
    out = capsys.readouterr().out
    assert "tsk_live" in out and "skipping the restart" in out
    assert restarted == []  # a live stage must never be killed


def test_update_restart_only_if_idle_restarts_when_quiet(tmp_path, monkeypatch, capsys):
    from harness import cli

    main(["init", "--root", str(tmp_path)])  # no .processing => idle
    capsys.readouterr()

    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(cli, "uv_executable", lambda: Path("/uv"))
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **k: Ok())
    monkeypatch.setattr(cli, "installed_version_report", lambda: "harness 0.9.0")
    monkeypatch.setattr("harness.cli.sys.platform", "darwin")
    restarted = []
    monkeypatch.setattr(cli, "kickstart", lambda uid, label: restarted.append(label))

    assert main(["update", "--root", str(tmp_path), "--restart", "--only-if-idle"]) == 0
    assert restarted == ["com.harness"]
    assert "restarted service com.harness" in capsys.readouterr().out


def test_update_without_restart_still_only_prints_the_hint(monkeypatch, capsys):
    from harness import cli

    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(cli, "uv_executable", lambda: Path("/uv"))
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **k: Ok())
    monkeypatch.setattr(cli, "installed_version_report", lambda: "harness 0.9.0")
    restarted = []
    monkeypatch.setattr(cli, "kickstart", lambda uid, label: restarted.append(label))

    assert main(["update"]) == 0
    assert restarted == []
    assert "kickstart" in capsys.readouterr().out


# --- autoupdate schedule ---------------------------------------------------


def test_autoupdate_hours_parse_and_reject():
    from harness.cli import _parse_hours
    import pytest as _pytest

    assert _parse_hours("2,8,14,20") == [2, 8, 14, 20]
    assert _parse_hours("20,2,2") == [2, 20]  # sorted + deduped
    for bad in ("24", "-1", "x", ""):
        with _pytest.raises(ValueError):
            _parse_hours(bad)


def test_autoupdate_plist_runs_the_idle_gated_update():
    from harness.drivers.launchd import autoupdate_plist_bytes
    import plistlib

    raw = autoupdate_plist_bytes(
        label="com.harness.autoupdate",
        harness=Path("/Users/rem/.local/bin/harness"),
        service_label="com.harness",
        hours=[2, 14],
        path_entries=["/Users/rem/.local/bin", "/usr/bin"],
        log_dir=Path("/r/logs"),
        home=Path("/Users/rem"),
    )
    d = plistlib.loads(raw)

    assert d["ProgramArguments"] == [
        "/Users/rem/.local/bin/harness",
        "update",
        "--restart",
        "--only-if-idle",
        "--label",
        "com.harness",
    ]
    assert d["StartCalendarInterval"] == [{"Hour": 2, "Minute": 0}, {"Hour": 14, "Minute": 0}]
    assert "KeepAlive" not in d  # a periodic one-shot, not a daemon
