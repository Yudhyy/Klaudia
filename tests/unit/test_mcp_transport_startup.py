"""Require explicit authentication for each network MCP transport."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import fastmcp
from fastmcp.server.auth.providers.jwt import JWTVerifier
import httpx
import pytest

TRANSPORT_SECRET = "http-test-verification-key-with-at-least-32-bytes"


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
    monkeypatch.delenv("FASTMCP_HOST", raising=False)
    return module


def test_network_transport_requires_bearer_configuration(entrypoint):
    """The supported network transport keeps the authentication gate."""
    with pytest.raises(RuntimeError, match="requires MCP_JWT_SECRET"):
        entrypoint.run_server("http")


def test_stdio_does_not_require_http_credentials(entrypoint):
    """The local subprocess transport remains available."""
    entrypoint.run_server("stdio")
    entrypoint.mcp.run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize("transport", ["sse", "websocket", "streamable-http", ""])
def test_server_rejects_removed_or_unknown_transports(entrypoint, transport):
    with pytest.raises(ValueError, match="stdio.*http"):
        entrypoint.run_server(transport)
    entrypoint.mcp.run.assert_not_called()


@pytest.mark.parametrize("path", ["/sse", "/ledger/sse/", "/%73se"])
def test_server_rejects_reserved_http_paths(entrypoint, monkeypatch, path):
    monkeypatch.setenv("MCP_JWT_SECRET", TRANSPORT_SECRET)
    monkeypatch.setattr(fastmcp.settings, "streamable_http_path", path)
    with pytest.raises(ValueError, match="removed.*Streamable HTTP"):
        entrypoint.run_server("http")
    entrypoint.mcp.run.assert_not_called()


def test_cli_explains_how_to_replace_sse():
    server_dir = Path(__file__).resolve().parents[2] / "mcp-ledger"
    result = subprocess.run(
        [sys.executable, "main.py", "--transport", "sse"],
        cwd=server_dir,
        env={**os.environ, "MCP_JWT_SECRET": "", "MCP_ALLOW_INSECURE_HTTP": "false"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "removed" in result.stderr and "--transport http" in result.stderr


def test_sdk_settings_include_the_parent_dotenv_file(tmp_path):
    server_dir = tmp_path / "mcp-ledger"
    server_dir.mkdir()
    source = Path(__file__).resolve().parents[2] / "mcp-ledger/main.py"
    (server_dir / "main.py").write_text(source.read_text())
    (tmp_path / ".env").write_text("FASTMCP_STREAMABLE_HTTP_PATH=/sse\n")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FASTMCP_")
    }
    environment["MCP_JWT_SECRET"] = ""
    environment["MCP_ALLOW_INSECURE_HTTP"] = "false"
    result = subprocess.run(
        [sys.executable, "main.py", "--transport", "http"],
        cwd=server_dir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert (
        "removed" in result.stderr and "FASTMCP_STREAMABLE_HTTP_PATH" in result.stderr
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_insecure_development_override_allows_loopback(entrypoint, monkeypatch, host):
    monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("FASTMCP_HOST", host)
    entrypoint.run_server("http")
    assert entrypoint.mcp.run.call_args.kwargs["host"] == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.7", "ledger.example"])
def test_insecure_development_override_cannot_bind_externally(
    entrypoint, monkeypatch, host
):
    monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("FASTMCP_HOST", host)
    with pytest.raises(RuntimeError, match="loopback"):
        entrypoint.run_server("http")
    entrypoint.mcp.run.assert_not_called()


@pytest.fixture
def http_app(entrypoint, monkeypatch):
    """Build the real HTTP app using the options passed by the entry point."""
    monkeypatch.setenv("MCP_JWT_SECRET", TRANSPORT_SECRET)
    monkeypatch.setattr(
        entrypoint.mcp,
        "auth",
        JWTVerifier(public_key=TRANSPORT_SECRET, algorithm="HS256"),
    )
    monkeypatch.setattr(fastmcp.settings, "http_allowed_hosts", None)
    monkeypatch.setattr(fastmcp.settings, "http_allowed_origins", None)
    entrypoint.run_server("http")
    options = {
        key: value
        for key, value in entrypoint.mcp.run.call_args.kwargs.items()
        if key not in {"host", "port"}
    }
    return entrypoint.mcp.http_app(**options)


async def test_external_origin_is_checked_without_explicit_allowlists(http_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_app), base_url="http://ledger.example"
    ) as client:
        response = await client.post(
            "/mcp", headers={"Origin": "https://untrusted.example"}, json={}
        )
    assert response.status_code == 403


async def test_request_without_origin_still_requires_authentication(http_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_app), base_url="http://ledger.example"
    ) as client:
        response = await client.post("/mcp", json={})
    assert response.status_code == 401


async def test_untrusted_host_is_rejected(http_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_app), base_url="http://ledger.example"
    ) as client:
        response = await client.post(
            "/mcp", headers={"Host": "untrusted.example"}, json={}
        )
    assert response.status_code == 421


@pytest.mark.parametrize(
    "origin_case",
    [("https://klaudia.example", 401), ("https://untrusted.example", 403)],
)
async def test_configured_origin_policy_is_applied(
    entrypoint, monkeypatch, origin_case
):
    origin, status = origin_case
    monkeypatch.setenv("MCP_JWT_SECRET", TRANSPORT_SECRET)
    monkeypatch.setattr(
        entrypoint.mcp,
        "auth",
        JWTVerifier(public_key=TRANSPORT_SECRET, algorithm="HS256"),
    )
    monkeypatch.setattr(fastmcp.settings, "http_allowed_hosts", ["ledger.example"])
    monkeypatch.setattr(
        fastmcp.settings, "http_allowed_origins", ["https://klaudia.example"]
    )
    entrypoint.run_server("http")
    options = {
        key: value
        for key, value in entrypoint.mcp.run.call_args.kwargs.items()
        if key not in {"host", "port"}
    }
    app = entrypoint.mcp.http_app(**options)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://10.0.0.7"
    ) as client:
        response = await client.post(
            "/mcp", headers={"Host": "ledger.example", "Origin": origin}, json={}
        )
    assert response.status_code == status
