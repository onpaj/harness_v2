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
        ), f"{path.name} importuje driver"


def test_orchestration_does_not_import_drivers():
    """Dispatcher a consumer znají jen porty. Wiring patří do app.py."""
    for name in ("dispatcher.py", "consumer.py"):
        assert not any(
            module.startswith("harness.drivers")
            for module in imported_modules(SOURCE / name)
        ), f"{name} importuje driver"


def test_only_app_and_cli_wire_drivers():
    wiring = {"app.py", "cli.py"}
    for path in SOURCE.glob("*.py"):
        if path.name in wiring:
            continue
        assert not any(
            module.startswith("harness.drivers") for module in imported_modules(path)
        ), f"{path.name} importuje driver mimo wiring"


# --- Consumer musí jen doručit outcome, nikdy o něm nerozhodovat -----------
#
# Nahrazuje `test_consumer_has_no_branch_on_outcome_value` z tests/test_consumer.py,
# která hledala tři string literály přes `inspect.getsource(Consumer)`. To
# selhávalo na:
#   - `if outcome == "done":`               (kontrolovalo se jen "request_changes")
#   - alias importu (`from ... import Outcome as O`, pak `O.DONE`)
#   - větev přesunutou do modulové funkce mimo tělo třídy Consumer
#     (`inspect.getsource(Consumer)` pokrývá jen tělo třídy)
#
# Tahle verze parsuje `ast` celého modulu (ne jen třídy) a hledá JAKÉKOLI
# porovnání (`ast.Compare`), jehož operand se odvozuje od outcome — ať už jde
# o proměnnou/atribut obsahující "outcome" (case-insensitive, chytí i
# `last_outcome`), nebo o člena enumu `Outcome` naimportovaného pod libovolným
# aliasem.


def _outcome_import_aliases(tree: ast.Module) -> set[str]:
    """Lokální jména, pod kterými je v tomto modulu dostupný `harness.models.Outcome`
    (i přes `as` alias), aby `O.DONE` bylo poznat stejně jako `Outcome.DONE`."""
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
    """Rozhodování patří do ConsumerBehavior (co se stalo) a dispatcheru (kam
    to jde) — consumer.py smí outcome jen doručit. Hledá se v celém modulu
    (funkce i třídy), ne jen v těle Consumer, protože se to větví přesunout
    i mimo třídu."""
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
        "consumer.py obsahuje porovnání odvozené od outcome na řádku "
        f"{offending[0].lineno} — rozhodování patří do ConsumerBehavior/dispatcheru"
    )


def test_projection_does_not_import_drivers():
    """Read model sahá na porty, ne na drivery."""
    assert not any(
        module.startswith("harness.drivers")
        for module in imported_modules(SOURCE / "projection.py")
    )


def test_api_does_not_import_drivers():
    """UI nesmí vědět nic o driverech, na kterých harness běží."""
    for path in (SOURCE / "api").rglob("*.py"):
        assert not any(
            module.startswith("harness.drivers") for module in imported_modules(path)
        ), f"{path.name} importuje driver"
