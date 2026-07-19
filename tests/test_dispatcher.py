from harness.dispatcher import Dispatcher
from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    MemoryWorkflowRepository,
)
from harness.models import END, FAILED, Task, Transition, Workflow
from harness.ports.strategy import EnqueueStrategy

WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
        Transition(from_step="review", on="request_changes", to_step="plan"),
    ),
)


class FirstStrategy(EnqueueStrategy):
    def select(self, tasks):
        return tasks[0] if tasks else None


def build(task: Task | None = None):
    inbox = MemoryTaskQueue("tasks")
    step_queues = {step: MemoryTaskQueue(step) for step in WORKFLOW.steps()}
    done = MemoryTaskQueue("done")
    failed = MemoryTaskQueue("failed")
    events = MemoryEventSink()
    dispatcher = Dispatcher(
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        workflows=MemoryWorkflowRepository({"default": WORKFLOW}),
        strategy=FirstStrategy(),
        events=events,
        clock=FakeClock(),
    )
    if task is not None:
        inbox.put(task)
    return dispatcher, inbox, step_queues, done, failed, events


def make_task(status=None, last_outcome=None, template="default") -> Task:
    return Task(
        id="tsk_1",
        workflow_template=template,
        created="2026-07-19T10:00:00Z",
        status=status,
        last_outcome=last_outcome,
        data={"payload": "nedotknutelné"},
    )


def test_tick_on_empty_inbox_does_nothing():
    dispatcher, *_ = build()

    assert dispatcher.tick() is False


def test_new_task_goes_to_start_queue_with_status_set():
    dispatcher, inbox, step_queues, _, _, events = build(make_task())

    assert dispatcher.tick() is True

    assert inbox.list() == []
    routed = step_queues["plan"].list()[0]
    assert routed.status == "plan"
    assert routed.lock_id is None
    _, fields = next(item for item in events.events if item[0] == "dispatched")
    assert fields["task_id"] == "tsk_1"
    assert fields["from"] is None
    assert fields["to"] == "plan"
    assert fields["outcome"] is None


def test_dispatch_appends_history():
    dispatcher, _, step_queues, _, _, _ = build(make_task())

    dispatcher.tick()

    entry = step_queues["plan"].list()[0].history[-1]
    assert entry.actor == "dispatcher"
    assert entry.from_step is None
    assert entry.to_step == "plan"
    assert entry.at == "2026-07-19T10:00:00Z"


def test_forward_edge_moves_to_next_queue():
    dispatcher, _, step_queues, _, _, _ = build(make_task("plan", "done"))

    dispatcher.tick()

    assert step_queues["review"].list()[0].status == "review"


def test_backward_edge_moves_back():
    dispatcher, _, step_queues, _, _, _ = build(make_task("review", "request_changes"))

    dispatcher.tick()

    assert step_queues["plan"].list()[0].status == "plan"


def test_end_node_lands_in_done():
    dispatcher, _, _, done, _, events = build(make_task("review", "done"))

    dispatcher.tick()

    assert done.list()[0].id == "tsk_1"
    assert "finished" in events.names()


def test_unknown_workflow_template_lands_in_failed():
    dispatcher, _, _, _, failed, events = build(make_task(template="neznamy"))

    assert dispatcher.tick() is True

    task = failed.list()[0]
    assert task.history[-1].to_step == "failed"
    assert "neznamy" in task.history[-1].reason
    assert "failed" in events.names()


def test_unknown_workflow_template_task_carries_terminal_failed_status():
    """Task s neznámým workflowTemplate nemá status; failed/ ho ale musí
    nést jako terminální 'failed', ne nechat status na None — jinak by
    cokoli čtoucí status bez procházení history vidělo lež."""
    dispatcher, _, _, _, failed, _ = build(make_task(template="neznamy"))

    dispatcher.tick()

    assert failed.list()[0].status == FAILED


def test_missing_edge_lands_in_failed():
    dispatcher, _, _, _, failed, _ = build(make_task("plan", "request_changes"))

    dispatcher.tick()

    assert failed.list()[0].history[-1].to_step == "failed"


def test_mid_workflow_failure_task_carries_terminal_failed_status():
    """Task, který selže uprostřed workflow (chybějící hrana), nesmí ve
    failed/ zůstat se starým jménem kroku ('plan') — status musí přepsat
    na terminální 'failed', stejně jako _finish přepisuje na 'end'."""
    dispatcher, _, _, _, failed, _ = build(make_task("plan", "request_changes"))

    dispatcher.tick()

    assert failed.list()[0].status == FAILED


def test_step_without_queue_lands_in_failed():
    workflow = Workflow(
        name="default",
        start="chybejici",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )
    inbox = MemoryTaskQueue("tasks")
    failed = MemoryTaskQueue("failed")
    inbox.put(make_task())
    dispatcher = Dispatcher(
        inbox=inbox,
        step_queues={},
        done=MemoryTaskQueue("done"),
        failed=failed,
        workflows=MemoryWorkflowRepository({"default": workflow}),
        strategy=FirstStrategy(),
        events=MemoryEventSink(),
        clock=FakeClock(),
    )

    dispatcher.tick()

    assert "chybejici" in failed.list()[0].history[-1].reason


def test_one_bad_task_does_not_stop_the_loop():
    dispatcher, inbox, step_queues, _, failed, _ = build()
    inbox.put(make_task(template="neznamy"))
    inbox.put(
        Task(id="tsk_2", workflow_template="default", created="2026-07-19T10:00:01Z")
    )

    dispatcher.tick()
    dispatcher.tick()

    assert len(failed.list()) == 1
    assert len(step_queues["plan"].list()) == 1


def test_dispatcher_does_not_touch_payload():
    dispatcher, _, step_queues, _, _, _ = build(make_task())

    dispatcher.tick()

    assert step_queues["plan"].list()[0].data == {"payload": "nedotknutelné"}


def test_dispatched_event_carries_task_snapshot_and_queue():
    dispatcher, _, _, _, _, events = build(make_task())

    dispatcher.tick()

    name, fields = next(item for item in events.events if item[0] == "dispatched")
    assert fields["queue"] == "plan"
    assert fields["task"]["id"] == "tsk_1"
    assert fields["task"]["status"] == "plan"
    assert fields["task"]["lockId"] is None


def test_finished_event_carries_done_queue():
    dispatcher, _, _, _, _, events = build(make_task("review", "done"))

    dispatcher.tick()

    _, fields = next(item for item in events.events if item[0] == "finished")
    assert fields["queue"] == "done"
    assert fields["task"]["id"] == "tsk_1"


def test_failed_event_carries_failed_queue():
    dispatcher, _, _, _, _, events = build(make_task(template="neznamy"))

    dispatcher.tick()

    _, fields = next(item for item in events.events if item[0] == "failed")
    assert fields["queue"] == "failed"
    assert fields["task"]["id"] == "tsk_1"
