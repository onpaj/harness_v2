"""Harness CLI."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata as metadata
import json
import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import uvicorn

from harness.api.app import create_app
from harness.app import LANDING_STEP, HarnessLayout, build
from harness.behaviors.open_issue import OpenIssueBehavior
from harness.drivers.claude_cli import ClaudeCliRunner
from harness.drivers.fake_forge import FakeForge
from harness.drivers.fs_agents import FilesystemAgentAdmin, FilesystemAgentCatalog
from harness.drivers.fs_processes import FilesystemProcessAdmin
from harness.drivers.github_issues import GithubIssueTracker
from harness.drivers.memory import MemoryIssueTracker
from harness.drivers.fs_repos import FilesystemRepositoryRegistry
from harness.drivers.fs_workflows import (
    FilesystemWorkflowAdmin,
    FilesystemWorkflowRepository,
    invalid_step_name,
    invalid_workflow_name,
)
from harness.drivers.git_remote import github_slug
from harness.drivers.git_workspace import GitWorkspace
from harness.drivers.github_client import GithubClient, HttpGithubClient
from harness.drivers.github_forge import GithubForge
from harness.drivers.github_issue_checker import GithubIssueChecker
from harness.drivers.github_merge_checker import GithubMergeChecker
from harness.drivers.github_source import GithubLabelReflector, GithubTaskSource
from harness.drivers.jira_client import HttpJiraClient, JiraClient
from harness.drivers.label_issue import LabelIssueBehavior
from harness.drivers.slack_sink import SlackWebhookSink
from harness.drivers.uv_updater import UvUpdater
from harness.drivers.launchd import (
    DEFAULT_LABEL,
    ServiceError,
    autoupdate_plist_bytes,
    autoupdate_wrapper_script,
    format_interval,
    kickstart,
    load,
    parse_interval_minutes,
    periodic_plist_bytes,
    plist_bytes,
    plist_path,
    status,
    unload,
    wrapper_script,
)
from harness.drivers.system_clock import SystemClock
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.ids import new_task_id
from harness.models import DONE, REQUEST_CHANGES, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.issue_state import IssueChecker
from harness.ports.merge import MergeChecker
from harness.ports.repos import RepositoryNotFound, RepositoryRegistry
from harness.ports.source import TaskSource
from harness.ports.triggers import CheckFactory
from harness.ports.workflows import WorkflowNotFound

PACKAGE_NAME = "harness"

# Written to `<root>/secrets.env` (0600) when the service is installed, unless
# the file already exists. Sourced by the wrapper; the operator fills in the
# token that `claude` needs under launchd, where the keychain is unreachable.
_SECRETS_TEMPLATE = """\
# harness service secrets — sourced by harness-run.sh. Keep this file 0600.
# `claude` cannot read the macOS login keychain when run under launchd, so the
# background service needs a token in the environment. Create one with
# `claude setup-token` and uncomment the line below with its value:
#
# CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
#
# GITHUB_TOKEN is taken from `gh auth token` automatically; set it here only to
# override that.
# GITHUB_TOKEN=ghp_...
"""

DEFAULT_WORKFLOW = "development"

# A sensible coarse mapping of the development workflow's steps to labels.
# Other steps get no label → less noise. It's just a default, not a law.
DEFAULT_STEP_LABELS = {
    "development": "harness:in-progress",
    "review": "harness:in-review",
    "land": "harness:landing",
}

DEFAULT_DEFINITION = {
    "name": "development",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "architecture"},
        {"from": "architecture", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}

DEFAULT_RESOLVER_WORKFLOW = "resolver"

RESOLVER_DEFINITION = {
    "name": "resolver",
    "start": "resolve",
    "transitions": [
        {"from": "resolve", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
    ],
}

DEFAULT_HEAL_WORKFLOW = "heal"

HEAL_DEFINITION = {
    "name": "heal",
    "start": "heal",
    "transitions": [
        {"from": "heal", "on": "file", "to": "dedup",
         "hint": "a harness bug, or an operational/tuning problem worth filing"},
        {"from": "heal", "on": "skip", "to": "end",
         "hint": "external/transient, or the task's own request was impossible — nothing to file"},
        {"from": "dedup", "on": "unique", "to": "file-issue",
         "hint": "nothing similar is open in the harness repo"},
        {"from": "dedup", "on": "duplicate", "to": "end",
         "hint": "a correlated issue is already open — settle silently"},
        {"from": "file-issue", "on": "done", "to": "end"},
    ],
    "descriptions": {
        "heal": "diagnose the failed task from its report; decide whether it warrants a GitHub issue",
        "dedup": "read the harness repo's open issues; decide whether the drafted issue is new",
    },
    "finishers": {"file-issue": "open-issue"},
}


def _root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    return Path(os.environ.get("HARNESS_HOME", "~/.harness")).expanduser()


def _init(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)

    layout.agents.mkdir(parents=True, exist_ok=True)
    (root / "triggers").mkdir(parents=True, exist_ok=True)
    (root / "processes").mkdir(parents=True, exist_ok=True)
    _write_default_repos(layout)

    if args.no_workflow:
        layout.tasks.mkdir(parents=True, exist_ok=True)
        print(f"harness ready at {root} (no workflow — add steps under {layout.agents})")
        return 0

    if invalid_workflow_name(args.workflow):
        print(f"error: invalid workflow name: {args.workflow!r}", file=sys.stderr)
        return 2

    layout.workflows.mkdir(parents=True, exist_ok=True)

    definition_path = layout.workflows / f"{args.workflow}.json"
    if not definition_path.exists():
        definition_path.write_text(
            json.dumps(DEFAULT_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    resolver_definition_path = layout.workflows / f"{DEFAULT_RESOLVER_WORKFLOW}.json"
    if not resolver_definition_path.exists():
        resolver_definition_path.write_text(
            json.dumps(RESOLVER_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # `heal`/`file-issue` (ADR-0018): dormant data, shipped unconditionally
    # exactly like the resolver workflow — the process that actually drives it
    # (`processes/autoheal.json`) is gated behind `--heal-repo` instead (a bare
    # `harness init` has no repo to file issues against).
    heal_definition_path = layout.workflows / f"{DEFAULT_HEAL_WORKFLOW}.json"
    if not heal_definition_path.exists():
        heal_definition_path.write_text(
            json.dumps(HEAL_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    try:
        harness = build(root, args.workflow)
        resolver_workflow = FilesystemWorkflowRepository(layout.workflows).get(
            DEFAULT_RESOLVER_WORKFLOW
        )
        heal_workflow = FilesystemWorkflowRepository(layout.workflows).get(
            DEFAULT_HEAL_WORKFLOW
        )
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    workflow = harness.workflows[args.workflow]
    _write_default_agents(layout, workflow)
    _write_default_agents(layout, resolver_workflow)
    _write_default_agents(layout, heal_workflow)

    print(f"harness ready at {root}")
    print(f"steps: {', '.join(workflow.steps())}")
    return 0


# Default step personas, carried over from harness v1 (repo onpaj/harness,
# agentharness/data/agents/) and adapted to phase 3 conventions: the prompt is
# only the **persona** (role, inputs, what to deliver) — how to read the
# artifacts of previous steps, where to write output, and how to close with a
# verdict block is supplied at runtime by `compose_prompt`, so we don't repeat
# it here. The persona is data (invariant 14): a step → (prompt, tools) map, not
# a branch in code. The model is per queue (invariant): each step gets the tier
# its v1 persona ran on (see `AGENT_MODELS`), written as an alias so it tracks
# the latest of that tier; the operator can still pin an exact id in
# `agents/<step>.json`.
#
#   plan          ← v1 analyst + planner (first step: brief → spec + rough plan)
#   design        ← v1 designer
#   architecture  ← v1 architect
#   development    ← v1 developer (no commit — the worker does that, invariant 9)
#   review        ← v1 reviewer + code-reviewer (PASS/REVISION → done/request_changes)

_PLAN_PERSONA = (
    "You are a senior product manager and technical lead — the first step of "
    "the pipeline. From the task's request you produce a structured "
    "specification and a rough plan that the later steps (design, architecture, "
    "development) build on.\n\n"
    "The output has this structure:\n"
    "- Summary — 2–3 sentences on what this is about.\n"
    "- Context — why it's needed.\n"
    "- Functional requirements — numbered (FR-1, FR-2, …), each with testable "
    "acceptance criteria.\n"
    "- Non-functional requirements — performance, security, where it makes "
    "sense.\n"
    "- Data model — the key entities and how they relate.\n"
    "- Interfaces — endpoints, events, or UI flows at a high level.\n"
    "- Dependencies and scope — what it rests on and what is explicitly out of "
    "scope.\n"
    "- Rough plan — the implementation steps at a high level.\n"
    "- Open questions — what's unclear; where the request is ambiguous, pick a "
    "sensible default and note it here.\n\n"
    "Be specific and complete. Vague requirements lead to bad implementation."
)

_DESIGN_PERSONA = (
    "You are a senior software designer. From the specification and the "
    "architectural assessment of the previous steps you produce a concrete "
    "design.\n\n"
    "First, from the inputs, work out whether the feature has a user "
    "interface. If it has no UI, omit the UX/UI section entirely — don't write "
    "placeholders.\n\n"
    "The design covers:\n"
    "- UX/UI — only when there is a user interface: wireframes (ASCII), the "
    "component hierarchy, the key interactions.\n"
    "- Component design — the boundaries, responsibilities, and interfaces of "
    "the individual components or modules.\n"
    "- Data schemas — DB schemas, request and response shapes, event "
    "payloads.\n\n"
    "Don't define developer tasks — that's the development step's job."
)

_ARCHITECTURE_PERSONA = (
    "You are a senior software architect. From the brief and the specification "
    "you produce an architectural assessment that steers the implementation. "
    "You don't write code — you define the structure the developers will "
    "follow.\n\n"
    "Before you start writing, actively explore the project so the design "
    "rests on reality:\n"
    "1. Documentation first — architecture docs, ADRs, README, descriptions of "
    "patterns.\n"
    "2. When the docs are missing or insufficient, read the code — use "
    "Grep/Glob/Bash to find similar existing implementations and confirm the "
    "design fits the conventions.\n"
    "3. Never guess — when unsure, read the relevant source before proposing "
    "something that may conflict with it.\n\n"
    "The assessment contains:\n"
    "- Alignment with existing patterns and the integration points.\n"
    "- The proposed architecture — an overview of the components and the key "
    "decisions (options considered, the chosen approach, the rationale).\n"
    "- Implementation guidance — where new code belongs, the key interfaces "
    "and contracts, the data flow.\n"
    "- Risks and their mitigations, prerequisites before implementation "
    "begins.\n\n"
    "Have an opinion. Developers need a clear direction, not a list of "
    "options. When unsure, state your assumption and why."
)

_DEVELOPMENT_PERSONA = (
    "You are a senior developer. Following the specification, architecture, and "
    "design from the previous steps, you implement the request. You run "
    "non-interactively in an automated pipeline.\n\n"
    "The working directory is already a checkout of your branch — make all "
    "changes right here:\n"
    "1. DO NOT create a git worktree, and DO NOT create or switch branches. "
    "Code outside this directory will never be seen by the pipeline and "
    "silently disappears.\n"
    "2. DO NOT commit or push yourself, and don't open a PR — the harness "
    "handles committing your work and opening the PR. You just write the "
    "changes into the working directory.\n"
    "3. Write tests for what you implement.\n"
    "4. Never wait for interactive input — where a skill or tool would prompt "
    "you to choose, take the non-interactive path and carry on.\n\n"
    "When you're in a revision round (there's a review of the previous attempt "
    "among the artifacts), read it in full along with your previous "
    "implementation and address every point it raises.\n\n"
    "In your output artifact, summarize what was implemented, which files were "
    "created or changed, and how to verify it."
)

_REVIEW_PERSONA = (
    "You are a senior code reviewer. You check the implementation against the "
    "specification and architecture from the previous steps. Be fair but "
    "rigorous — this is about correctness and conformance to the request, not "
    "stylistic preferences.\n\n"
    "Before anything else, sync the task branch with the repository's base "
    "branch:\n"
    "1. Run `git fetch origin`.\n"
    "2. Determine the base branch: run `git symbolic-ref "
    "refs/remotes/origin/HEAD` and strip the `refs/remotes/origin/` prefix; "
    "if that fails, use `main`.\n"
    "3. Run `git merge origin/<base>`. You are already checked out on the "
    "task branch — DO NOT create or switch branches, and DO NOT force-push "
    "or force-resolve anything.\n"
    "4. If the merge reports conflicts:\n"
    "   - Run `git diff --name-only --diff-filter=U` to capture the "
    "conflicting file paths.\n"
    "   - Run `git merge --abort` to leave the working tree clean.\n"
    "   - Do not attempt to resolve the conflict yourself, and do not judge "
    "code correctness — skip the rest of this review below.\n"
    "   - Write your output artifact and finish with outcome "
    "`request_changes`. The summary and the artifact must both state that "
    "merging `origin/<base>` produced conflicts and must list every "
    "conflicting file path from the previous step.\n"
    "5. If the merge succeeds — fast-forward, a merge commit, or \"Already "
    "up to date\" — continue with the review exactly as below. This sync "
    "step alone must never change your verdict.\n\n"
    "Check:\n"
    "- Conformance to the spec — does the implementation meet the functional "
    "requirements?\n"
    "- Adherence to the architecture — does it follow the proposed patterns "
    "and structure?\n"
    "- Plan conformance — does the implementation follow the agreed plan "
    "(`docs/superpowers/plans/…` or the task's own `plan-*.md` artifact) "
    "without silently skipping or reinterpreting planned steps?\n"
    "- ADR / invariant conformance — read the ADRs in `docs/adr/` relevant to "
    "the files you're reviewing (and the matching entries in CLAUDE.md's "
    "\"Invariants — do not break\" list) and verify none is violated.\n"
    "- Completeness — are the acceptance criteria met and the required tests "
    "written?\n"
    "- Correctness — obvious logic errors, missing error handling, security or "
    "concurrency problems.\n\n"
    "Return the verdict `request_changes` only when:\n"
    "- a functional requirement from the spec is not met,\n"
    "- the implementation conflicts with the architecture,\n"
    "- tests that were explicitly required are missing,\n"
    "- there is a clear correctness bug,\n"
    "- the implementation deviates from the plan without justification, or\n"
    "- the implementation violates an ADR or a documented invariant from "
    "CLAUDE.md.\n"
    "In that case, write in the summary — specifically and actionably — "
    "what's wrong and what to fix, naming the concrete artifact that's out "
    "of alignment (the spec requirement, the plan step, or the ADR number / "
    "invariant) rather than describing the symptom alone; the development "
    "step will go into another round based on it.\n\n"
    "Don't return `request_changes` over stylistic nitpicks, subjective "
    "preferences, out-of-scope improvements, or missing documentation. When "
    "the implementation is sound, return `done` (optionally with non-binding "
    "cleanup suggestions)."
)

_RESOLVE_PERSONA = (
    "You are a senior developer whose only job right now is to resolve a git "
    "merge conflict. The working directory already contains a real conflict "
    "from merging the base branch into this PR's branch — files with "
    "<<<<<<<, =======, >>>>>>> markers. Read each conflicted file, understand "
    "both sides using the surrounding code and tests, and produce a correct "
    "resolution: remove every marker, preserve the combined intent of both "
    "changes, and leave a tree that would pass the project's existing "
    "tests.\n\n"
    "Do not commit, create a branch, or open a worktree — the harness handles "
    "all of that."
)

_HEALER_PERSONA = (
    "You are the harness healer. A task in the orchestration harness has failed "
    "and landed in the `failed/` queue; your job is to read the failure report "
    "you are given and triage it.\n\n"
    "Classify the failure into one of three kinds:\n"
    "- A fixable bug in the HARNESS ITSELF — a driver contract that was "
    "violated, a wiring gap, a missing workflow edge, an unhandled error "
    "path.\n"
    "- An operational or tuning problem — a step that ran out of its "
    "per-agent `timeout`, or hit a resource limit, but the harness itself "
    "behaved correctly.\n"
    "- An external or transient failure — a flaky network, an unauthenticated "
    "tool, or the task's own request being simply wrong or impossible.\n\n"
    "Be conservative: only propose a change when there is a concrete, "
    "plausible one.\n\n"
    "For a harness bug or an operational/tuning problem, draft a proposed "
    "GitHub issue to the file the harness told you to write your output to "
    "above. Its first line must be a title `# <concise title>`; then a short "
    "diagnosis (what failed and why), and a concrete proposed change. For an "
    "operational/tuning problem, recommend diagnostically rather than "
    "prescriptively: name the exceeded budget and the two levers available — "
    "raising the step's per-agent `timeout`, or decomposing the step into "
    "smaller ones — without prescribing a specific number. Then finish with "
    "the outcome that files it.\n\n"
    "For an external or transient failure, write nothing and finish with the "
    "outcome that skips — its summary saying briefly why there is nothing to "
    "file.\n\n"
    "You are working from the failure report alone; you do not have the "
    "task's own worktree. Do not attempt to run or fix code — your "
    "deliverable is the issue draft."
)

_DEDUP_PERSONA = (
    "You decide whether a drafted GitHub issue duplicates one already open. "
    "Read the drafted `issue.md` in `.artifacts/<task>/heal/…`. List the "
    "repo's open issues with `gh issue list --state open --limit 100` and "
    "read the bodies of any that look related. If a currently-open issue "
    "describes the same underlying problem (a strong correlate, not just the "
    "same area), finish with the outcome that treats this as a duplicate and "
    "name the issue number in your summary. Otherwise finish with the "
    "outcome that treats it as new."
)


# Step → (persona, default tools). The tools are names of Claude Code tools,
# which `claude_cli` passes through via `--allowedTools`.
AGENT_PERSONAS: dict[str, tuple[str, list[str]]] = {
    "plan": (_PLAN_PERSONA, ["Read", "Grep", "Glob"]),
    "design": (_DESIGN_PERSONA, ["Read", "Grep", "Glob"]),
    "architecture": (_ARCHITECTURE_PERSONA, ["Read", "Grep", "Glob", "Bash"]),
    "development": (
        _DEVELOPMENT_PERSONA,
        ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"],
    ),
    "review": (_REVIEW_PERSONA, ["Read", "Grep", "Glob", "Bash"]),
    "resolve": (_RESOLVE_PERSONA, ["Read", "Edit", "Bash", "Grep", "Glob"]),
    "heal": (_HEALER_PERSONA, ["Read", "Write"]),
    "dedup": (_DEDUP_PERSONA, ["Read", "Bash"]),
}


# Step → model tier, carried over from the v1 personas (repo onpaj/harness,
# agentharness/data/agents/). v1 pinned exact ids per persona; we keep the same
# tier per step but write it as a CLI alias so it resolves to the latest of that
# tier and doesn't rot to a retired version:
#   plan          ← analyst + planner (opus)
#   design        ← designer (sonnet)
#   architecture  ← architect (opus)
#   development    ← developer (sonnet)
#   review        ← code-reviewer, the full-diff reviewer (sonnet)
#   resolve       ← developer-class conflict fix (sonnet)
# A step with no entry keeps `model = null` (the CLI's configured default).
AGENT_MODELS: dict[str, str] = {
    "plan": "opus",
    "design": "sonnet",
    "architecture": "opus",
    "development": "sonnet",
    "review": "sonnet",
    "resolve": "sonnet",
    "heal": "opus",
    "dedup": "opus",
}


def _agent_model(step: str) -> str | None:
    """Default model tier for the step; an unknown step gets none (`null`)."""
    return AGENT_MODELS.get(step)


def _agent_persona(step: str) -> str:
    """Step persona. Known steps have a persona carried over from v1; an unknown
    step gets a generic instruction (the rest of the boilerplate is supplied by
    `compose_prompt`)."""
    known = AGENT_PERSONAS.get(step)
    if known is not None:
        return known[0]
    return (
        f"You are the agent for the '{step}' step. Read the artifacts of the "
        f"previous steps in your working directory, do the step's work, and "
        f"write the output where the task prompt directs you."
    )


def _agent_tools(step: str) -> list[str]:
    """Default tools for the step; an unknown step gets none."""
    known = AGENT_PERSONAS.get(step)
    return list(known[1]) if known is not None else []


def _agent_definition_template(step: str, allowed_outcomes: list[str]) -> dict:
    """The full, valid AgentSpec-JSON dict for `step`.

    Known steps (AGENT_PERSONAS) get their carried-over persona and tool list;
    any other step name gets the generic fallback. `allowed_outcomes` is the
    caller's responsibility (derived from a workflow via
    `Workflow.outcomes_for`) — this function has no knowledge of workflows.

    The seeded value written here is now only the *workflow-less fallback*:
    once a workflow drives the step, `Workflow.outcomes_for(step)` is the live
    authority (Package B/C of the workflow-defined-outcomes design) and this
    snapshot is advisory only, kept for the workflow-less path and to avoid
    churning every existing `agents/<step>.json` fixture.
    """
    return {
        "prompt": _agent_persona(step),
        "model": _agent_model(step),
        "fallback_model": None,
        "allowed_tools": _agent_tools(step),
        "allowed_outcomes": allowed_outcomes,
        "timeout": None,
    }


def _write_default_agents(layout: HarnessLayout, workflow) -> None:
    layout.agents.mkdir(parents=True, exist_ok=True)
    for step in workflow.steps():
        # The landing step (bound to "open-pr" by app.build()'s own implicit
        # default when no served workflow declares `finishers`) and any step a
        # workflow explicitly binds to a finisher kind (e.g. `heal.json`'s
        # `file-issue` → "open-issue") are driven by the finisher registry, not
        # an agent persona — additive, not a replacement: `land` relies on the
        # implicit default (its own `Workflow` carries no `finishers` entry for
        # it), so the explicit `LANDING_STEP` check must stay alongside the
        # generic one, not instead of it.
        if step == LANDING_STEP or workflow.finisher_for(step) is not None:
            continue
        path = layout.agents / f"{step}.json"
        if path.exists():
            continue
        # `allowed_outcomes` written here is only the workflow-less fallback
        # (invariant #42); a step with a custom outcome vocabulary (e.g.
        # `heal`'s file/skip, `dedup`'s unique/duplicate) would otherwise
        # write a persona file `fs_agents._parse_agent_spec` can't load, since
        # it restricts the field to {done, request_changes}. Clamp to that
        # loadable subset here, falling back to `[DONE]` when the workflow's
        # own outcomes don't intersect it at all.
        fallback = [o for o in workflow.outcomes_for(step) if o in (DONE, REQUEST_CHANGES)] or [DONE]
        definition = _agent_definition_template(step, fallback)
        path.write_text(
            json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _write_default_repos(layout: HarnessLayout) -> None:
    if not layout.repos.exists():
        layout.repos.write_text(
            json.dumps({}, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _submit(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"error: {root} is not initialized, run `harness init`", file=sys.stderr)
        return 2

    try:
        data = json.loads(args.data) if args.data else {}
    except json.JSONDecodeError as error:
        print(f"error: --data is not valid JSON: {error}", file=sys.stderr)
        return 2

    workflow_name = args.workflow
    step = args.step
    if workflow_name is None and step is None:
        workflow_name = DEFAULT_WORKFLOW
    if step is not None and invalid_step_name(step):
        print(f"error: invalid step name: {step!r}", file=sys.stderr)
        return 2

    task = Task(
        id=new_task_id(),
        workflow_template=workflow_name,
        step=step,
        created=SystemClock().now(),
        repository=args.repo,
        worktree=args.worktree,
        data=data,
    )
    (layout.tasks / f"{task.id}.json").write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(task.id)
    return 0


def _agent_init(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"error: {root} is not initialized, run `harness init`", file=sys.stderr)
        return 2

    workflows = FilesystemWorkflowRepository(layout.workflows)
    try:
        workflow = workflows.get(args.workflow)
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.step == LANDING_STEP:
        print(
            f"error: {args.step!r} is the landing step, driven by the built-in "
            "landing behavior, not an agent",
            file=sys.stderr,
        )
        return 2
    if args.step not in workflow.steps():
        print(
            f"error: step {args.step!r} is not part of workflow {args.workflow!r}",
            file=sys.stderr,
        )
        return 2

    layout.agents.mkdir(parents=True, exist_ok=True)
    path = layout.agents / f"{args.step}.json"
    text = path.read_text(encoding="utf-8") if path.exists() else None

    if text is not None and not args.force:
        print(f"{path} already exists, not overwritten (use --force to replace it)")
        print(text)
        return 0

    # Same clamp as `_write_default_agents`: `allowed_outcomes` written here
    # is only the workflow-less fallback (invariant #42), and `fs_agents`
    # restricts it to {done, request_changes} — an unclamped custom-outcome
    # step (e.g. `heal`'s file/skip, `dedup`'s unique/duplicate) would write
    # a persona file that fails to load, and `app.build()` loads agents
    # eagerly, so the next `harness run` would crash at startup.
    fallback = [o for o in workflow.outcomes_for(args.step) if o in (DONE, REQUEST_CHANGES)] or [DONE]
    definition = _agent_definition_template(args.step, fallback)
    text = json.dumps(definition, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    print(str(path))
    print(text)
    return 0


def _github_sources(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    slug_of=github_slug,
    client: GithubClient | None = None,
) -> list[TaskSource]:
    """One `GithubTaskSource` per repo in `repos.json` that has a GitHub origin.

    The slug is derived from each clone's git origin (`slug_of`); a repo with no
    GitHub origin is skipped with a warning. Without `GITHUB_TOKEN` (and no
    injected client) there are no sources and the harness runs on `harness
    submit` alone."""
    if client is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return []
        client = HttpGithubClient(token)

    worktree_root = args.worktree_root or str(root / "worktrees")
    workflow = args.github_workflow
    step = args.github_step
    if workflow is None and step is None:
        workflow = DEFAULT_WORKFLOW
    sources: list[TaskSource] = []
    for name in registry.names():
        slug = slug_of(registry.resolve(name))
        if slug is None:
            print(f"warning: {name} has no GitHub origin, not scanned", file=sys.stderr)
            continue
        sources.append(
            GithubTaskSource(
                client=client,
                clock=SystemClock(),
                repo=slug,
                workflow=workflow,
                step=step,
                repository=name,
                worktree_root=worktree_root,
                select_label=args.github_label,
                step_labels=DEFAULT_STEP_LABELS,
            )
        )
    return sources


def _github_reflectors(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    slug_of=github_slug,
    client: GithubClient | None = None,
) -> list[TaskSource]:
    """One `GithubLabelReflector` per repo in `repos.json` that has a GitHub
    origin — the outbound half of GitHub reflection, registered whenever
    classic ingestion (`GithubTaskSource`) is *not* also registered for that
    repo (`_run` gates both on `--no-github-source`), so exactly one
    reflecting source per repo ever exists — never doubled label calls.
    Mirrors `_github_sources`'s enumeration exactly: no token (and no injected
    client) → no sources, a repo with no GitHub origin is skipped."""
    if client is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return []
        client = HttpGithubClient(token)

    sources: list[TaskSource] = []
    for name in registry.names():
        slug = slug_of(registry.resolve(name))
        if slug is None:
            continue  # already warned about by _github_sources for the same repo
        sources.append(
            GithubLabelReflector(
                client=client,
                repo=slug,
                step_labels=DEFAULT_STEP_LABELS,
            )
        )
    return sources


def _scheduled_sources(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    clock: Clock,
    known_targets: set[str] | None,
) -> list[TaskSource]:
    """Scheduled triggers declared under `<root>/triggers/*.json`.

    Each becomes a `ScheduledTrigger` — a `TaskSource` that produces tasks on a
    clock gate and reflects nothing outward (a `Trigger`) — appended to the run's
    existing `sources` list; `build()` gains no parameter. A missing/empty
    `triggers/` directory yields `[]`, so the harness runs exactly as before.
    `known_targets` (served workflow names ∪ known step names) lets the
    repository reject a trigger that names an unknown target up front."""
    from harness.drivers.fs_triggers import FilesystemTriggerRepository

    repo = FilesystemTriggerRepository(root / "triggers")
    worktree_root = args.worktree_root or str(root / "worktrees")
    return repo.build(
        clock=clock,
        repository=None,
        worktree_root=worktree_root,
        known_targets=known_targets,
    )


def _process_check_factories(
    args: argparse.Namespace,
    registry: RepositoryRegistry,
    *,
    client: GithubClient | None = None,
    jira_client: JiraClient | None = None,
) -> dict[str, CheckFactory]:
    """Check kinds `processes/*.json` may name that need a dependency
    `BUILTIN_CHECKS` can't carry — `github-issues`/`github-conflicts`, each
    closed over a `GithubClient` + the repo registry, and `jira-issues`,
    closed over a `JiraClient` + the repo registry. The clients come from the
    caller (tests) or the environment (`GITHUB_TOKEN`; `JIRA_BASE_URL`/
    `JIRA_EMAIL`/`JIRA_API_TOKEN`).

    Returns just the factory dict — process *compilation* itself now happens
    inside `app.build()` (ADR-0018), which merges this dict over
    `BUILTIN_CHECKS` alongside its own internal `"failed-tasks"` factory (a
    dependency this function has no reason to carry: that check needs the
    harness's own live `failed`/`healed` queues, not an external client).
    """
    from harness.drivers.fs_processes import ProcessValidationError
    from harness.drivers.github_conflicts_check import SPEC as GITHUB_CONFLICTS_SPEC
    from harness.drivers.github_conflicts_check import GithubConflictsCheck
    from harness.drivers.github_issues_check import SPEC as GITHUB_ISSUES_SPEC
    from harness.drivers.github_issues_check import GithubIssuesCheck
    from harness.drivers.jira_issues_check import JiraIssuesCheck
    from harness.ports.triggers import CheckDefinition

    if client is None:
        token = os.environ.get("GITHUB_TOKEN")
        client = HttpGithubClient(token) if token else None

    if jira_client is None:
        base_url = os.environ.get("JIRA_BASE_URL")
        email = os.environ.get("JIRA_EMAIL")
        api_token = os.environ.get("JIRA_API_TOKEN")
        jira_client = (
            HttpJiraClient(base_url, email, api_token)
            if base_url and email and api_token
            else None
        )

    def github_issues_factory(params: dict) -> GithubIssuesCheck:
        if client is None:
            raise ProcessValidationError(
                "github-issues action requires GITHUB_TOKEN", field="check"
            )
        label = params.get("label", args.github_label)
        claimed_label = params.get("claimed_label", "harness:queued")
        if not isinstance(label, str) or not isinstance(claimed_label, str):
            raise ProcessValidationError(
                "github-issues action requires label/claimed_label to be strings",
                field="params",
            )
        return GithubIssuesCheck(
            client=client,
            registry=registry,
            label=label,
            claimed_label=claimed_label,
        )

    def github_conflicts_factory(params: dict) -> GithubConflictsCheck:
        if client is None:
            raise ProcessValidationError(
                "github-conflicts action requires GITHUB_TOKEN", field="check"
            )
        return GithubConflictsCheck(
            client=client,
            registry=registry,
            head_prefix=params.get("head_prefix", "harness/"),
        )

    def jira_issues_factory(params: dict) -> JiraIssuesCheck:
        if jira_client is None:
            raise ProcessValidationError(
                "jira-issues action requires JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN",
                field="check",
            )
        repository = params.get("repository")
        if not isinstance(repository, str) or not repository:
            raise ProcessValidationError(
                "jira-issues action requires params.repository", field="params"
            )
        if repository not in registry.names():
            raise ProcessValidationError(
                f"jira-issues action names an unknown repository {repository!r}",
                field="params",
            )
        label = params.get("label", "harness-todo")
        claimed_label = params.get("claimed_label", "harness-queued")
        jql = params.get("jql")
        project = params.get("project")
        if not isinstance(label, str) or not isinstance(claimed_label, str):
            raise ProcessValidationError(
                "jira-issues action requires label/claimed_label to be strings",
                field="params",
            )
        if jql is None and project is None:
            raise ProcessValidationError(
                "jira-issues action requires params.jql or params.project",
                field="params",
            )
        if jql is not None and not isinstance(jql, str):
            raise ProcessValidationError(
                "jira-issues action requires params.jql to be a string", field="params"
            )
        if project is not None and not isinstance(project, str):
            raise ProcessValidationError(
                "jira-issues action requires params.project to be a string",
                field="params",
            )
        return JiraIssuesCheck(
            client=jira_client,
            repository=repository,
            label=label,
            claimed_label=claimed_label,
            jql=jql,
            project=project,
        )

    # Bundle each factory with its declarative spec so the process form renders
    # these actions' parameters from data, exactly like the built-ins. No
    # `CheckSpec` exists yet for `jira-issues`, so it stays a bare factory —
    # `check_spec_of`'s generic name-only fallback covers it in the form.
    return {
        "github-issues": CheckDefinition(
            spec=GITHUB_ISSUES_SPEC, factory=github_issues_factory
        ),
        "github-conflicts": CheckDefinition(
            spec=GITHUB_CONFLICTS_SPEC, factory=github_conflicts_factory
        ),
        "jira-issues": jira_issues_factory,
    }


def _declared_sink_kinds(processes_root: Path) -> set[str]:
    """Which sink kinds `processes/*.json` declares, read straight off the raw
    JSON — no `Check`/`compile_process` involved. This pre-scan only decides
    whether a `SlackWebhookSink` should exist; a malformed process file's real
    failure surfaces later, loudly, when `app.build()` actually compiles it."""
    kinds: set[str] = set()
    if not processes_root.is_dir():
        return kinds
    for path in processes_root.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, dict):
            continue
        kind = (raw.get("sink") or {}).get("kind")
        if isinstance(kind, str):
            kinds.add(kind)
    return kinds


def _slack_sinks(declared_kinds: set[str]) -> list[TaskSource]:
    """One `SlackWebhookSink` when `SLACK_WEBHOOK_URL` is set — the outbound
    destination for any process-born task stamped `data.sink == {"kind":
    "slack"}`. The webhook URL is a secret and comes only from the environment
    (the service holds no secret — it never enters a JSON file). A process
    declaring a slack sink with the variable missing gets a warning and the
    sink is simply inert — never fatal: the harness keeps running, the
    reflection is skipped."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook_url:
        return [SlackWebhookSink(webhook_url=webhook_url)]
    if "slack" in declared_kinds:
        print(
            "warning: a process declares a slack sink but SLACK_WEBHOOK_URL "
            "is not set, slack reflection is disabled",
            file=sys.stderr,
        )
    return []


AUTOHEAL_PROCESS_DEFINITION = {
    "trigger": {"interval": "30s"},
    "action": {"check": "failed-tasks", "params": {}},
    "target": {"workflow": "heal"},
    "dedup": "per-state",
    "sink": {"kind": "none"},
}
"""Target is `{"workflow": "heal"}`, not `{"step": "heal"}`: a workflow-less
(bare `step`) task finishes after a single hop through `route()` (see
`router.py`) — `file-issue` and its `open-issue` finisher would never run,
silently. `heal`/`file-issue` is a genuine two-step workflow, so it needs a
real `Workflow` in scope on the *second* `route()` call, which only happens
when `task.workflow_template` is set (ADR-0018)."""


def _ensure_autoheal_process(layout: HarnessLayout, heal_repo: str) -> None:
    """`--heal-repo`'s thin-generator half: write `processes/autoheal.json`
    unless one already exists — never clobbering an operator's hand-edited
    file. Written directly (like `_init`'s `HEAL_DEFINITION`/
    `RESOLVER_DEFINITION`), **not** through `FilesystemProcessAdmin.write`:
    validating `"failed-tasks"` needs the merged registry `app.build()`
    assembles (`harness.process_checks` — which `serve()` *does* hand the
    dashboard's admin), and at this point in `_run` no harness exists yet.
    The real validation this file needs to pass happens when `build()`
    compiles it moments later, not at write time.
    """
    path = layout.processes / "autoheal.json"
    if path.exists():
        return
    layout.processes.mkdir(parents=True, exist_ok=True)
    definition = {
        **AUTOHEAL_PROCESS_DEFINITION,
        "action": {"check": "failed-tasks", "params": {"repository": heal_repo}},
    }
    path.write_text(
        json.dumps(definition, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def service_path_entries(harness: Path) -> list[str]:
    """`PATH` for the service: the venv's bin first, then the usual locations.

    launchd starts a process with a minimal `PATH`, so `git`, `gh` and `claude`
    would all be missing. `~/.npm-global/bin` and `~/.local/bin` are here
    because that is where a user-installed `claude` and `python3.11` land.
    """
    home = Path.home()
    return [
        str(harness.parent),
        str(home / ".npm-global" / "bin"),
        str(home / ".local" / "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]


def installed_commit() -> str | None:
    """The git commit a `uv tool install git+...` came from, or None.

    `pyproject.toml` carries a single static version, so two different installs
    both report `0.1.0` and `--version` alone cannot tell you whether an update
    landed. pip/uv record the source in `direct_url.json` (PEP 610); the commit
    from there is the only honest answer.
    """
    try:
        raw = metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        commit = json.loads(raw).get("vcs_info", {}).get("commit_id")
    except json.JSONDecodeError:
        return None
    return commit[:7] if isinstance(commit, str) and commit else None


def version_string() -> str:
    """The installed version, with the source commit when there is one."""
    try:
        version = metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:  # running from source without an install
        return "unknown (not installed)"
    commit = installed_commit()
    return f"{version} (git {commit})" if commit else version


def build_timestamp() -> str | None:
    """An approximation of "when this install was placed", not a true build
    time — the project has no build-stamp pipeline (ships via
    `uv tool install git+...`, see CLAUDE.md). Derived from the installed
    distribution's on-disk mtime; `None` when that can't be determined (no
    install, or a `Distribution` backend this heuristic didn't anticipate).
    Never raises — degrades to `None` on any failure, the caller shows
    "unknown" instead.
    """
    try:
        location = metadata.distribution(PACKAGE_NAME).locate_file("")
        mtime = Path(location).stat().st_mtime
    except (metadata.PackageNotFoundError, OSError, AttributeError, TypeError):
        return None
    return (
        datetime.fromtimestamp(mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def uv_shim() -> Path:
    """Where `uv tool install` puts the stable `harness` shim."""
    return Path.home() / ".local" / "bin" / "harness"


def service_entry_point() -> Path:
    """Absolute path to the `harness` the service should exec.

    Prefers uv's shim: `uv tool upgrade` rebuilds the tool environment, but the
    shim path is the contract uv keeps stable, so an upgrade never invalidates
    an installed LaunchAgent. Falls back to this environment's own script for a
    from-source venv.

    `sys.prefix` is the venv root. `sys.executable` is not usable here because
    resolving it follows the venv's python symlink out to the base interpreter
    (with uv-managed CPython that lands in `~/.local/share/uv/python/...`, where
    no `harness` script exists). `sys.argv[0]` is no good either — it is
    whatever the caller typed, or `pytest`.
    """
    shim = uv_shim()
    if shim.exists():
        return shim
    return Path(sys.prefix) / "bin" / "harness"


def uv_executable() -> Path | None:
    """The `uv` binary, or None when it is not installed.

    Checked explicitly rather than relying on `PATH`: `harness update` may be
    invoked from the service context, whose `PATH` we build ourselves.
    """
    found = shutil.which("uv")
    if found:
        return Path(found)
    candidate = Path.home() / ".local" / "bin" / "uv"
    return candidate if candidate.exists() else None


def installed_version_report() -> str:
    """Ask the installed `harness` script what it is now.

    Called right after an upgrade, from the process the upgrade replaced — so
    it must shell out rather than read its own already-stale metadata.
    """
    entry = service_entry_point()
    if not entry.is_file():
        return "harness (installed; run `harness --version` to confirm)"
    result = subprocess.run(
        [str(entry), "--version"], capture_output=True, text=True, check=False
    )
    reported = result.stdout.strip()
    if result.returncode != 0 or not reported:
        return "harness (installed; run `harness --version` to confirm)"
    return reported


def _update(args: argparse.Namespace) -> int:
    """Upgrade the installed harness in place via `uv tool upgrade`.

    With `--restart-service LABEL` (the scheduled-autoupdate path), also
    kickstarts that LaunchAgent, but only when the version actually changed —
    both the "before" and "after" snapshots go through
    `installed_version_report()` so they are byte-comparable; comparing it
    against `version_string()` (a different string shape) would report
    "changed" on every run and restart the service even on a no-op upgrade.
    """
    uv = uv_executable()
    if uv is None:
        print(
            "error: uv is not installed — install it with\n"
            "  curl -LsSf https://astral.sh/uv/install.sh | sh",
            file=sys.stderr,
        )
        return 2

    restart_service = getattr(args, "restart_service", None)
    before = installed_version_report() if restart_service else None

    result = subprocess.run(
        [str(uv), "tool", "upgrade", PACKAGE_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print(f"error: uv tool upgrade failed (exit {result.returncode})", file=sys.stderr)
        return 1

    # This process is still the *old* code, so version_string() here would
    # report the version we just replaced. Ask the freshly installed script.
    after = installed_version_report()
    print(f"\nnow: {after}")

    # PR #49's autoupdate wrapper drives this path: `harness update
    # --restart-service <label>`. Restart only when the version actually
    # changed, so a no-op poll doesn't kill a healthy service.
    if restart_service:
        if before != after:
            try:
                kickstart(os.getuid(), restart_service)
            except ServiceError as error:
                print(
                    f"error: update succeeded but restart failed: {error}",
                    file=sys.stderr,
                )
                return 1
            print(f"restarted service {restart_service} (version changed)")
        else:
            print(f"service {restart_service} left running (no version change)")
        return 0

    # main's autoupdate schedule drives this path: `harness update --restart
    # [--only-if-idle] [--label L]`. Idle-gated so a firing mid-stage defers.
    if not getattr(args, "restart", False):
        print(
            "the running service still has the previous version — restart it with\n"
            f"  launchctl kickstart -k gui/$(id -u)/{getattr(args, 'label', DEFAULT_LABEL)}"
        )
        return 0

    label = getattr(args, "label", DEFAULT_LABEL)
    if getattr(args, "only_if_idle", False):
        active = active_stages(_root(getattr(args, "root", None)))
        if active:
            print(
                f"a stage is running ({', '.join(active)}); skipping the restart. "
                "The update is on disk and will apply at the next idle restart."
            )
            return 0

    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    try:
        kickstart(os.getuid(), label)
    except ServiceError as error:
        print(f"error: restart failed: {error}", file=sys.stderr)
        return 1
    print(f"restarted service {label}")
    return 0


def active_stages(root: Path) -> list[str]:
    """Task ids currently claimed in a step queue — i.e. a stage is executing.

    `claim()` is an atomic rename into `<queue>/.processing/`, so a `.json` there
    means an agent is mid-run. This is the "no active work" signal the idle-gated
    restart checks: restarting with one of these live would kill the agent
    subprocess and waste the attempt.
    """
    queues = HarnessLayout(root).queues
    if not queues.is_dir():
        return []
    return sorted(
        path.stem
        for path in queues.glob("*/.processing/*.json")
    )


def _require_macos() -> str | None:
    """The error message for a non-macOS host, or None when launchd is available."""
    if sys.platform != "darwin":
        return (
            f"`harness service` needs macOS launchd; this is {sys.platform}. "
            "Run `harness run` under your own supervisor (systemd, supervisord)."
        )
    return None


def _service_install(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"error: {root} is not initialized, run `harness init`", file=sys.stderr)
        return 2

    harness = service_entry_point()
    if not harness.is_file():
        print(
            f"error: cannot locate the harness entry point at {harness} — "
            "install the package into this environment first",
            file=sys.stderr,
        )
        return 2

    home = Path.home()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # The secrets file the wrapper sources. Create it 0600 with a template if it
    # is absent — never overwrite it, since that is where the operator's tokens
    # live. `claude` under launchd cannot read the login keychain, so the claude
    # token has to travel through the environment from here.
    env_file = root / "secrets.env"
    env_file_created = not env_file.exists()
    if env_file_created:
        env_file.write_text(_SECRETS_TEMPLATE, encoding="utf-8")
    env_file.chmod(0o600)

    wrapper = root / "harness-run.sh"
    wrapper.write_text(
        wrapper_script(
            harness=harness,
            root=root,
            api_port=args.api_port,
            path_entries=service_path_entries(harness),
            env_file=env_file,
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    target = plist_path(home, args.label)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        plist_bytes(
            label=args.label,
            wrapper=wrapper,
            working_dir=root,
            log_dir=log_dir,
            home=home,
        )
    )

    try:
        load(os.getuid(), target, args.label)
    except ServiceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"service {args.label} installed and started")
    print(f"  wrapper: {wrapper}")
    print(f"  plist:   {target}")
    print(f"  secrets: {env_file}")
    print(f"  logs:    {log_dir}/harness.log, {log_dir}/harness.error.log")
    print(f"  board:   http://127.0.0.1:{args.api_port}/")

    # An *active* assignment, not the commented example in the template.
    token_set = any(
        line.lstrip().startswith("CLAUDE_CODE_OAUTH_TOKEN=")
        for line in env_file.read_text(encoding="utf-8").splitlines()
    )
    if not token_set:
        print()
        print("NEXT: claude cannot use the macOS keychain under launchd. Give the")
        print("service a token so agent steps work:")
        print("  1. claude setup-token")
        print(f"  2. add CLAUDE_CODE_OAUTH_TOKEN=<token> to {env_file}")
        print(f"  3. launchctl kickstart -k gui/{os.getuid()}/{args.label}")
    return 0


def _service_uninstall(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    was_loaded = unload(os.getuid(), args.label)
    target = plist_path(Path.home(), args.label)
    existed = target.exists()
    target.unlink(missing_ok=True)

    if not was_loaded and not existed:
        print(f"service {args.label} was not installed")
        return 0
    print(f"service {args.label} removed")
    return 0


def _print_service_report(label: str, target: Path, report: str | None) -> int:
    """The shared "label / plist / launchctl state" block for `status` output.

    Shared by `_service_status` and `_service_autoupdate_status`, which only
    differ in an extra `interval:` line the caller prints around this call.
    """
    print(f"label:  {label}")
    print(f"plist:  {target} ({'present' if target.exists() else 'missing'})")
    if report is None:
        print("state:  not loaded")
        return 1
    for line in report.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "pid =", "last exit code =")):
            print(f"        {stripped}")
    print("state:  loaded")
    return 0


def _service_status(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    target = plist_path(Path.home(), args.label)
    report = status(os.getuid(), args.label)
    return _print_service_report(args.label, target, report)


def _resolve_served_workflows(
    args: argparse.Namespace, layout: HarnessLayout
) -> tuple[str, ...] | None:
    """The set of workflow names `harness run` should serve, or None on error
    (an error message has already been printed to stderr)."""
    if args.workflows and args.all_workflows:
        print(
            "error: --workflow and --all-workflows are mutually exclusive",
            file=sys.stderr,
        )
        return None
    if args.all_workflows:
        names = FilesystemWorkflowRepository(layout.workflows).names()
        if not names:
            print(
                f"error: no workflow definitions found under {layout.workflows}",
                file=sys.stderr,
            )
            return None
        return names
    if args.workflows:
        return tuple(args.workflows)
    # Neither --workflow nor --all-workflows: probe for the primary workflow.
    # Present (a normal `harness init`) → serve `development`.
    # Absent → workflow-less (FR-6): serve no workflow and run the catalog
    # agents directly, rather than failing on a missing `development.json`.
    if (layout.workflows / f"{DEFAULT_WORKFLOW}.json").is_file():
        return (DEFAULT_WORKFLOW,)
    return ()


def _parse_hours(raw: str) -> list[int]:
    """Parse "2,8,14,20" into sorted unique hours, rejecting anything out of 0-23."""
    hours = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if not piece.isdigit() or not (0 <= int(piece) <= 23):
            raise ValueError(f"invalid hour {piece!r} (expected 0-23)")
        hours.append(int(piece))
    if not hours:
        raise ValueError("no hours given")
    return sorted(set(hours))


# --- harness service autoupdate --------------------------------------------


def _service_autoupdate_install(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    try:
        interval_seconds = parse_interval_minutes(args.every)
    except ServiceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    root = _root(args.root)
    layout = HarnessLayout(root)
    if not layout.tasks.is_dir():
        print(f"error: {root} is not initialized, run `harness init`", file=sys.stderr)
        return 2

    harness = service_entry_point()
    if not harness.is_file():
        print(
            f"error: cannot locate the harness entry point at {harness} — "
            "install the package into this environment first",
            file=sys.stderr,
        )
        return 2

    home = Path.home()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    wrapper = root / "harness-autoupdate.sh"
    wrapper.write_text(
        autoupdate_wrapper_script(
            harness=harness,
            service_label=args.service_label,
            path_entries=service_path_entries(harness),
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    target = plist_path(home, args.label)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        periodic_plist_bytes(
            label=args.label,
            wrapper=wrapper,
            working_dir=root,
            log_dir=log_dir,
            home=home,
            start_interval_seconds=interval_seconds,
        )
    )

    try:
        load(os.getuid(), target, args.label)
    except ServiceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"service {args.label} installed and started")
    print(f"  wrapper:  {wrapper}")
    print(f"  plist:    {target}")
    print(
        f"  logs:     {log_dir}/harness-autoupdate.log, "
        f"{log_dir}/harness-autoupdate.error.log"
    )
    print(f"  interval: {format_interval(interval_seconds)}")
    print(
        "  note: install also runs it once immediately "
        "(RunAtLoad + the initial kickstart)"
    )
    return 0


def _service_autoupdate_uninstall(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    was_loaded = unload(os.getuid(), args.label)
    target = plist_path(Path.home(), args.label)
    existed = target.exists()
    target.unlink(missing_ok=True)

    if not was_loaded and not existed:
        print(f"service {args.label} was not installed")
        return 0
    print(f"service {args.label} removed")
    return 0


def _service_autoupdate_status(args: argparse.Namespace) -> int:
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    target = plist_path(Path.home(), args.label)
    report = status(os.getuid(), args.label)

    interval = None
    if target.exists():
        try:
            with target.open("rb") as handle:
                definition = plistlib.load(handle)
            interval = definition.get("StartInterval")
        except (plistlib.InvalidFileException, OSError):
            interval = None

    code = _print_service_report(args.label, target, report)
    print(f"interval: {format_interval(interval) if interval else 'unknown'}")
    return code


def _service_autoupdate_schedule(args: argparse.Namespace) -> int:
    """Calendar-based autoupdate (main's design): schedule `harness update
    --restart --only-if-idle` at a handful of fixed hours. A sibling to the
    interval-based `install`/`uninstall`/`status` trio, kept so the shipped
    calendar scheduler stays reachable from the CLI."""
    problem = _require_macos()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    home = Path.home()
    autoupdate_label = f"{args.label}.autoupdate"
    target = plist_path(home, autoupdate_label)

    if args.remove:
        was_loaded = unload(os.getuid(), autoupdate_label)
        existed = target.exists()
        target.unlink(missing_ok=True)
        print(
            f"autoupdate {autoupdate_label} removed"
            if (was_loaded or existed)
            else f"autoupdate {autoupdate_label} was not installed"
        )
        return 0

    try:
        hours = _parse_hours(args.hours)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    harness = service_entry_point()
    root = _root(args.root)
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        autoupdate_plist_bytes(
            label=autoupdate_label,
            harness=harness,
            service_label=args.label,
            hours=hours,
            path_entries=service_path_entries(harness),
            log_dir=log_dir,
            home=home,
        )
    )
    try:
        load(os.getuid(), target, autoupdate_label)
    except ServiceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    pretty = ", ".join(f"{h:02d}:00" for h in hours)
    print(f"autoupdate {autoupdate_label} installed — runs at {pretty}")
    print(f"  it runs: harness update --restart --only-if-idle --label {args.label}")
    print(f"  log:     {log_dir}/autoupdate.log")
    return 0


def _build_forge(kind: str, root: Path, registry: RepositoryRegistry | None = None):
    """The forge for a real run. `fake` writes into `<root>/forge/prs.json`.

    `github` without a `GITHUB_TOKEN` yields a forge that fails at `land` rather
    than one that refuses to start: the harness stays usable for `harness
    submit`, and the operator sees exactly which task needs the token.
    """
    if kind == "fake":
        return FakeForge(root / "forge")
    token = os.environ.get("GITHUB_TOKEN")
    return GithubForge(
        HttpGithubClient(token) if token else None, registry=registry
    )


def _build_merge_checker(args: argparse.Namespace) -> MergeChecker | None:
    """A live `MergeChecker`, gated on `GITHUB_TOKEN` — same condition as
    `GithubForge`, independent of `--forge`. Reconciliation only ever exists
    for tasks a real forge landed: `--forge fake` synthesizes non-GitHub
    `repo` placeholders (`local/<branch>`) that a real merge check can't
    resolve, so a fake-forge run must never get a live checker.
    """
    token = os.environ.get("GITHUB_TOKEN")
    return GithubMergeChecker(HttpGithubClient(token)) if token else None


def _build_issue_checker(args: argparse.Namespace) -> IssueChecker | None:
    """A live `IssueChecker`, gated on `GITHUB_TOKEN` — same condition as the
    merge checker. It reads the repo/issue off each task's own `data.source`, so
    one checker serves every GitHub-sourced task; a submitted task (no source)
    is left untouched. Without a token there is no checker and the issue
    reconciler loop simply never runs.
    """
    token = os.environ.get("GITHUB_TOKEN")
    return GithubIssueChecker(HttpGithubClient(token)) if token else None


def _run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    served_names = _resolve_served_workflows(args, layout)
    if served_names is None:
        return 2

    # `--github-workflow` defaults to `None` (not `DEFAULT_WORKFLOW`) so this
    # check only fires when the operator actually named a workflow for GitHub
    # ingestion. Validating the *default* against the served set would reject
    # e.g. `run --workflow hotfix` with no GitHub flags at all -- a regression
    # against FR-6, since no GithubTaskSource is ever built in that case.
    # `--github-step` (workflow-less GitHub ingestion) skips the check: it names
    # a step, not a workflow, and `_github_sources` applies its own defaulting.
    if args.github_workflow is not None and args.github_workflow not in served_names:
        print(
            f"error: --github-workflow {args.github_workflow!r} is not served "
            f"by this harness (served: {', '.join(served_names) or '(none)'})",
            file=sys.stderr,
        )
        return 2

    # The real run: agent behind `claude -p`, git worktree under a shared root,
    # repo name→path from `repos.json`, personas from `agents/`, artifacts
    # versioned in the worktree, and a real GitHub forge (`--forge fake` swaps
    # in prs.json for offline runs and tests).
    registry = FilesystemRepositoryRegistry(layout.repos)
    # `--agent dummy` leaves catalog/runner unset, which makes `build()` fall
    # back to DummyBehavior for the step queues while everything around it stays
    # real: real worktree, real commits, real push, real PR. That exercises the
    # whole pipeline on a machine where `claude` is unavailable or unauthenticated.
    use_agent = args.agent == "claude"
    catalog = FilesystemAgentCatalog(layout.agents) if use_agent else None
    runner = ClaudeCliRunner() if use_agent else None
    workspace = GitWorkspace(registry, layout.worktrees)
    artifact_view = WorktreeArtifactView(layout.worktrees)
    forge = _build_forge(args.forge, root, registry)
    github = [] if args.no_github_source else _github_sources(args, root, registry)
    # The outbound reflector is registered only when classic ingestion is off
    # (`--no-github-source`) — never alongside `GithubTaskSource` for the same
    # repo, which already reflects via its own composed reflector.
    reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
    sources = github + reflectors
    merge_checker = _build_merge_checker(args)
    issue_checker = _build_issue_checker(args)
    # The resolver workflow rides alongside the primary one so its tasks — queued
    # by a `github-conflicts` process — get their own step queues and board
    # columns. Served whenever its definition exists: a process-only detection
    # path still needs a served target (a process targeting an unserved
    # workflow fails to compile).
    resolver_defined = (layout.workflows / f"{args.resolver_workflow}.json").is_file()
    if resolver_defined and args.resolver_workflow not in served_names:
        served_names = [*served_names, args.resolver_workflow]

    # Self-healing (ADR-0018): enabled by a heal repo — `--heal-repo
    # <owner/repo>` OR the `HARNESS_HEAL_REPO` env var, the flag-free path that
    # mirrors how `SLACK_WEBHOOK_URL` gates the slack sink (config in the
    # service's env, never a run flag). Either way the `open-issue` finisher
    # files heal issues on that repo. This must run *before* `known_targets` is
    # computed below — the autoheal process's `{"workflow": "heal"}` target (and
    # any bare trigger naming it) needs "heal" in the served set to validate. It
    # reuses the claude agent, so it needs `--agent claude`; offline (no
    # GITHUB_TOKEN) it falls back to the in-memory tracker so the loop still
    # runs harmlessly.
    finishers: dict[
        str, Callable[[str, dict, Callable[[], ConsumerBehavior]], ConsumerBehavior]
    ] = {}
    heal_repo = args.heal_repo or os.environ.get("HARNESS_HEAL_REPO")
    if heal_repo:
        if args.heal_repo and not use_agent:
            print(
                "error: --heal-repo needs --agent claude (the healer is a claude agent)",
                file=sys.stderr,
            )
            return 2
        if not use_agent:
            print(
                "warning: HARNESS_HEAL_REPO is set but --agent is not claude; the"
                " heal step cannot run and failed tasks will settle unhealed.",
                file=sys.stderr,
            )
        # The `heal_repo` slug is also `task.repository` on every heal task
        # (invariant #25) — `GitWorkspace.attach` resolves it through this same
        # `registry`, and an unregistered slug raises `RepositoryNotFound` at
        # attach time. That failure lands the heal task in `failed/`, which the
        # recursion guard (invariant #25/#26) then retires straight to
        # `healed/` with no issue filed — self-healing goes silently inert. Warn
        # up front rather than let the operator discover it that way; this must
        # never block startup (a WARNING, not an error).
        try:
            registry.resolve(heal_repo)
        except RepositoryNotFound:
            print(
                f"warning: heal repo {heal_repo!r} is not registered in"
                f" {layout.repos} — heal tasks will fail to attach a worktree"
                " until it is added there, so self-healing will file nothing.",
                file=sys.stderr,
            )
        if DEFAULT_HEAL_WORKFLOW not in served_names:
            served_names = [*served_names, DEFAULT_HEAL_WORKFLOW]
        token = os.environ.get("GITHUB_TOKEN")
        issue_tracker = (
            GithubIssueTracker(HttpGithubClient(token))
            if token
            else MemoryIssueTracker()
        )
        # `open-issue` replaces the file-issue step's behavior (like `open-pr`):
        # it ignores step/config/inner and files the drafted heal issue. A
        # factory, per the finisher registry contract (invariant #41).
        finishers["open-issue"] = lambda step, config, inner: OpenIssueBehavior(
            tracker=issue_tracker,
            repo=heal_repo,
            artifacts=artifact_view,
            clock=SystemClock(),
        )
        _ensure_autoheal_process(layout, heal_repo)

    # Scheduled triggers (`triggers/*.json`) are `TaskSource`s that ride the
    # existing `sources` list — no new loop, no `build()` parameter. A trigger's
    # target must be a served workflow or a known step; `known_targets` (served
    # workflow names ∪ their steps ∪ any catalog agent) lets the repository
    # reject a misnamed target up front rather than failing at dispatch time.
    known_targets: set[str] = set(served_names)
    wf_repo = FilesystemWorkflowRepository(layout.workflows)
    for name in served_names:
        try:
            known_targets |= set(wf_repo.get(name).steps())
        except WorkflowNotFound:
            continue
    if catalog is not None:
        known_targets |= set(catalog.names())
    sources = sources + _scheduled_sources(
        args, root, registry, clock=SystemClock(), known_targets=known_targets
    )
    # A single GitHub client threads into both the process check factories
    # (`github-issues`/`github-conflicts`) and the `label-issue` finisher —
    # one client per wiring site, like every other GitHub-touching helper here.
    token = os.environ.get("GITHUB_TOKEN")
    github_client = HttpGithubClient(token) if token else None

    # Same shape for Jira: all three env vars are required, or the
    # `jira-issues` action fails fast at process build time (mirrors the
    # `GITHUB_TOKEN` gate above).
    jira_base_url = os.environ.get("JIRA_BASE_URL")
    jira_email = os.environ.get("JIRA_EMAIL")
    jira_api_token = os.environ.get("JIRA_API_TOKEN")
    jira_client = (
        HttpJiraClient(jira_base_url, jira_email, jira_api_token)
        if jira_base_url and jira_email and jira_api_token
        else None
    )

    # Processes (`processes/*.json`) compile inside `app.build()` itself now
    # (ADR-0018) — the `failed-tasks` check needs the harness's own live
    # `failed`/`healed`/`events`, which only exist once `build()` has
    # constructed them. `_process_check_factories` supplies just the
    # externally-dependent check kinds (`github-issues`/`github-conflicts`/
    # `jira-issues`); the Slack-sink *decision*, though, still has to happen
    # here, before `build()` — a `SlackWebhookSink` must be present in
    # `sources` before `build()` constructs `SourceReflectorSink(sources)`
    # internally. Reading the raw declared sink kinds needs no compilation at
    # all (invariant #40).
    sources = sources + _slack_sinks(_declared_sink_kinds(layout.processes))
    extra_checks = _process_check_factories(
        args, registry, client=github_client, jira_client=jira_client
    )

    # "label-issue" (a finisher, invariant #41): applies an outcome -> label
    # mapping to a task's source GitHub issue, wrapping (not replacing) the
    # step's own agent behavior — used by a triage Process's PM persona to
    # relabel an issue harness:todo/harness:needs-info after judging it. Only
    # registered when a token is configured; a workflow binding a step to it
    # otherwise fails at `build()` through the existing "unknown finisher kind"
    # error, no new error path.
    if github_client is not None:
        finishers["label-issue"] = lambda step, config, inner: LabelIssueBehavior(
            inner=inner(), client=github_client, labels=config.get("labels", {})
        )

    try:
        harness = build(
            root,
            served_names,
            workspace=workspace,
            forge=forge,
            runner=runner,
            catalog=catalog,
            artifact_view=artifact_view,
            agent_timeout=args.agent_timeout,
            sources=sources or None,
            merge_checker=merge_checker,
            issue_checker=issue_checker,
            finishers=finishers or None,
            delay=args.delay,
            request_changes_once_at=args.request_changes_at,
            extra_checks=extra_checks,
            repository_registry=registry,
        )
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        # e.g. a served workflow names a finisher kind nothing registered — most
        # commonly `heal.json`'s `file-issue` step served without `--heal-repo`
        # (which is what wires the "open-issue" kind into the registry).
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        asyncio.run(
            serve(
                harness,
                args.api_port,
                args.poll,
                args.source_poll,
                args.pr_poll,
                args.reconcile_poll,
                registry=registry,
            )
        )
    except KeyboardInterrupt:
        return 0
    return 0


async def serve(
    harness,
    port: int,
    poll_interval: float,
    source_interval: float = 30.0,
    pr_poll_interval: float = 0.0,
    reconcile_interval: float = 300.0,
    registry: RepositoryRegistry | None = None,
) -> None:
    """The loop and the board in a single event loop."""
    stop = asyncio.Event()
    loop = asyncio.create_task(
        harness.run(
            poll_interval=poll_interval,
            source_interval=source_interval,
            pr_poll_interval=pr_poll_interval,
            reconcile_interval=reconcile_interval,
            stop=stop,
        )
    )

    if port == 0:
        await loop
        return

    root = harness.layout.root
    updater = UvUpdater(
        package=PACKAGE_NAME,
        entry_point=service_entry_point(),
        uid=os.getuid(),
        label=DEFAULT_LABEL,
        is_stage_active=lambda: active_stages(root),
    )
    app = create_app(
        view=harness.projection,
        artifacts=harness.artifacts,
        output=harness.stage_output,
        control=harness.control,
        clock=SystemClock(),
        agent_admin=FilesystemAgentAdmin(harness.layout.agents),
        workflow_admin=FilesystemWorkflowAdmin(harness.layout.workflows),
        # The harness's own effective registry (built-ins + `extra_checks` +
        # `failed-tasks`), so the process form offers and validates exactly
        # the checks this run compiles — a GitHub-backed process is authorable
        # in the dashboard, not only by hand-editing `processes/*.json`.
        process_admin=FilesystemProcessAdmin(
            harness.layout.processes, checks=harness.process_checks, registry=registry
        ),
        updater=updater,
        version=version_string(),
        build_time=build_timestamp(),
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = asyncio.create_task(uvicorn.Server(config).serve())
    try:
        done, _ = await asyncio.wait({loop, server}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # propagate the exception if either task crashed
    finally:
        stop.set()
        server.cancel()
        await asyncio.gather(loop, server, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness")
    parser.add_argument(
        "--version",
        action="version",
        version=f"harness {version_string()}",
    )
    # --root and --workflow are declared only on the subcommands (see below). A
    # declaration on the top-level parser would be dead: argparse's
    # _SubParsersAction overwrites the parent's namespace with the subcommand's
    # values, so a --root given before the subcommand would be silently dropped
    # and the harness would reach for the wrong (default) root. The subcommand
    # is required=True, so this collision always occurs.
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create the directory tree")
    init.add_argument("--root", default=None)
    init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    init.add_argument(
        "--no-workflow",
        action="store_true",
        help="skip writing a default workflow; add steps under agents/ directly",
    )
    init.set_defaults(handler=_init)

    submit = subparsers.add_parser("submit", help="submit a new task")
    submit.add_argument("--root", default=None)
    submit_target = submit.add_mutually_exclusive_group()
    submit_target.add_argument(
        "--workflow",
        default=None,
        help="run the named workflow (mutually exclusive with --step)",
    )
    submit_target.add_argument(
        "--step",
        default=None,
        help="run this one step and finish (mutually exclusive with --workflow)",
    )
    submit.add_argument("--repo", default=None)
    submit.add_argument("--worktree", default=None, help="path to the task's worktree")
    submit.add_argument("--data", default=None, help="JSON payload")
    submit.set_defaults(handler=_submit)

    run = subparsers.add_parser("run", help="start the orchestration loop")
    run.add_argument("--root", default=None)
    run.add_argument(
        "--workflow",
        action="append",
        dest="workflows",
        default=None,
        help="workflow to serve (repeatable); unset serves 'development' when it "
        "exists, otherwise runs workflow-less on the catalog agents",
    )
    run.add_argument(
        "--all-workflows",
        action="store_true",
        help="serve every workflow definition found under <root>/workflows "
        "(mutually exclusive with --workflow)",
    )
    run.add_argument("--delay", type=float, default=5.0)
    run.add_argument("--poll", type=float, default=0.2)
    run.add_argument(
        "--source-poll",
        type=float,
        default=30.0,
        dest="source_poll",
        help="interval (s) for polling the task source (e.g. GitHub); kept "
        "well above --poll to respect remote API rate limits",
    )
    run.add_argument(
        "--pr-poll",
        type=float,
        default=0.0,
        dest="pr_poll",
        help="interval (s) for archiving landed tasks whose PR has resolved "
        "(merged or closed unmerged); 0 disables it (default)",
    )
    run.add_argument(
        "--reconcile-poll",
        type=float,
        default=300.0,
        dest="reconcile_poll",
        help="interval (s) for checking done tasks' PR merge status and "
        "archiving them once merged; deliberately long to respect GitHub "
        "rate limits",
    )
    run.add_argument("--agent-timeout", type=float, default=1800.0, dest="agent_timeout")
    run.add_argument("--request-changes-at", default=None, dest="request_changes_at")
    run.add_argument(
        "--github-label",
        default="harness:todo",
        help="label that selects issues to ingest",
    )
    run.add_argument(
        "--no-github-source",
        action="store_true",
        dest="no_github_source",
        help="skip the built-in GithubTaskSource ingestion (use when a "
        "github-issues process owns it) — avoids double-claiming the same issue",
    )
    github_target = run.add_mutually_exclusive_group()
    github_target.add_argument(
        "--github-workflow",
        default=None,
        help="workflow assigned to GitHub-sourced tasks (default: 'development'); "
        "an explicit value must be in the served set",
    )
    github_target.add_argument(
        "--github-step",
        default=None,
        dest="github_step",
        help="single step assigned to GitHub-sourced tasks (workflow-less; "
        "mutually exclusive with --github-workflow)",
    )
    run.add_argument("--worktree-root", default=None, help="root of the task worktrees")
    run.add_argument(
        "--heal-repo",
        default=None,
        dest="heal_repo",
        help="enable self-healing: assign a healer agent to the failed queue that "
        "opens diagnostic issues on this repo (owner/repo); needs --agent claude",
    )
    run.add_argument(
        "--api-port",
        type=int,
        default=8420,
        help="board port; 0 disables the board",
    )
    run.add_argument(
        "--agent",
        choices=("claude", "dummy"),
        default="claude",
        help="who does the work in each step (dummy: no claude, for testing the pipeline)",
    )
    run.add_argument(
        "--forge",
        choices=("github", "fake"),
        default="github",
        help="where landing proposes the change (default: real GitHub)",
    )
    run.add_argument(
        "--resolver-workflow",
        default=DEFAULT_RESOLVER_WORKFLOW,
        dest="resolver_workflow",
        help="workflow the 'resolver' PR-conflict tasks (e.g. from a "
        "github-conflicts process) are served under",
    )
    run.set_defaults(handler=_run)

    agent = subparsers.add_parser("agent", help="manage per-step agent definitions")
    agent_actions = agent.add_subparsers(dest="action", required=True)

    agent_init = agent_actions.add_parser(
        "init", help="scaffold agents/<step>.json from the built-in template"
    )
    agent_init.add_argument("step")
    agent_init.add_argument("--root", default=None)
    agent_init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    agent_init.add_argument("--force", action="store_true")
    agent_init.set_defaults(handler=_agent_init)

    service = subparsers.add_parser(
        "service", help="run the harness as a background service (macOS launchd)"
    )
    service_actions = service.add_subparsers(dest="action", required=True)

    service_install = service_actions.add_parser(
        "install", help="write the LaunchAgent and start it"
    )
    service_install.add_argument("--root", default=None)
    service_install.add_argument("--label", default=DEFAULT_LABEL)
    service_install.add_argument(
        "--api-port", type=int, default=8420, dest="api_port"
    )
    service_install.set_defaults(handler=_service_install)

    service_uninstall = service_actions.add_parser(
        "uninstall", help="stop the service and remove its LaunchAgent"
    )
    service_uninstall.add_argument("--label", default=DEFAULT_LABEL)
    service_uninstall.set_defaults(handler=_service_uninstall)

    service_status = service_actions.add_parser(
        "status", help="report whether the service is loaded"
    )
    service_status.add_argument("--root", default=None)
    service_status.add_argument("--label", default=DEFAULT_LABEL)
    service_status.set_defaults(handler=_service_status)

    service_autoupdate = service_actions.add_parser(
        "autoupdate",
        help="periodically run `harness update` and restart the service",
    )
    autoupdate_actions = service_autoupdate.add_subparsers(
        dest="autoupdate_action", required=True
    )

    autoupdate_install = autoupdate_actions.add_parser(
        "install", help="write the autoupdate LaunchAgent and start it"
    )
    autoupdate_install.add_argument("--root", default=None)
    autoupdate_install.add_argument(
        "--every", required=True, help="e.g. 15m, 2h, 1d (minutes/hours/days)"
    )
    autoupdate_install.add_argument(
        "--label", default=f"{DEFAULT_LABEL}.autoupdate"
    )
    autoupdate_install.add_argument(
        "--service-label",
        default=DEFAULT_LABEL,
        dest="service_label",
        help="LaunchAgent label to restart after a version change",
    )
    autoupdate_install.set_defaults(handler=_service_autoupdate_install)

    autoupdate_uninstall = autoupdate_actions.add_parser(
        "uninstall", help="stop the autoupdate service and remove its LaunchAgent"
    )
    autoupdate_uninstall.add_argument("--label", default=f"{DEFAULT_LABEL}.autoupdate")
    autoupdate_uninstall.set_defaults(handler=_service_autoupdate_uninstall)

    autoupdate_status = autoupdate_actions.add_parser(
        "status", help="report whether the autoupdate service is loaded"
    )
    autoupdate_status.add_argument("--label", default=f"{DEFAULT_LABEL}.autoupdate")
    autoupdate_status.set_defaults(handler=_service_autoupdate_status)

    autoupdate_schedule = autoupdate_actions.add_parser(
        "schedule",
        help="schedule `harness update --restart --only-if-idle` a few times a day",
    )
    autoupdate_schedule.add_argument("--label", default=DEFAULT_LABEL)
    autoupdate_schedule.add_argument("--root", default=None)
    autoupdate_schedule.add_argument(
        "--hours",
        default="2,8,14,20",
        help="comma-separated hours (0-23) to run the update (default: 2,8,14,20)",
    )
    autoupdate_schedule.add_argument(
        "--remove", action="store_true", help="remove the autoupdate schedule"
    )
    autoupdate_schedule.set_defaults(handler=_service_autoupdate_schedule)

    update = subparsers.add_parser(
        "update", help="upgrade the installed harness via uv"
    )
    update.add_argument("--root", default=None)
    update.add_argument("--label", default=DEFAULT_LABEL)
    update.add_argument(
        "--restart-service",
        default=None,
        dest="restart_service",
        metavar="LABEL",
        help="kickstart the given LaunchAgent label after a version change",
    )
    update.add_argument(
        "--restart",
        action="store_true",
        help="restart the service after upgrading, so it runs the new version",
    )
    update.add_argument(
        "--only-if-idle",
        action="store_true",
        dest="only_if_idle",
        help="with --restart: skip the restart while a stage is running",
    )
    update.set_defaults(handler=_update)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
