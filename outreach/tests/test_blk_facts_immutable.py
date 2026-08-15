"""Build guard: no package code may open blk_facts.json for writing."""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache"}
WRITE_METHODS = {"write_text", "write_bytes", "unlink", "rename", "replace"}


def python_files() -> list[Path]:
    return [
        path for path in PROJECT_ROOT.rglob("*.py")
        if not any(part in SKIP_DIRS for part in path.parts)
    ]


def _mentions_blk_facts(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and "blk_facts.json" in child.value
        for child in ast.walk(node)
    )


def _path_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if value is not None and _mentions_blk_facts(value):
                names.update(
                    target.id for target in targets if isinstance(target, ast.Name)
                )
    return names


def _is_blk_path(node: ast.AST, path_names: set[str]) -> bool:
    return _mentions_blk_facts(node) or (
        isinstance(node, ast.Name) and node.id in path_names
    )


def _write_mode(call: ast.Call, positional_index: int) -> bool:
    mode_node = call.args[positional_index] if len(call.args) > positional_index else next(
        (keyword.value for keyword in call.keywords if keyword.arg == "mode"),
        None,
    )
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        return False
    return any(flag in mode_node.value for flag in "wax+")


@pytest.mark.parametrize("path", python_files(), ids=lambda path: path.name)
def test_blk_facts_is_never_opened_for_writing(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    path_names = _path_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
            assert not (_is_blk_path(node.args[0], path_names) and _write_mode(node, 1)), (
                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} opens "
                "blk_facts.json in write mode. Only a human may edit that file."
            )
        if isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if node.func.attr == "open":
                assert not (_is_blk_path(receiver, path_names) and _write_mode(node, 0)), (
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} opens "
                    "blk_facts.json in write mode. Only a human may edit that file."
                )
            if node.func.attr in WRITE_METHODS:
                assert not _is_blk_path(receiver, path_names), (
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} mutates "
                    "blk_facts.json. Only a human may edit that file."
                )
