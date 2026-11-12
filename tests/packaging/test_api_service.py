"""Run canonical service entrypoints without the repository on the import path."""

import json
from pathlib import Path
import shutil

from tests.packaging.test_ledger_core import (
    REPO_ROOT,
    install_agent_packages,
    probe,
    run_command,
)


def test_installed_service_loads_all_processes_and_assets(tmp_path: Path):
    """Load the service and its retained imports with no checkout source."""
    python = install_agent_packages(
        tmp_path,
        (
            "packages/ledger-core",
            "packages/ledger-postgres",
            "packages/agent-core",
            "packages/agent-skills",
            "packages/identity-core",
            "packages/workflow-core",
            "packages/memory-core",
            "packages/document-intelligence",
            "services/api",
            "mcp-ledger",
        ),
        {"klaudia-api": ("api", "worker", "mcp")},
    )
    hashes = json.loads(
        (REPO_ROOT / "tests/fixtures/service_layout/migration_assets.json").read_text()
    )
    legacy_modules = json.loads(
        (REPO_ROOT / "tests/fixtures/service_layout/legacy_imports.json").read_text()
    )
    output = probe(
        python,
        f"hashes = {hashes!r}\nlegacy_modules = {legacy_modules!r}\n"
        + """
from importlib import import_module
import hashlib
from pathlib import Path
import sys
from unittest.mock import Mock

import asyncpg
asyncpg.create_pool = Mock(side_effect=AssertionError('Imports opened a pool'))

from klaudia_api.main import app
from app.main import app as legacy_app
from klaudia_api.composition.api import KlaudiaContainer
from app.services.core.container import KlaudiaContainer as LegacyContainer
from klaudia_api.composition.settings import Settings
from config.settings import Settings as LegacySettings
from klaudia_api.interfaces.workers.extraction_worker import extract_page_task
from app.services.extraction.queue.tasks import extract_page_task as legacy_task
from klaudia_api.interfaces.mcp.ledger.server import mcp
from ledger.server import mcp as legacy_mcp
from klaudia_api.adapters.persistence.migrations import APPLICATION_HISTORY, HISTORIES

assert legacy_app is app
assert LegacyContainer is KlaudiaContainer
assert LegacySettings is Settings
assert legacy_task is extract_page_task
assert legacy_mcp is mcp
assert extract_page_task.task_name == 'ocr.extract_page'
assert list(HISTORIES) == ['ledger', 'application']
assert APPLICATION_HISTORY.directory.is_relative_to(Path(sys.prefix))
assert APPLICATION_HISTORY.scripts().get_heads() == ['app_0001']
for name, digest in hashes.items():
    assert hashlib.sha256((APPLICATION_HISTORY.directory / name).read_bytes()).hexdigest() == digest
for name in ('app', 'config', 'klaudia_api'):
    assert Path(import_module(name).__file__).resolve().is_relative_to(Path(sys.prefix))
for name in legacy_modules:
    import_module(name)
asyncpg.create_pool.assert_not_called()
print('installed-service')
""",
    )
    assert output == "installed-service"
    assert "upgrade" in run_command(
        [str(python), "-I", "-m", "klaudia_api.adapters.persistence", "--help"],
        tmp_path,
    )

    for relative in ("receipt/001-receipt.jpeg", "labels/images/001-receipt.json"):
        fixture = tmp_path / "sample-data" / relative
        fixture.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / "sample-data" / relative, fixture)
    assert (
        probe(
            python,
            """
import asyncio
import json
from pathlib import Path
from klaudia_api.adapters.documents.kie.client import KIEClient
from klaudia_api.adapters.documents.media.normalizer import to_canonical_jpg
from klaudia_api.composition.settings import Settings

client = KIEClient(Settings(_env_file=None, MOCK_KIE=True, LANGFUSE_ENABLED=False))
image = to_canonical_jpg(Path('sample-data/receipt/001-receipt.jpeg').read_bytes())
expected = json.loads(Path('sample-data/labels/images/001-receipt.json').read_text())
assert asyncio.run(client.extract_from_image(image)) == expected
assert asyncio.run(client.extract_from_image(b'unknown'))['info']['store_name'] == 'MOCK_FALLBACK'
asyncio.run(client.shutdown())
print('installed-mock-fixtures')
""",
        )
        == "installed-mock-fixtures"
    )


