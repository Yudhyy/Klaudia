"""Require explicit authentication for each network MCP transport."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def entrypoint(monkeypatch):
    """Load the ledger entry point without starting a server."""
    path = Path(__file__).resolve().parents[2] / "mcp-ledger/main.py"
    specification = importlib.util.spec_from_file_location("ledger_entrypoint", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    monkeypatch.setattr(module.mcp, "run", Mock())
    monkeypatch.delenv("MCP_JWT_SECRET", raising=False)
    monkeypatch.delenv("MCP_ALLOW_INSECURE_HTTP", raising=False)
    return module


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_network_transport_requires_bearer_configuration(entrypoint, transport):
    """SSE cannot bypass the network authentication startup gate."""
    with pytest.raises(RuntimeError, match="requires MCP_JWT_SECRET"):
        entrypoint.run_server(transport)


def test_stdio_does_not_require_http_credentials(entrypoint):
    """The local subprocess transport remains available."""
    entrypoint.run_server("stdio")
    entrypoint.mcp.run.assert_called_once_with(transport="stdio")
