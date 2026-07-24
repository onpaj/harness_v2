"""Processes as data: `<root>/<name>.json` → `ScheduledTrigger`s.

A **Process** is the operator's top-level authoring aggregate — it names, as
distinct nested roles, a *trigger* (cadence), an *action* (a named `Check` +
params), a *target* (a workflow **or** a step) and a *sink* (the outbound
reflection destination). It is a *compile-time* concept only: this repository
reads each file and **compiles it into a `ScheduledTrigger`** (a `TaskSource`),
reusing the whole generic-triggers machinery. Nothing under orchestration ever
learns the word "process" (ADR-0015).

Symmetric to `FilesystemTriggerRepository`, but over the richer nested schema:
`build()` reads the directory once, validates every file up front and fails fast
— raising `ProcessValidationError` (naming the offending file) rather than
letting a malformed process fire silently at runtime.

`compile_process` is the single validator: the repository runs it over every
file at startup, and `FilesystemProcessAdmin.write` (the write-side editor for
the admin UI) runs the *identical* code over one submission before writing —
so a process the operator assembles in the dashboard fails exactly where a
hand-edited file would. A `ProcessValidationError` carries an optional `field`
so the admin can map a compile failure to the right form field.

The `sink` field names the outbound reflection destination (invariant #40):
`none` (fire-and-forget, the default) or `slack`. A non-`none` sink is passed
to the compiled `ScheduledTrigger`, which stamps it into every task it fires as
`data["sink"] = {"kind": ...}` — the destination identity an outbound-only sink
driver (`SlackWebhookSink`) routes on. The shape stays `{"kind": "..."}` with
no params: the Slack webhook URL is a secret and never enters a JSON file (it
arrives via `SLACK_WEBHOOK_URL` at wiring time — the service holds no secret).
An unknown kind is still a build error.

`build()` additionally guards against a cross-file `github-issues` label
collision (two triage/ingestion processes fighting over the same scan or
claim label): every file whose `action.check == "github-issues"` has its
`label`/`claimed_label` pair collected in the same pass, and a value reused by
two different files fails the whole build, naming both files. This is a
**static, exact-string check only** — it does not catch a label typo, a
collision mediated through an agent-authored label, or two processes that are
logically incompatible without sharing a literal string; that residual
footgun is accepted, not solved. `FilesystemProcessAdmin.write` validates one
file at a time and has no visibility into siblings, so it cannot run this
check at all — a hand-edited or admin-written file only gets it at the next
full `harness run` startup (`FilesystemProcessRepository.build()`), not at
admin-save time.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harness.drivers.checks import BUILTIN_CHECKS
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.ports.clock import Clock
from harness.ports.process_admin import (
    ProcessAdmin,
    ProcessAdminValidationError,
    ProcessFields,
    ProcessNotFound,
)
from harness.ports.repos import RepositoryRegistry
from harness.ports.triggers import (
    CheckFactory,
    CheckSpec,
    CronSchedule,
    check_spec_of,
    parse_cron,
    parse_interval,
)

_ACCEPTED_SINK_KINDS = {"none", "slack", "github"}
"""The accepted sink kinds. A new destination adds a kind here plus a driver.

