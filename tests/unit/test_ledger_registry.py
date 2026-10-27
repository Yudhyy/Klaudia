"""The sheet API connects only to the ledger transport."""

import pytest
from pydantic import ValidationError

from app.services.core.container import _build_ledger_registry
from config.settings import Settings


def test_ledger_registry_uses_the_application_database():
    """Local ledger subprocesses inherit the configured database."""
    database_url = "postgresql://fixture:fixture@localhost:5433/isolated"
    registry = _build_ledger_registry(
        Settings(_env_file=None, DATABASE_URL=database_url)
    )
    assert registry._name == "mcp-ledger"
    assert registry._stdio_env["DATABASE_URL"] == database_url
    assert registry._stdio_cwd.endswith("/mcp-ledger")


def test_database_url_cannot_be_empty():
    """Startup rejects a missing database destination."""
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None, DATABASE_URL="")


@pytest.mark.parametrize("path", ["/mcp", "/ledger/read/", "/custom?tenant=one"])
def test_remote_ledger_registry_preserves_authentication(path):
    """Transport selection retains the configured ledger endpoint and token."""
    registry = _build_ledger_registry(
        Settings(
            _env_file=None,
            MCP_TRANSPORT="http",
            MCP_LEDGER_URL=f"https://mcp.example{path}",
            MCP_AUTH_TOKEN="signed-token",
        )
    )
    assert registry._url == f"https://mcp.example{path}"
    assert registry._auth_token == "signed-token"


@pytest.mark.parametrize(
    "transport", ["sse", "SSE", "websocket", "streamable-http", ""]
)
def test_settings_reject_removed_or_unknown_transports(transport):
    with pytest.raises(ValidationError, match="stdio.*http"):
        Settings(_env_file=None, MCP_TRANSPORT=transport)


def test_removed_transport_has_migration_instructions():
    with pytest.raises(ValidationError, match="removed.*Streamable HTTP"):
        Settings(_env_file=None, MCP_TRANSPORT="sse")


@pytest.mark.parametrize("transport", ["HTTP", " http ", "STDIO"])
def test_transport_settings_normalize_supported_values(transport):
    settings = Settings(_env_file=None, MCP_TRANSPORT=transport)
    assert settings.mcp_transport == transport.strip().lower()
