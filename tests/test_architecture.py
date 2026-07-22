import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "harness"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_models_imports_nothing_from_the_package():
    assert not {
        module for module in imported_modules(SOURCE / "models.py")
        if module.startswith("harness")
    }


def test_router_only_knows_models():
    imports = {
        module for module in imported_modules(SOURCE / "router.py")
        if module.startswith("harness")
    }
    assert imports == {"harness.models"}


def test_ports_do_not_import_drivers():
    for path in (SOURCE / "ports").glob("*.py"):
        assert not any(
            module.startswith("harness.drivers") for module in imported_modules(path)
        ), f"{path.name} imports a driver"


def test_orchestration_does_not_import_drivers():
    """Dispatcher and consumer know only ports. Wiring belongs in app.py."""
    for name in ("dispatcher.py", "consumer.py"):
        assert not any(
            module.startswith("harness.drivers")
            for module in imported_modules(SOURCE / name)
        ), f"{name} imports a driver"


WORK_PORTS = (
    "harness.ports.workspace",
    "harness.ports.forge",
    "harness.ports.artifacts",
    # Phase 3: the agent, the persona catalog and the repo registry are also
    # work, not orchestration — only behavior / wiring reaches for them.
    "harness.ports.agent",
    "harness.ports.repos",
)

WORK_DRIVERS = (
    # Phase 3 drivers behind the work ports. The generic `test_orchestration_
    # does_not_import_drivers` covers them like any driver; here they are listed
    # by name so a regression is readable (dispatcher/consumer must not bind to the agent).
    "harness.drivers.claude_cli",
    "harness.drivers.fs_agents",
    "harness.drivers.fs_repos",
    "harness.drivers.worktree_artifacts",
)


def test_orchestration_does_not_import_source_port():
    """The outside world of tasks (TaskSource) is unknown to dispatcher/consumer.
    Only SourcePoller (core) and SourceReflectorSink (driver) reach for it,
    wired in app.py."""
    for name in ("dispatcher.py", "consumer.py"):
        assert "harness.ports.source" not in imported_modules(SOURCE / name), (
            f"{name} imports ports.source"
        )


def test_orchestration_does_not_import_control():
    """The operator-control port (TaskControl) is not orchestration. Only the
    task-control service, the API and the wiring reach for it — never the
    dispatcher or consumer."""
    for name in ("dispatcher.py", "consumer.py"):
        assert "harness.ports.control" not in imported_modules(SOURCE / name), (
            f"{name} imports ports.control"
        )


def test_source_poller_imports_only_ports_and_models():
    """SourcePoller is core: it knows only ports and models, no driver."""
    imports = {
        module
        for module in imported_modules(SOURCE / "source_poller.py")
        if module.startswith("harness")
    }
    assert all(
        module == "harness.models" or module.startswith("harness.ports")
        for module in imports
    ), f"source_poller.py imports outside ports/models: {imports}"


def test_reconciler_imports_only_ports_and_models():
    """MergeReconciler is core: it knows only ports, models and the base `ids`
    module (the same base layer dispatcher/consumer draw lock ids from), no
    driver."""
    imports = {
        module
        for module in imported_modules(SOURCE / "merge_reconciler.py")
        if module.startswith("harness")
    }
    assert all(
        module in ("harness.models", "harness.ids") or module.startswith("harness.ports")
        for module in imports
    ), f"merge_reconciler.py imports outside ports/models/ids: {imports}"


def test_orchestration_does_not_import_merge_port():
    """MergeChecker is unknown to dispatcher/consumer. Only MergeReconciler
    (core) and app.py/cli.py (wiring) reach for it."""
    for name in ("dispatcher.py", "consumer.py"):
        assert "harness.ports.merge" not in imported_modules(SOURCE / name), (
            f"{name} imports ports.merge"
        )


def test_healer_imports_only_ports_models_and_ids():
    """The Healer loop is core (sibling of SourcePoller): it knows only ports,
    models and ids — never a driver. Wiring lives in app.py."""
    imports = {
        module
        for module in imported_modules(SOURCE / "healer.py")
        if module.startswith("harness")
    }
    assert all(
        module in ("harness.models", "harness.ids")
        or module.startswith("harness.ports")
        for module in imports
    ), f"healer.py imports outside ports/models/ids: {imports}"


def test_orchestration_does_not_import_issues_or_healer():
    """The IssueTracker port and the Healer loop are unknown to the dispatcher and
    consumer — only the healer/wiring reach for them (invariant 27)."""
    for name in ("dispatcher.py", "consumer.py"):
        imports = imported_modules(SOURCE / name)
        assert "harness.ports.issues" not in imports, f"{name} imports ports.issues"
        assert "harness.healer" not in imports, f"{name} imports healer"


