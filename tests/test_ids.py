from agentharness.ids import new_run_id, new_task_id, new_trace_id


def test_ids_are_prefixed():
    assert new_task_id().startswith("t_")
    assert new_trace_id().startswith("tr_")
    assert new_run_id().startswith("r_")


def test_ids_are_unique():
    assert len({new_task_id() for _ in range(100)}) == 100


def test_ids_sort_chronologically():
    ids = [new_task_id() for _ in range(20)]
    assert ids == sorted(ids)
