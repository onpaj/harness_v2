from harness.drivers.fs_artifacts import FilesystemArtifactStore
from harness.ports.artifacts import ArtifactRef


def test_attempt_grows_on_disk_across_begins(tmp_path):
    store = FilesystemArtifactStore(tmp_path)

    first = store.begin("tsk_1", "design")
    second = store.begin("tsk_1", "design")

    assert first.attempt == 0
    assert second.attempt == 1
    assert (tmp_path / "tsk_1" / "design" / "0").is_dir()
    assert (tmp_path / "tsk_1" / "design" / "1").is_dir()


def test_put_read_roundtrip(tmp_path):
    store = FilesystemArtifactStore(tmp_path)

    slot = store.begin("tsk_1", "design")
    slot.put("design.md", "# design\n")

    assert (tmp_path / "tsk_1" / "design" / "0" / "design.md").read_text(
        encoding="utf-8"
    ) == "# design\n"
    assert store.read("tsk_1", "design", 0, "design.md") == "# design\n"


def test_second_attempt_does_not_overwrite_first(tmp_path):
    store = FilesystemArtifactStore(tmp_path)

    first = store.begin("tsk_1", "design")
    first.put("design.md", "first")
    second = store.begin("tsk_1", "design")
    second.put("design.md", "second")

    assert store.read("tsk_1", "design", 0, "design.md") == "first"
    assert store.read("tsk_1", "design", 1, "design.md") == "second"


def test_list_across_steps_and_attempts(tmp_path):
    store = FilesystemArtifactStore(tmp_path)

    store.begin("tsk_1", "design").put("design.md", "d")
    review_first = store.begin("tsk_1", "review")
    review_first.put("review.md", "r0")
    review_second = store.begin("tsk_1", "review")
    review_second.put("review.md", "r1")

    refs = store.list("tsk_1")

    assert refs == (
        ArtifactRef("design", 0, "design.md"),
        ArtifactRef("review", 0, "review.md"),
        ArtifactRef("review", 1, "review.md"),
    )


def test_read_missing_returns_none(tmp_path):
    store = FilesystemArtifactStore(tmp_path)

    assert store.read("tsk_1", "design", 0, "missing.md") is None
    assert store.list("unknown_task") == ()