def test_orchestration_does_not_import_work_ports():
    """Worktree, forge, artifacts, agent and the repo registry are unknown to
    dispatcher/consumer — only behavior reaches for them. Otherwise orchestration
    would know about the payload a task works on, and about how the work is done."""
    for name in ("dispatcher.py", "consumer.py"):
        imports = imported_modules(SOURCE / name)
        leaked = [
            module
            for module in (*WORK_PORTS, *WORK_DRIVERS)
            if module in imports
        ]
        assert not leaked, f"{name} imports {leaked}"


def test_behaviors_import_only_ports_not_drivers():
    """Behaviors (behaviors/) reach for ports and models, not for other drivers."""
    for path in (SOURCE / "behaviors").glob("*.py"):
        assert not any(
            module.startswith("harness.drivers")
            for module in imported_modules(path)
        ), f"{path.name} imports a driver"


def test_only_app_and_cli_wire_drivers():
    wiring = {"app.py", "cli.py"}
    for path in SOURCE.glob("*.py"):
        if path.name in wiring:
            continue
        assert not any(
            module.startswith("harness.drivers") for module in imported_modules(path)
        ), f"{path.name} imports a driver outside wiring"


# --- Consumer must only deliver the outcome, never decide on it -------------
#
# Replaces `test_consumer_has_no_branch_on_outcome_value` from tests/test_consumer.py,
# which searched for three string literals via `inspect.getsource(Consumer)`. That
# failed on:
#   - `if outcome == "done":`               (only "request_changes" was checked)
#   - import alias (`from ... import Outcome as O`, then `O.DONE`)
#   - a branch moved into a module function outside the Consumer class body
#     (`inspect.getsource(Consumer)` covers only the class body)
#
# This version parses the `ast` of the whole module (not just the class) and looks
# for ANY comparison (`ast.Compare`) whose operand derives from outcome — whether
# a variable/attribute containing "outcome" (case-insensitive, catches even
# `last_outcome`), or a member of the `Outcome` enum imported under any alias.


def _outcome_import_aliases(tree: ast.Module) -> set[str]:
    """Local names under which `harness.models.Outcome` is available in this module
    (including via an `as` alias), so `O.DONE` is recognized the same as `Outcome.DONE`."""
    aliases = {"Outcome"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "harness.models":
            for alias in node.names:
                if alias.name == "Outcome":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _derives_from_outcome(expr: ast.AST, aliases: set[str]) -> bool:
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and (
            "outcome" in node.id.lower() or node.id in aliases
        ):
            return True
        if isinstance(node, ast.Attribute) and "outcome" in node.attr.lower():
            return True
    return False


def test_consumer_has_no_branch_on_outcome_value():
    """Decision-making belongs to ConsumerBehavior (what happened) and the
    dispatcher (where it goes) — consumer.py may only deliver the outcome. We
    search the whole module (functions and classes), not just the Consumer body,
    because the branch could be moved outside the class."""
    tree = ast.parse((SOURCE / "consumer.py").read_text(encoding="utf-8"))
    aliases = _outcome_import_aliases(tree)

    offending = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if any(_derives_from_outcome(operand, aliases) for operand in operands):
            offending.append(node)

    assert not offending, (
        "consumer.py contains a comparison derived from outcome on line "
        f"{offending[0].lineno} — decision-making belongs to ConsumerBehavior/dispatcher"
    )


def test_projection_does_not_import_drivers():
    """The read model reaches for ports, not drivers."""
    assert not any(
        module.startswith("harness.drivers")
        for module in imported_modules(SOURCE / "projection.py")
    )


def test_api_does_not_import_drivers():
    """The UI must know nothing about the drivers the harness runs on."""
    for path in (SOURCE / "api").rglob("*.py"):
        assert not any(
            module.startswith("harness.drivers") for module in imported_modules(path)
        ), f"{path.name} imports a driver"


def _imported_names_from(path: Path, module: str) -> set[str]:
    """Names imported from `module` in file `path` (`from module import X`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def test_api_does_not_import_cli():
    """`create_app` receives version/build_time as already-computed strings —
    it must not reach back into `cli.py` (or `importlib.metadata` directly) to
    compute them itself, which would both cycle (`cli.py` imports
    `create_app`) and leak how the harness is packaged/run into the UI layer."""
    for path in (SOURCE / "api").rglob("*.py"):
        imports = imported_modules(path)
        assert "harness.cli" not in imports, f"{path.name} imports harness.cli"
        assert "importlib.metadata" not in imports, f"{path.name} imports importlib.metadata"


def test_api_reads_artifacts_only_through_view():
    """`api/` reads artifacts only through `ArtifactView` (read-side). The
    write-side `ArtifactStore` and its drivers do not belong in the UI — the
    board must not be able to create an artifact, only show it. Phase 3: the
    read-side driver is `WorktreeArtifactView`, but `api/` doesn't know about it,
    it reaches only for the port."""
    for path in (SOURCE / "api").rglob("*.py"):
        names = _imported_names_from(path, "harness.ports.artifacts")
        assert "ArtifactStore" not in names, f"{path.name} imports ArtifactStore"
        assert "ArtifactSlot" not in names, f"{path.name} imports ArtifactSlot"
