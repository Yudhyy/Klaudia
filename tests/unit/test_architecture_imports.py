"""Protect the agreed dependency direction and persistence-free agent imports."""

import ast
from collections.abc import Iterator
from importlib.util import resolve_name
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_IMPORT_ROOT = REPO_ROOT / "packages/ledger-core/src"
CORE_ROOT = CORE_IMPORT_ROOT / "ledger_core"
LEDGER_IMPLEMENTATIONS = (
    "ledger.operations",
    "ledger.table_operations",
    "ledger.authoring",
    "ledger.store",
    "ledger.catalogue",
    "ledger.typed_cells",
    "ledger.typed_storage",
    "ledger.financial_store",
    "ledger.authorization",
    "ledger.server",
)
PURE_MODULES = (
    "append_contracts",
    "authoring_contracts",
    "query",
    "calculations",
    "financial_contracts",
    "typed_contracts",
    "resources",
    "errors",
    "evidence",
    "grid",
    "formula_engine",
)


def imported_modules(source: str, package: str) -> Iterator[tuple[int, str]]:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package)
            yield node.lineno, module
            for alias in node.names:
                yield node.lineno, f"{module}.{alias.name}"


@pytest.mark.parametrize(
    ("directory", "forbidden"),
    [
        ("mcp-ledger/ledger", ("app", "klaudia", "config")),
        ("klaudia", ("app", "config", "fastmcp", "mcp", "ledger", "ledger_core")),
        ("klaudia/core", ("asyncpg", "ledger", "ledger_core")),
        ("klaudia/core/agent/tools.py", ("ledger", "ledger_core")),
        ("klaudia/core/agent/ports", ("ledger", "ledger_core")),
        ("app/services/core/archive_tools.py", ("langchain_core",)),
        ("app/services/memory/tools.py", ("langchain_core",)),
        ("app/services/memory/reconciliation.py", ("langchain_core",)),
        *[
            (
                f"mcp-ledger/ledger/{module}.py",
                ("asyncpg", "fastmcp", "mcp", "fastapi", *LEDGER_IMPLEMENTATIONS),
            )
            for module in PURE_MODULES
        ],
    ],
)
def test_module_dependency_direction(directory, forbidden):
    violations = []
    root = REPO_ROOT / directory
    sources = sorted(root.rglob("*.py")) if root.is_dir() else [root]
    import_root = (
        REPO_ROOT / "mcp-ledger" if directory.startswith("mcp-ledger/") else REPO_ROOT
    )
    for source in sources:
        package = ".".join(source.parent.relative_to(import_root).parts)
        for line, imported in imported_modules(source.read_text(), package):
            if any(
                imported == name or imported.startswith(name + ".")
                for name in forbidden
            ):
                violations.append(
                    f"{source.relative_to(REPO_ROOT)}:{line}: forbidden import {imported}"
                )
    assert not violations, "\n".join(violations)


def test_entire_core_package_has_only_pure_dependencies():
    sources = sorted(CORE_ROOT.rglob("*.py"))
    assert sources, "The independently installable core package must exist"
    allowed = sys.stdlib_module_names | {"pydantic", "ledger_core"}
    violations = []
    for source in sources:
        package = ".".join(source.parent.relative_to(CORE_IMPORT_ROOT).parts)
        for line, imported in imported_modules(source.read_text(), package):
            if imported.split(".")[0] not in allowed:
                violations.append(
                    f"{source.relative_to(REPO_ROOT)}:{line}: impure import {imported}"
                )
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("package", "allowed"),
    [
        ("agent-core", {"agent_core", "agent_skills", "langchain_core", "pydantic"}),
        ("agent-skills", {"agent_skills", "pydantic"}),
    ],
)
def test_agent_packages_have_only_consumed_dependencies(package, allowed):
    """Check every canonical module, including imports inside functions."""
    import_root = REPO_ROOT / "packages" / package / "src"
    sources = sorted(import_root.rglob("*.py"))
    assert sources, "The canonical package must contain its implementation"
    violations = []
    for source in sources:
        namespace = ".".join(source.parent.relative_to(import_root).parts)
        for line, imported in imported_modules(source.read_text(), namespace):
            if imported.split(".")[0] not in sys.stdlib_module_names | allowed:
                violations.append(f"{source}:{line}: forbidden import {imported}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "package", "expected"),
    [
        (
            "def validate():\n    from .operations import AppendRows\n",
            "ledger",
            [(2, "ledger.operations"), (2, "ledger.operations.AppendRows")],
        ),
        (
            "from . import operations as persistence\n",
            "ledger",
            [(1, "ledger"), (1, "ledger.operations")],
        ),
        (
            "from ..catalogue import CatalogueStore\n",
            "ledger.contracts",
            [(1, "ledger.catalogue"), (1, "ledger.catalogue.CatalogueStore")],
        ),
    ],
)
def test_relative_imports_resolve_to_their_owning_package(source, package, expected):
    assert list(imported_modules(source, package)) == expected


@pytest.mark.parametrize(
    "module",
    [
        *[f"ledger_core.{module}" for module in PURE_MODULES],
        "ledger.append_contracts",
        "ledger.authoring_contracts",
        "klaudia.core.agent.agent",
        "klaudia.core.agent.documents",
        "klaudia.core.agent.memory",
        "klaudia.core.agent.reconciliation",
        "klaudia.core.agent.ports.documents",
        "klaudia.core.agent.ports.memory",
        "klaudia.core.agent.ports.reconciliation",
        "klaudia.core.agent.ports.catalogue",
        "klaudia.core.agent.ports.operations",
        "klaudia.core.agent.ports.authoring",
        "klaudia.core.agent.ports.typed",
    ],
)
def test_contracts_and_agent_import_without_infrastructure(module):
    script = """
import importlib
from importlib.abc import MetaPathFinder
import sys

sys.path[:0] = sys.argv[2:]

class NoInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = {'asyncpg', 'fastmcp', 'mcp', 'fastapi', 'app', 'config'}
        if sys.argv[1].startswith(('ledger_core.', 'klaudia.')):
            blocked.add('ledger')
        if sys.argv[1].startswith('klaudia.'):
            blocked.add('ledger_core')
        if fullname.split('.')[0] in blocked:
            raise ImportError(f'Infrastructure import: {fullname}')

sys.meta_path.insert(0, NoInfrastructure())
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            module,
            str(REPO_ROOT),
            str(REPO_ROOT / "mcp-ledger"),
            str(CORE_IMPORT_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
