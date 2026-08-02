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
from harness.app import (
    LANDING_STEP,
    HarnessLayout,
    UnknownFinisherKind,
    build,
    validate_workflow_finishers,
)
from harness.behaviors.merge_pr import (
    DEFAULT_METHOD,
    DEFAULT_MIN_CONFIDENCE,
    MergePrBehavior,
)
from harness.behaviors.open_issue import OpenIssueBehavior
from harness.drivers.claude_cli import ClaudeCliRunner
from harness.drivers.fake_forge import FakeForge
from harness.drivers.fs_agents import FilesystemAgentAdmin, FilesystemAgentCatalog
from harness.drivers.fs_processes import FilesystemProcessAdmin, ProcessValidationError
from harness.drivers.github_issues import GithubIssueTracker
from harness.drivers.github_pr_merger import GithubPullRequestMerger
from harness.drivers.memory import MemoryIssueTracker, MemoryPullRequestMerger
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
# `DEFAULT_SKIP_LABEL` is aliased because *both* PR checks export that name with
# deliberately different values — `harness:no-automerge` vetoes a merge,
# `harness:no-autofix` vetoes an agent touching the branch at all.
from harness.drivers.github_unhealthy_prs_check import (
    DEFAULT_GIVE_UP_LABEL,
    DEFAULT_LOG_TAIL_LINES,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_SKIP_LABEL as UNHEALTHY_SKIP_LABEL,
)
from harness.drivers.jira_client import HttpJiraClient, JiraClient
from harness.drivers.label_issue import LabelIssueBehavior
from harness.drivers.slack_sink import SlackWebhookSink
from harness.drivers.subprocess_command import SubprocessCommandRunner
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
from harness.retention_reconciler import DEFAULT_RETENTION_DAYS
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.issue_state import IssueChecker
from harness.ports.issues import IssueError
from harness.ports.merge import MergeChecker
from harness.ports.pr_merge import MERGE_METHODS
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
        {"from": "development", "on": "done", "to": "verify"},
        {"from": "verify", "on": "done", "to": "review"},
        {"from": "verify", "on": "request_changes", "to": "development"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
    # Prompt-side these steer the agent (invariant #42); board-side they are what
    # a column head says the step is *for*, instead of only naming it. Seeded
    # here so a fresh root's board explains itself; an existing workflow file is
    # never rewritten, so add them there through the workflow editor.
    "descriptions": {
        "plan": "break the request down into the work it actually implies",
        "design": "decide how it will be built, before any of it is",
        "architecture": "check the design against the codebase's invariants",
        "development": "write the code and the tests",
        "verify": "run the repository's own checks against the diff",
        "review": "read the diff as a reviewer would; send it back or pass it on",
        "land": "sync the base branch in and open the pull request",
    },
    "finishers": {"verify": "verify"},
}

DEFAULT_UNBLOCK_WORKFLOW = "unblock-pr"

UNBLOCK_PR_DEFINITION = {
    "name": "unblock-pr",
    "start": "unblock",
    "transitions": [
        {"from": "unblock", "on": "done", "to": "land",
         "hint": "the conflict is resolved and/or the failing checks should now pass"},
        {"from": "unblock", "on": "stuck", "to": "end",
         "hint": "you could not fix this from what you were given — push nothing"},
        {"from": "land", "on": "done", "to": "end"},
    ],
    "descriptions": {
        "unblock": (
            "fix whatever is blocking this pull request — merge conflicts, "
            "failing checks, or both"
        ),
        "land": "commit the fix and push it to the pull request's branch",
    },
    # `maxParallel`, not `max_parallel`: `fs_workflows._parse_workflow` reads
    # the camelCase key and ignores everything else, so the snake_case spelling
    # would silently leave this step on the default of 1.
    "maxParallel": {"unblock": 2},
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
         "hint": "nothing similar is open in the task's repository"},
        {"from": "dedup", "on": "duplicate", "to": "end",
         "hint": "a correlated issue is already open — settle silently"},
        {"from": "file-issue", "on": "done", "to": "end"},
    ],
    "descriptions": {
        "heal": "diagnose the failed task from its report; decide whether it warrants a GitHub issue",
        "dedup": "read the task's repository's open issues; decide whether the drafted issue is new",
    },
    "finishers": {
        "file-issue": {
            "kind": "open-issue",
            "from_step": "heal",
            "label": "harness:self-heal",
            # Ships withheld: configuring the Process is not the same as
            # trusting it. `allowed_labels` is deliberately absent — adding
            # it here would switch on unattended auto-fixing (the
            # `harness:todo` label the persona now drafts, cli.py's
            # `heal` prompt) for every `harness init` deployment. The
            # operator opts in per repo by editing this binding themselves.
        }
    },
}


DEFAULT_AUTOMERGE_WORKFLOW = "automerge"

