"""Exercise the ledger-core wheel outside the workspace and legacy ledger."""

import json
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tomllib
from zipfile import ZipFile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_MODULES = (
    "append_contracts",
    "authoring_contracts",
    "resources",
    "errors",
    "evidence",
    "query",
    "grid",
    "calculations",
    "financial_contracts",
    "typed_contracts",
    "formula_engine",
)


def run_command(command: list[str], directory: Path) -> str:
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        pytest.fail(f"{command!r}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


@pytest.fixture(scope="module")
def installed_core(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("installed-ledger-core")
    run_command(
        [
            "uv",
            "build",
            str(REPO_ROOT / "packages/ledger-core"),
            "--wheel",
            "--no-sources",
            "--offline",
            "--out-dir",
            str(directory / "wheels"),
        ],
        directory,
    )
    wheel = next((directory / "wheels").glob("*.whl"))
    environment = directory / "environment"
    run_command(["uv", "venv", "--python", sys.executable, str(environment)], directory)
    python = environment / "bin/python"
    constraints = directory / "constraints.txt"
    constraints.write_text(f"pydantic=={version('pydantic')}\n")
    run_command(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            str(wheel),
        ],
        directory,
    )
    baseline = REPO_ROOT / "tests/unit/fixtures/ledger_proposal_contracts.json"
    (directory / "proposal-contracts.json").write_text(baseline.read_text())
    return python


@pytest.fixture(scope="module")
def installed_agent(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Install agent wheels with declared dependencies outside the checkout.

    Args:
        tmp_path_factory: Factory for an isolated build and installation directory.

    Returns:
        Python executable in the fresh agent environment.
    """
    directory = tmp_path_factory.mktemp("installed-agent")
    wheels = directory / "wheels"
    run_command(
        [
            "uv",
            "build",
            str(REPO_ROOT / "klaudia"),
            "--wheel",
            "--no-sources",
            "--offline",
            "--out-dir",
            str(wheels),
        ],
        directory,
    )
    environment = directory / "environment"
    run_command(["uv", "venv", "--python", sys.executable, str(environment)], directory)
    python = environment / "bin/python"
    constraints = directory / "constraints.txt"
    packages = tomllib.loads((REPO_ROOT / "uv.lock").read_text())["package"]
    constraints.write_text(
        "\n".join(
            f"{package['name']}=={package['version']}"
            for package in packages
            if "registry" in package["source"]
        )
        + "\n"
    )
    run_command(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            *map(str, sorted(wheels.glob("*.whl"))),
        ],
        directory,
    )
    return python


def probe(python: Path, source: str) -> str:
    return run_command([str(python), "-I", "-c", source], python.parents[2])


def test_agent_wheel_preserves_namespace_and_runtime_skills(tmp_path: Path) -> None:
    run_command(
        [
            "uv",
            "build",
            str(REPO_ROOT / "klaudia"),
            "--wheel",
            "--no-sources",
            "--offline",
            "--out-dir",
            str(tmp_path),
        ],
        tmp_path,
    )
    with ZipFile(next(tmp_path.glob("klaudia-*.whl"))) as wheel:
        paths = set(wheel.namelist())
    required = {
        "klaudia/core/agent/agent.py",
        "klaudia/core/agent/documents.py",
        "klaudia/core/agent/memory.py",
        "klaudia/core/agent/reconciliation.py",
        "klaudia/core/agent/ports/documents.py",
        "klaudia/core/agent/ports/memory.py",
        "klaudia/core/agent/ports/reconciliation.py",
        "klaudia/core/agent/ports/catalogue.py",
        "klaudia/core/agent/ports/calculations.py",
        "klaudia/core/agent/ports/financial.py",
        "klaudia/core/agent/ports/append.py",
        "klaudia/core/agent/ports/authoring.py",
        "klaudia/core/agent/ports/operations.py",
        "klaudia/core/agent/ports/typed.py",
        "klaudia/core/agent/ports/errors.py",
        "klaudia/core/skills/registry.py",
        "klaudia/core/skills/table-append.md",
    }
    assert required <= paths
    assert not any(path.startswith("klaudia/interfaces/") for path in paths)


def test_installed_agent_runs_without_mcp(installed_agent: Path) -> None:
    """Run the installed agent and load each skill with MCP absent."""
    result = probe(
        installed_agent,
        '''
import asyncio
from importlib.util import find_spec
from pathlib import Path
import sys

import klaudia
from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.context import TaskContext
from klaudia.core.skills.registry import SkillRegistry
from langchain_core.language_models.fake_chat_models import FakeListChatModel

for name in ('app', 'config', 'ledger', 'ledger_core', 'asyncpg', 'fastapi', 'fastmcp', 'mcp'):
    assert find_spec(name) is None, name
assert find_spec('klaudia.interfaces') is None
assert Path(klaudia.__file__).resolve().is_relative_to(Path(sys.prefix))

registry = SkillRegistry()
assert registry.descriptions
for skill in registry.descriptions:
    loaded = registry.load(skill.name)
    assert loaded['version'] == skill.version and loaded['content']

class ToolModel(FakeListChatModel):
    """Accept tool binding for a scripted response without provider calls."""

    def bind_tools(self, tools, **kwargs):
        """Keep the scripted model when the agent supplies its tools."""
        return self

agent = MainAgent(ToolModel(responses=['Ready']), catalogue=object())
outcome = asyncio.run(agent.run('Hello', TaskContext(user_id=42)))
assert outcome.status == 'answered' and outcome.content == 'Ready'
print('isolated')
''',
    )
    assert result == "isolated"


def test_installed_capability_tools_run_without_application(
    installed_agent: Path,
) -> None:
    """Run all optional read tools from the wheel with application modules absent.

    Args:
        installed_agent: Python executable in the isolated wheel environment.
    """
    saved = json.loads(
        (REPO_ROOT / "tests/fixtures/agent_compatibility/v1/requests.json").read_text()
    )
    requests = {request["tool"]: request["input"] for request in saved}
    output = probe(
        installed_agent,
        f"requests = {requests!r}\n"
        + '''
import asyncio
import json
from importlib.util import find_spec
from unittest.mock import AsyncMock

from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.context import TaskContext
from klaudia.core.agent.documents import DocumentTools
from klaudia.core.agent.memory import MemoryTools
from klaudia.core.agent.reconciliation import PolicyReconciliationTools
from klaudia.core.agent.ports.documents import DocumentReader
from klaudia.core.agent.ports.memory import MemoryReader
from klaudia.core.agent.ports.reconciliation import PolicyReconciler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

for name in ('app', 'config', 'ledger', 'asyncpg', 'fastapi', 'fastmcp', 'mcp'):
    assert find_spec(name) is None, name

class ToolModel(FakeMessagesListChatModel):
    """Return scripted tool requests without a model provider."""

    def bind_tools(self, tools, **kwargs):
        """Accept the installed agent's complete tool set."""
        return self

documents = AsyncMock(spec=DocumentReader)
memory = AsyncMock(spec=MemoryReader)
reconciler = AsyncMock(spec=PolicyReconciler)
evidence = (
    {'documents': [], 'next_offset': None},
    {'extraction_text': 'Saved document facts', 'next_offset': None},
    {'authority': 'untrusted_context', 'accounting_validation': 'not_run'},
    {'records': [{'difference': '0.023456789012345678901'}]},
)
for method, payload in zip(
    (documents.search, documents.read, memory.read, reconciler.reconcile), evidence
):
    method.return_value = payload
archive = DocumentTools(documents).tools
memory_tool = MemoryTools(memory).read_tool
policy_tool = PolicyReconciliationTools(reconciler).tool
names = [tool.name for tool in (*archive, memory_tool, policy_tool)]
model = ToolModel(responses=[
    *[
        AIMessage(content='', tool_calls=[{
            'name': name, 'args': requests[name], 'id': name,
        }])
        for name in names
    ],
    AIMessage(content='Evidence read.'),
])
agent = MainAgent(
    model, catalogue=object(), archive_tools=archive,
    memory_tool=memory_tool, reconciliation_tool=policy_tool,
)
outcome = asyncio.run(agent.run('Read evidence', TaskContext(user_id=42)))
assert outcome.status == 'answered'
assert list(outcome.tools_called) == names
assert tuple(json.loads(entry[2]) for entry in outcome.tool_evidence) == evidence
print('isolated-capabilities')
''',
    )
    assert output == "isolated-capabilities"


def test_installed_ledger_tools_run_without_ledger(installed_agent: Path) -> None:
    """Run read and write tools with only fake consumer capabilities installed.

    Args:
        installed_agent: Python executable in the isolated wheel environment.
    """
    saved = json.loads(
        (REPO_ROOT / "tests/fixtures/agent_compatibility/v1/requests.json").read_text()
    )
    requests = {request["tool"]: request["input"] for request in saved}
    output = probe(
        installed_agent,
        f"requests = {requests!r}\n"
        + '''
import asyncio
import json
from importlib.util import find_spec
from unittest.mock import AsyncMock

from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.authoring import AuthoringReader
from klaudia.core.agent.context import TaskContext
from klaudia.core.agent.ports.catalogue import CatalogueReader
from klaudia.core.agent.ports.operations import OperationExecutor
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

for name in ('app', 'config', 'ledger', 'ledger_core', 'asyncpg', 'fastapi', 'fastmcp', 'mcp'):
    assert find_spec(name) is None, name

class ToolModel(FakeMessagesListChatModel):
    """Use saved requests and fake capabilities without provider calls."""

    def bind_tools(self, tools, **kwargs):
        """Check that every schema belongs to the consumer package."""
        assert all(tool.args_schema.__module__.startswith('klaudia.') for tool in tools)
        return self

catalogue = AsyncMock(spec=CatalogueReader)
operations = AsyncMock(spec=OperationExecutor)
authoring = AsyncMock(spec=AuthoringReader)
catalogue.search.return_value = {'candidates': []}
catalogue.inspect.return_value = {
    'table_id': 'tbl_claims', 'spreadsheet_id': 'wb_claims', 'sheet_id': 8,
    'range': 'A1:B2', 'catalogue_revision': 1, 'source_revision': 1,
    'current_sheet_revision': 1, 'freshness': 'current', 'columns': [],
}
catalogue.calculate.return_value = {'groups': [{'value': '0.123456789012345678901'}]}
catalogue.financial_query.return_value = {'records': []}
operations.inspect_typed.return_value = {'snapshot': '0' * 64}
for method in (operations.prepare, operations.prepare_authoring, operations.prepare_typed):
    method.return_value = {'operation_ref': 'prepared:original'}
receipt = {'status': 'committed', 'operation_id': 'op_original', 'target': {'sheet_id': 8}}
operations.execute.return_value = receipt
authoring.sheets.return_value = {'sheets': [], 'next_offset': None}
authoring.inspect.return_value = {'sheet_revision': 1}
names = (
    'search_resources', 'inspect_resource', 'calculate', 'financial_query',
    'inspect_typed_workbook', 'prepare_typed_edit', 'prepare_table_append',
    'list_authoring_sheets', 'inspect_sheet_region', 'prepare_table_authoring',
    'execute_operation',
)
model = ToolModel(responses=[
    *[AIMessage(content='', tool_calls=[{'name': name, 'args': requests[name], 'id': name}]) for name in names],
    AIMessage(content='Original operation confirmed.'),
])
agent = MainAgent(model, catalogue, operations=operations, authoring=authoring)
outcome = asyncio.run(agent.run('Use supplied capabilities', TaskContext(user_id=42)))
assert outcome.status == 'answered'
assert outcome.tools_called == names
assert outcome.operation_references == ('prepared:original',)
assert outcome.operation_receipts == (receipt,)
assert outcome.working_set == ()
for method in (catalogue.search, catalogue.inspect, catalogue.calculate, catalogue.financial_query,
               operations.prepare, operations.prepare_authoring, operations.prepare_typed):
    owner, request = method.await_args.args
    assert owner == 42 and type(request).__module__.startswith('klaudia.')
operations.execute.assert_awaited_once_with(42, 'prepared:original')
print('isolated-ledger-capabilities')
''',
    )
    assert output == "isolated-ledger-capabilities"


def test_wheel_installs_without_legacy_or_infrastructure(installed_core: Path) -> None:
    result = probe(
        installed_core,
        """
from importlib.util import find_spec
import json

forbidden = ('ledger', 'asyncpg', 'fastmcp', 'mcp', 'fastapi', 'app', 'klaudia', 'config')
print(json.dumps([name for name in forbidden if find_spec(name) is not None]))
""",
    )
    assert json.loads(result) == []


@pytest.mark.parametrize("module", CORE_MODULES)
def test_installed_modules_resolve_from_the_wheel(
    installed_core: Path, module: str
) -> None:
    result = probe(
        installed_core,
        f"""
from importlib import import_module
from pathlib import Path
import sys

module = import_module('ledger_core.{module}')
print(Path(module.__file__).resolve().is_relative_to(Path(sys.prefix)))
""",
    )
    assert result == "True"


def test_installed_contracts_preserve_schema_and_payloads(installed_core: Path) -> None:
    baseline = installed_core.parents[2] / "proposal-contracts.json"
    result = probe(
        installed_core,
        f"""
import json
from pathlib import Path

from ledger_core.append_contracts import AppendRows, TableAppendProposal, TableAppend
from ledger_core.authoring_contracts import AuthoringProposal

baseline = json.loads(Path({str(baseline)!r}).read_text())
for model in (AppendRows, TableAppendProposal, TableAppend, AuthoringProposal):
    assert model.model_json_schema() == baseline['schemas'][model.__name__]
    saved = baseline['payloads'][model.__name__]
    assert model.model_validate(saved['input']).model_dump_json() == saved['json']
    assert model.model_validate_json(saved['json']).model_dump_json() == saved['json']
print('compatible')
""",
    )
    assert result == "compatible"


def test_installed_aggregation_preserves_exact_decimals(installed_core: Path) -> None:
    result = probe(
        installed_core,
        """
from ledger_core.calculations import decode_calculation_grid
from ledger_core.query import AggregateQuery, aggregate_grid

grid = decode_calculation_grid('[["Amount"], [0.1], [0.2], [9007199254740993]]')
request = AggregateQuery(
    table_range='A1:A4',
    metrics=[{'column': 'Amount', 'operation': 'sum'}],
)
print(aggregate_grid(grid, request)['groups'][0]['metrics'][0]['value'])
""",
    )
    assert result == "9007199254740993.3"


def test_installed_formula_dependencies_preserve_rounding(installed_core: Path) -> None:
    result = probe(
        installed_core,
        """
from ledger_core.formula_engine import Formula, evaluate_graph

policy = {'places': 2, 'mode': 'ROUND_HALF_EVEN'}
third = Formula.model_validate({
    'operation': 'divide',
    'operands': [{'literal': '1'}, {'literal': '3'}],
    'rounding': policy,
})
total = Formula.model_validate({
    'operation': 'multiply',
    'operands': [{'cell_id': 'third'}, {'literal': '3'}],
    'rounding': policy,
})
print(evaluate_graph({}, {'third': third, 'total': total})['total']['value'])
""",
    )
    assert result == "0.99"
