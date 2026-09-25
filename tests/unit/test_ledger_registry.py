"""The sheet API connects only to the ledger transport."""

import pytest
from pydantic import ValidationError

from app.services.core.container import _build_ledger_registry, _sse_url
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


@pytest.mark.parametrize("transport, suffix", [("http", "mcp"), ("sse", "sse")])
def test_remote_ledger_registry_preserves_authentication(transport, suffix):
    """Transport selection retains the configured ledger endpoint and token."""
    registry = _build_ledger_registry(
        Settings(
            _env_file=None,
            MCP_TRANSPORT=transport,
            MCP_LEDGER_URL="https://mcp.example/ledger/mcp",
            MCP_AUTH_TOKEN="signed-token",
        )
    )
    assert registry._url == f"https://mcp.example/ledger/{suffix}"
    assert registry._auth_token == "signed-token"


def test_sse_url_keeps_explicit_path():
    """An explicit SSE URL is not rewritten twice."""
    assert _sse_url("https://mcp.example/sse/") == "https://mcp.example/sse"
