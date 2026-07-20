import asyncio
import json

import pytest

from harness.cli import DEFAULT_WORKFLOW, main, serve
from harness.drivers.memory import MemoryArtifactStore
from harness.models import END, Task, Transition, Workflow
from harness.projection import BoardProjection

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
    capsys.readouterr()  # zahoď hlášky z init, dál nás zajímá jen submit

    assert main(["submit", "--root", str(tmp_path), "--data", "{rozbite"]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "platný JSON" in err


def test_submit_without_init_fails_cleanly(tmp_path, capsys):
    assert main(["submit", "--root", str(tmp_path / "prazdno")]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "inicializovaný" in err


def test_run_with_unknown_workflow_fails_cleanly(tmp_path, capsys):
    """Třetí zdokumentovaná chybová cesta: neznámý workflow (přes `run`)."""
    main(["init", "--root", str(tmp_path)])
    capsys.readouterr()

    assert main(["run", "--root", str(tmp_path), "--workflow", "neexistujici"]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "neexistujici" in err


def test_init_rejects_workflow_name_with_path_separator(tmp_path, capsys):
    assert main(["init", "--root", str(tmp_path), "--workflow", "foo/bar"]) == 2

    out, err = capsys.readouterr()
    assert out == ""
    assert "foo/bar" in err
    # ověření, že selhalo dřív, než se cokoliv zapsalo na disk
    assert not (tmp_path / "workflows").exists()


def test_root_before_subcommand_is_rejected_not_silently_applied(tmp_path, monkeypatch):
    """`--root` zadané PŘED podpříkazem se dřív tiše zahazovalo a harness sáhl
    na (chybný) výchozí kořen. Musí to selhat nahlas, ne zapsat jinam.
    HARNESS_HOME navíc přesměrujeme na tmp_path, aby test i při regresi nikdy
    nesáhl na reálný ~/.harness."""
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "should-not-be-used"))
    bogus_root = tmp_path / "bogus-root"

    with pytest.raises(SystemExit) as excinfo:
        main(["--root", str(bogus_root), "init"])

    assert excinfo.value.code == 2
    assert not bogus_root.exists()
    assert not (tmp_path / "should-not-be-used").exists()


def test_harness_home_used_only_when_root_absent(tmp_path, monkeypatch):
    """HARNESS_HOME se použije jen tehdy, když --root chybí; jinak má --root
    přednost."""
    env_root = tmp_path / "from-env"
    flag_root = tmp_path / "from-flag"
    monkeypatch.setenv("HARNESS_HOME", str(env_root))

    assert main(["init"]) == 0
    assert env_root.is_dir()
    assert not flag_root.exists()

    assert main(["init", "--root", str(flag_root)]) == 0
    assert flag_root.is_dir()


def test_run_accepts_api_port(monkeypatch, tmp_path):
    main(["init", "--root", str(tmp_path)])
    captured = {}

    async def fake_serve(harness, port, poll_interval):
        captured["port"] = port

    monkeypatch.setattr("harness.cli.serve", fake_serve)

    assert main(["run", "--root", str(tmp_path), "--api-port", "9123"]) == 0
    assert captured["port"] == 9123


async def test_serve_returns_when_uvicorn_stops_before_the_loop(monkeypatch):
    """Regrese: `serve()` dřív dělal `await asyncio.gather(loop, uvicorn.Server(...).serve())`
    a `stop.set()` volal až ve `finally`. Když uvicorn (po Ctrl+C) doběhne dřív a
    vrátí se BEZ výjimky, `gather` dál čekal na `loop` -- ten ale neskončí dřív,
    než se nastaví `stop`, ke kterému se kód dostane teprve PO návratu z `gather`.
    Uzavřený kruh: `serve()` by nikdy neskončil.

    Reprodukce je strukturální (jak to dělal reviewer): fake uvicorn server, co
    se sám vrátí okamžitě (jako uvicorn po Ctrl+C), + `loop`, co běží donekonečna,
    dokud nedostane `stop`. Na nedopravené verzi tenhle test zamrzne a spadne na
    `asyncio.wait_for` timeoutu; na opravené doběhne během zlomku sekundy."""

    class FakeHarness:
        def __init__(self):
            self.projection = BoardProjection(SERVE_TEST_WORKFLOW)
            self.artifacts = MemoryArtifactStore()
            self.stop_seen: asyncio.Event | None = None

        async def run(self, poll_interval, stop):
            self.stop_seen = stop
            while not stop.is_set():
                await asyncio.sleep(0.01)

    class FakeUvicornServer:
        def __init__(self, config):
            pass

        async def serve(self):
            return  # simuluje uvicorn, který se po Ctrl+C vrátí bez výjimky

    monkeypatch.setattr("harness.cli.uvicorn.Server", FakeUvicornServer)

    harness = FakeHarness()
    await asyncio.wait_for(serve(harness, 8000, 0.01), timeout=2.0)

    assert harness.stop_seen is not None
    assert harness.stop_seen.is_set()
