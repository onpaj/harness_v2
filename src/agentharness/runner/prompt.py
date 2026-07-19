"""Prompt composition.

The prompt stays deliberately tiny. State lives in files, not in the prompt --
that is what makes an agent a pure function of its committed inputs.
"""

from __future__ import annotations

from agentharness.models import AgentDef, Task

TERMINAL_SENTENCE = "You may not hand off to any agent; this is a terminal step."

PROTOCOL_PREAMBLE = """You are operating as the "{agent}" agent.

Your task is in {task_path}. Read it first.
Its intent is: {intent}
Input artifacts are already present in this working directory.

Do the work. Write any outputs where the task asks.

When finished, write {result_path} following this schema:
  status   - one of "ok", "failed", "needs_input"
  summary  - one sentence on what you did
  outputs  - list of paths you produced, relative to this directory
  handoffs - list of follow-up tasks, each {{agent, intent, payload, artifacts}}
  metrics  - optional object of numbers worth recording

{handoff_rule}"""


def compose_prompt(task: Task, agent: AgentDef) -> str:
    targets = agent.can_handoff_to
    if targets:
        handoff_rule = "Emit handoffs ONLY to these agents: " + ", ".join(targets) + "."
    else:
        handoff_rule = TERMINAL_SENTENCE

    return PROTOCOL_PREAMBLE.format(
        agent=agent.name,
        task_path=f"{task.artifact_dir}/task.json",
        result_path=f"{task.artifact_dir}/result.json",
        intent=task.intent,
        handoff_rule=handoff_rule,
    )
