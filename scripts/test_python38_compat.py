#!/usr/bin/env python3
"""Static regression checks for the remote Python 3.8 execution host."""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripts.agents.self_evolved_abc.flow.planner_batch import next_probe_cycle_id
from scripts.agents.self_evolved_abc.flow.source_patch_runner import (
    extract_source_patch_targets,
)
from scripts.summarize_cycle import discover_designs
EAGER_BUILTIN_GENERICS = {
    "dict",
    "frozenset",
    "list",
    "set",
    "tuple",
    "type",
}
PYTHON39_ONLY_METHODS = {
    "is_relative_to",
    "removeprefix",
    "removesuffix",
    "with_stem",
}


def production_files() -> Tuple[Path, ...]:
    return tuple(sorted((PROJECT_ROOT / "scripts").rglob("*.py")))


def imported_runtime_generics(tree: ast.AST) -> Set[str]:
    """Return pre-PEP-585 collection names imported by one module."""

    names: Set[str] = set()
    for node in getattr(tree, "body", ()):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in {"collections", "collections.abc"}:
            continue
        for alias in node.names:
            names.add(alias.asname or alias.name)
    return names


class RuntimeExpressionVisitor(ast.NodeVisitor):
    """Visit evaluated expressions while deliberately skipping annotations."""

    def __init__(self, forbidden_generics: Iterable[str]) -> None:
        self.forbidden_generics = set(forbidden_generics)
        self.eager_generic_lines: List[Tuple[int, str]] = []
        self.python39_method_lines: List[Tuple[int, str]] = []

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        if isinstance(node.value, ast.Name):
            name = node.value.id
            if name in self.forbidden_generics:
                self.eager_generic_lines.append((node.lineno, name))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in PYTHON39_ONLY_METHODS:
                self.python39_method_lines.append((node.lineno, method))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self.visit(node.value)

    def _visit_function(self, node: ast.AST) -> None:
        for decorator in getattr(node, "decorator_list", ()):
            self.visit(decorator)
        arguments = getattr(node, "args")
        for default in arguments.defaults:
            self.visit(default)
        for default in arguments.kw_defaults:
            if default is not None:
                self.visit(default)
        for statement in getattr(node, "body"):
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for statement in node.body:
            self.visit(statement)


class Python38CompatibilityTests(unittest.TestCase):
    def test_production_sources_parse_with_python38_grammar(self) -> None:
        for path in production_files():
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                source = path.read_text(encoding="utf-8")
                ast.parse(source, filename=str(path), feature_version=8)

    def test_runtime_expressions_avoid_post_python38_apis(self) -> None:
        failures: Dict[str, List[str]] = {}
        for path in production_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path), feature_version=8)
            forbidden = EAGER_BUILTIN_GENERICS | imported_runtime_generics(tree)
            visitor = RuntimeExpressionVisitor(forbidden)
            visitor.visit(tree)
            messages = [
                "line {}: eager {}[...]".format(line, name)
                for line, name in visitor.eager_generic_lines
            ]
            messages.extend(
                "line {}: .{}() requires Python 3.9".format(line, method)
                for line, method in visitor.python39_method_lines
            )
            if messages:
                failures[str(path.relative_to(PROJECT_ROOT))] = messages
        self.assertEqual(failures, {})

    def test_replaced_prefix_and_suffix_paths_keep_their_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            experiments = repo_root / "experiments"
            (experiments / "probe_001").mkdir(parents=True)
            (experiments / "probe_009").mkdir()
            (experiments / "probe_invalid").mkdir()
            self.assertEqual(next_probe_cycle_id(repo_root), "probe_010")

            logs = repo_root / "logs"
            outputs = repo_root / "outputs"
            logs.mkdir()
            outputs.mkdir()
            (logs / "alpha.vanilla.log").touch()
            (logs / "beta.flowtune.log").touch()
            (outputs / "gamma.vanilla.aig").touch()
            (outputs / "delta.flowtune.aig").touch()
            (outputs / "epsilon.flowtune.script").touch()
            self.assertEqual(
                discover_designs(logs, outputs, {"zeta": "skipped"}),
                ["alpha", "beta", "delta", "epsilon", "gamma", "zeta"],
            )

            patch_plan = repo_root / "source_patch_plan.md"
            patch_plan.write_text(
                "# Patch Plan\n\n"
                "## Proposed Target Files\n\n"
                "- third_party/FlowTune/src/src/opt/example.c\n",
                encoding="utf-8",
            )
            self.assertEqual(
                extract_source_patch_targets(patch_plan),
                ("third_party/FlowTune/src/src/opt/example.c",),
            )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    sys.exit(not result.result.wasSuccessful())
