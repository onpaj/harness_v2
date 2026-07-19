from datetime import datetime, timezone

from agentharness.models import AgentDef, Task
from agentharness.runner.prompt import TERMINAL_SENTENCE, compose_prompt


def task() -> Task:
    return Task(
        task_id="t_9",
        trace_id="tr_9",
        agent="writer",
        intent="draft_article",
        idempotency_key="k",
        created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )


def agent(**over) -> AgentDef:
    base = dict(name="writer", description="writes", allowed_tools=["Read", "Write"])
    base.update(over)
    return AgentDef(**base)


def test_prompt_names_the_agent():
    assert '"writer" agent' in compose_prompt(task(), agent())


def test_prompt_points_at_the_task_and_result_paths():
    p = compose_prompt(task(), agent())
    assert ".harness/runs/tr_9/t_9/task.json" in p
    assert ".harness/runs/tr_9/t_9/result.json" in p


def test_prompt_states_the_intent():
    assert "draft_article" in compose_prompt(task(), agent())


def test_prompt_lists_every_allowed_handoff_target():
    p = compose_prompt(task(), agent(can_handoff_to=["reviewer", "publisher"]))
    assert "reviewer" in p and "publisher" in p


def test_prompt_declares_a_terminal_step_when_no_handoffs_allowed():
    assert TERMINAL_SENTENCE in compose_prompt(task(), agent(can_handoff_to=[]))


def test_prompt_omits_the_terminal_sentence_when_handoffs_are_allowed():
    p = compose_prompt(task(), agent(can_handoff_to=["reviewer"]))
    assert TERMINAL_SENTENCE not in p


def test_prompt_never_mentions_resuming():
    assert "resume" not in compose_prompt(task(), agent()).lower()


def test_prompt_stays_tiny():
    """State belongs in files. A growing prompt means state is leaking into it."""
    assert len(compose_prompt(task(), agent(can_handoff_to=["reviewer"]))) < 2000