`github` is the degenerate, same-as-origin case (`GithubLabelReflector` can
only ever target a task's origin issue) — schema-valid here, but only
*functional* for a task whose `data.source` actually carries a GitHub repo/
issue (e.g. one produced by the `github-issues` check); a Process using any
other action stamps `data.sink` with no matching `data.source` to resolve an
issue from, so the sink is accepted but inert for it."""


class ProcessValidationError(Exception):
    """A process file is malformed. The message names the offending file; the
    optional `field` (one of
    ``interval|cron|check|params|target|dedup|sink|trigger|repository`` or
    ``None`` for a whole-file problem like broken JSON) lets the admin map the
    failure onto a form field. `trigger` covers a malformed trigger *block*
    (missing/not-a-dict, both/neither of interval+cron present, or an
    unsupported `trigger.kind`); `interval`/`cron` cover a present-but-invalid
    cadence *value*. `str()` is unchanged — still just the message."""

    def __init__(self, message: str, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


def invalid_process_name(name: str) -> bool:
    """The name must not carry a path separator and must not be "", "." or ".."."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


class _LocalClock(Clock):
    """A minimal `Clock` for the admin's validation call. `compile_process`
    only *stores* the clock inside the `ScheduledTrigger` it builds — it never
    ticks it — so this exists purely to satisfy the constructor without
    importing a sibling driver (fs_processes stays a thin aggregate, guarded by
    `test_architecture.py`)."""

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - never called
        return None


def compile_process(
    name: str,
    raw: dict,
    *,
    clock: Clock,
    checks: dict[str, CheckFactory] = BUILTIN_CHECKS,
    repository: str | None = None,
    worktree_root: str | None = None,
    known_targets: set[str] | None = None,
    known_repositories: set[str] | None = None,
    where: str | None = None,
) -> ScheduledTrigger:
    """Validate one process definition and compile it into a `ScheduledTrigger`.

    The single validator behind both the repository (compile every file at
    startup) and the admin (validate one submission before writing). `where`
    (defaults to `name`) is the label used in messages. Each failure raises
    `ProcessValidationError(msg, field=...)` with `field` one of
    ``interval|cron|check|params|target|dedup|sink|trigger|repository``.

    `repository` is a fallback default (the caller's own wiring-time value,
    e.g. a global `--repository`); the file's own `"repository"` key, when
    present, takes precedence over it. Either way, an observation the check
    itself produces with its own `repository` (e.g. `github-issues` stamping
    each issue's own repo) still wins — that composition happens inside
    `ScheduledTrigger._task_for` via `obs.repository or self._repository`,
    unchanged by this function.
    """
    where = where or name
    interval, cron = _parse_cadence(where, raw.get("trigger"))
    _check_trigger_kind(where, raw.get("trigger"))
    check = _parse_action(where, raw.get("action"), checks)
    workflow, step = _parse_target(where, raw.get("target"), known_targets)
    dedup = _parse_dedup(where, raw.get("dedup", "per-interval"))
    sink = _parse_sink(where, raw.get("sink"))
    file_repository = _parse_repository(where, raw.get("repository"), known_repositories)

    return ScheduledTrigger(
        name=name,
        clock=clock,
        interval=interval,
        cron=cron,
        check=check,
        workflow=workflow,
        step=step,
        repository=file_repository or repository,
        worktree_root=worktree_root,
        dedup=dedup,
        sink=sink,
    )


def _parse_cadence(where: str, trigger: object) -> tuple[float | None, CronSchedule | None]:
    """Read the trigger block's cadence — exactly one of `interval`/`cron`.

    A missing/malformed trigger block, or one carrying both/neither, is a
    `field="trigger"` error (the block itself is malformed); a present but
    unparseable value is `field="interval"`/`field="cron"` (that one value is
    malformed) — see `ProcessValidationError`'s docstring for the full split.
    """
    if not isinstance(trigger, dict):
        raise ProcessValidationError(
            f"process {where} must have a trigger object", field="trigger"
        )
    has_interval = "interval" in trigger
    has_cron = "cron" in trigger
    if has_interval == has_cron:
        raise ProcessValidationError(
            f"process {where} trigger must have exactly one of interval/cron",
            field="trigger",
        )
    if has_cron:
        try:
            return None, parse_cron(trigger["cron"])
        except (ValueError, TypeError) as error:
            raise ProcessValidationError(
                f"process {where} has an invalid cron expression: {error}",
                field="cron",
            ) from None
    try:
        return parse_interval(trigger["interval"]), None
    except (ValueError, TypeError) as error:
        raise ProcessValidationError(
            f"process {where} has an invalid interval: {error}", field="interval"
        ) from None


def _parse_action(where: str, action: object, checks: dict[str, CheckFactory]):
    if not isinstance(action, dict) or "check" not in action:
        raise ProcessValidationError(
            f"process {where} must have an action naming a check", field="check"
        )
    check_name = action["check"]
    if check_name not in checks:
        raise ProcessValidationError(
            f"process {where} names an unknown check {check_name!r}", field="check"
        )
    try:
        return checks[check_name](action.get("params", {}))
    except (KeyError, ValueError, TypeError) as error:
        raise ProcessValidationError(
            f"process {where} has invalid params for check {check_name!r}: {error}",
            field="params",
        ) from None


def _parse_target(
    where: str,
    target: object,
    known_targets: set[str] | None,
) -> tuple[str | None, str | None]:
    if not isinstance(target, dict) or set(target) not in ({"workflow"}, {"step"}):
        raise ProcessValidationError(
            f"process {where} must have a target of exactly one of "
            f"{{'workflow': ...}} / {{'step': ...}}",
            field="target",
        )

    workflow = target.get("workflow")
    step = target.get("step")
    value = workflow if workflow is not None else step
    if known_targets is not None and value not in known_targets:
        raise ProcessValidationError(
            f"process {where} targets {value!r}, which is not a known "
            f"workflow or step",
            field="target",
        )
    return workflow, step


def _parse_dedup(where: str, dedup: object) -> str:
    if dedup not in ("per-interval", "per-state"):
        raise ProcessValidationError(
            f"process {where} has an invalid dedup strategy: {dedup!r}", field="dedup"
        )
    return dedup


def _parse_repository(
    where: str, repository: object, known_repositories: set[str] | None
) -> str | None:
    """None (the key is absent) is valid and means "no process-level
    default". A present value must be a non-empty string; when
    `known_repositories` is given it must also be a member of it — an
    unrecognized name is a `field="repository"` error, same as an unknown
    `target`. `known_repositories=None` (no registry reachable) is lenient,
    matching every other `known_*=None` escape hatch in this module."""
    if repository is None:
        return None
    if not isinstance(repository, str) or not repository:
        raise ProcessValidationError(
            f"process {where} has an invalid repository: {repository!r}",
            field="repository",
        )
    if known_repositories is not None and repository not in known_repositories:
        raise ProcessValidationError(
            f"process {where} names repository {repository!r}, which is not "
            "in the repository registry",
            field="repository",
        )
    return repository


def _check_trigger_kind(where: str, trigger: object) -> None:
    # Forward-compat reservation, the same zero-cost move `sink` got: the
    # trigger object may carry a `kind`, but only `"schedule"` exists — a
    # future condition-driven trigger adds a kind here plus a compile path,
    # never a schema change. `_parse_cadence` has already established that
    # `trigger` is a dict.
    kind = trigger.get("kind", "schedule") if isinstance(trigger, dict) else "schedule"
    if kind != "schedule":
        raise ProcessValidationError(
            f"process {where} has an unsupported trigger kind {kind!r} "
            f"(only 'schedule' is supported in this version)",
            field="trigger",
        )


def _parse_sink(where: str, sink: object) -> dict | None:
    # The destination half of invariant #40: the accepted kinds are `none`
    # (fire-and-forget), `slack` (`SlackWebhookSink` routes on the stamped
    # `data.sink`) and `github` (`GithubLabelReflector`, the degenerate
    # same-as-origin case). A new destination adds an accepted kind here and
    # a driver — no schema change.
    if sink is None:
        return None
    if not isinstance(sink, dict) or sink.get("kind") not in _ACCEPTED_SINK_KINDS:
        accepted = ", ".join(repr(kind) for kind in sorted(_ACCEPTED_SINK_KINDS))
        raise ProcessValidationError(
            f"process {where} has an unsupported sink {sink!r} "
            f"(accepted kinds: {accepted})",
            field="sink",
        )
    return sink


class FilesystemProcessRepository:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def build(
        self,
        *,
        clock: Clock,
        checks: dict[str, CheckFactory] = BUILTIN_CHECKS,
        repository: str | None = None,
        worktree_root: str | None = None,
        known_targets: set[str] | None = None,
        known_repositories: set[str] | None = None,
        default_github_issues_label: str = "harness:todo",
    ) -> list[ScheduledTrigger]:
        if not self._root.exists():
            return []

        triggers: list[ScheduledTrigger] = []
        seen: dict[str, str] = {}  # github-issues label/claimed_label -> file name
        for path in sorted(self._root.glob("*.json")):
            triggers.append(
                self._build_one(
                    path,
                    clock=clock,
                    checks=checks,
                    repository=repository,
                    worktree_root=worktree_root,
                    known_targets=known_targets,
                    known_repositories=known_repositories,
                )
            )
            self._check_github_issues_collision(
                path, seen, default_github_issues_label=default_github_issues_label
            )
        return triggers

    @staticmethod
    def _check_github_issues_collision(
        path: Path,
        seen: dict[str, str],
        *,
        default_github_issues_label: str,
    ) -> None:
        # Re-reads the file's already-validated JSON purely to see the raw
        # `action` — `_build_one` above already raised if it were malformed,
        # so this second parse is a startup-time, one-file cost, not a hot path.
        raw = json.loads(path.read_text(encoding="utf-8"))
        action = raw.get("action") if isinstance(raw, dict) else None
        if not isinstance(action, dict) or action.get("check") != "github-issues":
            return
        params = action.get("params", {})
        params = params if isinstance(params, dict) else {}
        label = params.get("label", default_github_issues_label)
        claimed_label = params.get("claimed_label", "harness:queued")
        for value in (label, claimed_label):
            if not isinstance(value, str):
                continue
            if value in seen and seen[value] != path.name:
                raise ProcessValidationError(
                    f"processes {seen[value]!r} and {path.name!r} both use the "
                    f"github-issues label {value!r} (label or claimed_label) — "
                    "this would double-claim or leave claiming ambiguous",
                    field="params",
                )
            seen[value] = path.name

    def _build_one(
        self,
        path: Path,
        *,
        clock: Clock,
        checks: dict[str, CheckFactory],
        repository: str | None,
        worktree_root: str | None,
        known_targets: set[str] | None,
        known_repositories: set[str] | None,
    ) -> ScheduledTrigger:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ProcessValidationError(
                f"process {path.name} has a broken definition: {error}"
            ) from None

        if not isinstance(raw, dict):
            raise ProcessValidationError(
                f"process {path.name} has an invalid definition: expected object, "
                f"got {type(raw).__name__}"
            )

        name = raw.get("name", path.stem)
        return compile_process(
            name,
            raw,
            clock=clock,
            checks=checks,
            repository=repository,
            worktree_root=worktree_root,
            known_targets=known_targets,
            known_repositories=known_repositories,
            where=path.name,
        )


class FilesystemProcessAdmin(ProcessAdmin):
    """Read/write access to the same `<root>/<name>.json` files
    `FilesystemProcessRepository` reads — the admin UI's driver.

    `write` validates a submission with the *same* `compile_process` the
    repository runs at startup, so nothing is written unless it would compile;
    a `ProcessValidationError` (with its `field`) becomes a
    `ProcessAdminValidationError({field: message})`.

    `checks` is the same name → factory registry the runtime compiles processes
    with — wiring passes the *extended* registry (built-ins plus the
    GitHub-backed actions closed over a client in `cli._process_checks`), so
    the form's dropdown and save-time validation see exactly the checks a
    `harness run` would accept. `None` falls back to `BUILTIN_CHECKS`, the
    client-free subset.

    `registry` is the same `RepositoryRegistry` (`repos.json`) the real run
    resolves repository names with — machine-local, static config, available
    at both compile sites (unlike the served-workflow set `known_targets`
    depends on). `None` means no registry was wired: `repository_names()`
    returns `()` and `write()` validates any repository name leniently
    (matching every other `known_*=None` escape hatch)."""

    def __init__(
        self,
        root: Path,
        *,
        checks: dict[str, CheckFactory] | None = None,
        registry: RepositoryRegistry | None = None,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._checks = checks if checks is not None else BUILTIN_CHECKS
        self._registry = registry

    def list(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.stem
                for path in self._root.glob("*.json")
                if not invalid_process_name(path.stem)
            )
        )

    def read(self, name: str) -> ProcessFields:
        if invalid_process_name(name):
            raise ProcessNotFound(f"invalid process name: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProcessNotFound(f"process {name!r} does not exist ({path})") from None
        except json.JSONDecodeError as error:
            raise ProcessNotFound(
                f"process {name!r} has a broken definition: {error}"
            ) from None

        try:
            return _fields_from_raw(raw)
        except (KeyError, TypeError, AttributeError) as error:
            raise ProcessNotFound(
                f"process {name!r} has an invalid definition: {error}"
            ) from None

    def write(self, name: str, fields: ProcessFields) -> ProcessFields:
        if invalid_process_name(name):
            raise ProcessAdminValidationError({"name": f"invalid process name: {name!r}"})

        raw = _raw_from_fields(fields)
        known_repositories = set(self._registry.names()) if self._registry else None
        try:
            compile_process(
                name,
                raw,
                clock=_LocalClock(),
                checks=self._checks,
                known_targets=None,
                known_repositories=known_repositories,
            )
        except ProcessValidationError as error:
            raise ProcessAdminValidationError({error.field or "_": str(error)}) from None

        path = self._root / f"{name}.json"
        self._write(path, raw)
        return _fields_from_raw(raw)

    def delete(self, name: str) -> bool:
        if invalid_process_name(name):
            return False
        path = self._root / f"{name}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def check_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._checks))

    def check_specs(self) -> tuple[CheckSpec, ...]:
        # `check_spec_of` reads the spec a `CheckDefinition` carries, or
        # synthesizes a generic one for a bare factory — so every registered
        # action surfaces to the form, whether or not it declared a spec.
        return tuple(
            check_spec_of(name, self._checks[name]) for name in sorted(self._checks)
        )

    def sink_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(_ACCEPTED_SINK_KINDS))

    def repository_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._registry.names())) if self._registry else ()

    def _write(self, path: Path, raw: dict) -> None:
        # Same idiom as `FilesystemAgentAdmin._write` (drivers/fs_agents.py):
        # unique temp name in the same directory, then an atomic replace, so a
        # crash mid-write never leaves a half-written JSON file behind.
        temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.json.tmp")
        try:
            temporary.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _raw_from_fields(fields: ProcessFields) -> dict:
    """Assemble the nested `processes/*.json` shape from the flat form fields.
    `name` is never written — the file's stem is the name (like agents). Nor is
    `trigger.kind`: the reservation is read-tolerated (`_fields_from_raw` simply
    ignores it), not an editable field, so the admin never emits it.

    `fields.cadence` picks which value field is authoritative — a submission
    that toggled to "cron" writes `{"cron": ...}` even if `fields.interval`
    still holds a stale value from before the toggle, and vice versa."""
    trigger = {"cron": fields.cron} if fields.cadence == "cron" else {"interval": fields.interval}
    raw = {
        "trigger": trigger,
        "action": {"check": fields.check, "params": dict(fields.params)},
        "target": {fields.target_kind: fields.target},
        "dedup": fields.dedup,
        "sink": {"kind": fields.sink_kind},
    }
    if fields.repository:
        raw["repository"] = fields.repository
    return raw


def _fields_from_raw(raw: dict) -> ProcessFields:
    """Reconstruct the flat editable fields from a nested file. Raises
    KeyError/TypeError/AttributeError on a malformed shape — the caller (`read`)
    turns that into `ProcessNotFound`."""
    trigger = raw["trigger"]
    action = raw["action"]
    target = raw["target"]
    if "workflow" in target:
        target_kind, target_value = "workflow", target["workflow"]
    elif "step" in target:
        target_kind, target_value = "step", target["step"]
    else:
        raise KeyError("target names neither a workflow nor a step")
    sink_kind = (raw.get("sink") or {}).get("kind", "none")
    if "cron" in trigger:
        cadence, interval, cron = "cron", "", trigger["cron"]
    else:
        cadence, interval, cron = "interval", trigger["interval"], ""
    return ProcessFields(
        cadence=cadence,
        interval=interval,
        cron=cron,
        check=action["check"],
        target_kind=target_kind,
        target=target_value,
        params=dict(action.get("params", {})),
        sink_kind=sink_kind,
        dedup=raw.get("dedup", "per-interval"),
        repository=raw.get("repository") or "",
    )
