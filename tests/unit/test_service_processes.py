"""Protect process contracts while the application becomes an installed service."""

import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest


FIXTURES = Path(__file__).parents[1] / "fixtures/service_layout"


@pytest.mark.parametrize(
    "module", json.loads((FIXTURES / "legacy_imports.json").read_text())
)
def test_supported_legacy_service_import_loads(module):
    """Load retained imports, including their parent package exports."""
    import_module(module)


def test_service_preserves_the_http_contract():
    """Keep existing route schemas and responses during the process move."""
    application = import_module("klaudia_api.main").app
    assert application.openapi() == json.loads((FIXTURES / "openapi.json").read_text())


def test_api_launches_mcp_without_a_checkout_directory():
    """Start the installed MCP module with the configured database and identity."""
    composition = import_module("klaudia_api.composition.api")
    settings = import_module("klaudia_api.composition.settings").Settings(
        _env_file=None,
        DATABASE_URL="postgresql://fixture:fixture@localhost:5433/isolated",
        JWT_SECRET="service-process-test-key-with-at-least-32-bytes",
    )
    registry = composition._build_ledger_registry(settings)
    assert registry._stdio_args == [
        "-m",
        "klaudia_api.interfaces.mcp.host",
        "--transport",
        "stdio",
    ]
    assert registry._stdio_cwd is None
    assert registry._stdio_env["DATABASE_URL"] == settings.database_url
    assert registry._stdio_env["JWT_SECRET"] == settings.jwt_secret
    assert registry._stdio_env["MCP_USER_ID"] == ""
    assert registry._stdio_env["LEDGER_SCHEMA_MODE"] == "check"


def test_relative_credentials_use_the_launch_environment_directory(
    tmp_path, monkeypatch
):
    """Resolve a relative credential file without the repository source path."""
    composition = import_module("klaudia_api.composition.api")
    settings_type = import_module("klaudia_api.composition.settings").Settings
    (tmp_path / ".env").write_text("STAGE=development\n")
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}\n")
    launch_directory = tmp_path / "launch"
    launch_directory.mkdir()
    monkeypatch.chdir(launch_directory)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    settings = settings_type(
        _env_file=None, GOOGLE_APPLICATION_CREDENTIALS=credentials.name
    )
    composition._ensure_gcp_credentials(settings)
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(credentials)


async def test_api_lifespan_owns_its_container(monkeypatch):
    """Attach the composed services and close their container once on shutdown."""
    factory = import_module("klaudia_api.interfaces.http.app_factory")
    container = Mock(shutdown=AsyncMock())
    settings = Mock()
    monkeypatch.setattr(factory, "get_settings", Mock(return_value=settings))
    monkeypatch.setattr(
        factory.KlaudiaContainer, "create", AsyncMock(return_value=container)
    )
    orchestrator = Mock()
    monkeypatch.setattr(factory, "KlaudiaOrchestrator", Mock(return_value=orchestrator))
    application = factory.create_app()
    async with factory.lifespan(application):
        assert application.state.container is container
        assert application.state.orchestrator is orchestrator
        container.shutdown.assert_not_awaited()
    settings.validate_production_config.assert_called_once_with()
    container.shutdown.assert_awaited_once_with()


@pytest.mark.parametrize("schema_mode", ["initialize", "check"])
async def test_mcp_owns_its_store_and_retains_schema_mode(schema_mode, monkeypatch):
    """Keep standalone setup distinct from integrated revision checks."""
    composition = import_module("klaudia_api.composition.mcp")
    store = Mock(
        connect=AsyncMock(),
        connect_existing=AsyncMock(),
        ensure_spreadsheet=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(composition, "LedgerStore", Mock(return_value=store))
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://fixture:fixture@localhost/isolated"
    )
    monkeypatch.setenv("LEDGER_SCHEMA_MODE", schema_mode)
    async with composition.ledger_lifespan(None) as active_store:
        assert active_store is store
        store.close.assert_not_awaited()
    assert store.connect.await_count == int(schema_mode == "initialize")
    assert store.connect_existing.await_count == int(schema_mode == "check")
    store.ensure_spreadsheet.assert_awaited_once_with(composition.DEFAULT_WORKSPACE)
    store.close.assert_awaited_once_with()


def test_service_preserves_application_history_assets():
    """Keep the released history and frozen schema assets byte for byte."""
    history = import_module(
        "klaudia_api.adapters.persistence.migrations"
    ).APPLICATION_HISTORY
    hashes = json.loads((FIXTURES / "migration_assets.json").read_text())
    assert {
        name: hashlib.sha256((history.directory / name).read_bytes()).hexdigest()
        for name in hashes
    } == hashes
    assert history.scripts().get_heads() == ["app_0001"]


async def test_worker_owns_and_closes_its_process_resources(monkeypatch):
    """Reuse one worker context without constructing the API container."""
    composition = import_module("klaudia_api.composition.extraction_worker")
    database = Mock(connect=AsyncMock(), close=AsyncMock(), pool=object())
    cache = Mock(connect=AsyncMock(), close=AsyncMock())
    objects = Mock(ensure_bucket=AsyncMock())
    ocr = Mock(shutdown=AsyncMock())
    monkeypatch.setattr(composition, "_ctx", None)
    monkeypatch.setattr(composition, "ApplicationDatabase", Mock(return_value=database))
    monkeypatch.setattr(composition, "DedupCache", Mock(return_value=cache))
    monkeypatch.setattr(composition, "MinIOClient", Mock(return_value=objects))
    monkeypatch.setattr(composition, "KIEClient", Mock(return_value=ocr))
    await composition._startup(None)
    context = composition.get_context()
    assert context.db is database
    assert context.cache is cache
    assert context.store is objects
    assert context.ocr is ocr
    assert composition.get_context() is context
    await composition._shutdown(None)
    await composition._shutdown(None)
    database.close.assert_awaited_once_with()
    cache.close.assert_awaited_once_with()
    ocr.shutdown.assert_awaited_once_with()
    with pytest.raises(RuntimeError, match="not initialized"):
        composition.get_context()


def test_legacy_worker_imports_do_not_register_another_task():
    """Retained imports expose the same broker, task and context accessor."""
    tasks = import_module("klaudia_api.interfaces.workers.extraction_worker")
    legacy = import_module("app.services.extraction.queue.tasks")
    state = import_module("app.services.extraction.queue.state")
    composition = import_module("klaudia_api.composition.extraction_worker")
    assert legacy.extract_page_task is tasks.extract_page_task
    assert legacy.broker is tasks.broker
    assert state.get_context is composition.get_context
    assert tasks.extract_page_task.task_name == "ocr.extract_page"
    assert tasks.extract_page_task.labels == {
        "retry_on_error": True,
        "max_retries": 3,
        "delay": 2,
    }