AUTOMERGE_DEFINITION = {
    "name": "automerge",
    "start": "merge-review",
    "transitions": [
        {"from": "merge-review", "on": "approve", "to": "merge",
         "hint": "you would merge this yourself without asking anyone"},
        {"from": "merge-review", "on": "reject", "to": "end",
         "hint": "anything less than that — leave the PR for a human, nothing is merged"},
        {"from": "merge", "on": "done", "to": "end"},
    ],
    "descriptions": {
        "merge-review": (
            "review the pull request checked out in your worktree and decide "
            "whether it is safe to merge unattended"
        ),
    },
    "finishers": {
        "merge": {
            "kind": "merge-pr",
            "from_step": "merge-review",
            "min_confidence": 0.8,
            "method": "squash",
            # Ships withheld: configuring the Process is not the same as
            # trusting it. The operator flips this to false once the recorded
            # decisions on real PRs justify it (ADR-0023).
            "dry_run": True,
        }
    },
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

    # `unblock-pr`/`unblock` (ADR-0026), the retired `resolver`/`resolve`
    # workflow's successor. Seeded together with `processes/unblock-pr.json`
    # below, so the feature is reachable from a fresh install rather than
    # needing three files hand-written first.
    unblock_definition_path = layout.workflows / f"{DEFAULT_UNBLOCK_WORKFLOW}.json"
    if not unblock_definition_path.exists():
        unblock_definition_path.write_text(
            json.dumps(UNBLOCK_PR_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # `heal`/`file-issue` (ADR-0018): dormant data, shipped unconditionally
    # exactly like the unblock-pr workflow. `processes/autoheal.json` is shipped
    # unconditionally too — self-healing is now configured entirely through
    # that file, like every other Process; a bare `harness init` seeds it
    # with an empty `action.params.repository`, and the operator points it
    # at a registered repo by editing the file or through the dashboard's
    # process editor.
    heal_definition_path = layout.workflows / f"{DEFAULT_HEAL_WORKFLOW}.json"
    if not heal_definition_path.exists():
        heal_definition_path.write_text(
            json.dumps(HEAL_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    # `automerge`/`merge-review` (ADR-0023): shipped unconditionally exactly
    # like the unblock-pr and heal workflows — and, unlike what the ADR
    # originally decided, so is `processes/automerge.json` (seeded just below
    # by `_ensure_automerge_process`, which explains the revision). So this
    # workflow *runs* on a freshly initialized root: it reviews every clean
    # harness-authored PR across every registered repo and records what it
    # would have merged. What withholds the merge itself is
    # `finishers.merge.dry_run`, seeded `true` until an operator flips it.
    automerge_definition_path = layout.workflows / f"{DEFAULT_AUTOMERGE_WORKFLOW}.json"
    if not automerge_definition_path.exists():
        automerge_definition_path.write_text(
            json.dumps(AUTOMERGE_DEFINITION, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    _ensure_autoheal_process(layout)
    _ensure_automerge_process(layout)
    _ensure_unblock_pr_process(layout)

    # Workflows are read straight off the filesystem here, not through
    # `build()`: `build()` also compiles `processes/*.json` (ADR-0018), and
    # the autoheal process just seeded above targets `{"workflow": "heal"}` —
    # a target `build()` only accepts from a harness that *serves* `heal`.
    # This call only ever wants to serve `args.workflow`, so widening the
    # served set just to satisfy that one process's validation would be
    # backwards, and it wouldn't even work: `heal`'s `file-issue` step binds
    # the `open-issue` finisher, which nothing here registers (that wiring is
    # `_run`-only), so serving `heal` through `build()` fails a *second*
    # validation on top of the first. Reading the workflow definitions
    # directly avoids both.
    try:
        raw_workflows = FilesystemWorkflowRepository(layout.workflows)
        workflow = raw_workflows.get(args.workflow)
        unblock_workflow = raw_workflows.get(DEFAULT_UNBLOCK_WORKFLOW)
        heal_workflow = raw_workflows.get(DEFAULT_HEAL_WORKFLOW)
        automerge_workflow = raw_workflows.get(DEFAULT_AUTOMERGE_WORKFLOW)
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    # Queues/tasks/done/failed directories, previously a side effect of
    # calling `build()` here — reproduced directly now that `build()` is no
    # longer called, including each queue's `.processing/` subdirectory
    # (`FilesystemTaskQueue.__init__`, `drivers/fs_queue.py`). Scoped to
    # `args.workflow`'s own steps only, exactly as before: `unblock-pr`/`heal`
    # get their queues the first time a `harness run` actually serves them
    # (every workflow file on disk, invariant #24).
    for queue_dir in (layout.tasks, layout.done, layout.failed, layout.healed, layout.archived):
        queue_dir.mkdir(parents=True, exist_ok=True)
        (queue_dir / ".processing").mkdir(parents=True, exist_ok=True)
    for step in workflow.steps():
        step_dir = layout.queues / step
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / ".processing").mkdir(parents=True, exist_ok=True)

    _write_default_agents(layout, workflow)
    # Writes `agents/unblock.json` only — `land` is the landing step, skipped.
    # Its `allowed_outcomes` comes out as `["done"]`, not the workflow's own
    # `done`/`stuck`: `fs_agents._parse_agent_spec` accepts only `done` and
    # `request_changes` in that field, so writing `stuck` there would make the
    # persona unloadable. Harmless — the live vocabulary is the workflow's
    # (invariant #42), and `UnblockPrBehavior` is wired with the workflow
    # repository, so `stuck` reaches the agent from `unblock-pr.json`'s edges.
    _write_default_agents(layout, unblock_workflow)
    _write_default_agents(layout, heal_workflow)
    # Writes `agents/merge-review.json` only — `merge` binds the `merge-pr`
    # finisher, so `_write_default_agents` skips it (no persona, no agent).
    _write_default_agents(layout, automerge_workflow)

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
    "implementation and address every point it raises.\n"
    "A revision round may also be triggered by a failed verify run — a "
    "verify-NN.md artifact with the test command's output. Read it and fix "
    "the failures it shows.\n\n"
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

_UNBLOCK_PERSONA = (
    "You are a senior developer whose only job right now is to get one pull "
    "request unblocked. The working directory is a checkout of the PR's own "
    "branch, and the base branch has already been merged in — so if there was "
    "a conflict, the files with <<<<<<< ======= >>>>>>> markers are in front "
    "of you now.\n\n"
    "Your brief above says what is wrong: a conflict, one or more failing "
    "checks with the tail of their logs, or both. Fix all of it.\n\n"
    "For a conflict: read each conflicted file, understand both sides from the "
    "surrounding code and tests, and produce a resolution that preserves the "
    "combined intent. Remove every marker.\n\n"
    "For a failing check: the log tail tells you what failed, not always why. "
    "Read the code the failure points at before you change it. Then run the "
    "relevant tests yourself and confirm they pass — a fix you have not run is "
    "a guess, and pushing a guess costs another round of CI.\n\n"
    "A log tail may be absent for checks whose logs this harness cannot fetch. "
    "Say so and work from the check's name and the diff rather than inventing "
    "what it said.\n\n"
    "Do not widen the scope. You are fixing what is broken, not improving what "
    "happens to be nearby — an unrelated change here lands on someone else's "
    "PR.\n\n"
    "If you cannot fix it from what you have — the failure is environmental, "
    "the log is uninformative, or the right fix is a judgement call that is "
    "not yours to make — choose \"stuck\" and explain why in your artifact. "
    "Stuck is a perfectly good answer and costs a human one glance. A "
    "speculative push costs a full CI run and burns one of three attempts.\n\n"
    "Do not commit, push, create a branch, or open a worktree — the harness "
    "does all of that."
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
    "For a harness bug or an operational/tuning problem, write your diagnosis "
    "to the file the harness told you to write your output to above, and end "
    "that file with a fenced ```json block holding a one-element array:\n"
    '```json\n'
    '[{"title": "<concise title>", "labels": ["harness:todo"], '
    '"body": "<the work order — see below>"}]\n'
    "```\n"
    "The harness reads that block by machine and opens the issue itself — you "
    "must never open one. That drafts array belongs only in the artifact "
    "file — never repeat or echo it in your final message, which must carry "
    "only the verdict block described below (a JSON object, not this array). "
    "For an operational/tuning problem, recommend "
    "diagnostically rather than prescriptively: name the exceeded budget and "
    "the two levers available — raising the step's per-agent `timeout`, or "
    "decomposing the step into smaller ones — without prescribing a specific "
    "number. Then finish with the outcome that files it.\n\n"
    "The issue you draft is consumed by an automated development pipeline, "
    "not read by a person before work starts — its first step turns your body "
    "into numbered requirements with acceptance criteria. So write the body "
    "as a work order, with these sections:\n"
    "- **Symptom** — what failed, quoting the exact error.\n"
    "- **Reproduction** — the sequence that produces it, or plainly why it "
    "cannot be reproduced on demand.\n"
    "- **Proposed change** — the concrete change, naming files and functions "
    "wherever you can.\n"
    "- **Acceptance criteria** — what must be true for the fix to be done, "
    "stated testably.\n\n"
    "State in the body which kind of finding this is. A code defect is scoped "
    "as a code change; an operational/tuning finding must be scoped as a "
    "configuration change and never as a refactor.\n\n"
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


_MERGE_REVIEW_PERSONA = (
    "You are the last reviewer before a pull request merges into the default "
    "branch with no human in the loop. Your worktree is checked out on the "
    "PR's own branch.\n\n"
    "Read the change before you judge it. `git diff origin/<base>...HEAD` is "
    "the change; `git log origin/<base>..HEAD` is how it got there. Read the "
    "PR body and the issue it closes, then read the surrounding source of "
    "every file it touches — a diff that looks clean in isolation can still be "
    "wrong in context.\n\n"
    "Ask, in this order:\n"
    "1. Does the change do what its PR and issue say it does — no more, no "
    "less? Unrelated scope is a reason to withhold, even when the code is "
    "good.\n"
    "2. Is it correct? Look for the failure cases the tests do not cover, not "
    "just the ones they do.\n"
    "3. Does it touch anything with blast radius — auth, secrets, migrations, "
    "deletion, payments, CI/release config, public API shape?\n"
    "4. Is it consistent with the conventions of the code around it, and does "
    "it respect the project's documented invariants?\n\n"
    "Then decide. Approve only when you would merge it yourself without asking "
    "anyone. Anything else — an unreviewable diff, a missing test you would "
    "have asked for, a risk you cannot rule out by reading — is a rejection, "
    "and a rejection costs nothing but a human's glance. A wrong merge costs "
    "the default branch.\n\n"
    "Confidence is your own estimate that merging this is the right call, from "
    "0.0 to 1.0. It is not a measure of how well you understood the diff, and "
    "it is not a formality: report it honestly, because the harness merges on "
    "it. Large, cross-cutting or infrastructural changes should rarely clear "
    "0.9 no matter how clean they read. If you did not read every changed "
    "file, say so and stay below the bar.\n\n"
    "Write your review to the artifact and end it with a fenced json block — "
    "the last one in the file wins:\n"
    "```json\n"
    '{"confidence": 0.0, "reasoning": "one or two sentences", '
    '"risks": ["..."]}\n'
    "```\n"
    "You never merge anything yourself and you have no tool that can. The "
    "harness merges only if your confidence clears the operator's threshold; "
    "your job is the judgement, not the button."
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
    "unblock": (
        _UNBLOCK_PERSONA,
        ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
    ),
    "heal": (_HEALER_PERSONA, ["Read", "Write"]),
    "dedup": (_DEDUP_PERSONA, ["Read", "Bash"]),
    "merge-review": (_MERGE_REVIEW_PERSONA, ["Read", "Grep", "Glob", "Bash"]),
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
#   unblock       ← developer-class PR unblocking (sonnet)
# A step with no entry keeps `model = null` (the CLI's configured default).
AGENT_MODELS: dict[str, str] = {
    "plan": "opus",
    "design": "sonnet",
    "architecture": "opus",
    "development": "sonnet",
    "review": "sonnet",
    "unblock": "sonnet",
    "heal": "opus",
    "dedup": "opus",
    # The most consequential judgement the harness makes unattended — the one
    # step where paying for the top tier is obviously worth it.
    "merge-review": "opus",
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


def _issue_import_factory(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    client: GithubClient | None = None,
):
    """The Ahanas board's manual "Add issue" write port, as an
    `IssueImportFactory` (invariant #43) — the same "cli.py closes over the
    external dependency, build() supplies the live queues once they exist"
    shape as `extra_checks`/`finishers`. `None` without a client (no
    `GITHUB_TOKEN`), so `build()` falls back to its own `NullIssueImport`.

    Reuses `--github-workflow`/`--github-step` — the same target the
    automatic `github-issues` ingestion uses (`_github_sources`'s identical
    defaulting) — and the same `worktree_root` computation, so a task created
    via manual import gets a worktree path consistent with one created via
    automatic ingestion.
    """
    if client is None:
        return None

    from harness.drivers.github_issue_import import GithubIssueImportService

    worktree_root = args.worktree_root or str(root / "worktrees")
    workflow = args.github_workflow
    step = args.github_step
    if workflow is None and step is None:
        workflow = DEFAULT_WORKFLOW

    def factory(*, inbox, step_queues, done, failed, healed, archived, events, clock):
        return GithubIssueImportService(
            client=client,
            registry=registry,
            inbox=inbox,
            step_queues=step_queues,
            done=done,
            failed=failed,
            healed=healed,
            archived=archived,
            events=events,
            clock=clock,
            workflow=workflow,
            step=step,
            worktree_root=worktree_root,
        )

    return factory


def _scheduled_sources(
    args: argparse.Namespace,
    root: Path,
    registry: RepositoryRegistry,
    *,
    clock: Clock,
    known_steps: set[str] | None,
    known_workflows: set[str] | None,
) -> list[TaskSource]:
    """Scheduled triggers declared under `<root>/triggers/*.json`.

    Each becomes a `ScheduledTrigger` — a `TaskSource` that produces tasks on a
    clock gate and reflects nothing outward (a `Trigger`) — appended to the run's
    existing `sources` list; `build()` gains no parameter. A missing/empty
    `triggers/` directory yields `[]`, so the harness runs exactly as before.
    `known_steps` (the real step-queue namespace) and `known_workflows` (served
    workflow names) let the repository reject a trigger whose target names
    neither its own namespace nor has no dispatch queue, up front."""
    from harness.drivers.fs_triggers import FilesystemTriggerRepository

    repo = FilesystemTriggerRepository(root / "triggers")
    worktree_root = args.worktree_root or str(root / "worktrees")
    return repo.build(
        clock=clock,
        repository=None,
        worktree_root=worktree_root,
        known_steps=known_steps,
        known_workflows=known_workflows,
    )


def _process_check_factories(
    args: argparse.Namespace,
    registry: RepositoryRegistry,
    *,
    client: GithubClient | None = None,
    jira_client: JiraClient | None = None,
) -> dict[str, CheckFactory]:
    """Check kinds `processes/*.json` may name that need a dependency
    `BUILTIN_CHECKS` can't carry — `github-issues`/`github-unhealthy-prs`/
    `github-mergeable`, each closed over a `GithubClient` + the repo registry,
    and `jira-issues`, closed over a `JiraClient` + the repo registry. The clients come from the
    caller (tests) or the environment (`GITHUB_TOKEN`; `JIRA_BASE_URL`/
    `JIRA_EMAIL`/`JIRA_API_TOKEN`).

    Returns just the factory dict — process *compilation* itself now happens
    inside `app.build()` (ADR-0018), which merges this dict over
    `BUILTIN_CHECKS` alongside its own internal `"failed-tasks"` factory (a
    dependency this function has no reason to carry: that check needs the
    harness's own live `failed`/`healed` queues, not an external client).
    """
    from harness.drivers.fs_processes import (
        MissingCredential,
        ProcessValidationError,
    )
    from harness.drivers.github_issues_check import SPEC as GITHUB_ISSUES_SPEC
    from harness.drivers.github_issues_check import GithubIssuesCheck
    from harness.drivers.github_mergeable_check import DEFAULT_SKIP_LABEL
    from harness.drivers.github_mergeable_check import SPEC as GITHUB_MERGEABLE_SPEC
    from harness.drivers.github_mergeable_check import GithubMergeableCheck

    # The unhealthy-PRs defaults (`UNHEALTHY_SKIP_LABEL`,
    # `DEFAULT_GIVE_UP_LABEL`, `DEFAULT_MAX_ATTEMPTS`,
    # `DEFAULT_LOG_TAIL_LINES`) are imported at module level — the local
    # `DEFAULT_SKIP_LABEL` bound just above is the *automerge* one, which is
    # why the unhealthy-PRs skip label carries an alias everywhere it is read.
    from harness.drivers.github_unhealthy_prs_check import (
        SPEC as GITHUB_UNHEALTHY_PRS_SPEC,
        GithubUnhealthyPrsCheck,
    )
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
            raise MissingCredential(
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

    def github_unhealthy_prs_factory(params: dict) -> GithubUnhealthyPrsCheck:
        if client is None:
            raise MissingCredential(
                "github-unhealthy-prs action requires GITHUB_TOKEN", field="check"
            )
        max_attempts = params.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
        log_tail_lines = params.get("log_tail_lines", DEFAULT_LOG_TAIL_LINES)
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ProcessValidationError(
                "github-unhealthy-prs action requires params.max_attempts to be "
                "a positive integer",
                field="params",
            )
        # `< 1`, not `< 0`: the check tails with `lines[-log_tail_lines:]`, and
        # Python's `-0 == 0` would make a zero mean the *entire* log rather than
        # none of it — the one value that quietly defeats the tail.
        if not isinstance(log_tail_lines, int) or log_tail_lines < 1:
            raise ProcessValidationError(
                "github-unhealthy-prs action requires params.log_tail_lines to be "
                "a positive integer",
                field="params",
            )
        return GithubUnhealthyPrsCheck(
            client=client,
            registry=registry,
            head_prefix=params.get("head_prefix", "harness/"),
            skip_label=params.get("skip_label", UNHEALTHY_SKIP_LABEL),
            give_up_label=params.get("give_up_label", DEFAULT_GIVE_UP_LABEL),
            max_attempts=max_attempts,
            log_tail_lines=log_tail_lines,
        )

    def github_mergeable_factory(params: dict) -> GithubMergeableCheck:
        if client is None:
            raise MissingCredential(
                "github-mergeable action requires GITHUB_TOKEN", field="check"
            )
        return GithubMergeableCheck(
            client=client,
            registry=registry,
            head_prefix=params.get("head_prefix", "harness/"),
            skip_label=params.get("skip_label", DEFAULT_SKIP_LABEL),
        )

    def jira_issues_factory(params: dict) -> JiraIssuesCheck:
        if jira_client is None:
            raise MissingCredential(
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
        "github-unhealthy-prs": CheckDefinition(
            spec=GITHUB_UNHEALTHY_PRS_SPEC, factory=github_unhealthy_prs_factory
        ),
        "github-mergeable": CheckDefinition(
            spec=GITHUB_MERGEABLE_SPEC, factory=github_mergeable_factory
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


def _warn_missing_autoheal_repository(
    processes_root: Path, dropped_workflows: set[str] | None = None
) -> None:
    """Startup warning — never fatal — for a `failed-tasks` process with no
    `action.params.repository`: `harness init` seeds `processes/autoheal.json`
    with `action.params == {}` (invariant #25), which is valid and stays
    valid, but self-healing then runs on every failure — spending an agent
    call on `heal` and one on `dedup` — and files nothing, because
    `OpenIssueBehavior`'s `slug_for` has no repository to resolve. Read
    straight off the raw JSON, like `_declared_sink_kinds` — this only ever
    decides whether to print a warning, so it doesn't need to wait for a full
    `compile_process` to know the field is missing.

    A *present but wrong* `params.repository` is a different case entirely —
    a typo, not "not configured yet" — and already fails loud at
    process-compile time (`fs_processes._validate_action_repository_param`,
    `ProcessValidationError`, exit 2); this function only ever handles the
    absent case, per ADR-0022.

    `dropped_workflows` (default none, mirroring `build()`'s own
    `dropped_workflows` param) names workflows `_validate_served_workflows`
    already dropped from the served set. A `failed-tasks` process whose own
    `target.workflow` is one of them is made inert by
    `FilesystemProcessRepository.build()`'s `_targets_a_dropped_workflow`
    check — it never fires, so it will not "run heal/dedup on every failure"
    the way this warning claims. Printing this warning for that process
    contradicts the drop warning already printed for its target workflow
    (which says outright that the process is disabled); skip it here instead
    of leaving two startup warnings that disagree with each other."""
    if not processes_root.is_dir():
        return
    for path in sorted(processes_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, dict):
            continue
        action = raw.get("action")
        if not isinstance(action, dict) or action.get("check") != "failed-tasks":
            continue
        if dropped_workflows:
            target = raw.get("target")
            if isinstance(target, dict) and target.get("workflow") in dropped_workflows:
                continue
        params = action.get("params")
        repository = params.get("repository") if isinstance(params, dict) else None
        if not repository:
            print(
                f"warning: {path.name} enables self-healing (action.check == "
                "'failed-tasks') with no action.params.repository set — it "
                "will run heal/dedup on every failure but file nothing until "
                "you point it at a repository name from repos.json",
                file=sys.stderr,
            )


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
silently. `heal` is a genuine three-step workflow (`heal` → `dedup` →
`file-issue`, invariant #26), so it needs a real `Workflow` in scope on every
`route()` call past the first, which only happens when `task.workflow_template`
is set (ADR-0018)."""


AUTOMERGE_PROCESS_DEFINITION = {
    "trigger": {"interval": "5m"},
    "action": {"check": "github-mergeable", "params": {"head_prefix": "harness/"}},
    "target": {"workflow": "automerge"},
    "dedup": "per-state",
    "sink": {"kind": "none"},
}
"""One Process covers **every** repository: `GithubMergeableCheck.evaluate()`
iterates `RepositoryRegistry.names()` and scans each repo's open PRs, so there
is nothing per-repo to author — adding a repo to `repos.json` puts it under
automerge review automatically, and a non-GitHub repo is skipped.

`dedup` is `per-state`, and that is load-bearing rather than stylistic: the
check emits one observation *per candidate PR*, and under the default
`per-interval` every observation in a tick collapses onto the same dedup key,
so `SourcePoller._seen` would keep the first and silently drop the rest (three
mergeable PRs → one review). `per-state` keys each task on `slug:pr:head_sha`,
which is also what re-reviews a re-pushed PR and leaves an unchanged one alone.

Seeding this file makes automerge *run*; it does not make it *merge*. The
`merge` step's `dry_run` lives in `workflows/automerge.json`'s finisher binding
and ships `true`, so a seeded root reviews every clean PR and records what it
would have merged until the operator flips that one field (ADR-0023).

Safe to seed even with no `GITHUB_TOKEN`: the `github-mergeable` factory raises
`MissingCredential`, which `FilesystemProcessRepository.build()` skips with a
warning instead of failing the run — without that, this file alone would make
every tokenless run exit 2 and break the harness's "no token is not fatal"
promise."""


def _ensure_automerge_process(layout: HarnessLayout) -> None:
    """Seed `processes/automerge.json` unless one already exists — never
    clobbering an operator's hand-edited file, exactly like the autoheal seeder.

    ADR-0023 originally seeded no Process at all, on the reasoning that
    automerging is a posture rather than a queue that needs draining. That held
    while the *only* safety gate was "the operator must author a file". It no
    longer needs to: `dry_run: true` in the workflow binding is the real gate,
    and it is a strictly better one — it exercises the whole path on real PRs
    and shows the operator what this persona would have done, which authoring a
    file from scratch never did. So the Process ships, and the withholding
    moves entirely to `dry_run`.
    """
    path = layout.processes / "automerge.json"
    if path.exists():
        return
    layout.processes.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(AUTOMERGE_PROCESS_DEFINITION, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


UNBLOCK_PR_PROCESS_DEFINITION = {
    "trigger": {"interval": "60s"},
    "action": {
        "check": "github-unhealthy-prs",
        "params": {
            "head_prefix": "harness/",
            "skip_label": UNHEALTHY_SKIP_LABEL,
            "give_up_label": DEFAULT_GIVE_UP_LABEL,
            "max_attempts": DEFAULT_MAX_ATTEMPTS,
            "log_tail_lines": DEFAULT_LOG_TAIL_LINES,
        },
    },
    "target": {"workflow": "unblock-pr"},
    "dedup": "per-state",
    "sink": {"kind": "none"},
}
"""One Process covers **every** repository, exactly like the automerge one:
`GithubUnhealthyPrsCheck.evaluate()` iterates `RepositoryRegistry.names()`, so
adding a repo to `repos.json` puts its open PRs under triage automatically and
a non-GitHub repo is skipped.

`head_prefix` is seeded `"harness/"`, matching `automerge.json` — a fresh
`harness init` plus a `GITHUB_TOKEN` therefore touches only branches the
harness itself authored. ADR-0026's decision is that the *feature* may work any
open PR, and it can: widening it is one field of this file and no code change.
But the widening is the operator's explicit act, not something a default install
does on its own, because the widest setting means merging into, committing to
and pushing every unhealthy open PR in every registered repository — including
branches a human has checked out — from the first tick. The sibling automerge
Process ships equally withheld (`dry_run: true` in its finisher binding); this
is the same posture expressed with the knob this check actually has.

What contains the widened setting, once an operator chooses it, is on the PR
rather than in this file: nothing is ever force-pushed, a fork PR is never
touched, `harness:no-autofix` vetoes one PR with no config change, and both the
three-attempt budget and the agent's own give-up end at `harness:needs-human`
instead of looping.

`dedup` is `per-state`, and load-bearing rather than stylistic: the check emits
one observation *per unhealthy PR*, and under the default `per-interval` every
observation in a tick collapses onto one dedup key, so `SourcePoller._seen`
would keep the first and silently drop the rest.

Safe to seed with no `GITHUB_TOKEN`: the `github-unhealthy-prs` factory raises
`MissingCredential`, which `FilesystemProcessRepository.build()` skips with a
warning rather than failing the run."""


def _ensure_unblock_pr_process(layout: HarnessLayout) -> None:
    """Seed `processes/unblock-pr.json` unless one already exists — never
    clobbering an operator's hand-edited file, exactly like the autoheal and
    automerge seeders.

    Seeded for the same reason `automerge.json` is: authoring a file from
    scratch was never the safety gate people took it for, and leaving the
    feature unreachable from a fresh install only meant the previous release
    kept seeding the *retired* `resolver` workflow instead — whose `resolve`
    step no longer matches `app.UNBLOCK_STEP`, so it fell through to the
    generic `ClaudeCliBehavior` and committed without merging the base or
    excluding `.artifacts`.
    """
    path = layout.processes / "unblock-pr.json"
    if path.exists():
        return
    layout.processes.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(UNBLOCK_PR_PROCESS_DEFINITION, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _ensure_autoheal_process(layout: HarnessLayout) -> None:
    """Seed `processes/autoheal.json` unless one already exists — never
    clobbering an operator's hand-edited file.

    Self-healing is configured like every other Process: this file. Its
    `action.params.repository` is deliberately empty here — an operator points
    it at a registered repo (by name, as in `repos.json`) by editing this file
    or through the dashboard's process editor. Until they do, a heal task is
    repository-less, and the `open-issue` finisher fails it with a message
    saying exactly that.

    Written directly (like `_init`'s `HEAL_DEFINITION`/`RESOLVER_DEFINITION`),
    **not** through `FilesystemProcessAdmin.write`: validating `"failed-tasks"`
    needs the merged registry `app.build()` assembles, which does not exist at
    init time. The real validation happens when `build()` compiles it.

    Ordering note: a previous version of this function ran from `_run` and had
    to run before `_scheduled_sources(...)` compiled `processes/*.json`, or the
    file it just wrote would sit unread until the next restart. Now that the
    seeding lives here, in `_init` — a separate command from `run` — that
    constraint is gone entirely: nothing writes a process file during `run`'s
    startup wiring any more, so there is no ordering left to preserve between
    this call and anything `run`'s startup does. (`FilesystemProcessAdmin.write`
    still writes `processes/*.json` from the dashboard while `run` is live —
    those are picked up on the next restart, by design.)
    """
    path = layout.processes / "autoheal.json"
    if path.exists():
        return
    layout.processes.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(AUTOHEAL_PROCESS_DEFINITION, indent=2, ensure_ascii=False),
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
    # Snapshotted for either restart path (`--restart-service` and `--restart`),
    # since both gate on the version actually having changed. Skipped entirely
    # for a plain `harness update`, which restarts nothing and so would only pay
    # the subprocess for an answer it never reads.
    may_restart = bool(restart_service) or bool(getattr(args, "restart", False))
    before = installed_version_report() if may_restart else None

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
    # Same gate as the `--restart-service` path above: a no-op poll must not kill
    # a healthy service. Without this, the every-30-minutes autoupdate schedule
    # SIGKILLs the running harness on every fire whether or not anything was
    # upgraded, so no stage lasting longer than the schedule's period can ever
    # complete. The idle check below is not a substitute: it only defers a
    # restart while a stage is *claimed*, and cannot know the restart was
    # pointless to begin with.
    if before == after:
        print(f"service {label} left running (no version change)")
        return 0

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


def _resolve_served_workflows(layout: HarnessLayout) -> tuple[str, ...]:
    """The set of workflow names `harness run` serves: every definition under
    `<root>/workflows/`.

    Serving is data, not configuration — dropping a workflow file into the root
    serves it, and removing it stops serving it. An empty or missing directory
    is workflow-less mode (FR-6): no workflow is served and the catalog agents
    run directly, rather than a startup error.
    """
    return FilesystemWorkflowRepository(layout.workflows).names()


def _processes_targeting_workflow(processes_root: Path, workflow_name: str) -> list[str]:
    """Which `processes/*.json` files declare `{"target": {"workflow":
    workflow_name}}`, read straight off the raw JSON — no `compile_process`
    involved, like `_declared_sink_kinds`/`_warn_missing_autoheal_repository`.
    Used only to enrich the served-workflow drop warning below with the
    process(es) it takes down with it, so "my self-healing stopped" is
    traceable from that one warning line rather than needing to correlate it
    with a second one printed somewhere else."""
    names: list[str] = []
    if not processes_root.is_dir():
        return names
    for path in sorted(processes_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, dict):
            continue
        target = raw.get("target")
        if isinstance(target, dict) and target.get("workflow") == workflow_name:
            names.append(path.name)
    return names


def _validate_served_workflows(
    layout: HarnessLayout,
    wf_repo: FilesystemWorkflowRepository,
    served_names: tuple[str, ...],
    finishers: dict[str, Callable[[str, dict, Callable[[], ConsumerBehavior]], ConsumerBehavior]],
) -> tuple[str, ...]:
    """Drop a served workflow whose own finisher bindings can't be wired
    against `finishers` for a config reason, printing a `warning:` that names
    the file, the step and the reason — instead of letting it take the whole
    run down. An *unknown* finisher kind (`UnknownFinisherKind`, a `ValueError`
    subclass) is a different failure shape entirely and is deliberately not
    caught here: it propagates out of this function unchanged, and `_run`'s
    own `except UnknownFinisherKind` around this call still fails the whole
    run (exit 2) for it — see the operator's governing rule below.

    `harness run` serves every `workflows/*.json` on disk (ADR-0022), so a
    single stale file — e.g. the pre-generic string form
    `"file-issue": "open-issue"`, which parses to an empty config and fails
    the `open-issue` factory's own `label` check — would otherwise crash-loop
    a launchd-supervised service on every restart. That is a *missing* value
    (the binding names a real, registered kind but doesn't carry what its
    factory needs), so it only warns and drops here, before `build()` ever
    sees the workflow.

    An unknown kind is different: it is a value that is *set and wrong* (a
    typo, or a binding naming a kind that was never registered — e.g.
    `label-issue` while `GITHUB_TOKEN` is unset). Per the operator's rule —
    an explicitly-set bad value fails fast, only a missing one warns — that
    must still fail the *whole* run, exactly as `app.build()`'s own
    equivalent check always has (invariant #41): this function lets
    `UnknownFinisherKind` propagate rather than dropping the workflow for it.

    A workflow that can't even be *parsed* (`WorkflowNotFound` — broken JSON,
    an unknown step in `finishers`, ...) is left in the returned set
    untouched: that's `build()`'s own, unrelated fail-fast for a malformed
    *workflow file*, not a finisher-binding problem, and this function has no
    business silently dropping it.
    """
    kept: list[str] = []
    for name in served_names:
        try:
            workflow = wf_repo.get(name)
        except WorkflowNotFound:
            kept.append(name)
            continue
        try:
            validate_workflow_finishers(workflow, finishers)
        except UnknownFinisherKind:
            raise
        except ValueError as error:
            path = layout.workflows / f"{name}.json"
            dependents = _processes_targeting_workflow(layout.processes, name)
            note = (
                f" — also disables process(es) {', '.join(dependents)}, which "
                f"target it"
                if dependents
                else ""
            )
            print(
                f"warning: workflow {name!r} ({path}) cannot be served — "
                f"{error} — dropped from the served set{note}",
                file=sys.stderr,
            )
            continue
        kept.append(name)
    return tuple(kept)


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


def _slug_resolver(registry: RepositoryRegistry) -> Callable[[str | None], str]:
    """`task.repository` → `owner/repo`, the way every other GitHub-touching
    driver does it: resolve the name to a clone through the registry, then read
    the slug off that clone's `origin`. `repos.json` holds paths only — the
    slug is never duplicated in config. Every failure is an `IssueError`, so it
    lands the task in `failed/` with a message naming the cause."""

    def slug_for(name: str | None) -> str:
        if not name:
            raise IssueError(
                "the task has no repository, so no GitHub repo can be resolved "
                "— set the process's params.repository"
            )
        try:
            path = registry.resolve(name)
        except RepositoryNotFound as error:
            raise IssueError(str(error)) from None
        slug = github_slug(path)
        if slug is None:
            raise IssueError(f"repo {name!r} ({path}) has no github.com origin remote")
        return slug

    return slug_for


def _retention_days() -> int:
    """The terminal-task retention window, from `HARNESS_RETENTION_DAYS`.

    A tuning knob, not a secret — so a bad value is a non-fatal startup
    warning and the default, never an exit. The harness refusing to start
    over a typo in a housekeeping number would be a worse failure than the
    window being wrong for one run.

    Set it in `<root>/secrets.env` — the wrapper `harness service install`
    generates sources that file under `set -a`, so anything in it is exported.
    An `export` in the operator's own shell never reaches the launchd service,
    which is handed almost no environment, and editing `harness-run.sh` is
    silently undone by the next install (including an autoupgrade).

    `0` is deliberately valid: it archives every settled task in the `done`
    column on the next sweep, which is the "clear the board now" setting.
    There is therefore no
    "off" value — `0` is the *most* aggressive setting, and effectively
    disabling the sweep means a very large window (`36500`, a century).
    """
    raw = os.environ.get("HARNESS_RETENTION_DAYS")
    if raw is None:
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw)
    except ValueError:
        print(
            f"warning: HARNESS_RETENTION_DAYS={raw!r} is not an integer; "
            f"using {DEFAULT_RETENTION_DAYS}",
            file=sys.stderr,
        )
        return DEFAULT_RETENTION_DAYS
    if days < 0:
        print(
            f"warning: HARNESS_RETENTION_DAYS={raw!r} is negative; "
            f"using {DEFAULT_RETENTION_DAYS}",
            file=sys.stderr,
        )
        return DEFAULT_RETENTION_DAYS
    return days


def _label_list(step: str, config: dict, key: str) -> tuple[str, ...]:
    """One of the `open-issue` binding's two label lists, validated.

    A plain `ValueError` on a malformed value, so ADR-0022's rule applies
    unchanged: the binding is unwirable, `_validate_served_workflows` drops
    that one workflow with a warning naming file/step/reason, and the rest of
    the run is unaffected. A bare string is rejected rather than iterated into
    characters — the likeliest way to get this wrong by hand."""
    value = config.get(key, ())
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(one, str) and one for one in value
    ):
        raise ValueError(
            f"step {step!r} binds the 'open-issue' finisher with an invalid "
            f"{key!r} — expected a list of non-empty strings, got {value!r}"
        )
    return tuple(value)


def _run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    served_names = _resolve_served_workflows(layout)

    # `--github-workflow` defaults to `None` (not `DEFAULT_WORKFLOW`) so this
    # check only fires when the operator actually named a workflow for GitHub
    # ingestion. Validating the *default* against the served set would reject
    # a root whose served set happens to exclude "development" (e.g. no
    # `development.json` on disk) even with no GitHub flags at all -- a
    # regression against FR-6, since no GithubTaskSource is ever built in
    # that case. `--github-step` (workflow-less GitHub ingestion) skips the
    # check: it names a step, not a workflow, and `_github_sources` applies
    # its own defaulting.
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

    # The `open-issue` finisher kind, registered unconditionally: it derives
    # its repo from `task.repository` and takes its label from the binding, so
    # it needs no wiring-time configuration at all. That is what lets a root
    # serve the seeded `heal` workflow without any heal-specific setup.
    issue_token = os.environ.get("GITHUB_TOKEN")
    issue_tracker = (
        GithubIssueTracker(HttpGithubClient(issue_token))
        if issue_token
        else MemoryIssueTracker()
    )
    slug_for = _slug_resolver(registry)

    def _open_issue(step, config, inner):
        label = config.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(
                f"step {step!r} binds the 'open-issue' finisher without a "
                f"'label' — it is both the label every issue carries and the "
                f"scope of the idempotency search"
            )
        from_step = config.get("from_step")
        return OpenIssueBehavior(
            tracker=issue_tracker,
            artifacts=artifact_view,
            slug_for=slug_for,
            label=label,
            from_step=from_step,
            labels=_label_list(step, config, "labels"),
            allowed_labels=_label_list(step, config, "allowed_labels"),
            # Replace shape when a step is named; wrap shape otherwise. The
            # thunk is only called in the wrap shape, so a step bound in the
            # replace shape never triggers `catalog.get` (ADR-0018).
            inner=None if from_step else inner(),
        )

    # The `merge-pr` finisher kind (ADR-0023), registered unconditionally for
    # the same reason `open-issue` is: it takes every deployment-specific
    # value from the task (`data.source`) and the binding (threshold, method,
    # dry_run), so it needs no wiring-time configuration. Without a token the
    # merger is the in-memory fake, so the seeded `automerge` workflow is
    # servable — and harmless — on a root with no GitHub access at all,
    # exactly like the seeded `heal` workflow.
    pr_merger = (
        GithubPullRequestMerger(HttpGithubClient(issue_token))
        if issue_token
        else MemoryPullRequestMerger()
    )

    def _merge_pr(step, config, inner):
        method = config.get("method", DEFAULT_METHOD)
        if method not in MERGE_METHODS:
            raise ValueError(
                f"step {step!r} binds the 'merge-pr' finisher with an unknown "
                f"merge method {method!r} (known: {', '.join(MERGE_METHODS)})"
            )
        min_confidence = config.get("min_confidence", DEFAULT_MIN_CONFIDENCE)
        if isinstance(min_confidence, bool) or not isinstance(
            min_confidence, (int, float)
        ):
            raise ValueError(
                f"step {step!r} binds the 'merge-pr' finisher with a "
                f"non-numeric 'min_confidence': {min_confidence!r}"
            )
        if not 0.0 <= float(min_confidence) <= 1.0:
            raise ValueError(
                f"step {step!r} binds the 'merge-pr' finisher with a "
                f"'min_confidence' outside 0.0–1.0: {min_confidence!r}"
            )
        dry_run = config.get("dry_run", True)
        if not isinstance(dry_run, bool):
            raise ValueError(
                f"step {step!r} binds the 'merge-pr' finisher with a non-boolean "
                f"'dry_run': {dry_run!r} — omit it to keep the safe default"
            )
        from_step = config.get("from_step")
        return MergePrBehavior(
            merger=pr_merger,
            artifacts=artifact_view,
            from_step=from_step,
            min_confidence=float(min_confidence),
            method=method,
            dry_run=dry_run,
            # Replace shape when a step is named (the seeded `automerge`
            # workflow's agent-less `merge` step, reading `merge-review`'s
            # artifact); wrap shape otherwise — the same split as `open-issue`.
            inner=None if from_step else inner(),
        )

    finishers: dict[
        str, Callable[[str, dict, Callable[[], ConsumerBehavior]], ConsumerBehavior]
    ] = {"open-issue": _open_issue, "merge-pr": _merge_pr}

    # A single GitHub client threads into both the process check factories
    # (`github-issues`/`github-unhealthy-prs`) and the `label-issue` finisher —
    # one client per wiring site, like every other GitHub-touching helper
    # here. Built here — ahead of where the process check factories and the
    # issue-import factory consume it further down — so `finishers` below is
    # already complete (including "label-issue") by the time the served-set
    # validation that follows reads it.
    token = os.environ.get("GITHUB_TOKEN")
    github_client = HttpGithubClient(token) if token else None

    # "label-issue" (a finisher, invariant #41): applies an outcome -> label
    # mapping to a task's source GitHub issue, wrapping (not replacing) the
    # step's own agent behavior — used by a triage Process's PM persona to
    # relabel an issue harness:todo/harness:needs-info after judging it. Only
    # registered when a token is configured; a workflow binding a step to it
    # otherwise fails the served-set validation below (or, for an unserved
    # workflow, `build()` itself) through the "unknown finisher kind" error,
    # no new error path.
    if github_client is not None:
        finishers["label-issue"] = lambda step, config, inner: LabelIssueBehavior(
            inner=inner(), client=github_client, labels=config.get("labels", {})
        )

    # A served workflow whose own finisher binding names a *known* kind that
    # rejects its config (e.g. the pre-generic string form `"open-issue"`,
    # which parses to an empty config and fails the factory's own `label`
    # check — exactly the shape `workflows/heal.json` shipped with on the
    # reference install before this branch generalized the finisher) must not
    # crash-loop the whole service. Drop it from the served set with a
    # warning instead of letting `build()` fail the entire run over one stale
    # file — see ADR-0022. An *unknown* kind is a different, set-and-wrong
    # failure shape and is not dropped: `_validate_served_workflows` re-raises
    # `UnknownFinisherKind` unchanged, and the `except` below still fails the
    # whole run (exit 2) for it, exactly as `build()`'s own equivalent check
    # always has (a genuine cross-workflow binding conflict, a malformed
    # workflow file, ... still fail there too, unchanged).
    wf_repo = FilesystemWorkflowRepository(layout.workflows)
    served_names_on_disk = served_names
    try:
        served_names = _validate_served_workflows(layout, wf_repo, served_names, finishers)
    except UnknownFinisherKind as error:
        # A set-and-wrong value (a typo, or a kind nothing ever registered) —
        # the operator's rule keeps this fatal for the whole run, exactly as
        # `app.build()`'s own equivalent check always has (invariant #41),
        # unlike the config-shaped failures `_validate_served_workflows`
        # itself warns-and-drops for. See ADR-0022.
        print(f"error: {error}", file=sys.stderr)
        return 2
    # Workflows dropped by the pre-filter above (e.g. the stale string-form
    # `heal.json`) can no longer be a Process's target either — a Process
    # targeting one is made inert further down (`build()`'s `dropped_workflows`
    # param, threaded to `FilesystemProcessRepository.build()`), rather than
    # failing the whole build the way an unresolvable target normally would.
    dropped_workflows = set(served_names_on_disk) - set(served_names)

    forge = _build_forge(args.forge, root, registry)
    github = [] if args.no_github_source else _github_sources(args, root, registry)
    # The outbound reflector is registered only when classic ingestion is off
    # (`--no-github-source`) — never alongside `GithubTaskSource` for the same
    # repo, which already reflects via its own composed reflector.
    reflectors = _github_reflectors(args, root, registry) if args.no_github_source else []
    sources = github + reflectors
    merge_checker = _build_merge_checker(args)
    issue_checker = _build_issue_checker(args)

    # Scheduled triggers (`triggers/*.json`) are `TaskSource`s that ride the
    # existing `sources` list — no new loop, no `build()` parameter. A trigger's
    # `{"step": ...}` target must have a real dispatch queue (`known_steps` —
    # their steps ∪ any catalog agent, never a served workflow's own name) and
    # a `{"workflow": ...}` target must be a served workflow name
    # (`known_workflows`) — two independent namespaces, so the repository
    # rejects a target that names the wrong one up front, rather than failing
    # at dispatch time.
    known_steps: set[str] = set()
    for name in served_names:
        try:
            known_steps |= set(wf_repo.get(name).steps())
        except WorkflowNotFound:
            continue
    if catalog is not None:
        known_steps |= set(catalog.names())
    known_workflows = set(served_names)
    sources = sources + _scheduled_sources(
        args,
        root,
        registry,
        clock=SystemClock(),
        known_steps=known_steps,
        known_workflows=known_workflows,
    )

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
    # externally-dependent check kinds (`github-issues`/`github-unhealthy-prs`/
    # `jira-issues`); the Slack-sink *decision*, though, still has to happen
    # here, before `build()` — a `SlackWebhookSink` must be present in
    # `sources` before `build()` constructs `SourceReflectorSink(sources)`
    # internally. Reading the raw declared sink kinds needs no compilation at
    # all (invariant #40).
    sources = sources + _slack_sinks(_declared_sink_kinds(layout.processes))
    # A compiled `failed-tasks` process with no `action.params.repository` is
    # valid — it's the seeded `harness init` default — but silently inert:
    # self-healing spends an agent call on `heal` and one on `dedup` every
    # time `failed/` drains, and files nothing. Warn about it (never an
    # error — a *missing* value is "not configured yet", not a typo; a
    # *present but wrong* one still fails loud at process-compile time,
    # unchanged — see ADR-0022) rather than leaving the token bill as the
    # only signal.
    _warn_missing_autoheal_repository(layout.processes, dropped_workflows)
    extra_checks = _process_check_factories(
        args, registry, client=github_client, jira_client=jira_client
    )

    # The Ahanas board's manual "Add issue" write port (invariant #43): built
    # inside `build()` from this factory once the harness's own live queues
    # exist — `None` without a token, so `build()` falls back to
    # `NullIssueImport` and the board still renders the button/dialog, just
    # with every submit reporting "not configured".
    issue_import_factory = _issue_import_factory(args, root, registry, client=github_client)

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
            issue_import_factory=issue_import_factory,
            repository_registry=registry,
            command_runner=SubprocessCommandRunner(),
            dropped_workflows=dropped_workflows,
            retention_days=_retention_days(),
            # The `unblock` step's give-up label (ADR-0026). The capability,
            # not the client: `behaviors/` may not import `drivers/`, so
            # `UnblockPrBehavior` takes a callable and the wiring closes the
            # client into it — the shape `OpenIssueBehavior` already uses for
            # `slug_for`. `None` without a token, exactly like the
            # `github-unhealthy-prs` check that mints those tasks, so the two
            # halves of the containment are never configured apart.
            pr_labeller=(
                github_client.add_label if github_client is not None else None
            ),
        )
    except WorkflowNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        # A backstop, not the primary path: `served_names` here has already
        # passed `_validate_served_workflows` above, so an unknown finisher
        # kind or a binding rejecting its own config normally exits 2 earlier
        # (the `except UnknownFinisherKind` around that call, or the warn/drop
        # inside it). This still catches a genuine cross-workflow finisher
        # conflict — two served workflows binding the same step differently —
        # which only `build()` itself can see (see `_open_issue` above for the
        # config-rejection shape).
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ProcessValidationError as error:
        # e.g. a process file's action.params.repository names a repo missing
        # from repos.json (invariant #25/#39) — a malformed process file must
        # not crash the service with a raw traceback under launchd.
        print(f"error: {error}", file=sys.stderr)
        return 2

    # A process whose action needs a credential this run does not have is
    # skipped, not fatal (`fs_processes.MissingCredential`): the harness runs
    # degraded — everything else still flows — rather than not at all, which
    # is what keeps "no token is not fatal" true once a `github-*` Process
    # exists on the root. A set-and-wrong value still exits 2 just above.
    # `getattr`: `build()` is a public seam a number of tests stub out with a
    # bare object; a real `Harness` always carries the attribute.
    for file_name, reason in getattr(harness, "skipped_processes", ()):
        print(
            f"warning: {file_name} is disabled for this run — {reason}",
            file=sys.stderr,
        )

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
            harness.layout.processes,
            checks=harness.process_checks,
            registry=registry,
            known_steps=set(harness.known_steps),
            known_workflows=set(harness.workflows),
        ),
        updater=updater,
        issue_import=harness.issue_import,
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
        help="interval (s) for the housekeeping sweeps that archive settled "
        "tasks: a done task whose PR merged, a task whose source issue was "
        "closed, and a settled task in the done column past "
        "HARNESS_RETENTION_DAYS (`failed/` is exempt). "
        "Deliberately long — none of them is latency-sensitive, and the first "
        "two poll GitHub, whose rate limits they must respect",
    )
    run.add_argument("--agent-timeout", type=float, default=5400.0, dest="agent_timeout")
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
