from harness.drivers.memory import MemoryArtifactStore
from harness.ports.artifacts import ArtifactRef


def test_begin_allocates_increasing_attempts():
    store = MemoryArtifactStore()

    first = store.begin("tsk_1", "development")
    second = store.begin("tsk_1", "development")

    assert first.attempt == 0
    assert second.attempt == 1


def test_attempts_are_independent_per_step():
    store = MemoryArtifactStore()

    assert store.begin("tsk_1", "plan").attempt == 0
    assert store.begin("tsk_1", "design").attempt == 0


def test_put_then_read_roundtrip():
    store = MemoryArtifactStore()
    slot = store.begin("tsk_1", "plan")

    slot.put("plan.md", "# plan\n")

    assert store.read("tsk_1", "plan", 0, "plan.md") == "# plan\n"


def test_read_missing_returns_none():
    store = MemoryArtifactStore()

    assert store.read("tsk_1", "plan", 0, "nope.md") is None


def test_list_returns_refs_across_steps_and_attempts():
    store = MemoryArtifactStore()
    store.begin("tsk_1", "development").put("code.py", "a")
    store.begin("tsk_1", "review").put("review.md", "r1")
    store.begin("tsk_1", "development").put("code.py", "b")  # attempt 1
    store.begin("tsk_2", "plan").put("plan.md", "other")

    refs = store.list("tsk_1")

    assert ArtifactRef("development", 0, "code.py") in refs
    assert ArtifactRef("development", 1, "code.py") in refs
    assert ArtifactRef("review", 0, "review.md") in refs
    assert all(ref.step != "plan" for ref in refs)  # tsk_2 is not mixed in


def test_second_attempt_does_not_overwrite_first():
    store = MemoryArtifactStore()
    store.begin("tsk_1", "review").put("review.md", "first")
    store.begin("tsk_1", "review").put("review.md", "second")

    assert store.read("tsk_1", "review", 0, "review.md") == "first"
    assert store.read("tsk_1", "review", 1, "review.md") == "second"
