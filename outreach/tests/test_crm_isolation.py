"""The CRM is never read at runtime.

scripts/derive_target_status.py is a manual step whose output is three columns
in data/targets.csv. If a pipeline module imported it, or opened the workbook
itself, then a live run would depend on a spreadsheet being present, closed, and
correct, and a stale or open workbook would silently change routing.

targets.csv is the only input to the gate and the router. This test is what
keeps that true.
"""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Everything that runs during a Phase 3 or Phase 4 run.
RUNTIME_PACKAGES = ("common", "contacts", "research", "drafts")

# Manual tooling, allowed to read the workbook.
ALLOWED = {"scripts", "tests", "tools", "legacy"}

FORBIDDEN_IMPORTS = {"openpyxl", "xlrd", "pandas", "gspread"}
FORBIDDEN_MODULE = "derive_target_status"


def runtime_modules() -> list[Path]:
    paths: list[Path] = []
    for package in RUNTIME_PACKAGES:
        for path in sorted((PROJECT_ROOT / package).rglob("*.py")):
            if not any(part in ALLOWED for part in path.parts):
                paths.append(path)
    return paths


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


@pytest.mark.parametrize("path", runtime_modules(), ids=lambda p: p.name)
def test_no_runtime_module_imports_a_spreadsheet_reader(path):
    names = _imported_names(ast.parse(path.read_text(encoding="utf-8")))
    offenders = {n for n in names if n.split(".")[0] in FORBIDDEN_IMPORTS}
    assert not offenders, (
        f"{path.name} imports {offenders}. A pipeline module must not open the CRM. "
        "Read the derived columns from data/targets.csv instead."
    )


@pytest.mark.parametrize("path", runtime_modules(), ids=lambda p: p.name)
def test_no_runtime_module_imports_the_derivation_script(path):
    names = _imported_names(ast.parse(path.read_text(encoding="utf-8")))
    assert not any(FORBIDDEN_MODULE in n for n in names), (
        f"{path.name} imports {FORBIDDEN_MODULE}. It is a standalone manual script."
    )


@pytest.mark.parametrize("path", runtime_modules(), ids=lambda p: p.name)
def test_no_runtime_module_names_the_workbook(path):
    """A hardcoded .xlsx path is the same dependency wearing a different hat."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert ".xlsx" not in node.value.lower(), (
                f"{path.name} names a workbook: {node.value!r}"
            )


def test_the_gate_and_router_read_only_targets_csv():
    """Both consume a plain mapping, so they cannot reach past targets.csv."""
    from contacts.gate import evaluate_pre_hunter_gate
    from drafts.routing import route_target

    row = {"firm": "X", "domain": "x.com", "contact_status": "cold_prospect",
           "has_known_contact": "FALSE", "contact_needs_refresh": "FALSE"}
    assert evaluate_pre_hunter_gate(row).skip is False
    assert route_target(row, slug="x") == "cold_prospect"