def test_installed_worker_does_not_require_api_or_agent_dependencies(tmp_path: Path):
    """Load the worker extra without HTTP adapters or agent runtime packages."""
    python = install_agent_packages(
        tmp_path,
        (
            "packages/ledger-core",
            "packages/ledger-postgres",
            "packages/identity-core",
            "packages/document-intelligence",
            "services/api",
        ),
        {"klaudia-api": ("worker",)},
    )
    assert (
        probe(
            python,
            """
from importlib.util import find_spec
import sys
from klaudia_api.interfaces.workers.extraction_worker import extract_page_task
from klaudia_api.composition.extraction_worker import get_context

assert extract_page_task.task_name == 'ocr.extract_page'
for name in ('fastapi', 'agent_core', 'agent_skills', 'langchain_openai', 'langchain_google_genai'):
    assert find_spec(name) is None, name
assert 'klaudia_api.composition.api' not in sys.modules
try:
    get_context()
except RuntimeError as error:
    assert 'not initialized' in str(error)
else:
    raise AssertionError('Import started worker resources')
print('installed-worker')
""",
        )
        == "installed-worker"
    )


def test_installed_mcp_does_not_require_api_or_model_dependencies(tmp_path: Path):
    """Install the retained MCP distribution with only its process requirements."""
    python = install_agent_packages(
        tmp_path,
        (
            "packages/ledger-core",
            "packages/ledger-postgres",
            "services/api",
            "mcp-ledger",
        ),
    )
    assert (
        probe(
            python,
            '''
import asyncio
from importlib.util import find_spec
from pathlib import Path
import sys
import klaudia_api
from fastmcp import Client
from klaudia_api.interfaces.mcp.ledger.server import mcp
from ledger.server import mcp as legacy

assert Path(klaudia_api.__file__).resolve().is_relative_to(Path(sys.prefix))
assert legacy is mcp
for name in ('fastapi', 'langchain_openai', 'google.genai', 'PIL', 'taskiq'):
    parent = name.split('.')[0]
    assert find_spec(parent) is None, name
assert 'klaudia_api.composition.api' not in sys.modules
assert 'klaudia_api.composition.extraction_worker' not in sys.modules

async def check_read_surface():
    """Discover the existing read surface without starting a database pool."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(server):
        """Supply no storage for transport discovery."""
        yield None

    mcp._lifespan = lifespan
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert len(tools) == 9
        assert all(tool.name.startswith('tool_') for tool in tools)

asyncio.run(check_read_surface())
print('installed-mcp')
''',
        )
        == "installed-mcp"
    )
    launch_directory = tmp_path / "launch"
    launch_directory.mkdir()
    (launch_directory / ".env").write_text(
        "LEDGER_TITLE=Installed test ledger\n"
        "MCP_JWT_SECRET=installed-dotenv-test-key-with-at-least-32-bytes\n"
    )
    launcher = launch_directory / "launch_mcp.py"
    launcher.write_text("""
import os
os.environ.pop('LEDGER_TITLE', None)
os.environ.pop('MCP_JWT_SECRET', None)
from klaudia_api.interfaces.mcp.ledger.server import LEDGER_TITLE, mcp
from fastmcp.server.auth.providers.jwt import JWTVerifier
assert LEDGER_TITLE == 'Installed test ledger'
assert isinstance(mcp.auth, JWTVerifier)
print('installed-dotenv-auth')
""")
    assert (
        run_command([str(python), "-I", str(launcher)], launch_directory)
        == "installed-dotenv-auth"
    )
