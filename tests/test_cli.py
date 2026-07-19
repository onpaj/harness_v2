import json

from harness.cli import DEFAULT_WORKFLOW, main
from harness.models import Task


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

    assert main(["submit", "--root", str(tmp_path), "--data", "{rozbite"]) == 2


def test_submit_without_init_fails_cleanly(tmp_path):
    assert main(["submit", "--root", str(tmp_path / "prazdno")]) == 2
