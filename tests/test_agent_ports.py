import pytest

from harness.drivers.memory import FakeAgentRunner, MemoryAgentCatalog
from harness.models import DONE, REQUEST_CHANGES, Task
from harness.ports.agent import AgentNotFound, AgentRun, AgentSpec


def make_task(status: str = "design", task_id: str = "tsk_1") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        status=status,
    )


def test_agent_spec_holds_fields_and_defaults():
    spec = AgentSpec(name="reviewer", prompt="Review it.")

    assert spec.name == "reviewer"
    assert spec.prompt == "Review it."
    assert spec.model is None
    assert spec.fallback_model is None
    assert spec.allowed_tools == ()
    assert spec.allowed_outcomes == (DONE,)
    assert spec.timeout is None


def test_agent_spec_accepts_overrides():
    spec = AgentSpec(
        name="reviewer",
        prompt="p",
        model="opus",
        fallback_model="sonnet",
        allowed_tools=("Read", "Edit"),
        allowed_outcomes=(DONE, REQUEST_CHANGES),
        timeout=120.0,
    )

    assert spec.model == "opus"
    assert spec.fallback_model == "sonnet"
    assert spec.allowed_tools == ("Read", "Edit")
    assert spec.allowed_outcomes == (DONE, REQUEST_CHANGES)
    assert spec.timeout == 120.0


def test_memory_catalog_get_returns_spec():
    spec = AgentSpec(name="planner", prompt="Plan it.")
    catalog = MemoryAgentCatalog({"plan": spec})

    assert catalog.get("plan") is spec


def test_memory_catalog_unknown_raises_not_found():
    catalog = MemoryAgentCatalog({})

    with pytest.raises(AgentNotFound):
        catalog.get("nope")


def test_memory_catalog_names_lists_every_spec():
    catalog = MemoryAgentCatalog(
        {"plan": AgentSpec(name="plan", prompt="p"), "review": AgentSpec(name="review", prompt="r")}
    )

    assert catalog.names() == ["plan", "review"]


def test_agent_run_token_fields_default_to_zero_and_none():
    run = AgentRun(DONE, "done")

    assert run.input_tokens == 0
    assert run.output_tokens == 0
    assert run.cache_read_tokens == 0
    assert run.cache_creation_tokens == 0
    assert run.total_cost_usd is None
    assert run.model is None


async def test_fake_runner_scripts_token_usage(tmp_path):
    """FR-3: FakeAgentRunner needs no new plumbing to script usage — a caller
    just constructs the AgentRun with the fields filled in."""
    spec = AgentSpec(name="planner", prompt="p")
    scripted = AgentRun(
        DONE,
        "done",
        input_tokens=120,
        output_tokens=45,
        cache_read_tokens=5,
        cache_creation_tokens=2,
        total_cost_usd=0.01,
        model="claude-sonnet-5",
    )
    runner = FakeAgentRunner(runs={"planner": scripted})

    result = await runner.run(prompt="do it", spec=spec, cwd=tmp_path, timeout=60.0)

    assert result.input_tokens == 120
    assert result.output_tokens == 45
    assert result.cache_read_tokens == 5
    assert result.cache_creation_tokens == 2
    assert result.total_cost_usd == 0.01
    assert result.model == "claude-sonnet-5"


async def test_fake_runner_returns_scripted_run_and_records_call(tmp_path):
    spec = AgentSpec(name="planner", prompt="p")
    scripted = AgentRun(DONE, "done", raw="{}")
    runner = FakeAgentRunner(runs={"planner": scripted})

    result = await runner.run(
        prompt="do it", spec=spec, cwd=tmp_path, timeout=60.0
    )

    assert result is scripted
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["prompt"] == "do it"
    assert call["spec"] is spec
    assert call["cwd"] == tmp_path
    assert call["timeout"] == 60.0


async def test_fake_runner_default_when_no_script(tmp_path):
    spec = AgentSpec(name="planner", prompt="p")
    default = AgentRun(REQUEST_CHANGES, "default run")
    runner = FakeAgentRunner(default=default)

    result = await runner.run(prompt="x", spec=spec, cwd=tmp_path, timeout=1.0)

    assert result is default


async def test_fake_runner_fallback_when_nothing_configured(tmp_path):
    spec = AgentSpec(name="planner", prompt="p")
    runner = FakeAgentRunner()

    result = await runner.run(prompt="x", spec=spec, cwd=tmp_path, timeout=1.0)

    assert result.outcome == DONE
    assert "planner" in result.summary


async def test_fake_runner_writes_files_into_cwd(tmp_path):
    spec = AgentSpec(name="dev", prompt="p")
    runner = FakeAgentRunner(
        writes={"dev": {".artifacts/tsk_1/dev-01.md": "content", "src/main.py": "print(1)"}}
    )

    await runner.run(prompt="x", spec=spec, cwd=tmp_path, timeout=1.0)

    artifact = tmp_path / ".artifacts" / "tsk_1" / "dev-01.md"
    code = tmp_path / "src" / "main.py"
    assert artifact.read_text() == "content"
    assert code.read_text() == "print(1)"
