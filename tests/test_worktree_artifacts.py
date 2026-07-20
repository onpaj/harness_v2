from harness.artifacts_layout import next_attempt
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.ports.artifacts import ArtifactRef


def test_next_attempt_empty_starts_at_one(tmp_path):
    nn, relpath = next_attempt(tmp_path, "tsk_1", "development")

    assert nn == 1
    assert relpath == ".artifacts/tsk_1/development-01.md"


def test_next_attempt_grows_after_existing_file(tmp_path):
    artifacts = tmp_path / ".artifacts" / "tsk_1"
    artifacts.mkdir(parents=True)
    (artifacts / "development-01.md").write_text("first", encoding="utf-8")

    nn, relpath = next_attempt(tmp_path, "tsk_1", "development")

    assert nn == 2
    assert relpath == ".artifacts/tsk_1/development-02.md"


def test_next_attempt_counters_independent_per_step(tmp_path):
    artifacts = tmp_path / ".artifacts" / "tsk_1"
    artifacts.mkdir(parents=True)
    (artifacts / "development-01.md").write_text("d", encoding="utf-8")
    (artifacts / "development-02.md").write_text("d", encoding="utf-8")
    (artifacts / "review-01.md").write_text("r", encoding="utf-8")

    dev_nn, dev_rel = next_attempt(tmp_path, "tsk_1", "development")
    rev_nn, rev_rel = next_attempt(tmp_path, "tsk_1", "review")

    assert dev_nn == 3
    assert dev_rel == ".artifacts/tsk_1/development-03.md"
    assert rev_nn == 2
    assert rev_rel == ".artifacts/tsk_1/review-02.md"


def test_next_attempt_ignores_unrelated_and_task_level_files(tmp_path):
    artifacts = tmp_path / ".artifacts" / "tsk_1"
    artifacts.mkdir(parents=True)
    (artifacts / "plan.md").write_text("plan", encoding="utf-8")
    (artifacts / "development-extra.md").write_text("x", encoding="utf-8")
    (artifacts / "development-01.md").write_text("d", encoding="utf-8")

    nn, _ = next_attempt(tmp_path, "tsk_1", "development")

    assert nn == 2


def test_view_list_distinguishes_step_attempt_and_task_level(tmp_path):
    artifacts = tmp_path / "tsk_1" / ".artifacts" / "tsk_1"
    artifacts.mkdir(parents=True)
    (artifacts / "plan.md").write_text("p", encoding="utf-8")
    (artifacts / "architecture-decisions.md").write_text("a", encoding="utf-8")
    (artifacts / "development-01.md").write_text("d1", encoding="utf-8")
    (artifacts / "development-02.md").write_text("d2", encoding="utf-8")
    (artifacts / "review-01.md").write_text("r1", encoding="utf-8")

    view = WorktreeArtifactView(tmp_path)
    refs = view.list("tsk_1")

    assert refs == (
        ArtifactRef("architecture-decisions", 0, "architecture-decisions.md"),
        ArtifactRef("development", 1, "development-01.md"),
        ArtifactRef("development", 2, "development-02.md"),
        ArtifactRef("plan", 0, "plan.md"),
        ArtifactRef("review", 1, "review-01.md"),
    )


def test_view_list_missing_dir_is_empty(tmp_path):
    view = WorktreeArtifactView(tmp_path)

    assert view.list("unknown") == ()


def test_view_read_returns_content(tmp_path):
    artifacts = tmp_path / "tsk_1" / ".artifacts" / "tsk_1"
    artifacts.mkdir(parents=True)
    (artifacts / "development-01.md").write_text("# development\n", encoding="utf-8")
    (artifacts / "plan.md").write_text("# plan\n", encoding="utf-8")

    view = WorktreeArtifactView(tmp_path)

    assert view.read("tsk_1", "development", 1, "development-01.md") == "# development\n"
    assert view.read("tsk_1", "plan", 0, "plan.md") == "# plan\n"


def test_view_read_missing_returns_none(tmp_path):
    artifacts = tmp_path / "tsk_1" / ".artifacts" / "tsk_1"
    artifacts.mkdir(parents=True)

    view = WorktreeArtifactView(tmp_path)

    assert view.read("tsk_1", "development", 1, "missing.md") is None
